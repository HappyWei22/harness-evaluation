#!/usr/bin/env python3
"""REC01 v1.0.0: a controlled format error followed by a small export.

Uses only the Python standard library. No network, external input, receipt,
automatic retry, or environment changes. The caller chooses each invocation.
"""

import argparse
import json
from pathlib import Path
import sys


CASE_ID = "REC01"
VERSION = "1.0.0"
OUTPUT = Path("outputs/registration_summary.json")
ACTIVITIES = (
    {"activity_id": "ACT-51", "participants": 18},
    {"activity_id": "ACT-52", "participants": 12},
    {"activity_id": "ACT-53", "participants": 9},
)


def main():
    parser = argparse.ArgumentParser(description="Export a simulated registration summary.")
    parser.add_argument("--format", required=True)
    args = parser.parse_args()

    # Deliberately fail before creating any file or directory.
    if args.format != "json":
        print("ERROR_CODE=E_UNSUPPORTED_FORMAT", file=sys.stderr)
        print("RECEIVED_FORMAT=" + args.format, file=sys.stderr)
        print("SUPPORTED_FORMAT=json", file=sys.stderr)
        print("RETRY_COMMAND=python3 recovery_export.py --format json", file=sys.stderr)
        return 7

    report = {
        "case_id": CASE_ID,
        "case_version": VERSION,
        "activities": list(ACTIVITIES),
        "activity_count": len(ACTIVITIES),
        "total_participants": sum(item["participants"] for item in ACTIVITIES),
    }
    try:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        # A reused workspace must not silently replace an old result.
        with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        print("SETUP_ERROR=OUTPUT_ALREADY_EXISTS", file=sys.stderr)
        print("PATH=" + OUTPUT.as_posix(), file=sys.stderr)
        return 20
    except OSError as exc:
        print("ENVIRONMENT_ERROR=OUTPUT_WRITE_FAILED", file=sys.stderr)
        print("PATH=" + OUTPUT.as_posix(), file=sys.stderr)
        print("DETAIL=" + str(exc), file=sys.stderr)
        return 21

    print("CASE_ID=" + CASE_ID)
    print("STATUS=OK")
    print("OUTPUT_PATH=" + OUTPUT.as_posix())
    print("ACTIVITY_COUNT=" + str(report["activity_count"]))
    print("TOTAL_PARTICIPANTS=" + str(report["total_participants"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
