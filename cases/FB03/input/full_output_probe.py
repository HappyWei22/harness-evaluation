"""FB03: generate one simulated report, exactly 256 KiB of ASCII stdout."""

import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    run_id = "FB03-FULL-" + secrets.token_hex(8)
    markers = {name: secrets.token_hex(16) for name in ("HEAD_TOKEN", "MIDDLE_TOKEN", "TAIL_TOKEN")}
    checkpoints = {
        1: "RUN_ID=" + run_id + " HEAD_TOKEN=" + markers["HEAD_TOKEN"],
        1024: "RUN_ID=" + run_id + " MIDDLE_TOKEN=" + markers["MIDDLE_TOKEN"],
        2048: "RUN_ID=" + run_id + " TAIL_TOKEN=" + markers["TAIL_TOKEN"] + " REPORT_COMPLETE=YES",
    }
    rows = []
    for number in range(1, 2049):
        text = checkpoints.get(number, "CHECK item={:04d} target=inputs/simulated_{:04d}.csv status=PASS simulation=YES".format(number, number))
        # Each ASCII row occupies 128 bytes including its final newline.
        if len(text.encode("ascii")) > 127:
            raise ValueError("report row exceeds fixed width")
        rows.append(text.ljust(127) + "\n")
    output = "".join(rows).encode("ascii")
    if len(output) != 262144:
        raise ValueError("unexpected report size")
    receipt = {
        "case_id": "FB03",
        "case_name": "截断后完整结果获取",
        "fixture_version": "1.0.0",
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "working_directory": str(Path.cwd()),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "expected_stdout": output.decode("ascii"),
        "expected_stdout_sha256": hashlib.sha256(output).hexdigest(),
        "expected_stdout_bytes": len(output),
        "expected_stdout_lines": len(rows),
        "expected_stderr": "",
        "expected_exit_code": 0,
        "markers": markers,
        "checkpoints": [{"line": n, "text": checkpoints[n]} for n in sorted(checkpoints)],
        "simulation": "Only simulated report text; no real business file is read or changed.",
        "evidence_scope": "Evaluator expectations only. Not a Harness-generated full-output artifact or proof of tool delivery.",
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
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
