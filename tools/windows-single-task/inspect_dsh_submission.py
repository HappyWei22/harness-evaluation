"""Verify a single diagnostic submission against an exported DSH v4 session."""

import argparse
import json
import ntpath
from pathlib import Path
import sys
from zipfile import BadZipFile, ZipFile


def verify_submission(lines: list[str], expected_workspace: str, expected_prompt: str) -> dict:
    rows = [json.loads(line) for line in lines if line.strip()]
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("An exported session header and JSON event objects are required")
    header, *events = rows
    if header.get("type") != "session" or header.get("version") != 4:
        raise ValueError("Unsupported DSH session header")
    session_id = header.get("id")
    workspace = header.get("cwd")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("The log has no native session ID")
    if not isinstance(workspace, str) or not ntpath.isabs(workspace) or not ntpath.isabs(expected_workspace):
        raise ValueError("Both workspace paths must be absolute Windows paths")
    workspace_matches = ntpath.normcase(ntpath.normpath(workspace)) == ntpath.normcase(ntpath.normpath(expected_workspace))
    fresh_session = header.get("isSeeded") is False and header.get("parentSession") is None

    user_messages = []
    unknown_user_sources = 0
    for event in events:
        if event.get("type") == "user/message":
            data = event.get("data")
            source = data.get("source") if isinstance(data, dict) else None
            kind = source.get("kind") if isinstance(source, dict) else None
            if not isinstance(kind, str) or not kind:
                unknown_user_sources += 1
            elif kind == "user":
                user_messages.append(data)

    matches = 0
    for message in user_messages:
        content = message.get("content")
        if isinstance(content, list) and content and all(
            isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            for part in content
        ):
            content = "".join(part["text"] for part in content)
        if isinstance(content, str) and content == expected_prompt:
            matches += 1

    return {
        "SessionId": session_id,
        "WorkspaceMatches": workspace_matches,
        "FreshSession": fresh_session,
        "OperatorSubmissions": len(user_messages),
        "ExactPromptMatches": matches,
        "UnknownUserSources": unknown_user_sources,
        "AcceptanceVerified": workspace_matches and fresh_session and len(user_messages) == matches == 1
        and unknown_user_sources == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        prompt = args.prompt_file.read_bytes().decode("utf-8")
        if not prompt:
            raise ValueError("The staged prompt file is empty")
        with ZipFile(args.export) as archive:
            logs = [entry for entry in archive.infolist() if entry.filename == "session.v4.jsonl"]
            if len(logs) != 1:
                raise ValueError("The export must contain exactly one root session.v4.jsonl")
            result = verify_submission(archive.read(logs[0]).decode("utf-8").split("\n"), args.workspace, prompt)
    except (OSError, BadZipFile, ValueError) as error:
        print(f"DSH acceptance unresolved: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["AcceptanceVerified"] else 1


if __name__ == "__main__":
    sys.exit(main())
