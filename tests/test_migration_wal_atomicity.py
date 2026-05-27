"""Lock-in regression test: SIGKILL inside an active BEGIN IMMEDIATE on a
WAL-mode SQLite leaves NO intermediate state on disk.

This test exists to anchor the runtime verdict of #497 (Halberg, 2026-05-27)
into the test suite. If SQLite ever changes its WAL durability semantics —
or if someone adds an `executescript()` call to a migration helper, or
weakens the `transaction()` wrapper — this test will fail.

The five-step migration pattern under test mirrors what
`db/schema.py::_migrate_qty_not_null` does (CREATE positions_new, INSERT,
DROP positions, RENAME, CREATE INDEX). For each kill point K (0 through 5),
the child process performs steps 0..K, then exits via `os._exit(137)`
without COMMIT. The OS releases the fd cold — Python's atexit handlers,
sqlite3 connection __del__, and any cleanup are all skipped. This is the
closest reachable approximation to SIGKILL on Windows (where SIGKILL does
not exist) and is exact on POSIX.

The assertion is then: on next open from a fresh connection, the DB shows
no tables. The mid-tx kill rolled back every write — including STEP 0's
table creation.

If this test ever fails, two things may be true:
  1. SQLite no longer honors its WAL commit-marker durability contract.
  2. A code path slipped in that breaks `transaction()`'s atomicity claim.

Either is a release-blocking discovery.

Related:
  - #497 (closed wontfix — premise rejected by this runtime evidence)
  - db/transaction.py:36-75 (the BEGIN IMMEDIATE / COMMIT wrapper)
  - db/schema.py:78 (PRAGMA journal_mode=WAL)
  - db/connection.py:81 (isolation_level=None)
  - db/schema.py:950-979 (corrected comment describing the orphan mechanism)
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# The child runs in its own process so that os._exit() can simulate a kill
# without taking the test runner down with it. Steps mirror
# _migrate_qty_not_null's recreate-positions sequence.
_CHILD_SCRIPT = """
import os
import sqlite3
import sys

db_path = sys.argv[1]
kill_after_step = int(sys.argv[2])

con = sqlite3.connect(db_path, isolation_level=None)
con.execute("PRAGMA journal_mode = WAL")
con.execute("PRAGMA busy_timeout = 5000")
con.execute("BEGIN IMMEDIATE")

con.execute('''CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    qty REAL,
    status TEXT
)''')
con.execute("INSERT INTO positions(symbol, qty, status) VALUES('BTC', 1.5, 'open')")
con.execute("INSERT INTO positions(symbol, qty, status) VALUES('ETH', NULL, 'open')")

if kill_after_step == 0:
    os._exit(137)

con.execute('''CREATE TABLE positions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    qty REAL,
    status TEXT,
    CHECK (qty IS NOT NULL OR status = 'legacy_unmeasurable')
)''')
if kill_after_step == 1:
    os._exit(137)

con.execute("UPDATE positions SET status='legacy_unmeasurable' WHERE qty IS NULL")
con.execute("INSERT INTO positions_new(id, symbol, qty, status) "
            "SELECT id, symbol, qty, status FROM positions")
if kill_after_step == 2:
    os._exit(137)

con.execute("DROP TABLE positions")
if kill_after_step == 3:
    os._exit(137)

con.execute("ALTER TABLE positions_new RENAME TO positions")
if kill_after_step == 4:
    os._exit(137)

con.execute("CREATE INDEX idx_positions_symbol ON positions(symbol)")
if kill_after_step == 5:
    os._exit(137)

con.execute("COMMIT")
con.close()
"""


def _observe(db_path: str) -> dict:
    con = sqlite3.connect(db_path, isolation_level=None)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        indexes = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        return {"tables": tables, "indexes": indexes}
    finally:
        con.close()


@pytest.mark.parametrize("kill_after_step", [0, 1, 2, 3, 4, 5])
def test_sigkill_inside_begin_immediate_leaves_no_state_on_disk(kill_after_step):
    """For every kill point inside an active BEGIN IMMEDIATE, the on-disk
    DB has no tables after reopen. WAL commit-marker absence forecloses
    every "kill between STEP N and STEP N+1" scenario.

    This is the empirical refutation of #497's 4-cell state diagram.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "atomicity.db")
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT, db_path, str(kill_after_step)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # The child exits with 137 only when kill_after_step is hit.
        # If it ever exits 0, the COMMIT ran — meaning kill_after_step was
        # past STEP 5; for K in [0..5] the child must NOT reach COMMIT.
        assert result.returncode == 137, (
            f"child did not exit via os._exit(137) at kill_after_step={kill_after_step}; "
            f"got exit_code={result.returncode}, stderr={result.stderr[:300]!r}"
        )

        observation = _observe(db_path)
        # The KEY assertion: nothing committed. Empty DB.
        assert observation["tables"] == [], (
            f"kill_after_step={kill_after_step} left tables on disk: "
            f"{observation['tables']}. This violates the WAL atomicity contract "
            f"and invalidates db/schema.py:944's claim that DDL inside transaction() "
            f"is atomic. Investigate before proceeding — every migration helper "
            f"depends on this guarantee."
        )
        assert observation["indexes"] == [], (
            f"kill_after_step={kill_after_step} left indexes on disk: "
            f"{observation['indexes']}. Same atomicity violation as above."
        )


def test_clean_commit_does_persist():
    """Sanity: when the child runs to COMMIT, the final state IS visible.
    Without this, the kill-tests above would pass vacuously (e.g., if the
    child were somehow not writing anything at all).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "atomicity_commit.db")
        # kill_after_step=99 means none of the kill branches trigger;
        # the child runs to COMMIT.
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT, db_path, "99"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"clean run failed: exit={result.returncode}, stderr={result.stderr[:300]!r}"
        )
        observation = _observe(db_path)
        assert "positions" in observation["tables"]
        assert "idx_positions_symbol" in observation["indexes"]
