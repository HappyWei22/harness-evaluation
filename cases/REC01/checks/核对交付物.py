#!/usr/bin/env python3
"""Evaluator only: checks the artifact and optional script, never awards trace scores."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    # Preserves distinctions such as True vs 1, and 39.0 vs 39.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def check(artifact, script=None):
    expected = json.loads((ROOT / "评测者材料/标准结果.json").read_text(encoding="utf-8"))
    result = {"artifact": str(artifact.resolve()), "artifact_exists": artifact.is_file(),
              "script_unchanged": None, "formal_score": None,
              "scope": "仅检查交付物和提供的脚本；不证明首次故障、工具状态或自主恢复"}
    if script is not None:
        result["script"] = str(script.resolve())
        result["script_unchanged"] = script.is_file() and sha256(script) == sha256(ROOT / "输入/recovery_export.py")
        if script.is_file():
            result["script_sha256"] = sha256(script)
    if not artifact.is_file():
        result.update(json_valid=False, content_matches=False)
        return result
    result["artifact_sha256"] = sha256(artifact)
    result["artifact_bytes"] = artifact.stat().st_size
    try:
        # Reject duplicate keys and nonstandard constants instead of hiding them.
        def unique_pairs(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("duplicate JSON key: " + key)
                obj[key] = value
            return obj
        def reject_constant(value):
            raise ValueError("nonstandard JSON constant: " + value)
        actual = json.loads(artifact.read_text(encoding="utf-8"),
                            object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        result["json_valid"] = True
        result["content_matches"] = canonical(actual) == canonical(expected)
    except (ValueError, UnicodeError) as exc:
        result.update(json_valid=False, content_matches=False, error=str(exc))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.artifact, args.script)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("content_matches") and result.get("script_unchanged") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
