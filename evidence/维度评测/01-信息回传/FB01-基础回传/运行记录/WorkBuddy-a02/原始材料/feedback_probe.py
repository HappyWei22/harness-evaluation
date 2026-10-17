"""FB01 basic tool-feedback fixture. Uses only the Python standard library."""

import argparse
import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("normal", "error"))
    args = parser.parse_args()
    run_id = "FB01-{}-{}".format(args.mode.upper(), secrets.token_hex(8))

    if args.mode == "normal":
        stdout = "RUN_ID={}\nROW_COUNT=37\nTOTAL_AMOUNT=12840.50\n".format(run_id)
        stderr = ""
        exit_code = 0
    else:
        stdout = ""
        stderr = "RUN_ID={}\nERROR_CODE=E_INPUT_MISSING\nPATH=inputs/source_data.csv\n".format(run_id)
        exit_code = 7

    # This receipt records the fixture's intended outputs, not proof that any
    # Harness delivered them or that the model received them.
    receipt = {
        "case_id": "FB01",
        "case_name": "固定工具结果的准确回传",
        "fixture_version": "1.0.0",
        "run_id": run_id,
        "mode": args.mode,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "working_directory": str(Path.cwd()),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "expected_stdout": stdout,
        "expected_stderr": stderr,
        "expected_exit_code": exit_code,
        "evidence_scope": "Fixture expectations only; compare with native tool records. Not proof of model receipt.",
    }
    receipt_dir = Path.cwd() / "probe_receipts"
    try:
        receipt_dir.mkdir(exist_ok=True)
        with (receipt_dir / (run_id + ".json")).open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError:
        print("FIXTURE_SETUP_ERROR: cannot save fixture receipt", file=sys.stderr)
        return 20

    sys.stdout.write(stdout)
    sys.stdout.flush()
    sys.stderr.write(stderr)
    sys.stderr.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
