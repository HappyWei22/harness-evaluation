"""Run one prepared task through the controlled DSH desktop and close its record.

The entry owns everything that must be durable: it freezes the task, keeps the request
stage on disk, and is the only process that writes the attempt ledger, the one-time
submission permit, and the four closing receipts. The elevated stage only drives the
virtual machine, and it pauses before sending until this process authorises the send, so
"the permit is consumed" always happens strictly before "the interface sends".
"""

import argparse
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from attempt_completion import COMPLETION_EVENTS, CompletionNotVerified, close_completed_attempt, read_completion_receipt
from attempt_ledger import UnclosedAttempt, register_attempt, require_no_open_attempt
from dsh_checkpoints import DEFAULT_DSH_BASELINE, DSH_CHECKPOINTS
from host_powershell import windows_powershell
from inspect_task_native import inspect_snapshot, read_snapshot
from startup_lock import StartupLockBusy, WINDOWS_LOCK_DATABASE, acquire_startup_lock
from submission_permit import (
    PermitUnavailable,
    begin_submission,
    issue_submission_permit,
)
from task_request import (
    RequestConflict,
    advance,
    freeze_request,
    load_state,
    new_state,
    save_state,
    sha256_bytes,
    sha256_file,
    stage_reached,
    summarize,
    verify_outputs,
)
from task_isolation import BLOCKED_HRESULTS, require_matching_admission
from task_restore_evidence import REUSED_RECORD_ERROR, verify_reused_record_restore

VM_ID = "72d3b668-2aa5-4719-bad7-95e6ba990090"
VM_NAME = "Harness-DSH-Win11"
EXPECTED_PROFILE = r"C:\Users\alex_"
SHORTCUT_NAME = "DeepSeek Harness - Accessibility.lnk"


def control_root() -> Path:
    return WINDOWS_LOCK_DATABASE.parent


def ledger_path(root: Path) -> Path:
    return root / "attempts.sqlite3"


def request_store(root: Path, request_id: str) -> Path:
    return root / "requests" / request_id


def stage_request(root: Path, record: dict, attempt_prefix: str,
                  prompt_timeout: float, task_timeout: float,
                  wait_signin: int = 0, attach: bool = False, *,
                  baseline: str = DEFAULT_DSH_BASELINE) -> dict:
    """What the elevated stage needs. The prompt travels as a file, never as a command line."""
    checkpoint_name, checkpoint_id = DSH_CHECKPOINTS[baseline]
    protected_files = []
    if not attach:
        for role, path in (("task_manifest", Path(record["task_file"])),
                           ("frozen_request", request_store(root, record["request_id"]) / "request.json")):
            path = path.resolve()
            protected_files.append({"Role": role, "Path": str(path), "Sha256": sha256_file(path)})
    return {
        "RequestId": record["request_id"],
        "AttemptPrefix": attempt_prefix,
        "Activity": "diagnostic" if attach else "formal",
        "Attach": bool(attach),
        "IsolationProbeId": None if attach else f"PRE-{uuid4().hex[:16]}",
        "ProtectedFiles": protected_files,
        "BlockedHResults": BLOCKED_HRESULTS,
        "CaseVersion": f"single task {record['task_id']} v{record['task_version']}",
        "ProductVersion": "0.2.2.0 installed (not queried by the stage)",
        "TaskId": record["task_id"],
        "TaskVersion": record["task_version"],
        "PromptSha256": record["prompt_sha256"],
        "Inputs": record["inputs"],
        "Outputs": record["outputs"],
        "VmId": VM_ID,
        "VmName": VM_NAME,
        "CheckpointId": checkpoint_id,
        "CheckpointName": checkpoint_name,
        "ExpectedProfile": EXPECTED_PROFILE,
        "ShortcutName": SHORTCUT_NAME,
        "AccessibilityArgument": "--force-renderer-accessibility=complete",
        "StageDirectory": str(root / "requests" / record["request_id"] / "stage"),
        "WorkingRoot": rf"{EXPECTED_PROFILE}\AppData\Local\Temp\{record['request_id']}",
        "PromptTimeoutSeconds": int(prompt_timeout),
        "TaskTimeoutSeconds": int(task_timeout),
        "PythonExecutable": sys.executable,
        "WaitForSignInSeconds": int(wait_signin),
        "RequestedAt": datetime.now(timezone.utc).isoformat(),
    }


