"""DB connection layer — SQLite handle factory + row factory + backup.

Extracted from btc_api.py:798-857 in PR0 of the api+db domain refactor (2026-04-27).

Design:
- get_db() returns a fresh sqlite3.Connection per call (no singleton).
  This is critical for thread safety: scanner_loop and FastAPI request
  handlers share the same DB file but each opens its own connection.
- _DictRow is a tuple subclass that supports both indexed access (row[0])
  AND dict-style access (row["column"]). It exists because health
  persistence tests rely on tuple equality while route code wants
  dict-style. sqlite3.Row doesn't support equality the way we need.
- backup_db uses sqlite3.Connection.backup() (online backup API) for a
  consistent snapshot even while the DB is actively being written to in
  WAL mode. Keeps the most recent _BACKUP_MAX_FILES files in _BACKUP_DIR.
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

    During the api+db refactor (PR0-PR7), btc_api.DB_FILE is the canonical
    source — tests routinely patch btc_api.DB_FILE for isolation, and that
    pattern must keep working through the re-export of get_db/backup_db
    from this module. This lookup honors that patch without forcing every
    test to switch to db.connection.DB_FILE. PR7 collapses btc_api.DB_FILE
    into this module and removes the lookup.
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


def get_db() -> sqlite3.Connection:
    """Open a fresh DB connection with the dict-row factory."""
    con = sqlite3.connect(_resolve_db_file())
    con.row_factory = _DictRow
    return con


def backup_db() -> None:
    """Create a timestamped backup of signals.db using sqlite3 online backup.
    Keeps last _BACKUP_MAX_FILES backups. Uses sqlite3.Connection.backup() for
    a consistent snapshot even while the database is actively being written to
    (WAL mode)."""
    db_file = _resolve_db_file()
    if not os.path.exists(db_file):
        return
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(_BACKUP_DIR, f"signals_{timestamp}.db")
    try:
        src = sqlite3.connect(db_file)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()
        log.info(f"DB backup: {backup_path}")
        # Cleanup old backups
        backups = sorted(glob.glob(os.path.join(_BACKUP_DIR, "signals_*.db")))
        for old in backups[:-_BACKUP_MAX_FILES]:
            os.remove(old)
            log.info(f"DB backup removed: {old}")
    except Exception as e:
        log.warning(f"DB backup failed: {e}")
