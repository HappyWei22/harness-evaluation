"""Durable host-side record for one single-task DSH run.

Everything needed to resume a request lives here: the frozen task (prompt text and input
fingerprints), the stage reached, and the retrieved results. This module never touches the
virtual machine, the startup lock, or the attempt ledger, so the whole state machine can be
exercised offline against a temporary directory.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

REQUEST_ROOT = Path(r"E:\Harness-Eval\Control\requests")

# A request only moves forward. The number is stored with every stage change so a resume can
# tell "the stage recorded on disk is behind the artifacts" from "nothing has happened yet".
STAGE_ORDER = ("accepted", "restored", "prepared", "dispatched", "completed", "retrieved", "closed")

# Paths the task manifest may name: relative, below the workspace, never absolute or parent.
FORBIDDEN_PARTS = {"..", ".", ""}


class RequestConflict(Exception):
    """The same request id already exists with different frozen content."""


class TaskLayoutError(Exception):
    """The task manifest or its files cannot be used as declared."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def stage_index(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError as error:
        raise TaskLayoutError(f"Unknown stage: {stage}") from error


def relative_workspace_path(value: str, field: str) -> str:
    """Accept only a plain relative path under the workspace."""
    if not isinstance(value, str) or not value.strip():
        raise TaskLayoutError(f"{field} must be a non-empty string")
    normalized = value.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if any(part in FORBIDDEN_PARTS for part in parts):
        raise TaskLayoutError(f"{field} escapes the workspace: {value}")
    if ":" in normalized:
        raise TaskLayoutError(f"{field} must not contain a drive or stream: {value}")
    return "/".join(parts)


