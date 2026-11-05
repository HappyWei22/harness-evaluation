"""Run with python3 -I -S -B reproduce.py using an unprivileged POSIX account."""

import errno
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import platform
import shutil
import stat
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
from uuid import uuid4


HERE = Path(__file__).resolve().parent
CANDIDATE = HERE / "scoped_mkdir_intervention.py"
CANDIDATE_SHA256 = "b211dd6d18a8288071cda1b4d1a80380e0c520f16a2efd4658cbe3b403828ed1"
MARKER = b"existing content must survive\n"
BEIJING = timezone(timedelta(hours=8))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_public_proxy():
    if sha256(CANDIDATE) != CANDIDATE_SHA256:
        raise RuntimeError("candidate fingerprint does not match the original comparison")
    if "sitecustomize" in sys.modules:
        raise RuntimeError("run with -I -S to avoid loading startup hooks")

    # This stand-in models the documented request/response behavior for the
    # four fixtures. It never loads or connects to the WorkBuddy runtime.
    shim = ModuleType("sitecustomize")
    shim._broker_host_ops_enabled = lambda: True
    sequence = 0

    def proxy_mkdir(path, mode=0o777, parents=False, exist_ok=False):
        nonlocal sequence
        if parents is not False or exist_ok is not True:
            raise ValueError("the public stand-in covers only the fixed fixture parameters")
        sequence += 1
        response = shim._broker_send_request({
            "id": f"python-{sequence}",
            "command": "HostFileOperation",
            "runtime": "python",
            "operation": "mkdir",
            "sessionId": "public-fixture",
            "path": os.path.abspath(path),
            "recursive": parents,
            "mode": mode,
        })
        if response["ok"] is True:
            return None
        raise PermissionError(response["error"])

    shim._brokered_path_mkdir = proxy_mkdir
    sys.modules["sitecustomize"] = shim
    candidate = ModuleType("archived_candidate")
    exec(compile(CANDIDATE.read_bytes(), str(CANDIDATE), "exec"), vars(candidate))
    return shim, candidate.ScopedMkdirIntervention


def target_state(target):
    if target.is_symlink():
        return "symlink"
    if target.is_dir():
        entries = sorted(p.name for p in target.iterdir())
        if not entries:
            return "empty-directory"
        if entries == ["marker"] and (target / "marker").read_bytes() == MARKER:
            return "directory-with-marker"
        return "other-directory"
    if target.is_file():
        return "file-with-marker" if target.read_bytes() == MARKER else "other-file"
    return "other" if target.exists() else "missing"


def run_phase(shim, intervention_type, call, case, phase, parent):
    parent.mkdir()
    target = parent / "target"
    if case["initial"] == "directory-with-marker":
        target.mkdir()
        (target / "marker").write_bytes(MARKER)
    elif case["initial"] == "file-with-marker":
        target.write_bytes(MARKER)
    parent.chmod(int(case["parent_mode"], 8))
    before = target_state(target)
    mode_before = format(stat.S_IMODE(parent.stat().st_mode), "04o")

    traffic = []

    def local_send(request):
        # Replace only the transport. The host stand-in calls the real OS.
        response = {"ok": True, "decision": "host-op"}
        host_errno = None
        try:
            os.mkdir(request["path"], request["mode"])
        except OSError as error:
            host_errno = errno.errorcode[error.errno]
            response.update(ok=False, error=f"{host_errno}: {error.strerror}, mkdir {error.filename!r}")
        traffic.append({"request": dict(request), "original_response": dict(response),
                        "response": response, "host_errno": host_errno})
        return response

    native_mkdir = Path.mkdir
    shim._broker_send_request = local_send
    intervention = intervention_type()
    intervention.normalize_existing_directory = phase == "on"
    Path.mkdir = shim._brokered_path_mkdir
    try:
        intervention.install()
        try:
            value = target.mkdir(**call)
            outcome = {"type": "returned", "value": value}
        except OSError as error:
            outcome = {"type": type(error).__name__, "args": list(error.args), "errno": error.errno}
        finally:
            intervention.uninstall()
        restored = Path.mkdir is shim._brokered_path_mkdir and shim._broker_send_request is local_send
    finally:
        Path.mkdir = native_mkdir
        parent.chmod(0o700)
    after = target_state(target)
    shutil.rmtree(parent)

    # Keep the checks separate from the captured traffic and caller outcome.
    one_exchange = len(traffic) == 1 and len(intervention.events) == 1
    exchange = traffic[0] if one_exchange else {}
    response = exchange.get("response", {})
    eligible = case["id"] == "existing-directory"
    event = intervention.events[0] if one_exchange else {}
    expected_request = {"path": str(target), "operation": "mkdir",
                        "recursive": call["parents"], "mode": call["mode"]}
    request = exchange.get("request", {})
    checks = {
        "initial_state": before == case["initial"] and mode_before == case["parent_mode"],
        "single_unchanged_exchange": one_exchange and exchange["original_response"] == response
        and "exist_ok" not in request and all(request.get(k) == v for k, v in expected_request.items()),
        "host_result": one_exchange and exchange["host_errno"] == case["host_error"]
        and response.get("ok") is (case["host_error"] is None)
        and response.get("decision") == "host-op",
        "caller_result": outcome["type"] == case[phase] and (
            outcome["value"] is None if outcome["type"] == "returned"
            else outcome["args"] == [response.get("error")]),
        "candidate_branch": event.get("normalized") is (eligible and phase == "on")
        and event.get("eligible_existing_directory_error") is (eligible if case["host_error"] else None),
        "final_state": after == case["final"],
        "bindings_restored": restored,
    }
    return {"phase": phase, "before": before, "parent_mode_before": mode_before,
            "traffic": traffic, "candidate_events": intervention.events,
            "caller": outcome, "after": after, "checks": checks}