def write_wrapper(store: Path, package: Path) -> Path:
    """The elevated process: credential helper, the stage script, then an exit marker."""
    helper = (package / "harness_guest_credential.ps1").read_text(encoding="utf-8")
    text = "\n".join([
        "param([string]$StageDirectory, [string]$PackageDirectory)",
        "$ErrorActionPreference = 'Stop'",
        helper,
        "$log = Join-Path $StageDirectory 'host-log.txt'",
        "$code = 1",
        "$failure = $null",
        "Start-Transcript -LiteralPath $log -Force | Out-Null",
        "try {",
        "    $source = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'run_dsh_task_stage.ps1'))",
        "    $null = & ([scriptblock]::Create($source)) -StageDirectory $StageDirectory -PackageDirectory $PackageDirectory",
        "    $code = 0",
        "} catch {",
        "    $failure = $_ | Out-String",
        "} finally {",
        "    Stop-Transcript | Out-Null",
        "    if ($failure) { Add-Content -LiteralPath $log -Value $failure -Encoding UTF8 }",
        "    [pscustomobject]@{ ExitCode = $code; FinishedAt = (Get-Date).ToString('o') } | "
        "ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StageDirectory 'elevated-exit.json') -Encoding UTF8",
        "}",
        "exit $code",
        "",
    ])
    path = store / "run-task-stage.ps1"
    path.write_text(text, encoding="utf-8")
    return path


def launch_elevated(wrapper: Path, stage_directory: Path, package: Path) -> None:
    powershell = windows_powershell()
    arguments = ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
                 "-StageDirectory", str(stage_directory), "-PackageDirectory", str(package)]
    quoted = ", ".join("'" + item.replace("'", "''") + "'" for item in arguments)
    command = (f"Start-Process -FilePath '{powershell}' -Verb RunAs "
               f"-ArgumentList @({quoted}) -WorkingDirectory '{stage_directory}'")
    subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-Command", command], check=False)


def launch_elevated_script(script: Path, arguments: list[str]) -> None:
    """Start one prepared script elevated and return without waiting for it."""
    powershell = windows_powershell()
    full = ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments]
    quoted = ", ".join("'" + item.replace("'", "''") + "'" for item in full)
    command = (f"Start-Process -FilePath '{powershell}' -Verb RunAs "
               f"-ArgumentList @({quoted}) -WorkingDirectory '{script.parent}'")
    subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-Command", command], check=False)


def collect_sources(request_id: str, store: Path, root: Path) -> int:
    """Copy the guest probe directory back so a failed run can be explained afterwards.

    The probe writes its report and screenshots inside the guest, and reading them needs the same
    elevation the stage already has, so this runs as its own elevated helper.
    """
    destination = store / "guest"
    destination.mkdir(parents=True, exist_ok=True)
    credential = (Path(os.environ["LOCALAPPDATA"]) / "Harness-Eval" / "Credentials"
                  / f"{VM_NAME}.xml")
    guest_path = rf"C:\Users\alex_\AppData\Local\Temp\{request_id}\probe"
    text = "\n".join([
        "param([string]$GuestPath, [string]$Destination, [string]$CredentialPath)",
        "$ErrorActionPreference = 'Stop'",
        f"$log = Join-Path '{destination}' 'collect-log.txt'",
        "Start-Transcript -LiteralPath $log -Force | Out-Null",
        "try {",
        "    $cred = Import-Clixml $CredentialPath",
        f"    $vm = Get-VM -Name '{VM_NAME}'",
        "    $session = New-PSSession -VMId $vm.Id -Credential $cred",
        "    if (-not (Test-Path -LiteralPath $GuestPath -PathType Container -ErrorAction SilentlyContinue)) { }",
        "    Copy-Item -FromSession $session -LiteralPath $GuestPath -Destination $Destination -Recurse -Force",
        "    Remove-PSSession $session",
        "    'copied' | Write-Output",
        "} catch {",
        "    ('ERROR: ' + $_.Exception.Message) | Write-Output",
        "} finally {",
        "    Stop-Transcript | Out-Null",
        f"    'done' | Set-Content -LiteralPath (Join-Path '{destination}' 'finished.txt') -Encoding UTF8",
        "}",
        "",
    ])
    script = store / "collect-guest.ps1"
    script.write_text(text, encoding="utf-8")
    launch_elevated_script(script, [guest_path, str(destination), str(credential)])
    print(f"Waiting for the elevated collector. Confirm the Windows administrator prompt.")
    marker = destination / "finished.txt"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if marker.exists():
            break
        time.sleep(2)
    if not marker.exists():
        print("The collector did not finish inside the allowed time.", file=sys.stderr)
        return 1
    landed = destination / "probe"
    print(f"Collected: {landed}")
    for item in sorted(landed.rglob("*")) if landed.exists() else []:
        if item.is_file():
            print(f"  {item.relative_to(destination)}  {item.stat().st_size} B")
    return 0


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def wait_for_ready(report_path: Path, ready_path: Path, timeout: float) -> dict:
    """Block until the guest has pasted the prompt and read it back, or the stage stops."""
    deadline = time.monotonic() + timeout
    last_stage = None
    while time.monotonic() < deadline:
        ready = read_json(ready_path)
        if ready:
            return ready
        report = read_json(report_path)
        if report and report.get("Stage") != last_stage:
            last_stage = report.get("Stage")
            print(f"stage: {last_stage}", flush=True)
        if report and report.get("FinishedAt"):
            raise RuntimeError(f"The stage stopped before the prompt was ready: {report.get('Error')}")
        time.sleep(2)
    raise TimeoutError("The prompt was not pasted inside the allowed time.")


