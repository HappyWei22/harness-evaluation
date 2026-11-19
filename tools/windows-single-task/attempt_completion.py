"""Close a registered attempt only after its verified lifecycle receipts exist."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from submission_permit import permit_transaction


COMPLETION_EVENTS = ("accepted", "terminated", "archived", "cleaned")


class CompletionNotVerified(Exception):
    """The attempt has no complete set of matching lifecycle receipts."""


def initialize_completion_table(database: Path):
    with permit_transaction(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS attempt_completion (
                attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES attempts(attempt_id),
                evidence_json TEXT NOT NULL
            )
        """)


def read_completion_receipt(path: Path, attempt_id: str, event: str) -> dict:
    try:
        content = path.read_bytes()
        receipt = json.loads(content)
    except (OSError, ValueError) as error:
        raise CompletionNotVerified(f"Missing or unreadable {event} receipt") from error
    if not isinstance(receipt, dict) or receipt.get("attempt_id") != attempt_id or receipt.get("event") != event:
        raise CompletionNotVerified(f"Mismatched {event} receipt")
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest()}


def close_completed_attempt(database: Path, attempt_id: str, receipt_directory: Path, *, kind: str = "formal"):
    # The adapter verifies raw evidence before writing the normalized receipts.
    # This function runs while the caller holds the shared startup lock.
    receipts = {
        event: read_completion_receipt(receipt_directory / f"{event}.json", attempt_id, event)
        for event in COMPLETION_EVENTS
    }
    with permit_transaction(database) as connection:
        closed_at = datetime.now(timezone.utc).isoformat()
        updated = connection.execute("""
            UPDATE attempts SET closed_at = ?
            WHERE attempt_id = ? AND kind = ? AND closed_at IS NULL
              AND EXISTS (SELECT 1 FROM submissions WHERE submissions.attempt_id = attempts.attempt_id)
        """, (closed_at, attempt_id, kind))
        if updated.rowcount != 1:
            raise CompletionNotVerified(f"An open {kind} attempt with a submission record is required")
        connection.execute("""
            INSERT INTO attempt_completion (attempt_id, evidence_json) VALUES (?, ?)
        """, (attempt_id, json.dumps(receipts)))

