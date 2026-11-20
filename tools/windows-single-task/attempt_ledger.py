"""Durable admission records kept separately from the startup lock."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from startup_lock import acquire_startup_lock


class UnclosedAttempt(Exception):
    def __init__(self, attempt_id: str, kind: str):
        self.attempt_id = attempt_id
        self.kind = kind
        super().__init__(f"Previous {kind} attempt {attempt_id} remains unclosed")


def initialize_attempt_ledger(database: Path):
    with database.open("xb"):
        pass
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("""
            CREATE TABLE attempts (
                attempt_id TEXT PRIMARY KEY NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('diagnostic', 'formal')),
                registered_at TEXT NOT NULL,
                closed_at TEXT
            )
        """)


def first_open_attempt(connection):
    return connection.execute("""
        SELECT attempt_id, kind
        FROM attempts
        WHERE closed_at IS NULL
        ORDER BY registered_at, attempt_id
        LIMIT 1
    """).fetchone()


def require_no_open_attempt(database: Path):
    """Refuse to stage a new run when the host ledger still has an open attempt."""
    uri = database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection:
        unclosed = first_open_attempt(connection)
        if unclosed:
            raise UnclosedAttempt(*unclosed)


def register_attempt(ledger_database: Path, attempt_id: str, kind: str, *, preflight=None):
    """Register an attempt while the caller holds the host startup lock."""
    uri = ledger_database.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        unclosed = first_open_attempt(connection)
        if preflight is not None:
            preflight(connection, unclosed)
        if unclosed:
            raise UnclosedAttempt(*unclosed)
        connection.execute(
            "INSERT INTO attempts (attempt_id, kind, registered_at) VALUES (?, ?, ?)",
            (attempt_id, kind, datetime.now(timezone.utc).isoformat()),
        )


@contextmanager
def register_startup(lock_database: Path, ledger_database: Path, attempt_id: str, kind: str, *, preflight=None):
    with acquire_startup_lock(lock_database):
        register_attempt(ledger_database, attempt_id, kind, preflight=preflight)
        # Commit the record before the caller can submit. Keep the separate startup lock held.
        yield