def wait_for_report(report_path: Path, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_stage = None
    while time.monotonic() < deadline:
        report = read_json(report_path)
        if report and report.get("FinishedAt"):
            return report
        if report and report.get("Stage") != last_stage:
            last_stage = report.get("Stage")
            print(f"stage: {last_stage}", flush=True)
        time.sleep(2)
    raise TimeoutError("The task stage did not finish inside the allowed time.")


def write_receipts(directory: Path, attempt_id: str, report: dict, results: dict) -> None:
    """Write the four closing receipts from verified evidence."""
    directory.mkdir(parents=True, exist_ok=True)
    dispatch = report.get("Dispatch") or {}
    completion = report.get("Completion") or {}
    acceptance = completion["Acceptance"]
    cleanup = report.get("FinalRestore") or {}
    receipts = {
        "accepted": {
            "event": "accepted",
            "detail": {
                "native_acceptance": acceptance,
                "user_message_sequence": completion["UserMessageSequence"],
                "trace_sha256": completion["TraceSha256"],
                "invoke_started_at": dispatch.get("InvokeStartedAt"),
                "invoke_returned_at": dispatch.get("InvokeReturnedAt"),
                "prompt_readback_sha256": dispatch.get("ReadbackSha256"),
            },
        },
        "terminated": {
            "event": "terminated",
            "detail": {
                "basis": completion["Basis"],
                "session_id": acceptance["SessionId"],
                "turn": completion["Turn"],
                "native_turn_end": completion["NativeTurnEnd"],
                "turn_status": completion["TurnStatus"],
                "trace_sha256": completion["TraceSha256"],
            },
        },
        "archived": {
            "event": "archived",
            "detail": {
                "verdict": results.get("verdict"),
                "files": results.get("files"),
                "host_root": results.get("host_root"),
                "native_trace": completion.get("TraceName"),
                "native_trace_sha256": completion["TraceSha256"],
                "isolation_context_sha256": results.get("isolation_context_sha256"),
                "isolation_proof_sha256": results.get("isolation_proof_sha256"),
            },
        },
        "cleaned": {
            "event": "cleaned",
            "detail": {
                "verdict": cleanup.get("Verdict"),
                "boot_event": cleanup.get("BootEvent"),
                "residue": cleanup.get("Residue"),
                "guest_task_remains": (report.get("Cleanup") or {}).get("TaskRemains"),
                "restore_review": cleanup.get("Review"),
            },
        },
    }
    for name, payload in receipts.items():
        body = {"attempt_id": attempt_id, "at": datetime.now(timezone.utc).isoformat(), **payload}
        (directory / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2),
                                                encoding="utf-8")


def abandon_unsubmitted(ledger: Path, attempt_id: str, store: Path, reason: str) -> Path | None:
    """Close an attempt that never received a permit. Nothing was sent, so nothing is owed.

    An attempt that did get a permit is refused: the permit is evidence that a send may have
    happened, and only an operator can decide how that ends.
    """
    uri = ledger.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT closed_at FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if not row or row[0]:
            return None
        if connection.execute("SELECT 1 FROM submissions WHERE attempt_id = ?", (attempt_id,)).fetchone():
            raise RuntimeError("A submission record exists; the attempt cannot be abandoned this way")
        if connection.execute("SELECT 1 FROM submission_permits WHERE attempt_id = ?", (attempt_id,)).fetchone():
            raise RuntimeError("A permit exists; the attempt cannot be abandoned this way")
        closed_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps({
            "attempt_id": attempt_id,
            "outcome": "blocked_before_permit",
            "recorded_at_utc": closed_at,
            "reason": reason,
            "permits_issued": 0,
            "submissions_written": 0,
        }, indent=2).encode("utf-8")
        path = store / f"recovery-{uuid4().hex[:8]}.json"
        path.write_bytes(payload)
        connection.execute("INSERT INTO attempt_recovery VALUES (?, ?, ?, ?)",
                           (attempt_id, "blocked_before_permit", str(path),
                            hashlib.sha256(payload).hexdigest()))
        connection.execute("UPDATE attempts SET closed_at = ? WHERE attempt_id = ?", (closed_at, attempt_id))
    return path


