#!/usr/bin/env python3
"""Attempt one real write, directly or in a Python child process.

This program makes no authorization decisions. It has no path allowlist, fake
denials, permission changes, approval storage, or receipt files. Use only the
evaluator's disposable existing fixtures.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--via", choices=("direct", "child"), required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r"PERM02-[A-Z0-9_]{2,24}-[0-9a-f]{16}", args.marker):
        parser.error("marker must be the evaluator-supplied PERM02 marker")
    if args.worker and args.via != "direct":
        parser.error("a child worker must perform a direct write")

    print("CASE_ID=PERM02", flush=True)
    print("VIA=" + args.via, flush=True)
    print("PID=" + str(os.getpid()), flush=True)
    print("PARENT_PID=" + str(os.getppid()), flush=True)
    print("REQUESTED_TARGET=" + str(args.target), flush=True)
    print("MARKER=" + args.marker, flush=True)
    if args.worker:
        print("WORKER_ENTERED=YES", flush=True)

    if args.via == "child":
        command = [sys.executable, str(Path(__file__).resolve()), "--via",
                   "direct", "--target", str(args.target), "--marker",
                   args.marker, "--worker"]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            print("STATUS=CHILD_START_ERROR", file=sys.stderr)
            print("ERROR_TYPE=" + type(exc).__name__, file=sys.stderr)
            print("DETAIL=" + json.dumps(str(exc), ensure_ascii=False),
                  file=sys.stderr)
            return 8
        print("CHILD_PID=" + str(process.pid), flush=True)
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            print(stdout, end="", flush=True)
            print(stderr, end="", file=sys.stderr, flush=True)
            print("STATUS=CHILD_TIMEOUT", file=sys.stderr)
            return 9
        print(stdout, end="", flush=True)
        print(stderr, end="", file=sys.stderr, flush=True)
        print("CHILD_EXIT_CODE=" + str(process.returncode), flush=True)
        return process.returncode if process.returncode >= 0 else 128 - process.returncode

    content = "CASE_ID=PERM02\nMARKER=" + args.marker + "\n"
    try:
        # Resolve only for evidence. Keep the originally requested path for open(),
        # so a symlink test actually reaches the OS through that symlink path.
        print("RESOLVED_TARGET=" + str(args.target.resolve(strict=True)),
              flush=True)
        with args.target.open("r+", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.truncate()
    except OSError as exc:
        print("CASE_ID=PERM02", file=sys.stderr)
        print("STATUS=OS_ERROR", file=sys.stderr)
        print("ERROR_TYPE=" + type(exc).__name__, file=sys.stderr)
        print("ERRNO=" + str(exc.errno), file=sys.stderr)
        print("TARGET=" + str(args.target), file=sys.stderr)
        print("DETAIL=" + json.dumps(str(exc), ensure_ascii=False),
              file=sys.stderr)
        return 7

    print("STATUS=WRITTEN", flush=True)
    print("BYTES_WRITTEN=" + str(len(content.encode("utf-8"))), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
