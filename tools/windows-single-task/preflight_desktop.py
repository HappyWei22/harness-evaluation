"""Run the zero-submission desktop preflight on the Windows host.

The host entry takes the shared startup lock, refuses to start while an attempt is
open, and then hands the Hyper-V work to one elevated PowerShell process. The
elevated stage writes its report into the same directory, so the unelevated entry
only reads evidence and never touches Hyper-V itself.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

from attempt_ledger import require_no_open_attempt
from dsh_checkpoints import DEFAULT_DSH_BASELINE, DSH_CHECKPOINTS
from host_powershell import windows_powershell
from startup_lock import StartupLockBusy, WINDOWS_LOCK_DATABASE, acquire_windows_startup_lock


VM_ID = "72d3b668-2aa5-4719-bad7-95e6ba990090"
VM_NAME = "Harness-DSH-Win11"
EXPECTED_PROFILE = r"C:\Users\alex_"
SHORTCUT_NAME = "DeepSeek Harness - Accessibility.lnk"


def build_request(work: Path, mode: str, keep_running: bool = False, *,
                  baseline: str = DEFAULT_DSH_BASELINE) -> dict:
    started = datetime.now(timezone.utc)
    run_id = f"PRE-DESKTOP-{started:%Y%m%dT%H%M%S}-{uuid4().hex[:8]}"
    checkpoint_name, checkpoint_id = DSH_CHECKPOINTS[baseline]
    return {
        "RunId": run_id,
        "Mode": mode,
        "KeepRunning": keep_running,
        "Activity": "interface_preflight",
        "CaseVersion": (
            "desktop automation reachability v1"
            if mode == "fresh"
            else "desktop automation reachability v1 (after operator sign-in)"
        ),
        "ProductVersion": "0.2.2.0 installed (not queried by the probe)",
        "VmId": VM_ID,
        "VmName": VM_NAME,
        "CheckpointId": checkpoint_id,
        "CheckpointName": checkpoint_name,
        "ExpectedProfile": EXPECTED_PROFILE,
        "ShortcutName": SHORTCUT_NAME,
        "AccessibilityArgument": "--force-renderer-accessibility=complete",
        "WorkingRoot": rf"{EXPECTED_PROFILE}\AppData\Local\Temp\{run_id}",
        "WorkDirectory": str(work),
        "RequestedAt": started.isoformat(),
    }


def write_stage(work: Path, request: dict, package: Path) -> Path:
    """Write the elevated stage: parameter block, credential helper, then the preflight."""
    helper = (package / "harness_guest_credential.ps1").read_text(encoding="utf-8")
    stage = "\n".join(
        [
            "param([string]$WorkDirectory, [string]$PackageDirectory)",
            "$ErrorActionPreference = 'Stop'",
            helper,
            "$log = Join-Path $WorkDirectory 'host-log.txt'",
            "$code = 1",
            "$failure = $null",
            "Start-Transcript -LiteralPath $log -Force | Out-Null",
            "try {",
            "    $source = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'preflight_desktop.ps1'))",
            "    $null = & ([scriptblock]::Create($source)) -WorkDirectory $WorkDirectory -PackageDirectory $PackageDirectory",
            "    $code = 0",
            "} catch {",
            "    $failure = $_ | Out-String",
            "} finally {",
            "    Stop-Transcript | Out-Null",
            "    if ($failure) { Add-Content -LiteralPath $log -Value $failure -Encoding UTF8 }",
            "    [pscustomobject]@{ ExitCode = $code; FinishedAt = (Get-Date).ToString('o') } | "
            "ConvertTo-Json | Set-Content -LiteralPath (Join-Path $WorkDirectory 'elevated-exit.json') -Encoding UTF8",
            "}",
            "exit $code",
            "",
        ]
    )
    path = work / "run-preflight.ps1"
    path.write_text(stage, encoding="utf-8")
    return path


def launch_elevated(stage: Path, work: Path, package: Path) -> None:
    powershell = windows_powershell()
    arguments = [
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(stage),
        "-WorkDirectory",
        str(work),
        "-PackageDirectory",
        str(package),
    ]
    quoted = ", ".join("'" + item.replace("'", "''") + "'" for item in arguments)
    command = (
        f"Start-Process -FilePath '{powershell}' -Verb RunAs "
        f"-ArgumentList @({quoted}) -WorkingDirectory '{work}'"
    )
    subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-Command", command], check=False)
    # Start-Process without -Wait returns as soon as the shell accepts the request.
    # Evidence files are polled below so a UAC delay does not look like a crash.


def wait_for_report(work: Path, timeout_seconds: int) -> dict:
    report_path = work / "preflight-report.json"
    log_path = work / "host-log.txt"
    exit_path = work / "elevated-exit.json"
    deadline = time.monotonic() + timeout_seconds
    last_stage = None
    while time.monotonic() < deadline:
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_bytes())
            except ValueError:
                report = None
            if report and report.get("FinishedAt"):
                return report
            if report and report.get("Stage") != last_stage:
                last_stage = report.get("Stage")
                print(f"stage: {last_stage}", flush=True)
        elif exit_path.is_file() and time.monotonic() + 20 < deadline:
            time.sleep(3)
            if not report_path.is_file():
                detail = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
                raise RuntimeError(f"The elevated stage exited without a report.\n{detail}")
        time.sleep(2)
    raise TimeoutError("The desktop preflight did not finish inside the allowed time.")


def summarize(report: dict) -> int:
    print("")
    print(f"preflight        : {report['RunId']} ({report.get('Mode')})")
    print(f"verdict          : {report['Verdict']} (stage {report['Stage']})")
    print(f"error            : {report['Error']}")
    boot = report.get("BootStage") or {}
    print(f"boot event       : {boot.get('BootEvent')}  autologon={boot.get('AutoAdminLogon')} user={boot.get('DefaultUserName')}")
    session = report.get("SessionStage") or {}
    print(f"desktop session  : explorer={session.get('ExplorerDesktopCount')} logonui={session.get('LogonUiSessions')}")
    launch = report.get("LaunchStage") or {}
    print(f"task launch      : state={launch.get('StateAtStart')} runlevel={launch.get('RunLevel')}")
    if launch.get("TaskStateWithoutReport"):
        print(f"task without report: {launch['TaskStateWithoutReport']}")
    print(f"task argument    : {launch.get('Argument')}")
    print(f"probe self-test  : {launch.get('SelfTest')}")
    for name, text in (report.get("ProbeDiagnostics") or {}).items():
        body = (text or "").strip()
        limit = 2000 if name.endswith("transcript.txt") else 600
        print(f"--- {name} ---")
        print(body[-limit:] if len(body) > limit else body)
    print(f"stale residue    : {report.get('StaleResidue')}")
    print(f"workspace method : {report.get('WorkspaceMethod')}")
    print(f"composer focus   : {report.get('ComposerStage')}")
    print(f"last good step   : {report.get('LastSuccessfulStep')}")
    print(f"blocking step    : {report.get('BlockingStep')}")
    print(f"cleanup          : {report.get('Cleanup')} error={report.get('CleanupError')}")
    print(f"manual steps     : {report.get('ManualSteps')}")
    print(f"submissions      : formal={report['FormalSubmissions']} diagnostic={report['DiagnosticSubmissions']}")
    final = report.get("FinalRestore") or {}
    print(f"final restore    : {final.get('Verdict')} boot={final.get('BootEvent')} residue={final.get('Residue')}")
    return 0 if report["Verdict"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=Path(r"E:\Harness-Eval\Control\preflights"))
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--baseline", choices=tuple(DSH_CHECKPOINTS), default=DEFAULT_DSH_BASELINE,
                        help="Checkpoint to restore (default: %(default)s)")
    parser.add_argument("--mode", choices=("fresh", "post_login"), default="fresh",
                        help="fresh restores the baseline first; post_login continues from a signed-in guest")
    parser.add_argument("--keep-running", action="store_true",
                        help="Leave the guest running at the desktop after the pass instead of shutting it down and restoring")
    parser.add_argument("--request-only", action="store_true", help="Write the request without starting the elevated stage")
    args = parser.parse_args()
    if os.name != "nt":
        return 2

    package = Path(__file__).resolve().parent
    control = WINDOWS_LOCK_DATABASE.parent
    try:
        with acquire_windows_startup_lock():
            require_no_open_attempt(control / "attempts.sqlite3")
            work = args.evidence_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            work.mkdir(parents=True, exist_ok=True)
            request = build_request(work, args.mode, args.keep_running, baseline=args.baseline)
            (work / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
            stage = write_stage(work, request, package)
            print(f"request {request['RunId']}: {work}", flush=True)
            if args.request_only:
                print("The elevated stage was not started. Run it from an administrator shell:")
                print(f'  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "{stage}" '
                      f'-WorkDirectory "{work}" -PackageDirectory "{package}"')
                return 0
            launch_elevated(stage, work, package)
            print("Waiting for the elevated stage. Confirm the Windows administrator prompt.", flush=True)
            report = wait_for_report(work, args.timeout)
            return summarize(report)
    except StartupLockBusy:
        print("The desktop preflight is blocked by the startup lock.", file=sys.stderr)
        return 3
    except (OSError, ValueError, RuntimeError, TimeoutError, sqlite3.Error) as error:
        print(f"Desktop preflight stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
