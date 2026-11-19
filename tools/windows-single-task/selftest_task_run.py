"""Offline checks for the single-task entry: no virtual machine, no real ledger.

The point is to prove the parts that decide correctness before anything touches Hyper-V:
freezing is idempotent, a changed task is refused, stages only move forward, missing and
unreadable outputs stay distinguishable, and the ledger still refuses to close without a
submission plus the four receipts.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

import run_dsh_task
import task_request as tr
from inspect_task_native import inspect_snapshot
from task_restore_evidence import REUSED_RECORD_ERROR, verify_reused_record_restore
from attempt_completion import CompletionNotVerified, close_completed_attempt, initialize_completion_table
from attempt_ledger import initialize_attempt_ledger, register_attempt
from submission_permit import (
    initialize_submission_tables,
    begin_submission,
    issue_submission_permit,
)

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}" + (f" — {detail}" if detail and not condition else ""))


def make_task(root: Path, prompt: str = "读取 input/sample.txt，把这一行原样写入 output/result.txt，然后回复“已完成”。") -> Path:
    package = root / "tasks" / "copy-line-v1"
    (package / "input").mkdir(parents=True, exist_ok=True)
    (package / "input" / "sample.txt").write_text("第一行样本\n", encoding="utf-8")
    manifest = {
        "task_id": "copy-line",
        "version": "1",
        "prompt": prompt,
        "inputs": ["input/sample.txt"],
        "outputs": ["output/result.txt"],
        "expect": {"output/result.txt": "第一行样本\n"},
    }
    path = package / "task.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_freeze(root: Path):
    task = make_task(root)
    store = root / "requests" / "demo-001"
    record, reused = tr.freeze_request(store, "demo-001", task)
    check("freeze writes a record", (store / "request.json").is_file() and not reused)
    check("freeze pins the prompt hash", record["prompt_sha256"] == tr.sha256_bytes(record["prompt"].encode("utf-8")))
    check("freeze pins the input hash", record["inputs"][0]["sha256"] == tr.sha256_file(task.parent / "input" / "sample.txt"))

    same, reused_again = tr.freeze_request(store, "demo-001", task)
    check("a repeat freeze reuses the record", reused_again and same["prompt_sha256"] == record["prompt_sha256"])

    changed = make_task(root, prompt="换成另一道题")
    try:
        tr.freeze_request(store, "demo-001", changed)
        check("a changed prompt is refused", False, "no exception")
    except tr.RequestConflict:
        check("a changed prompt is refused", True)


def test_stage_order(root: Path):
    state = tr.new_state({"request_id": "demo-001", "task_id": "copy-line", "task_version": "1"}, None, "formal")
    tr.advance(state, "restored")
    tr.advance(state, "dispatched")
    check("stages advance forward", state["stage"] == "dispatched")
    try:
        tr.advance(state, "prepared")
        check("stages refuse to move backwards", False, "no exception")
    except tr.TaskLayoutError:
        check("stages refuse to move backwards", True)
    check("stage_reached is monotonic", tr.stage_reached(state, "prepared") and not tr.stage_reached(state, "closed"))


def test_outputs(root: Path):
    record = {"outputs": ["output/result.txt"], "expect": {"output/result.txt": "第一行样本\n"}}
    results = root / "results"
    (results / "output").mkdir(parents=True, exist_ok=True)

    check("missing output is named as missing",
          tr.verify_outputs(results, record)["verdict"] == "task_missing_output")

    (results / "output" / "result.txt").write_text("第一行样本\n", encoding="utf-8")
    passing = tr.verify_outputs(results, record)
    check("correct content passes", passing["verdict"] == "pass" and passing["files"][0]["matches_expect"])

    (results / "output" / "result.txt").write_text("第一行样本\n多出来一行\n", encoding="utf-8")
    wrong = tr.verify_outputs(results, record)
    check("wrong content is its own verdict", wrong["verdict"] == "task_wrong_content")

    (results / "output" / "result.txt").write_bytes(b"\xff\xfe\x00broken")
    unreadable = tr.verify_outputs(results, record)
    check("unreadable content is not called a wrong answer",
          unreadable["verdict"] == "collection_incomplete")

    (results / "output" / "result.txt").write_text("第一行样本\n\n", encoding="utf-8")
    tolerant = tr.verify_outputs(results, record)
    check("one trailing newline is tolerated", tolerant["verdict"] == "pass")


def test_ledger(root: Path):
    ledger = root / "attempts.sqlite3"
    initialize_attempt_ledger(ledger)
    initialize_submission_tables(ledger)
    initialize_completion_table(ledger)
    attempt_id = "TASK-demo-001-abc12345"
    register_attempt(ledger, attempt_id, "formal")
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    permit = issue_submission_permit(ledger, attempt_id, "fp-1", expires)

    receipts = root / "receipts" / attempt_id
    report = {
        "Dispatch": {"InvokeStartedAt": "2026-11-19T00:00:01Z", "ReadbackSha256": "p"},
        "Completion": {
            "Acceptance": {"SessionId": "session-1234", "AcceptanceVerified": True},
            "Basis": "native_turn_end", "Turn": 1, "UserMessageSequence": 2,
            "NativeTurnEnd": {"type": "turn/end", "seq": 4}, "TurnStatus": "completed", "TraceSha256": "t",
        },
        "FinalRestore": {"Verdict": "pass", "BootEvent": {"RecordId": 1061}, "Residue": []},
        "Cleanup": {"TaskRemains": 0},
    }
    results = {"verdict": "pass", "files": [], "host_root": str(root / "results")}

    try:
        close_completed_attempt(ledger, attempt_id, receipts)
        check("closing without a submission is refused", False, "no exception")
    except CompletionNotVerified:
        check("closing without a submission is refused", True)

    begin_submission(ledger, attempt_id, permit, "fp-1")
    try:
        close_completed_attempt(ledger, attempt_id, receipts)
        check("closing without receipts is refused", False, "no exception")
    except CompletionNotVerified:
        check("closing without receipts is refused", True)

    run_dsh_task.write_receipts(receipts, attempt_id, report, results)
    close_completed_attempt(ledger, attempt_id, receipts)
    with sqlite3.connect(ledger) as connection:
        closed_at = connection.execute(
            "SELECT closed_at FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()[0]
        evidence = connection.execute(
            "SELECT evidence_json FROM attempt_completion WHERE attempt_id = ?", (attempt_id,)).fetchone()[0]
    check("a complete set of receipts closes the attempt", closed_at is not None)
    check("the closing evidence keeps all four receipts",
          set(json.loads(evidence)) == {"accepted", "terminated", "archived", "cleaned"})


def test_native_turn(root: Path):
    workspace = r"C:\Users\alex_\copy-line-test"
    prompt = "复制一行"
    session = "session-1234"
    header = {"type": "session", "version": 4, "id": session, "cwd": workspace, "isSeeded": False,
              "createdAt": 1100}
    start = {"type": "turn/start", "seq": 1, "data": {"turn": 1}}
    user = {"type": "user/message", "seq": 2, "data": {"source": {"kind": "user"}, "content": prompt}}
    reply = {"type": "assistant/message", "seq": 3,
             "data": {"turn": 1, "message": {"content": [{"type": "text", "text": "已完成"}]}}}
    end = {"type": "turn/end", "seq": 4, "data": {"turn": 1, "reason": {"kind": "completed"}}}

    def inspect(*events, expected_workspace=workspace, not_before=1000, trailing_newline=True):
        payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in (header, *events))
        if trailing_newline:
            payload += "\n"
        return inspect_snapshot(payload.encode("utf-8"), expected_workspace, prompt, session, not_before)

    complete = inspect(start, user, reply, end)
    check("fast native turn with one exact prompt completes", complete["NativeTurnEnd"] == end and
          complete["Acceptance"]["OperatorSubmissions"] == 1 and complete["ReplyText"] == "已完成")
    check("duplicate prompt does not confirm acceptance", not inspect(start, user, reply, end, {**user, "seq": 5}))
    check("foreign workspace and old session are excluded",
          not inspect(start, user, reply, end, expected_workspace=workspace + "-old") and
          not inspect(start, user, reply, end, not_before=1200))
    wrong_end = {**end, "data": {**end["data"], "turn": 2}}
    check("terminal event for another turn cannot finish the task", not inspect(start, user, reply, wrong_end))
    check("a partially written terminal row is not proof", "NativeTurnEnd" not in
          inspect(start, user, reply, end, trailing_newline=False))

    # A correct output may be written while the turn still runs.
    output = root / "native-test" / "output" / "result.txt"
    output.parent.mkdir(parents=True)
    output.write_text("sample", encoding="utf-8")
    pending = inspect(start, user, reply)
    check("an early output does not end the native turn", pending["Acceptance"]["AcceptanceVerified"] and
          "NativeTurnEnd" not in pending)
    aborted = {**end, "data": {**end["data"], "reason": {"kind": "aborted"}}}
    failed = inspect(start, user, aborted)
    check("non-completed native status remains visible", failed["TurnStatus"] == "aborted" and
          failed["ReplyText"] is None)


def test_summary(root: Path):
    store = root / "requests" / "demo-001"
    record = json.loads((store / "request.json").read_text(encoding="utf-8"))
    state = tr.new_state(record, "TASK-demo-001-abc12345", "formal")
    state["stage"] = "closed"
    state["result"] = {"task_result": "pass", "submissions": {"formal": 1, "diagnostic": 0}}
    tr.save_state(store, state)
    summary = tr.summarize(store)
    check("summary reports the final stage and submissions",
          summary["stage"] == "closed" and summary["submissions"]["formal"] == 1)



def test_isolation_admission(root: Path):
    scenarios = ("pass", "readable", "unknown-error", "file-not-found", "missing-read", "changed-boot",
                 "changed-config", "changed-proof", "wrong-account", "remote-desktop", "reachable", "missing-proof")
    for scenario in scenarios:
        directory = root / scenario
        directory.mkdir(parents=True)
        ledger = directory / "attempts.sqlite3"
        initialize_attempt_ledger(ledger)
        initialize_submission_tables(ledger)
        initialize_completion_table(ledger)
        go = directory / "go.json"
        proof_path = directory / "isolation-preflight.json"
        boot = {"RecordId": 1060, "CreatedUtc": "2026-11-19T04:00:00Z"}
        profile = r"C:\Users\alex_"
        sid = "S-1-5-21-123-456-789-1000"
        host_ip = "172.25.64.1"
        context_hash = "f" * 64
        files = [{"Role": "task_manifest", "Path": r"E:\tests\task.json", "Sha256": "a" * 64},
                 {"Role": "frozen_request", "Path": r"E:\control\request.json", "Sha256": "b" * 64}]
        targets = [rf"\\{host_ip}\E$\tests\task.json", rf"\\{host_ip}\E$\control\request.json"]
        request = {"RequestId": "copy-line-test", "VmId": "vm", "IsolationProbeId": "PRE-test",
                   "ExpectedProfile": profile, "ProtectedFiles": files, "PromptSha256": "prompt", "Inputs": []}
        network = {"SmbMappings": 0, "SmbConnections": 0, "NetworkDisks": 0}
        context = {**network, "VmId": "vm", "BootEvent": boot, "HostIPv4": host_ip, "HostAddresses": [host_ip],
                   "SmbEndpoints": [{"Address": host_ip, "Port": port, "Reachable": scenario == "reachable"}
                                    for port in (139, 445)],
                   "GuestProfile": profile, "GuestSid": sid,
                   "ProtectedFiles": [{**item, "UNC": target} for item, target in zip(files, targets)]}
        checks = [{"Path": target, "CanOpen": False, "HResult": "80070035"} for target in targets]
        guest = {**network, "ProbeId": "PRE-test", "BootEvent": boot, "Profile": profile, "Sid": sid,
                 "SessionId": 1, "SessionProtocol": 2 if scenario == "remote-desktop" else 0, "ReadChecks": checks}
        if scenario == "readable":
            checks[0].update(CanOpen=True, HResult=None)
        if scenario == "unknown-error":
            checks[0]["HResult"] = "80004005"
        if scenario == "file-not-found":
            checks[0]["HResult"] = "80070002"
        if scenario == "missing-read":
            checks.pop()
        if scenario == "wrong-account":
            guest["Sid"] += "1"
        proof = {"Activity": "interface_preflight", "Verdict": "pass", "RequestId": "copy-line-test",
                 "ProbeId": "PRE-test", "Context": context, "ContextSha256": context_hash, "Guest": guest,
                 "DesktopSessions": [1], "DshSubmissions": 0}
        payload = json.dumps(proof).encode("utf-8")
        if scenario != "missing-proof":
            proof_path.write_bytes(payload)
        ready = {"BootEvent": boot, "IsolationContextSha256": context_hash,
                 "IsolationProofSha256": tr.sha256_bytes(payload), "Workspace": r"C:\Users\alex_\formal"}
        if scenario == "changed-boot":
            ready["BootEvent"] = {**boot, "RecordId": 1061}
        if scenario == "changed-config":
            ready["IsolationContextSha256"] = "0" * 64
        if scenario == "changed-proof":
            proof_path.write_bytes(payload + b" ")
        try:
            run_dsh_task.authorize_send(ledger, go, request, ready, "formal-test", "formal")
        except ValueError:
            with sqlite3.connect(ledger) as connection:
                permits = connection.execute("SELECT COUNT(*) FROM submission_permits").fetchone()[0]
                attempts = connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            check(f"zero-submit isolation {scenario} blocks before registering a product run",
                  scenario != "pass" and permits == attempts == 0 and not go.exists())
            continue
        grant = json.loads(go.read_bytes())
        with sqlite3.connect(ledger) as connection:
            submissions = connection.execute("SELECT kind FROM submissions JOIN attempts USING (attempt_id)").fetchall()
        check("Windows preflight authorizes one formal submission and zero diagnostic submissions",
              scenario == "pass" and submissions == [("formal",)]
              and grant["isolation_context_sha256"] == context_hash
              and grant["isolation_proof_sha256"] == tr.sha256_bytes(payload))


def test_restore_review():
    boot_before = {"RecordId": 1060, "CreatedUtc": "2026-11-19T04:00:00Z"}
    boot_after = {"RecordId": 1060, "CreatedUtc": "2026-11-19T04:03:00Z"}
    request = {"RequestId": "test", "VmId": "vm", "CheckpointId": "v2-id", "CheckpointName": "v2",
               "ExpectedProfile": r"C:\Users\alex_"}
    baseline = {key: request[key] for key in ("VmId", "CheckpointId", "CheckpointName")}
    guest = {"ProfileMatches": True, "Profile": request["ExpectedProfile"], "DshProcesses": [], "HarnessTasks": []}
    residue = {"RunWorkspaceExists": False, "RunSessionExists": False, "RunLauncherExists": False,
               "PreflightTaskRemains": 0}
    first = {**baseline, "Verdict": "pass", "Guest": {**guest, "BootEvent": boot_before,
                                                       "BootTime": "2026-11-19T04:00:02Z"}}
    final = {**baseline, "Verdict": "fail", "Stage": "verify_guest", "Error": REUSED_RECORD_ERROR, "RunId": "attempt",
             "InitialVmState": "Off", "Heartbeat": "OkApplicationsUnknown", "StartedAt": "2026-11-19T04:03:01Z",
             "RestoreAppliedAt": "2026-11-19T04:03:02Z", "StartReturnedAt": "2026-11-19T04:03:03Z",
             "FinishedAt": "2026-11-19T04:03:10Z",
             "Guest": {**guest, "BootEvent": boot_after, "BootTime": "2026-11-19T04:03:04Z", "RunResidue": residue}}
    native_end = int(datetime.fromisoformat("2026-11-19T04:02:00+00:00").timestamp() * 1000)
    stage = {"RequestId": "test", "BootEvent": boot_before, "Cleanup": {"TaskRemains": 0, "DshRemains": 0,
             "WorkingRootExists": False}, "Completion": {"NativeTurnEnd": {"time": native_end}},
             "FinalRestore": {"BootEvent": boot_after, "Residue": residue, "Error": REUSED_RECORD_ERROR}}
    verify_reused_record_restore(request, stage, first, final, "attempt")
    check("valid recorded restore accepts a repeated event index with a new boot time", True)
    for reason, change in (("same boot", {"BootEvent": boot_before}),
                           ("residual session", {"RunResidue": {**residue, "RunSessionExists": True}}),
                           ("OS did not restart", {"BootTime": first["Guest"]["BootTime"]}),
                           ("wrong guest", {"ProfileMatches": False})):
        candidate = {**final, "Guest": {**final["Guest"], **change}}
        corresponding_stage = {**stage, "FinalRestore": {**stage["FinalRestore"],
                               "BootEvent": candidate["Guest"]["BootEvent"], "Residue": candidate["Guest"]["RunResidue"]}}
        try:
            verify_reused_record_restore(request, corresponding_stage, first, candidate, "attempt")
        except ValueError:
            check(f"recorded restore refuses {reason}", True)
        else:
            check(f"recorded restore refuses {reason}", False)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="harness-task-selftest-"))
    try:
        test_freeze(root)
        test_stage_order(root)
        test_outputs(root)
        test_native_turn(root)
        test_ledger(root)
        test_summary(root)
        test_isolation_admission(root / "isolation-fixtures")
        test_restore_review()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    print("offline self-test: pass (no virtual machine, no real ledger)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
