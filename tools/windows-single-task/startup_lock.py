"""Process-wide admission lock for the controlled Windows host."""

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3


WINDOWS_LOCK_DATABASE = Path(r"E:\Harness-Eval\Control\startup-lock.sqlite3")


class StartupLockBusy(Exception):
    """Another process holds the startup lock."""


@contextmanager
def acquire_startup_lock(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=0, isolation_level=None)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                raise StartupLockBusy(str(database)) from error
            raise
        # Keep the writer reservation until close. The attempt ledger uses a separate database.
        yield


def acquire_windows_startup_lock():
    if os.name != "nt":
        raise RuntimeError("The Windows startup lock requires a Windows host")
    return acquire_startup_lock(WINDOWS_LOCK_DATABASE)