def load_manifest(task_path: Path) -> dict:
    """Read and validate one task manifest, keeping expected text on the host only."""
    try:
        manifest = json.loads(task_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TaskLayoutError(f"The task manifest is unreadable: {task_path}") from error
    if not isinstance(manifest, dict):
        raise TaskLayoutError("The task manifest must be a JSON object")

    for field in ("task_id", "version", "prompt"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise TaskLayoutError(f"The task manifest needs a non-empty {field}")
    if "\u0000" in manifest["prompt"]:
        raise TaskLayoutError("The prompt must not contain a null character")

    inputs = manifest.get("inputs") or []
    outputs = manifest.get("outputs") or []
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise TaskLayoutError("inputs and outputs must be lists")
    manifest["inputs"] = [relative_workspace_path(item, "inputs[]") for item in inputs]
    manifest["outputs"] = [relative_workspace_path(item, "outputs[]") for item in outputs]
    if not manifest["outputs"]:
        raise TaskLayoutError("At least one declared output is required")

    expect = manifest.get("expect") or {}
    if not isinstance(expect, dict):
        raise TaskLayoutError("expect must map an output path to its expected text")
    cleaned = {}
    for key, value in expect.items():
        path = relative_workspace_path(key, "expect")
        if path not in manifest["outputs"]:
            raise TaskLayoutError(f"expect names an undeclared output: {key}")
        if not isinstance(value, str):
            raise TaskLayoutError(f"expect[{key}] must be a string")
        cleaned[path] = value
    manifest["expect"] = cleaned
    return manifest


def freeze_request(store: Path, request_id: str, task_path: Path) -> tuple[dict, bool]:
    """Persist the task once. A second call with identical content reuses the record.

    Freezing is what makes a retry safe: the prompt text and every input byte are pinned
    before the virtual machine is touched, so a later edit to the task file cannot silently
    turn a retry into a different question.
    """
    if not request_id or any(character in request_id for character in '\\/:*?"<>|'):
        raise TaskLayoutError("The request id contains characters a directory name cannot hold")
    task_path = task_path.resolve()
    manifest = load_manifest(task_path)
    task_root = task_path.parent

    inputs = []
    for relative in manifest["inputs"]:
        source = task_root / relative
        if not source.is_file():
            raise TaskLayoutError(f"A declared input is missing: {relative}")
        payload = source.read_bytes()
        inputs.append({"path": relative, "sha256": sha256_bytes(payload), "size": len(payload)})

    record = {
        "request_id": request_id,
        "frozen_at": now(),
        "task_file": str(task_path),
        "task_id": manifest["task_id"],
        "task_version": manifest["version"],
        "prompt": manifest["prompt"],
        "prompt_sha256": sha256_bytes(manifest["prompt"].encode("utf-8")),
        "inputs": inputs,
        "outputs": manifest["outputs"],
        "expect": manifest["expect"],
    }

    store.mkdir(parents=True, exist_ok=True)
    request_file = store / "request.json"
    if request_file.exists():
        existing = json.loads(request_file.read_text(encoding="utf-8"))
        if _fingerprint(existing) != _fingerprint(record):
            raise RequestConflict(
                f"Request {request_id} was frozen with different prompt, inputs, or outputs")
        return existing, True

    atomic_write_json(request_file, record)
    return record, False


def _fingerprint(record: dict) -> dict:
    """Only content that changes the question, not paths or timestamps."""
    return {
        "task_id": record.get("task_id"),
        "task_version": record.get("task_version"),
        "prompt_sha256": record.get("prompt_sha256"),
        "inputs": record.get("inputs"),
        "outputs": record.get("outputs"),
        "expect": record.get("expect"),
    }


def atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if path.exists():
        path.unlink()
    temporary.replace(path)


def load_state(store: Path) -> dict | None:
    state_file = store / "state.json"
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text(encoding="utf-8"))


def save_state(store: Path, state: dict) -> None:
    state["updated_at"] = now()
    atomic_write_json(store / "state.json", state)


def new_state(record: dict, attempt_id: str, activity: str) -> dict:
    return {
        "request_id": record["request_id"],
        "activity": activity,
        "task_id": record["task_id"],
        "task_version": record["task_version"],
        "attempt_id": attempt_id,
        "stage": "accepted",
        "started_at": now(),
        "updated_at": now(),
        "failure": None,
        "stages": [{"stage": "accepted", "at": now(), "detail": None}],
        "result": None,
    }


def advance(state: dict, stage: str, detail=None) -> dict:
    """Move the request forward and record why. Backwards or repeated moves are refused."""
    current = stage_index(state["stage"])
    target = stage_index(stage)
    if target < current:
        raise TaskLayoutError(f"The request cannot move back from {state['stage']} to {stage}")
    if target == current:
        return state
    state["stage"] = stage
    state.setdefault("stages", []).append({"stage": stage, "at": now(), "detail": detail})
    return state


def stage_reached(state: dict | None, stage: str) -> bool:
    if not state:
        return False
    return stage_index(state["stage"]) >= stage_index(stage)


def verify_outputs(results_root: Path, record: dict) -> dict:
    """Check every declared output against the host copy.

    Missing and unreadable are kept apart: a missing file is an honest task failure, while an
    unreadable one is a collection problem and must not be reported as a wrong answer.
    """
    report = {"files": [], "verdict": "pass", "missing": [], "unreadable": [], "mismatched": []}
    expected = record.get("expect") or {}
    for relative in record["outputs"]:
        path = results_root / relative
        entry = {"path": relative, "exists": path.is_file()}
        if not entry["exists"]:
            entry["status"] = "missing"
            report["missing"].append(relative)
            report["files"].append(entry)
            continue
        try:
            payload = path.read_bytes()
            text = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            entry["status"] = "unreadable"
            entry["error"] = f"{type(error).__name__}: {error}"
            report["unreadable"].append(relative)
            report["files"].append(entry)
            continue
        entry.update({"status": "present", "size": len(payload), "sha256": sha256_bytes(payload)})
        if relative in expected:
            wanted = expected[relative]
            same = text == wanted or text.rstrip("\r\n") == wanted.rstrip("\r\n")
            entry["matches_expect"] = same
            if not same:
                entry["expected_sha256"] = sha256_bytes(wanted.encode("utf-8"))
                report["mismatched"].append(relative)
        report["files"].append(entry)

    if report["unreadable"]:
        report["verdict"] = "collection_incomplete"
    elif report["missing"]:
        report["verdict"] = "task_missing_output"
    elif report["mismatched"]:
        report["verdict"] = "task_wrong_content"
    return report


def publish_results(results_root: Path, guest_root: Path, record: dict) -> dict:
    """Copy declared outputs from the guest staging directory, then fingerprint the host copies."""
    results_root.mkdir(parents=True, exist_ok=True)
    copied = []
    for relative in record["outputs"]:
        source = guest_root / relative
        target = results_root / relative
        entry = {"path": relative, "copied": False}
        if not source.is_file():
            entry["error"] = "the source file does not exist"
            copied.append(entry)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entry.update({
            "copied": True,
            "source_size": source.stat().st_size,
            "host_size": target.stat().st_size,
            "source_sha256": sha256_file(source),
            "host_sha256": sha256_file(target),
        })
        entry["identical"] = (entry["source_sha256"] == entry["host_sha256"]
                              and entry["source_size"] == entry["host_size"])
        copied.append(entry)
    return {"files": copied,
            "verdict": "pass" if all(item.get("identical") for item in copied) else "copy_mismatch"}


def summarize(store: Path) -> dict:
    record = json.loads((store / "request.json").read_text(encoding="utf-8"))
    state = load_state(store)
    return {
        "request_id": record["request_id"],
        "task": f"{record['task_id']} v{record['task_version']}",
        "stage": state["stage"] if state else "not started",
        "attempt_id": state.get("attempt_id") if state else None,
        "activity": state.get("activity") if state else None,
        "started_at": state.get("started_at") if state else None,
        "failure": state.get("failure") if state else None,
        "submissions": (state.get("result") or {}).get("submissions") if state else None,
        "task_result": (state.get("result") or {}).get("task_result") if state else None,
        "restore": (state.get("result") or {}).get("restore") if state else None,
    }
