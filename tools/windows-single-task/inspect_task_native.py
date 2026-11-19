"""Bind the single-task dispatch to one native DSH session and turn."""

import argparse
import json
from pathlib import Path
import subprocess

from inspect_dsh_submission import verify_submission


def read_snapshot(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix != ".zstd":
        return raw
    # DSH can append to an open Zstandard frame. Only complete JSONL rows are used below.
    program = """
const fs = require('node:fs');
const zlib = require('node:zlib');
const options = { maxOutputLength: 16777216 };
const data = fs.readFileSync(0);
try {
    process.stdout.write(zlib.zstdDecompressSync(data, options));
} catch (error) {
    if (error.code !== 'Z_BUF_ERROR') throw error;
    options.finishFlush = zlib.constants.ZSTD_e_flush;
    process.stdout.write(zlib.zstdDecompressSync(data, options));
}
"""
    return subprocess.check_output(["node", "-e", program], input=raw, timeout=6)


def inspect_snapshot(raw: bytes, workspace: str, prompt: str, session_id: str, not_before_ms: int) -> dict:
    complete_rows = raw.rpartition(b"\n")[0]
    if not complete_rows:
        return {}
    lines = complete_rows.decode("utf-8-sig").split("\n")
    header, *events = [json.loads(line) for line in lines if line.strip()]
    if not isinstance(header, dict) or not all(isinstance(event, dict) for event in events):
        raise ValueError("Expected native session and event objects")
    created_at = header.get("createdAt")
    if type(created_at) is not int or created_at < not_before_ms:
        return {}
    acceptance = verify_submission(lines, workspace, prompt)
    if not acceptance["AcceptanceVerified"] or acceptance["SessionId"] != session_id:
        return {}

    starts = [event for event in events if event.get("type") == "turn/start"]
    ends = [event for event in events if event.get("type") == "turn/end"]
    user = next(event for event in events if event.get("type") == "user/message"
                and event["data"]["source"]["kind"] == "user")
    if len(starts) != 1 or len(ends) > 1:
        return {}
    start = starts[0]
    turn = start.get("data", {}).get("turn")
    start_seq, user_seq = start.get("seq"), user.get("seq")
    if type(turn) is not int or type(start_seq) is not int or type(user_seq) is not int or start_seq >= user_seq:
        return {}
    proof = {"Acceptance": acceptance, "Turn": turn, "UserMessageSequence": user_seq}
    if not ends:
        return proof

    end = ends[0]
    end_seq = end.get("seq")
    end_data = end.get("data", {})
    status = end_data.get("reason", {}).get("kind")
    if end_data.get("turn") != turn or type(end_seq) is not int or end_seq <= user_seq:
        return {}
    if not isinstance(status, str) or not status:
        return {}

    reply = None
    for event in events:
        if event.get("type") != "assistant/message" or not user_seq < event["seq"] < end_seq:
            continue
        data = event.get("data", {})
        if data.get("turn") == turn:
            content = data.get("message", {}).get("content", [])
            message = "".join(part["text"] for part in content if part.get("type") == "text")
            if message:
                reply = message
    return {**proof, "Basis": "native_turn_end", "NativeTurnEnd": end, "TurnStatus": status, "ReplyText": reply}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("prompt_file", type=Path)
    parser.add_argument("workspace")
    parser.add_argument("session_id")
    parser.add_argument("not_before_ms", type=int)
    args = parser.parse_args()
    try:
        prompt = args.prompt_file.read_bytes().decode("utf-8")
        proof = inspect_snapshot(read_snapshot(args.snapshot), args.workspace, prompt,
                                 args.session_id, args.not_before_ms)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
        proof = {"EvidenceError": str(error)}
    print(json.dumps(proof))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
