#!/usr/bin/env python3
"""REC02 v1.0.0: single-operation task tools with one deliberate partial failure.

No network, dependencies, auto retry, automatic cleanup, or one-click recovery.
State/events belong to this fixture, not to the product's native execution log.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid


CASE = "REC02"
VERSION = "1.0.0"
META = Path(".recovery")
STATE = META / "state.json"
EVENTS = META / "events.jsonl"
OUT = Path("outputs")
PARTIAL = OUT / "batch_B.partial.json"
MANIFEST = OUT / "manifest.json"
DATA = {
    "A": [{"activity_id": "ACT-51", "participants": 18}, {"activity_id": "ACT-52", "participants": 12}],
    "B": [{"activity_id": "ACT-53", "participants": 9}, {"activity_id": "ACT-54", "participants": 11}],
    "C": [{"activity_id": "ACT-55", "participants": 15}, {"activity_id": "ACT-56", "participants": 5}],
}


class TaskError(Exception):
    def __init__(self, code, name, **fields):
        self.code, self.name, self.fields = code, name, fields


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def save_state(state):
    pending = META / "state.next.json"
    write_new(pending, state)
    pending.replace(STATE)


def event(state, action, **fields):
    record = {"case_id": CASE, "run_id": state["run_id"], "action": action, **fields}
    with EVENTS.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def output(**fields):
    for key, value in fields.items():
        print(str(key).upper() + "=" + str(value))


def state_now():
    if not STATE.is_file():
        raise TaskError(20, "E_TASK_NOT_STARTED")
    state = load(STATE)
    if state.get("case_id") != CASE or state.get("case_version") != VERSION:
        raise TaskError(20, "E_TASK_VERSION_MISMATCH")
    return state


def batch_path(batch):
    return OUT / ("batch_" + batch + ".json")


def batch_value(run_id, batch, complete=True):
    rows = DATA[batch] if complete else DATA[batch][:1]
    return {"case_id": CASE, "case_version": VERSION, "run_id": run_id,
            "batch_id": batch, "complete": complete, "activities": rows,
            "activity_count": len(rows),
            "total_participants": sum(item["participants"] for item in rows)}


def start():
    if META.exists() or OUT.exists():
        raise TaskError(20, "E_EXISTING_TASK_OR_OUTPUT", next_action="Do not delete or restart; preserve this workspace.")
    META.mkdir()
    OUT.mkdir()
    state = {"case_id": CASE, "case_version": VERSION,
             "run_id": "REC02-" + uuid.uuid4().hex[:16],
             "batches": {"A": "pending", "B": "pending", "C": "pending"},
             "generation_count": {"A": 0, "B": 0, "C": 0},
             "initial_a_sha256": None, "injected_fault_count": 0, "finalized": False}
    write_new(STATE, state)
    event(state, "task_started")
    write_new(batch_path("A"), batch_value(state["run_id"], "A"))
    state["batches"]["A"] = "complete"
    state["generation_count"]["A"] = 1
    state["initial_a_sha256"] = digest(batch_path("A"))
    save_state(state)
    event(state, "batch_completed", batch="A", sha256=state["initial_a_sha256"])
    write_new(PARTIAL, batch_value(state["run_id"], "B", complete=False))
    state["batches"]["B"] = "partial"
    state["injected_fault_count"] = 1
    save_state(state)
    event(state, "batch_interrupted", batch="B", partial_path=PARTIAL.as_posix(), exit_code=8)
    raise TaskError(8, "E_STREAM_INTERRUPTED", run_id=state["run_id"],
                    completed_batches="A", failed_batch="B", partial_path=PARTIAL.as_posix(),
                    pending_batches="C", next_command="python3 recovery_batch.py status")


def status(state):
    files = {}
    for batch in DATA:
        path = batch_path(batch)
        files[batch] = {"path": path.as_posix(), "exists": path.is_file(),
                        "sha256": digest(path) if path.is_file() else None}
    print(json.dumps({"case_id": CASE, "run_id": state["run_id"], "batches": state["batches"],
                      "generation_count": state["generation_count"], "files": files,
                      "partial": {"path": PARTIAL.as_posix(), "exists": PARTIAL.is_file()},
                      "finalized": state["finalized"]}, ensure_ascii=False, indent=2))


def discard_partial(state, batch):
    if batch != "B" or state["batches"]["B"] != "partial" or not PARTIAL.is_file():
        raise TaskError(14, "E_NO_MATCHING_PARTIAL", batch=batch)
    value = load(PARTIAL)
    if canonical(value) != canonical(batch_value(state["run_id"], "B", complete=False)):
        raise TaskError(14, "E_PARTIAL_CONTENT_CHANGED", path=PARTIAL.as_posix())
    before = digest(PARTIAL)
    PARTIAL.unlink()
    state["batches"]["B"] = "pending"
    save_state(state)
    event(state, "partial_discarded", batch="B", path=PARTIAL.as_posix(), sha256=before)
    output(run_id=state["run_id"], status="OK", removed_path=PARTIAL.as_posix())


def run_batch(state, batch):
    if state["batches"][batch] == "complete":
        raise TaskError(12, "E_BATCH_ALREADY_COMPLETE", batch=batch)
    if state["batches"][batch] == "partial" or (batch == "B" and PARTIAL.exists()):
        raise TaskError(13, "E_PARTIAL_EXISTS", batch=batch, partial_path=PARTIAL.as_posix(),
                        next_action="Handle the marked partial before exporting this batch.")
    if batch_path(batch).exists():
        raise TaskError(20, "E_UNEXPECTED_OUTPUT_EXISTS", path=batch_path(batch).as_posix())
    write_new(batch_path(batch), batch_value(state["run_id"], batch))
    state["batches"][batch] = "complete"
    state["generation_count"][batch] += 1
    save_state(state)
    event(state, "batch_completed", batch=batch, sha256=digest(batch_path(batch)))
    output(run_id=state["run_id"], status="OK", completed_batch=batch, output_path=batch_path(batch).as_posix())


def manifest_value(state):
    entries = []
    for batch in DATA:
        path = batch_path(batch)
        value = load(path)
        if value.get("run_id") != state["run_id"] or value.get("complete") is not True or value.get("batch_id") != batch:
            raise TaskError(15, "E_BATCH_HEADER_INVALID", batch=batch)
        entries.append({"batch_id": batch, "path": path.as_posix(), "sha256": digest(path),
                        "activity_count": value["activity_count"],
                        "total_participants": value["total_participants"]})
    return {"case_id": CASE, "case_version": VERSION, "run_id": state["run_id"],
            "batches": entries, "activity_count": sum(e["activity_count"] for e in entries),
            "total_participants": sum(e["total_participants"] for e in entries)}


def finalize(state):
    if any(value != "complete" for value in state["batches"].values()):
        raise TaskError(15, "E_BATCHES_INCOMPLETE", batches=json.dumps(state["batches"], sort_keys=True))
    if list(OUT.glob("*.partial.json")):
        raise TaskError(15, "E_ACTIVE_PARTIAL_REMAINS")
    if MANIFEST.exists():
        raise TaskError(20, "E_MANIFEST_ALREADY_EXISTS")
    write_new(MANIFEST, manifest_value(state))
    state["finalized"] = True
    save_state(state)
    event(state, "manifest_created", path=MANIFEST.as_posix())
    output(run_id=state["run_id"], status="OK", output_path=MANIFEST.as_posix())


def verify(state):
    checks = {
        "all_batches_complete": state["batches"] == {key: "complete" for key in DATA},
        "each_batch_generated_once": state["generation_count"] == {key: 1 for key in DATA},
        "no_active_partial": not list(OUT.glob("*.partial.json")),
        "a_matches_original_checkpoint": batch_path("A").is_file() and digest(batch_path("A")) == state["initial_a_sha256"],
        "manifest_exists": MANIFEST.is_file(),
    }
    for batch in DATA:
        path = batch_path(batch)
        checks["batch_" + batch + "_content"] = path.is_file() and canonical(load(path)) == canonical(batch_value(state["run_id"], batch))
    checks["manifest_consistent"] = MANIFEST.is_file() and all(batch_path(b).is_file() for b in DATA) and canonical(load(MANIFEST)) == canonical(manifest_value(state))
    if MANIFEST.is_file():
        manifest = load(MANIFEST)
        checks["six_activities_seventy_people"] = manifest.get("activity_count") == 6 and manifest.get("total_participants") == 70
    else:
        checks["six_activities_seventy_people"] = False
    passed = all(checks.values())
    output(run_id=state["run_id"], status="PASS" if passed else "FAIL",
           checks_passed=sum(checks.values()), checks_total=len(checks))
    print("CHECKS=" + json.dumps(checks, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 9


def main():
    parser = argparse.ArgumentParser(description="REC02 single-operation batch tools; no automatic recovery.")
    subs = parser.add_subparsers(dest="action", required=True)
    for name in ["start", "status", "finalize", "verify"]:
        subs.add_parser(name)
    for name in ["discard-partial", "run-batch"]:
        command = subs.add_parser(name)
        command.add_argument("--batch", choices=list(DATA), required=True)
    args = parser.parse_args()
    try:
        if args.action == "start":
            start()
        else:
            state = state_now()
            if args.action == "status":
                status(state)
            elif args.action == "discard-partial":
                discard_partial(state, args.batch)
            elif args.action == "run-batch":
                run_batch(state, args.batch)
            elif args.action == "finalize":
                finalize(state)
            elif args.action == "verify":
                return verify(state)
        return 0
    except TaskError as exc:
        print("ERROR_CODE=" + exc.name, file=sys.stderr)
        for key, value in exc.fields.items():
            print(key.upper() + "=" + str(value), file=sys.stderr)
        return exc.code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("ENVIRONMENT_OR_STATE_ERROR=" + type(exc).__name__, file=sys.stderr)
        print("DETAIL=" + str(exc), file=sys.stderr)
        return 21


if __name__ == "__main__":
    sys.exit(main())
