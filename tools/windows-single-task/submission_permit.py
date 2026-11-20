"""One-time permits and a durable marker written before an external send."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4


class PermitUnavailable(Exception):
    """The attempt has no matching, unused, unexpired permit."""


@contextmanager
def permit_transaction(database: Path):
    uri = database.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        yield connection


def initialize_submission_tables(database: Path):
    with permit_transaction(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS submission_permits (
                permit_id TEXT PRIMARY KEY NOT NULL,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
                binding_fingerprint TEXT NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES attempts(attempt_id),
                state TEXT NOT NULL CHECK (state = 'submitting'),
                started_at REAL NOT NULL
            )
        """)


def issue_submission_permit(database: Path, attempt_id: str, binding_fingerprint: str,
                            expires_at: datetime, *, kind: str = "formal") -> str:
    if expires_at.utcoffset() is None:
        raise ValueError("Permit expiration must include a time zone")
    permit_id = uuid4().hex
    with permit_transaction(database) as connection:
        inserted = connection.execute("""
            INSERT INTO submission_permits (permit_id, attempt_id, binding_fingerprint, expires_at)
            SELECT ?, attempt_id, ?, ? FROM attempts
            WHERE attempt_id = ? AND kind = ? AND closed_at IS NULL
        """, (permit_id, binding_fingerprint, expires_at.timestamp(), attempt_id, kind))
        if inserted.rowcount != 1:
            raise PermitUnavailable(f"An open {kind} attempt is required")
    return permit_id


def begin_submission(database: Path, attempt_id: str, permit_id: str, binding_fingerprint: str):
    with permit_transaction(database) as connection:
        now = datetime.now(timezone.utc).timestamp()
        consumed = connection.execute("""
            UPDATE submission_permits SET consumed_at = ?
            WHERE permit_id = ? AND attempt_id = ? AND binding_fingerprint = ?
              AND consumed_at IS NULL AND expires_at > ?
              AND EXISTS (SELECT 1 FROM attempts WHERE attempt_id = ? AND closed_at IS NULL)
        """, (now, permit_id, attempt_id, binding_fingerprint, now, attempt_id))
        if consumed.rowcount != 1:
            raise PermitUnavailable("Permit is unavailable for this submission context")
        connection.execute("""
            INSERT INTO submissions (attempt_id, state, started_at) VALUES (?, 'submitting', ?)
        """, (attempt_id, now))
    # The caller may send only after both database writes have committed.
