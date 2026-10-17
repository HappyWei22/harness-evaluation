"""FB02: emit exactly 200 lines of a simulated file-check report."""

import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    run_id = "FB02-LONG-" + secrets.token_hex(8)
    checkpoints = {
        1: "RUN_ID=" + run_id,
        60: "ERROR_COUNT=3",
        120: "ERROR_PATH=inputs/sales_2026.csv",
        200: "REPORT_COMPLETE=YES",
    }
    failures = {
        40: ("inputs/sales_2026.csv", "missing_required_column"),
        100: ("inputs/inventory_2026.csv", "invalid_numeric_value"),
        160: ("inputs/customers_2026.csv", "duplicate_customer_id"),
    }
    lines = []
    for number in range(1, 201):
        if number in checkpoints:
            line = checkpoints[number]
        elif number in failures:
            target, reason = failures[number]
            line = "CHECK item={:03d} target={} reason={} status=FAIL".format(number, target, reason)
        else:
            line = "CHECK item={:03d} target=inputs/simulated_file_{:03d}.csv read=OK schema=OK rows=100 status=PASS".format(number, number)
        lines.append(line)
    stdout = "\n".join(lines) + "\n"
    receipt = {
        "case_id": "FB02",
        "case_name": "较长检查报告中的关键信息回传",
        "fixture_version": "1.0.0",
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "working_directory": str(Path.cwd()),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "expected_stdout": stdout,
        "expected_stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "expected_stdout_bytes": len(stdout.encode("utf-8")),
        "expected_stdout_lines": len(lines),
        "expected_stderr": "",
        "expected_exit_code": 0,
        "checkpoints": [{"line": n, "text": checkpoints[n]} for n in sorted(checkpoints)],
        "simulation": "Report text only; no input file is read or checked.",
        "evidence_scope": "Fixture expectations only, not proof of Harness delivery. This receipt is not a Harness-generated full-output artifact.",
    }
    try:
        directory = Path.cwd() / "probe_receipts"
        directory.mkdir(exist_ok=True)
        with (directory / (run_id + ".json")).open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError:
        print("FIXTURE_SETUP_ERROR: cannot save fixture receipt", file=sys.stderr)
        return 20
    sys.stdout.write(stdout)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
