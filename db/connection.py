"""DB connection layer — row factory + backup + private connection opener.

Extracted from btc_api.py:798-857 in PR0 of the api+db domain refactor (2026-04-27).

Design:
- _DictRow is a tuple subclass that supports both indexed access (row[0])
  AND dict-style access (row["column"]). It exists because health
  persistence tests rely on tuple equality while route code wants
  dict-style. sqlite3.Row doesn't support equality the way we need.
- _open_configured_connection() is private; all data access goes through
  db.transaction.transaction().
- backup_db uses sqlite3.Connection.backup() (online backup API) for a
  consistent snapshot even while the DB is actively being written to in
  WAL mode. Keeps the most recent _BACKUP_MAX_FILES files in _BACKUP_DIR.
  Both connections close cleanly even if .backup() raises (Serrano F-08).
"""
from __future__ import annotations

import glob
import logging
import os
import sqlite3
from datetime import datetime

log = logging.getLogger("db.connection")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(_SCRIPT_DIR, "signals.db")
_BACKUP_DIR = os.path.join(_SCRIPT_DIR, "backups")
_BACKUP_MAX_FILES = 7


def _resolve_db_file() -> str:
    """Resolve the active DB path at call time.

    Checks btc_api.DB_FILE at call time to honour the legacy
    `monkeypatch.setattr(btc_api, "DB_FILE", path)` pattern used throughout
    the test suite. The lazy import is inside a function body — intentional
    escape hatch exempt from top-level import boundary checks.
    """
    try:
        import btc_api  # noqa: PLC0415
        return getattr(btc_api, "DB_FILE", DB_FILE)
    except ImportError:
        return DB_FILE


class _DictRow(tuple):
    """Row factory that behaves as a plain tuple (supports == comparison) while
    also supporting dict-style access via row["column"] and row.get("column").
    This makes health persistence tests work cleanly without sqlite3.Row quirks."""

    def __new__(cls, cursor, row):
        instance = super().__new__(cls, row)
        instance._mapping = {
            desc[0]: val for desc, val in zip(cursor.description, row)
        }
        return instance

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._mapping[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def keys(self):
        return self._mapping.keys()


def _open_configured_connection(busy_timeout_ms: int = 15000) -> sqlite3.Connection:
    """Open a configured sqlite3.Connection.

    PRIVATE. Use db.transaction.transaction() for all data access.

    isolation_level=None disables Python's implicit transaction management
    so transaction() can drive BEGIN/COMMIT/ROLLBACK explicitly.

    `busy_timeout_ms` defaults to 15000 (see comment below). Interactive
    endpoints that retry on contention (e.g. auth writes, prod incident
    2026-06-10) pass a shorter per-attempt timeout so total latency stays
    bounded across retries instead of blocking 15s on a single attempt.
    """
    db_file = _resolve_db_file()
    con = sqlite3.connect(db_file, isolation_level=None)
    con.row_factory = _DictRow
    # busy_timeout raised 5000 -> 15000 after a production lock-contention
    # incident (2026-05-29): the scanner's per-scan kill-switch decision burst
    # (~80 BEGIN IMMEDIATE writes/cycle on a 460MB DB) held the writer lock long
    # enough that read endpoints routed through transaction() (also
    # BEGIN IMMEDIATE) timed out at 5s and returned 500. Read endpoints should
    # use snapshot_connection() (WAL-concurrent, no writer lock) — this longer
    # timeout is the safety net for the writes + any remaining BEGIN IMMEDIATE
    # readers. Do NOT lower below 15000 without addressing the write-burst load.
    con.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return con


def backup_db() -> None:
    """Create a timestamped backup of signals.db using sqlite3 online backup.
    Keeps last _BACKUP_MAX_FILES backups. Uses sqlite3.Connection.backup() for
    a consistent snapshot even while the database is actively being written to
    (WAL mode). Both connections close cleanly even if .backup() raises
    (resolves Serrano F-08)."""
    from contextlib import closing
    db_file = _resolve_db_file()
    if not os.path.exists(db_file):
        return
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(_BACKUP_DIR, f"signals_{timestamp}.db")
    try:
        with closing(sqlite3.connect(db_file)) as src:
            with closing(sqlite3.connect(backup_path)) as dst:
                src.backup(dst)
        log.info(f"DB backup: {backup_path}")
        # Cleanup old backups
        backups = sorted(glob.glob(os.path.join(_BACKUP_DIR, "signals_*.db")))
        for old in backups[:-_BACKUP_MAX_FILES]:
            os.remove(old)
            log.info(f"DB backup removed: {old}")
    except Exception as e:
        log.warning(f"DB backup failed: {e}")