def main():
    started = datetime.now(BEIJING)
    run_id = f"WB-MKDIR-BOUNDARY-PUBLIC-{started:%Y%m%d}-{uuid4().hex}"
    fixture_path = HERE / "fixtures.json"
    fixture = json.loads(fixture_path.read_bytes())
    shim, intervention_type = load_public_proxy()
    results = []
    with TemporaryDirectory(prefix="wb-mkdir-boundary-") as temporary:
        parent = Path(temporary).resolve() / "fixture"
        for case in fixture["cases"]:
            off, on = [
                run_phase(shim, intervention_type, fixture["call"], case, phase, parent)
                for phase in ("off", "on")
            ]
            same_exchange = False
            if len(off["traffic"]) == len(on["traffic"]) == 1:
                a, b = off["traffic"][0], on["traffic"][0]
                same_exchange = (
                    {k: v for k, v in a["request"].items() if k != "id"}
                    == {k: v for k, v in b["request"].items() if k != "id"}
                    and a["original_response"] == b["original_response"]
                )
            passed = same_exchange and all(all(p["checks"].values()) for p in (off, on))
            results.append({"fixture": case, "same_request_except_id_and_same_response": same_exchange,
                            "phases": [off, on], "passed": passed})

    passed = all(result["passed"] for result in results)
    output = json.dumps({
        "run_id": run_id,
        "related_product_run_id": "WB-MKDIR-ABA-20261028-a01",
        "activity": "自测",
        "execution_path": "公开代理替身，调用原候选处理与本机文件系统",
        "fixture_version": fixture["version"],
        "product_version": "不适用，本地执行。原归档来源为 WorkBuddy 5.8.3",
        "model": "不适用，脚本执行未调用模型",
        "tool": {"command": "python3 -I -S -B reproduce.py",
                 "python": platform.python_version(), "os": platform.system(),
                 "os_release": platform.release(), "architecture": platform.machine()},
        "permissions": {"effective_uid": os.geteuid(), "parent_modes": ["0700", "0500"],
                        "product_permissions": "不适用"},
        "skills": "不适用，脚本执行未加载产品技能",
        "started_at": started.isoformat(),
        "ended_at": datetime.now(BEIJING).isoformat(),
        "fingerprints_sha256": {"fixtures": sha256(fixture_path), "script": sha256(Path(__file__)),
                                "archived_candidate": sha256(CANDIDATE)},
        "call": fixture["call"],
        "evidence": {"E2": "本地请求、响应、异常及前后路径状态",
                     "E3": "同一样例两种开关状态的本地对照"},
        "verdict": "四项本地对照通过" if passed else "至少一项本地对照未通过，检查具体判据",
        "product_runs": 0,
        "cases": results,
    }, ensure_ascii=False, indent=2)
    encoded_path = json.dumps(str(parent.parent), ensure_ascii=False)[1:-1]
    print(output.replace(encoded_path, "<TEMP_DIR>"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
