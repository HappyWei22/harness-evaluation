"""Verify the published single-task evidence without opening a VM or a product."""

import hashlib
import json
from pathlib import Path
import sys


EVIDENCE = Path(__file__).resolve().parent
TOOLS = EVIDENCE.parents[1] / "tools" / "windows-single-task"
sys.path.insert(0, str(TOOLS))
from inspect_task_native import inspect_snapshot


def load(path):
    return json.loads((EVIDENCE / path).read_text(encoding="utf-8"))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def verify_native(directory, expected_end):
    folder = EVIDENCE / directory
    trace = (folder / "native-events.jsonl").read_bytes()
    header, *events = (json.loads(line) for line in trace.splitlines())
    user = [row for row in events if row["type"] == "user/message"]
    check(len(user) == 1 and user[0]["seq"] == 8, f"{directory}: unexpected user event")
    prompt = "".join(part["text"] for part in user[0]["data"]["content"])
    proof = inspect_snapshot(trace, header["cwd"], prompt, header["id"], header["createdAt"])
    check(proof.get("Turn") == 1 and proof.get("TurnStatus") == "completed",
          f"{directory}: first turn needs a completed terminal event")
    check(proof["NativeTurnEnd"]["seq"] == expected_end, f"{directory}: unexpected terminal sequence")
    tools = [row["data"]["name"] for row in events if row["type"] == "tool/call"]
    if directory == "legacy-gate-block":
        results = {row["seq"]: row["data"] for row in events if row["type"] == "tool/result"}
        check(tools == ["read", "read"] and not results[22]["message"]["isError"]
              and results[23]["message"]["isError"] and results[23]["error"]["code"] == "FS_IO_ERROR",
              "legacy gate needs a successful local read and a failed protected read")
        return
    check(tools == ["read", "write", "read"], f"{directory}: unexpected tool sequence")
    check(prompt == (folder / "prompt.txt").read_bytes().decode("utf-8"), f"{directory}: prompt mismatch")
    check(proof["ReplyText"] == (folder / "final-reply.txt").read_bytes().decode("utf-8-sig"),
          f"{directory}: final reply mismatch")
    submitted_input = (folder / "input.txt").read_bytes()
    check(len(submitted_input) == 16 and submitted_input == (folder / "result.txt").read_bytes(),
          f"{directory}: result bytes differ from input")


def main():
    sources = load("source-index.json")
    for source in sources:
        published = EVIDENCE / source["published"]
        actual = hashlib.sha256(published.read_bytes()).hexdigest()
        check(actual == source["published_sha256"], f"published fingerprint mismatch: {published}")
        if source["transformation"] == "byte-for-byte copy":
            check(actual == source["source_sha256"], f"source fingerprint mismatch: {published}")

    verify_native("formal-v2", 40)
    verify_native("diagnostic-v2", 40)
    verify_native("legacy-gate-block", 29)

    preflight = load("formal-v2/isolation-preflight.json")
    check(preflight["Verdict"] == "pass" and preflight["DshSubmissions"] == 0, "formal isolation verdict")
    endpoints = preflight["Context"]["SmbEndpoints"]
    check(len(endpoints) == 8 and all(not endpoint["Reachable"] for endpoint in endpoints),
          "formal isolation endpoint checks")
    guest = preflight["Guest"]
    reads = guest["ReadChecks"]
    check(guest["SessionProtocol"] == 0 and len(reads) == 2
          and all(not row["CanOpen"] and row["HResult"] == "80070035" for row in reads),
          "formal isolation file checks")

    state = load("formal-v2/request-state.json")
    check(state["stage"] == "closed" and state["result"]["submissions"] == {"formal": 1, "diagnostic": 0},
          "formal run close and submission count")
    check(state["result"]["verification"]["verdict"] == "pass", "formal result verdict")
    for event in ("accepted", "terminated", "archived", "cleaned"):
        receipt = load(f"formal-v2/receipts/{event}.json")
        check(receipt["event"] == event and receipt["attempt_id"] == state["attempt_id"],
              f"formal receipt mismatch: {event}")
    ledger = load("ledger-query.json")
    for table, field in (("attempts", "closed_at"), ("submission_permits", "consumed_at"), ("submissions", "started_at")):
        rows = [row for row in ledger[table] if row["attempt_id"] == state["attempt_id"]]
        check(len(rows) == 1 and rows[0][field], f"formal ledger evidence missing: {table}")

    initial = load("formal-v2/restore-first.json")["Guest"]
    final = load("formal-v2/restore-final.json")
    restored = final["Guest"]
    check(final["Verdict"] == "fail" and load("formal-v2/restore-recheck.json")["verdict"] == "pass",
          "restore must preserve both original verdict and recheck")
    check(initial["BootEvent"]["RecordId"] == restored["BootEvent"]["RecordId"]
          and initial["BootEvent"]["CreatedUtc"] < restored["BootEvent"]["CreatedUtc"]
          and initial["BootTime"] < restored["BootTime"], "restore timestamps")
    check(not any(restored["RunResidue"].values()) and not restored["DshProcesses"] and not restored["HarnessTasks"],
          "restore residue")

    for directory, formal, diagnostic in (("formal-v2", 1, 0), ("diagnostic-v2", 0, 1), ("legacy-gate-block", 0, 1),
                                          ("port-block", 0, 0), ("startup-guard-before-fix", 0, 0)):
        report = load(f"{directory}/host-report.json")
        check((report["FormalSubmissions"], report["DiagnosticSubmissions"]) == (formal, diagnostic),
              f"{directory}: submission count")
    recheck = load("port-block/share-recheck.json")
    reachable = [row for row in recheck["Context"]["SmbEndpoints"] if row["Reachable"]]
    check(recheck["SameBoot"] and {row["Port"] for row in reachable} == {139, 445}
          and len({row["Address"] for row in reachable}) == 1, "reachable SMB comparison")
    check(load("port-block/isolation-preflight.json")["Verdict"] == "blocked"
          and load("legacy-gate-block/isolation-decision.json")["verdict"] == "blocked",
          "both formal requests must be blocked")
    check(all(not any(counts.values()) for counts in ledger["queries_with_zero_matches"].values()),
          "blocked requests should have no product entries in the ledger")
    desktop = load("desktop-v3/preflight-report.json")
    check(desktop["Verdict"] == "pass" and desktop["ComposerStage"]["FocusedIsComposer"]
          and desktop["FormalSubmissions"] == desktop["DiagnosticSubmissions"] == 0,
          "v3 desktop readiness with no submissions")
    print(f"PASS: {len(sources)} evidence fingerprints and native, output, isolation, restore and ledger checks.")


if __name__ == "__main__":
    main()