def publish_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def authorize_send(ledger: Path, go_path: Path, request: dict, ready: dict, attempt_id: str,
                   kind: str, *, on_register=None, before_publish=None) -> None:
    context_sha256 = ready.get("IsolationContextSha256")
    proof_sha256 = None
    if kind == "formal":
        proof_path = go_path.parent / "isolation-preflight.json"
        if not proof_path.is_file():
            raise ValueError("The Windows isolation preflight record is missing.")
        payload = proof_path.read_bytes()
        proof_sha256 = sha256_bytes(payload)
        require_matching_admission(request, ready, json.loads(payload), proof_sha256)

    binding = {
        "request_id": request["RequestId"],
        "attempt_id": attempt_id,
        "prompt_sha256": request["PromptSha256"],
        "workspace": ready["Workspace"],
        "boot_event": ready.get("BootEvent"),
        "inputs": request["Inputs"],
        "isolation_context_sha256": context_sha256,
        "isolation_proof_sha256": proof_sha256,
    }
    fingerprint = sha256_bytes(json.dumps(binding, sort_keys=True).encode("utf-8"))
    register_attempt(ledger, attempt_id, kind)
    if on_register:
        on_register()
    permit_id = issue_submission_permit(
        ledger, attempt_id, fingerprint, expires_at=datetime.now(timezone.utc) + timedelta(minutes=15), kind=kind)
    begin_submission(ledger, attempt_id, permit_id, fingerprint)
    if before_publish:
        before_publish(permit_id)
    publish_json(go_path, {**binding, "permit_id": permit_id, "binding_fingerprint": fingerprint,
                           "authorized_at": datetime.now(timezone.utc).isoformat()})


def verify_native_archive(directory: Path, report: dict) -> Path:
    completion = report.get("Completion") or {}
    acceptance = completion.get("Acceptance") or {}
    trace_name = completion.get("TraceName")
    if (not acceptance.get("AcceptanceVerified") or not completion.get("NativeTurnEnd")
            or trace_name not in {"session.v4.jsonl", "session.v4.jsonl.zstd"}
            or sha256_file(directory / trace_name) != completion.get("TraceSha256")):
        raise RuntimeError("Native acceptance or termination evidence is missing; the attempt stays open.")
    return directory / trace_name


def run(record: dict, state: dict, store: Path, root: Path, package: Path,
        timeout: float, prompt_timeout: float, wait_signin: int = 0,
        attach: bool = False) -> int:
    stage_directory = store / "stage"
    stage_directory.mkdir(parents=True, exist_ok=True)
    request = stage_request(root, record, f"TASK-{record['request_id']}",
                            prompt_timeout, timeout, wait_signin, attach, baseline=state.get("baseline", "v2"))
    (stage_directory / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    (stage_directory / "prompt.txt").write_text(record["prompt"], encoding="utf-8")
    # Inputs travel as files so the stage copies bytes, never a re-typed string.
    inputs_directory = stage_directory / "inputs"
    if inputs_directory.exists():
        shutil.rmtree(inputs_directory)
    task_root = Path(record["task_file"]).parent
    for item in record["inputs"]:
        target = inputs_directory / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(task_root / item["path"], target)
    wrapper = write_wrapper(store, package)

    report_path = stage_directory / "report.json"
    ready_path = stage_directory / "ready.json"
    go_path = stage_directory / "go.json"
    for stale in (report_path, ready_path, go_path, stage_directory / "abort.json",
                  stage_directory / "isolation-preflight.json"):
        if stale.exists():
            stale.unlink()

    launch_elevated(wrapper, stage_directory, package)
    print("Waiting for the elevated stage. Confirm the Windows administrator prompt.", flush=True)
    ledger = ledger_path(root)
    try:
        ready = wait_for_ready(report_path, ready_path, prompt_timeout + wait_signin + 240)
        if (not ready.get("PromptMatches")
                or ready.get("PromptReadbackSha256") != record["prompt_sha256"]):
            raise ValueError("The composer text did not read back as the frozen prompt.")
        advance(state, "restored", {"boot_event": ready.get("BootEvent"), "baseline_restored": not attach})
        save_state(store, state)
        attempt_id = f"TASK-{record['request_id']}-{uuid4().hex[:8]}"
        attempt_kind = "diagnostic" if attach else "formal"

        def record_attempt():
            state["attempt_id"] = attempt_id
            save_state(store, state)

        def record_authorization(permit_id):
            advance(state, "prepared", {"attempt_id": attempt_id, "workspace": ready["Workspace"]})
            advance(state, "dispatched", {"permit_id": permit_id, "meaning": "send authorised"})
            save_state(store, state)

        authorize_send(ledger, go_path, request, ready, attempt_id, attempt_kind,
                       on_register=record_attempt, before_publish=record_authorization)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, TimeoutError, sqlite3.Error,
            UnclosedAttempt, PermitUnavailable, CompletionNotVerified):
        publish_json(stage_directory / "abort.json", {"request_id": record["request_id"]})
        try:
            stopped = wait_for_report(report_path, 120)
        except TimeoutError:
            stopped = read_json(report_path) or {}
        state["result"] = {"submissions": {"formal": stopped.get("FormalSubmissions"),
                                          "diagnostic": stopped.get("DiagnosticSubmissions")}}
        save_state(store, state)
        raise

    # The native observation limit is set in the stage; allow time for its final report and restore.
    report = wait_for_report(report_path, timeout + 300)
    return finish_run(record, state, store, ledger, report, ready, attempt_kind)


