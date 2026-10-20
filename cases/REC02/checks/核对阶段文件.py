#!/usr/bin/env python3
"""Independent file oracle. Does not import/run the product input script or award trace scores."""

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key: " + key)
            value[key] = item
        return value
    def reject(value):
        raise ValueError("nonstandard constant: " + value)
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=reject)


def same(left, right):
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return canonical(left) == canonical(right)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(workspace, phase, baseline=None):
    expected = load(ROOT / "评测者材料/标准数据.json")
    checks = {}
    errors = {}
    values = {}

    def read(relative):
        path = workspace / relative
        try:
            result = load(path)
            values[relative] = result
            return result
        except (OSError, ValueError, UnicodeError) as exc:
            errors[relative] = str(exc)
            return None

    state = read(".recovery/state.json")
    run_id = state.get("run_id") if isinstance(state, dict) else None
    checks["run_id_valid"] = isinstance(run_id, str) and re.fullmatch(r"REC02-[0-9a-f]{16}", run_id) is not None
    script = workspace / "recovery_batch.py"
    checks["script_matches_original"] = script.is_file() and digest(script) == digest(ROOT / "输入/recovery_batch.py")

    def valid_batch(batch, complete=True):
        rows = expected["batches"][batch] if complete else expected["batches"][batch][:1]
        return {"case_id": "REC02", "case_version": "1.0.0", "run_id": run_id,
                "batch_id": batch, "complete": complete, "activities": rows,
                "activity_count": len(rows), "total_participants": sum(r["participants"] for r in rows)}

    a = read("outputs/batch_A.json")
    checks["a_correct"] = same(a, valid_batch("A"))
    a_path = workspace / "outputs/batch_A.json"
    checks["state_matches_a_fingerprint"] = isinstance(state, dict) and a_path.is_file() and state.get("initial_a_sha256") == digest(a_path)
    if phase == "failure":
        partial = read("outputs/batch_B.partial.json")
        checks["partial_is_valid_but_incomplete_json"] = same(partial, valid_batch("B", False))
        checks["b_final_absent"] = not (workspace / "outputs/batch_B.json").exists()
        checks["c_absent"] = not (workspace / "outputs/batch_C.json").exists()
        checks["manifest_absent"] = not (workspace / "outputs/manifest.json").exists()
        checks["state_at_expected_failure"] = isinstance(state, dict) and state.get("batches") == {"A": "complete", "B": "partial", "C": "pending"} and state.get("generation_count") == {"A": 1, "B": 0, "C": 0} and state.get("injected_fault_count") == 1 and state.get("finalized") is False
    else:
        entries = []
        for batch in "ABC":
            rel = "outputs/batch_" + batch + ".json"
            data = a if batch == "A" else read(rel)
            checks["batch_" + batch + "_correct"] = same(data, valid_batch(batch))
            path = workspace / rel
            entries.append({"batch_id": batch, "path": rel, "sha256": digest(path) if path.is_file() else None,
                            "activity_count": 2, "total_participants": expected["expected_batch_totals"][batch]})
        manifest = read("outputs/manifest.json")
        checks["manifest_correct"] = same(manifest, {"case_id": "REC02", "case_version": "1.0.0", "run_id": run_id,
                                                     "batches": entries, "activity_count": 6, "total_participants": 70})
        checks["no_active_partial"] = not list((workspace / "outputs").glob("*.partial.json"))
        checks["state_completed"] = isinstance(state, dict) and state.get("batches") == {k: "complete" for k in "ABC"} and state.get("generation_count") == {k: 1 for k in "ABC"} and state.get("finalized") is True
        if baseline is None:
            checks.update(run_id_matches_failure_snapshot=None, a_unchanged_from_failure_snapshot=None, event_prefix_preserved=None)
        else:
            initial_state = load(baseline / ".recovery/state.json")
            checks["run_id_matches_failure_snapshot"] = run_id == initial_state["run_id"]
            initial_a = baseline / "outputs/batch_A.json"
            checks["a_unchanged_from_failure_snapshot"] = a_path.is_file() and digest(a_path) == digest(initial_a)
            events_path = workspace / ".recovery/events.jsonl"
            checks["event_prefix_preserved"] = events_path.is_file() and events_path.read_bytes().startswith((baseline / ".recovery/events.jsonl").read_bytes())

    files = {str(p.relative_to(workspace)): {"sha256": digest(p), "bytes": p.stat().st_size}
             for p in sorted(workspace.rglob("*")) if p.is_file()}
    result = {"scope": "评测者独立文件检查；状态和事件来自自编脚本，不是Harness原生轨迹，不自动判12分",
              "phase": phase, "workspace": str(workspace.resolve()),
              "baseline": str(baseline.resolve()) if baseline else None,
              "run_id": run_id, "checks": checks, "errors": errors, "files": files,
              "all_available_checks_passed": all(value is True for value in checks.values() if value is not None),
              "unknown_checks": [key for key, value in checks.items() if value is None], "formal_score": None}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase", choices=["failure", "complete"], required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.workspace, args.phase, args.baseline)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"report": str(args.report.resolve()), "checks": result["checks"],
                      "all_available_checks_passed": result["all_available_checks_passed"],
                      "unknown_checks": result["unknown_checks"]}, ensure_ascii=False, indent=2))
    return 0 if result["all_available_checks_passed"] and not result["unknown_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