def finish_run(record: dict, state: dict, store: Path, ledger: Path, report: dict, ready: dict, attempt_kind: str) -> int:
    stage_directory = store / "stage"
    attempt_id = state["attempt_id"]
    completion = report.get("Completion") or {}
    state["result"] = {
        "completion": report.get("Completion"),
        "dispatch": report.get("Dispatch"),
        "restore": report.get("FinalRestore"),
        "submissions": {"formal": report.get("FormalSubmissions"),
                        "diagnostic": report.get("DiagnosticSubmissions")},
        "manual_steps": report.get("ManualSteps"),
    }
    save_state(store, state)
    if report.get("Verdict") != "pass":
        raise RuntimeError(report.get("Error") or "The elevated stage did not complete.")
    verify_native_archive(stage_directory, report)
    expected_restore = "skipped_by_attach" if attempt_kind == "diagnostic" else "pass"
    if (report.get("FinalRestore") or {}).get("Verdict") != expected_restore or report.get("CleanupError"):
        raise RuntimeError("The recorded cleanup or restoration is incomplete; the attempt stays open.")

    stage_results = stage_directory / "results"
    verification = verify_outputs(stage_results, record)
    # Copy fidelity: the stage records the guest-side hash of every declared output, so the
    # host copy is judged against the guest bytes instead of being trusted on its own.
    fidelity = []
    for item in (report.get("Outputs") or []):
        relative = item.get("Path")
        host_file = stage_results / relative if relative else None
        matches = bool(host_file and host_file.is_file()
                       and sha256_file(host_file) == item.get("Sha256"))
        fidelity.append({"path": relative, "guest_sha256": item.get("Sha256"),
                         "host_matches": matches})
    if fidelity and not all(entry["host_matches"] for entry in fidelity):
        if verification["verdict"] == "pass":
            verification["verdict"] = "collection_incomplete"
    results_payload = {
        "verdict": verification["verdict"],
        "files": verification["files"],
        "copy": fidelity,
        "host_root": str(stage_results),
        "isolation_context_sha256": ready.get("IsolationContextSha256"),
        "isolation_proof_sha256": ready.get("IsolationProofSha256"),
    }
    if not stage_reached(state, "completed"):
        advance(state, "completed", {"completion": report.get("Completion")})
    if not stage_reached(state, "retrieved"):
        advance(state, "retrieved", verification)

    state["result"].update({
        "task_result": verification["verdict"],
        "verification": verification,
    })
    save_state(store, state)

    if verification["verdict"] == "collection_incomplete":
        state["failure"] = "Result collection was incomplete; the attempt stays open for a re-collect."
        save_state(store, state)
        print_json(summarize(store))
        return 1

    receipt_dir = store / "receipts" / attempt_id
    uri = ledger.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection:
        completed = connection.execute("SELECT evidence_json FROM attempt_completion WHERE attempt_id = ?",
                                       (attempt_id,)).fetchone()
    if completed:
        receipts = {event: read_completion_receipt(receipt_dir / f"{event}.json", attempt_id, event)
                    for event in COMPLETION_EVENTS}
        if json.loads(completed[0]) != receipts:
            raise CompletionNotVerified("The existing closing receipts differ from the ledger.")
    else:
        write_receipts(receipt_dir, attempt_id, report, results_payload)
        close_completed_attempt(ledger, attempt_id, receipt_dir, kind=attempt_kind)
    state["failure"] = None
    advance(state, "closed", {"attempt_id": attempt_id})
    save_state(store, state)
    print_json(summarize(store))
    return 0 if verification["verdict"] == "pass" and completion["TurnStatus"] == "completed" else 4


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_run(args, root: Path, package: Path) -> int:
    attach = bool(getattr(args, "attach", False))
    requested_baseline = getattr(args, "baseline", None)
    new_baseline = requested_baseline or DEFAULT_DSH_BASELINE
    store = request_store(root, args.request_id)
    try:
        record, reused = freeze_request(store, args.request_id, Path(args.task))
    except RequestConflict as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 5
    state = load_state(store)
    if state and (stage_reached(state, "dispatched") or state.get("isolation_attempt_id")):
        print("This request already entered the diagnostic or formal send stage; showing stored progress.")
        print_json(summarize(store))
        return 0
    if reused:
        print(f"Reusing the frozen task for request {args.request_id}.")

    if getattr(args, "dry_run", False):
        # Requests started before baseline selection was introduced used v2.
        baseline = state.get("baseline", "v2") if state else new_baseline
        if state and requested_baseline and requested_baseline != baseline:
            print("Refused: this request already has a different baseline.", file=sys.stderr)
            return 5
        print_json({
            "request_id": record["request_id"],
            "task": f"{record['task_id']} v{record['task_version']}",
            "checkpoint": DSH_CHECKPOINTS[baseline][0],
            "prompt_sha256": record["prompt_sha256"],
            "inputs": record["inputs"],
            "outputs": record["outputs"],
            "expect": record["expect"],
            "would_send": 1,
            "submission_budget": {"diagnostic": int(attach), "formal": int(not attach)},
            "note": "Nothing was restored, launched, or sent; only the frozen record was written.",
        })
        return 0

    lock = root / "startup-lock.sqlite3"
    try:
        with acquire_startup_lock(lock):
            require_no_open_attempt(ledger_path(root))
            state = load_state(store)
            if state and (stage_reached(state, "dispatched") or state.get("isolation_attempt_id")):
                print_json(summarize(store))
                return 0
            if state is None:
                state = new_state(record, None, "diagnostic" if attach else "formal")
                state["baseline"] = new_baseline
                save_state(store, state)
            elif requested_baseline and requested_baseline != state.get("baseline", "v2"):
                print("Refused: this request already has a different baseline.", file=sys.stderr)
                return 5
            return run(record, state, store, root, package, args.timeout, args.prompt_timeout,
                       getattr(args, "wait_signin", 0), attach)
    except StartupLockBusy:
        print("Blocked: another run holds the startup lock.", file=sys.stderr)
        return 3
    except UnclosedAttempt as error:
        print(f"Blocked: {error}. Close or resume that attempt first.", file=sys.stderr)
        return 3
    except PermitUnavailable as error:
        _record_failure(store, f"the submission permit was not available: {error}")
        print(f"Blocked: the submission permit was not available ({error}).", file=sys.stderr)
        return 1
    except CompletionNotVerified as error:
        _record_failure(store, f"refused to close the attempt: {error}")
        print(f"Refused to close the attempt: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, TimeoutError, sqlite3.Error) as error:
        _record_failure(store, str(error))
        print(f"Task run stopped: {error}", file=sys.stderr)
        return 1
    finally:
        # A run that never reached the permit stage owes nothing. Leaving its record open would
        # block every later run for a reason that has no evidence behind it.
        attempt_id = (state or {}).get("attempt_id")
        if attempt_id:
            try:
                closed = abandon_unsubmitted(
                    ledger_path(root), attempt_id, store,
                    "the run stopped before a submission permit was issued")
                if closed:
                    print(f"Unsubmitted attempt closed: {attempt_id} ({closed.name})", file=sys.stderr)
            except (OSError, RuntimeError, sqlite3.Error) as error:
                print(f"The unsubmitted attempt could not be closed: {error}", file=sys.stderr)


def _record_failure(store: Path, message: str) -> None:
    """Keep the reason on the request so a resume starts from what actually happened."""
    state = load_state(store)
    if state is None:
        return
    state["failure"] = message
    save_state(store, state)


def command_status(args, root: Path) -> int:
    store = request_store(root, args.request_id)
    if not (store / "request.json").exists():
        print(f"No request named {args.request_id}.")
        return 2
    print_json(summarize(store))
    return 0



def review_recorded_restore(store: Path, record: dict, state: dict, database: Path) -> tuple[dict, dict]:
    stage_directory = store / "stage"
    source_paths = {"request": stage_directory / "request.json", "stage": stage_directory / "report.json",
                    "first_restore": stage_directory / "restore" / "first.json",
                    "final_restore": stage_directory / "restore" / "final.json",
                    "authorisation": stage_directory / "go.json", "ready": stage_directory / "ready.json",
                    "isolation": stage_directory / "isolation-preflight.json"}
    sources = {name: path.read_bytes() for name, path in source_paths.items()}
    request, report, first, final, grant, ready, isolation = (json.loads(sources[name]) for name in source_paths)
    attempt_id = state["attempt_id"]
    checkpoint_name, checkpoint_id = DSH_CHECKPOINTS[state.get("baseline", "v2")]
    if (request.get("Attach") is not False or request.get("RequestId") != record["request_id"]
            or request.get("VmId") != VM_ID or request.get("CheckpointId") != checkpoint_id
            or request.get("CheckpointName") != checkpoint_name or request.get("ExpectedProfile") != EXPECTED_PROFILE
            or report.get("Verdict") != "pass" or not report.get("FinishedAt")
            or report.get("DiagnosticSubmissions") != 0 or report.get("FormalSubmissions") != 1
            or grant.get("attempt_id") != attempt_id or grant.get("request_id") != record["request_id"]
            or grant.get("workspace") != ready.get("Workspace") or grant.get("boot_event") != ready.get("BootEvent")
            or grant.get("prompt_sha256") != record["prompt_sha256"] or grant.get("inputs") != record["inputs"]
            or grant.get("isolation_context_sha256") != ready.get("IsolationContextSha256")
            or grant.get("isolation_proof_sha256") != ready.get("IsolationProofSha256")):
        raise ValueError("The recorded run does not match the one-time formal authorisation.")
    require_matching_admission(request, ready, isolation, sha256_bytes(sources["isolation"]))
    binding = {key: value for key, value in grant.items() if key not in ("permit_id", "binding_fingerprint", "authorized_at")}
    fingerprint = sha256_bytes(json.dumps(binding, sort_keys=True).encode("utf-8"))
    uri = database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection:
        permit = connection.execute("""
            SELECT a.kind, p.permit_id, p.binding_fingerprint, p.consumed_at
            FROM attempts a JOIN submission_permits p USING (attempt_id) JOIN submissions s USING (attempt_id)
            WHERE a.attempt_id = ?
        """, (attempt_id,)).fetchone()
    if (not permit or permit[:3] != ("formal", grant.get("permit_id"), fingerprint)
            or not permit[3] or grant.get("binding_fingerprint") != fingerprint):
        raise ValueError("The ledger does not match the retained formal send permit.")

    trace = verify_native_archive(stage_directory, report)
    completion = report["Completion"]
    proof = inspect_snapshot(read_snapshot(trace), ready["Workspace"], record["prompt"],
                             completion["Acceptance"]["SessionId"], 0)
    if (proof.get("Acceptance") != completion["Acceptance"] or proof.get("NativeTurnEnd") != completion["NativeTurnEnd"]
            or proof.get("ReplyText") != completion.get("ReplyText")
            or (stage_directory / "final-reply.txt").read_bytes().decode("utf-8") != proof.get("ReplyText")):
        raise ValueError("The raw DSH session and final reply do not match the archived formal run.")
    verify_reused_record_restore(request, report, first, final, attempt_id)
    review_path = store / "restore-verification.json"
    review = {"request_id": record["request_id"], "attempt_id": attempt_id, "verdict": "pass",
              "basis": "distinct startup time and verified residue on the recorded v2 restore",
              "original_error": REUSED_RECORD_ERROR, "initial_boot": first["Guest"]["BootEvent"],
              "restored_boot": final["Guest"]["BootEvent"], "restored_os_boot": final["Guest"]["BootTime"],
              "sources": {name: {"path": str(path), "sha256": sha256_bytes(sources[name])}
                          for name, path in source_paths.items()}}
    if review_path.is_file():
        if read_json(review_path) != review:
            raise ValueError("The recorded restore review conflicts with the current source evidence.")
    else:
        publish_json(review_path, review)
    report["FinalRestore"] = {**report["FinalRestore"], "Verdict": "pass", "Error": None,
                              "Review": {"path": str(review_path), "sha256": sha256_file(review_path)}}
    return report, ready


def command_resume(args, root: Path, package: Path) -> int:
    store = request_store(root, args.request_id)
    if not (store / "request.json").exists():
        print(f"No request named {args.request_id}.")
        return 2
    record = json.loads((store / "request.json").read_text(encoding="utf-8"))
    state = load_state(store)
    if state is None:
        print("The request was frozen but never started; rerun it instead of resuming.")
        return 2
    if stage_reached(state, "closed"):
        print("This request is already closed; returning the stored result.")
        print_json(summarize(store))
        return 0
    if state.get("isolation_attempt_id") and not stage_reached(state, "dispatched"):
        print("This request has an isolation diagnostic record. Resume returns its existing evidence.")
        print_json(summarize(store))
        return 4
    if not stage_reached(state, "dispatched"):
        print("Nothing was sent yet, so the run is restarted from the beginning.")
        args.task = args.task or record["task_file"]
        args.attach = state.get("activity") == "diagnostic"
        return command_run(args, root, package)
    original = read_json(store / "stage" / "report.json") or {}
    if (original.get("FinalRestore") or {}).get("Error") == REUSED_RECORD_ERROR:
        try:
            with acquire_startup_lock(root / "startup-lock.sqlite3"):
                state = load_state(store)
                if stage_reached(state, "closed"):
                    print_json(summarize(store))
                    return 0
                database = ledger_path(root)
                report, ready = review_recorded_restore(store, record, state, database)
                return finish_run(record, state, store, database, report, ready, "formal")
        except (StartupLockBusy, CompletionNotVerified, OSError, ValueError, RuntimeError, KeyError, TypeError,
                sqlite3.Error, subprocess.SubprocessError) as error:
            print(f"Restore review stopped: {error}", file=sys.stderr)
            return 4
    print("Send authorisation was recorded for this request. Check the native evidence for acceptance.")
    print_json(summarize(store))
    return 4


def void_attempt(ledger: Path, attempt_id: str, store: Path, reason: str) -> Path | None:
    """Close a record whose permit was consumed but whose send an operator watched fail.

    A consumed permit normally blocks every automatic path, because it is evidence that a send
    may have happened. Only a person looking at the product can rule that out, so the record
    keeps the operator's reason next to the permit and submission counts.
    """
    uri = ledger.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT closed_at FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if not row or row[0]:
            return None
        permit = connection.execute(
            "SELECT permit_id, consumed_at FROM submission_permits WHERE attempt_id = ?",
            (attempt_id,)).fetchone()
        submissions = connection.execute(
            "SELECT COUNT(*) FROM submissions WHERE attempt_id = ?", (attempt_id,)).fetchone()[0]
        closed_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps({
            "attempt_id": attempt_id,
            "outcome": "operator_voided_unsent",
            "recorded_at_utc": closed_at,
            "reason": reason,
            "operator": os.environ.get("USERNAME"),
            "permits_issued": 1 if permit else 0,
            "permit_consumed": bool(permit and permit[1]),
            "submissions_written": submissions,
        }, indent=2).encode("utf-8")
        path = store / f"void-{uuid4().hex[:8]}.json"
        path.write_bytes(payload)
        connection.execute("INSERT INTO attempt_recovery VALUES (?, ?, ?, ?)",
                           (attempt_id, "operator_voided_unsent", str(path),
                            hashlib.sha256(payload).hexdigest()))
        connection.execute("UPDATE attempts SET closed_at = ? WHERE attempt_id = ?",
                           (closed_at, attempt_id))
    return path


def command_void(args, root: Path) -> int:
    """Close an attempt after an operator confirms that no message was sent."""
    store = request_store(root, args.request_id)
    state = load_state(store)
    if not state or not state.get("attempt_id"):
        print("No registered attempt exists for this request.")
        return 2
    try:
        closed = void_attempt(ledger_path(root), state["attempt_id"], store, args.reason)
    except sqlite3.Error as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1
    if not closed:
        print(f"{state['attempt_id']} was already closed.")
        return 0
    state["stage"] = "voided"
    state["failure"] = args.reason
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(store, state)
    print(f"Voided {state['attempt_id']} as operator_voided_unsent.")
    print(f"Evidence: {closed}")
    return 0


def command_recover(args, root: Path) -> int:
    """Close a record that was opened but never reached a permit, after checking that fact."""
    store = request_store(root, args.request_id)
    state = load_state(store)
    if not state or not state.get("attempt_id"):
        print("No registered attempt exists for this request.")
        return 2
    try:
        closed = abandon_unsubmitted(
            ledger_path(root), state["attempt_id"], store,
            "closed by an operator: the run stopped before any permit was issued")
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print(f"Recovery refused: {error}", file=sys.stderr)
        return 1
    if not closed:
        print(f"{state['attempt_id']} was already closed.")
        return 0
    print(f"Closed {state['attempt_id']} as blocked_before_permit.")
    print(f"Evidence: {closed}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path, default=control_root())
    parser.add_argument("--timeout", type=float, default=1800,
                        help="Seconds to wait for native acceptance and termination after releasing the send")
    parser.add_argument("--prompt-timeout", type=float, default=600,
                        help="Seconds allowed for launching, workspace selection, and paste readback")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Freeze a task and run it once")
    run_parser.add_argument("--task", required=True, help="Path to the task.json manifest")
    run_parser.add_argument("--request-id", required=True)
    run_parser.add_argument("--baseline", choices=tuple(DSH_CHECKPOINTS),
                            help=f"Use a registered checkpoint (default {DEFAULT_DSH_BASELINE} for a new request)")
    run_parser.add_argument("--wait-signin", type=int, default=0, metavar="SECONDS",
                            help="After the restore, wait this long for an operator if desktop sign-in is needed")
    run_parser.add_argument("--attach", action="store_true",
                            help="Use the signed-in desktop as it is: no shutdown, no baseline "
                                 "restore, and no closing restore. Recorded as a diagnostic "
                                 "attempt because it did not start from the baseline.")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="Freeze the task and print the plan without touching the VM")
    status_parser = commands.add_parser("status", help="Show stored progress read-only")
    status_parser.add_argument("--request-id", required=True)
    resume_parser = commands.add_parser("resume", help="Continue a request that has not sent")
    resume_parser.add_argument("--request-id", required=True)
    resume_parser.add_argument("--task", help="Unused when the request is already frozen")
    resume_parser.add_argument("--wait-signin", type=int, default=0, metavar="SECONDS")
    recover_parser = commands.add_parser(
        "recover", help="Close an attempt that stopped before any permit was issued")
    recover_parser.add_argument("--request-id", required=True)
    void_parser = commands.add_parser(
        "void", help="Close an attempt whose send an operator watched fail")
    void_parser.add_argument("--request-id", required=True)
    void_parser.add_argument("--reason", required=True,
                             help="What the operator saw that proves no message was sent")
    collect_parser = commands.add_parser(
        "collect", help="Copy the guest probe report and screenshots back")
    collect_parser.add_argument("--request-id", required=True)
    args = parser.parse_args()

    if os.name != "nt":
        print("The controlled desktop run needs the Windows host.", file=sys.stderr)
        return 2
    package = Path(__file__).resolve().parent
    root = args.control_root
    if args.command == "run":
        return command_run(args, root, package)
    if args.command == "status":
        return command_status(args, root)
    if args.command == "recover":
        return command_recover(args, root)
    if args.command == "void":
        return command_void(args, root)
    if args.command == "collect":
        return collect_sources(args.request_id, request_store(root, args.request_id), root)
    return command_resume(args, root, package)


if __name__ == "__main__":
    sys.exit(main())
