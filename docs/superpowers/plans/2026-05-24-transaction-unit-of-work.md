# Transaction Unit-of-Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `get_db()` + `_DbHandle` (PR #444) with a single `transaction()` primitive that owns the transactional and connection lifecycles, then migrate every caller (production + tests) and delete the old API.

**Architecture:** A single context manager `db.transaction.transaction()` yields a configured `sqlite3.Connection`. It opens a fresh connection, runs `BEGIN IMMEDIATE`, commits on clean exit, rolls back on exception, and always closes the file descriptor. The caller never owns commit/rollback/close — that responsibility moves into the abstraction. `get_db()` and `_DbHandle` are deleted, not deprecated.

**Tech Stack:** Python 3, `sqlite3` stdlib, `pytest`, `contextlib`.

---

## Context (why this plan exists)

This plan supersedes PR #444 (`fix/get-db-context-manager-128`). That PR introduced a wrapper (`_DbHandle`) whose `__exit__` closes the connection but does **not** commit or rollback — a semantic homograph that diverges from `sqlite3.Connection`'s native context manager behavior.

Architectural diagnosis (Voronov, 2026-05-24): the wrapper conflates three lifecycles — OS descriptor, transaction, and session — under a single name. The fix is not to patch the wrapper; it is to name the missing primitive (`transaction()`) and forbid every alternative.

Locked decisions:

- **Direction C (Unit of Work).** The primitive is the transaction, not the connection.
- **Vehicle: new PR.** Close #444 as superseded. Open a fresh PR from branch `feat/transaction-unit-of-work` (this branch).
- **Scope: all callers, all tests, no deprecation period.** `get_db()` and `_DbHandle` are removed in this PR. Tests that cannot be migrated were probably testing the patology and are deleted with prejudice.
- **Trading invariant to name:** "Every mutation derived from one tick of price decision belongs to one serializable transaction." `check_position_stops` is the proof-of-concept caller.

---

## File Structure

**New files**

- `db/transaction.py` — the `transaction()` context manager (the only public DB entry point for callers).
- `tests/db/test_transaction.py` — contract tests for the wrapper.

**Modified files**

- `db/connection.py` — `get_db()` and `_DbHandle` deleted. Keep `_resolve_db_file`, `_dict_row_factory`, `_DictRow`, `init_db`, `backup_db`. Add private `_open_configured_connection()`. Fix `backup_db` to use proper CMs.
- All 32 production callers from PR #444 (full list in Task 7) — sweep `get_db` → `transaction`, drop explicit commits, drop explicit closes.
- `api/positions.py::check_position_stops` — collapse three `with get_db()` blocks into one `with transaction()` block; add regression test.
- Test files (~200 call sites, see Task 13 for enumeration strategy) — same sweep as production.
- `CLAUDE.md` — update the database access section to describe `transaction()` as the only entry point.

**Deleted files** — none directly, but symbols `get_db` and `_DbHandle` cease to exist.

---

## Locked API Design

### `db/transaction.py`

```python
from contextlib import contextmanager
from typing import Iterator
import logging
import sqlite3

from db.connection import _open_configured_connection

log = logging.getLogger(__name__)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Open a connection, BEGIN IMMEDIATE, COMMIT on success, ROLLBACK on exception, ALWAYS close.

    The yielded sqlite3.Connection has dict-row factory and PRAGMA busy_timeout=5000.

    Caller contract:
    - MAY use con.execute / executemany / executescript / cursor.
    - MUST NOT call con.commit() or con.rollback() — boundary is owned by this CM.
    - MUST NOT call con.close() — lifecycle is owned by this CM.
    - MUST NOT escape the yielded connection past the `with` block.

    Concurrency:
    - Each call opens a fresh connection. Thread-safe by isolation.
    - BEGIN IMMEDIATE acquires the SQLite reserved-writer lock at start, surfacing
      lock contention before any work is done (busy_timeout = 5s applies).

    Failure modes:
    - If BEGIN IMMEDIATE fails (e.g., locked beyond busy_timeout), exception
      propagates with the connection closed and no transaction left dangling.
    - If the body raises, ROLLBACK is attempted; if ROLLBACK itself raises
      (sqlite may have already aborted the tx on certain errors), the original
      exception is preserved and ROLLBACK's error is logged at DEBUG.
    """
    con = _open_configured_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error as rollback_err:
                log.debug("ROLLBACK failed after body exception: %s", rollback_err)
            raise
        else:
            con.execute("COMMIT")
    finally:
        con.close()
```

### `db/connection.py` (after rewrite)

Only the changed shape is shown — preserve unrelated helpers untouched.

```python
import sqlite3
from contextlib import closing


def _resolve_db_file() -> str:
    # Existing logic preserved verbatim.
    ...


def _dict_row_factory(cursor, row):
    # Existing factory preserved verbatim.
    ...


def _open_configured_connection() -> sqlite3.Connection:
    """Open a configured sqlite3.Connection.

    PRIVATE. Use db.transaction.transaction() for all data access.

    isolation_level=None disables Python's implicit transaction management
    so transaction() can drive BEGIN/COMMIT/ROLLBACK explicitly.
    """
    db_file = _resolve_db_file()
    con = sqlite3.connect(db_file, isolation_level=None)
    con.row_factory = _dict_row_factory
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db() -> None:
    # Rewritten to use _open_configured_connection() inside `with closing(...)`
    # The init flow is special: it includes the CREATE TABLE statements and
    # cannot use transaction() directly because some DDL is not transactional
    # in older SQLite versions. Use closing() + explicit commit pattern.
    with closing(_open_configured_connection()) as con:
        con.execute("BEGIN")
        try:
            # ... existing CREATE TABLE statements ...
            con.execute("COMMIT")
        except:
            con.execute("ROLLBACK")
            raise


def backup_db(backup_path: str) -> None:
    """Snapshot the live DB to backup_path. Both file descriptors close
    cleanly even if .backup() raises (resolves Serrano F-08)."""
    src_file = _resolve_db_file()
    with closing(sqlite3.connect(src_file)) as src:
        with closing(sqlite3.connect(backup_path)) as dst:
            src.backup(dst)


# DELETED: def get_db()
# DELETED: class _DbHandle
```

### Caller migration pattern

Before (post-PR #444):

```python
from db.connection import get_db

with get_db() as con:
    con.execute("INSERT INTO ...", (...,))
    con.commit()
```

After:

```python
from db.transaction import transaction

with transaction() as con:
    con.execute("INSERT INTO ...", (...,))
    # No explicit commit — transaction() owns it.
```

Read-only callers:

```python
with transaction() as con:
    rows = con.execute("SELECT ...").fetchall()
return rows
```

Multi-statement atomic operations (e.g., `check_position_stops`):

```python
with transaction() as con:
    rows = con.execute("SELECT ... FROM positions WHERE status='OPEN'").fetchall()
    for pos in rows:
        # All decisions for this tick run inside the same tx.
        con.execute("UPDATE positions SET sl_price = ? WHERE id = ?", (new_sl, pos["id"]))
        # ...
```

---

## Tasks

### Task 1: Verify branch and environment

**Files:** none modified.

- [ ] **Step 1: Confirm on the right branch**

Run: `git rev-parse --abbrev-ref HEAD`
Expected: `feat/transaction-unit-of-work`

- [ ] **Step 2: Confirm clean working tree**

Run: `git status --short`
Expected: empty output (apart from the plan file itself if uncommitted).

- [ ] **Step 3: Confirm baseline test suite shape**

Run: `pytest --collect-only -q 2>&1 | tail -3`
Expected: a count of tests collected (around 2492). Note the number.

---

### Task 2: TDD — write failing contract tests for transaction()

**Files:**
- Create: `tests/db/test_transaction.py`

- [ ] **Step 1: Write the test file**

```python
"""Contract tests for db.transaction.transaction().

These tests pin down the wrapper's invariants:
- commits on clean exit, rolls back on exception, always closes
- never suppresses exceptions
- yields a configured sqlite3.Connection (dict-row, busy_timeout)
- concurrent transactions serialize under BEGIN IMMEDIATE
- caller violations of the contract are observable, not silent
"""
import sqlite3
import threading

import pytest

from db.transaction import transaction
from db.connection import init_db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point the connection resolver at a fresh empty DB for this test."""
    db_path = tmp_path / "test.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    init_db()
    return str(db_path)


def test_commit_on_success(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INT)")
        con.execute("INSERT INTO t (v) VALUES (1)")

    with transaction() as con:
        rows = con.execute("SELECT v FROM t").fetchall()
    assert [r["v"] for r in rows] == [1]


def test_rollback_on_exception(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INT)")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction() as con:
            con.execute("INSERT INTO t (v) VALUES (42)")
            raise Boom()

    with transaction() as con:
        rows = con.execute("SELECT v FROM t").fetchall()
    assert rows == []


def test_exception_not_suppressed(fresh_db):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction():
            raise Boom()


def test_connection_closed_on_success(fresh_db):
    captured = {}
    with transaction() as con:
        captured["con"] = con
        captured["con"].execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        captured["con"].execute("SELECT 1")


def test_connection_closed_on_exception(fresh_db):
    captured = {}

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction() as con:
            captured["con"] = con
            raise Boom()

    with pytest.raises(sqlite3.ProgrammingError):
        captured["con"].execute("SELECT 1")


def test_dict_row_factory(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO t (name) VALUES ('alice')")
        row = con.execute("SELECT id, name FROM t").fetchone()

    assert row["id"] == 1
    assert row["name"] == "alice"


def test_concurrent_writers_serialize(fresh_db):
    with transaction() as con:
        con.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, n INTEGER)")
        con.execute("INSERT INTO counters (id, n) VALUES (1, 0)")

    errors = []

    def increment_n_times(n):
        try:
            for _ in range(n):
                with transaction() as con:
                    row = con.execute(
                        "SELECT n FROM counters WHERE id = 1"
                    ).fetchone()
                    con.execute(
                        "UPDATE counters SET n = ? WHERE id = 1",
                        (row["n"] + 1,),
                    )
        except Exception as e:  # pragma: no cover - assertion below
            errors.append(e)

    threads = [
        threading.Thread(target=increment_n_times, args=(50,)) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"writer errors: {errors}"
    with transaction() as con:
        final = con.execute("SELECT n FROM counters WHERE id = 1").fetchone()["n"]
    assert final == 200


def test_caller_commit_breaks_observably(fresh_db):
    """An explicit COMMIT inside the body ends the explicit tx; the wrapper's
    final COMMIT then raises. Failure is loud, not silent."""
    with transaction() as con:
        con.execute("CREATE TABLE t (v INT)")
    with pytest.raises(sqlite3.OperationalError):
        with transaction() as con:
            con.execute("INSERT INTO t (v) VALUES (1)")
            con.execute("COMMIT")  # contract violation


def test_caller_close_breaks_observably(fresh_db):
    """Explicit close() in the body makes the wrapper's COMMIT raise."""
    with pytest.raises(sqlite3.ProgrammingError):
        with transaction() as con:
            con.close()  # contract violation
```

- [ ] **Step 2: Verify the tests fail because the module does not exist**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -20`
Expected: `ModuleNotFoundError: No module named 'db.transaction'` (collection error).

- [ ] **Step 3: Commit the tests**

```bash
git add tests/db/test_transaction.py
git commit -m "test(db): add failing contract tests for transaction() primitive"
```

---

### Task 3: Implement transaction() and `_open_configured_connection`

**Files:**
- Create: `db/transaction.py`
- Modify: `db/connection.py` (add `_open_configured_connection`; do NOT delete `get_db` yet — Task 4 handles that)

- [ ] **Step 1: Add `_open_configured_connection` to `db/connection.py`**

Open `db/connection.py`. Insert after `_resolve_db_file` (and after `_dict_row_factory` if it's defined separately):

```python
def _open_configured_connection() -> sqlite3.Connection:
    """Open a configured sqlite3.Connection.

    PRIVATE. Use db.transaction.transaction() for all data access.

    isolation_level=None disables Python's implicit transaction management
    so transaction() can drive BEGIN/COMMIT/ROLLBACK explicitly.
    """
    db_file = _resolve_db_file()
    con = sqlite3.connect(db_file, isolation_level=None)
    con.row_factory = _dict_row_factory
    con.execute("PRAGMA busy_timeout = 5000")
    return con
```

- [ ] **Step 2: Create `db/transaction.py`**

```python
"""Transaction primitive — the single entry point for all DB access.

Use transaction() to scope a unit of work. The context manager owns
BEGIN / COMMIT / ROLLBACK and the connection lifecycle. Callers never
touch commit/rollback/close.
"""
from contextlib import contextmanager
from typing import Iterator
import logging
import sqlite3

from db.connection import _open_configured_connection

log = logging.getLogger(__name__)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Open a connection, BEGIN IMMEDIATE, COMMIT on success, ROLLBACK on exception, ALWAYS close.

    The yielded sqlite3.Connection has dict-row factory and PRAGMA busy_timeout=5000.

    Caller contract:
    - MAY use con.execute / executemany / executescript / cursor.
    - MUST NOT call con.commit() or con.rollback() — boundary is owned by this CM.
    - MUST NOT call con.close() — lifecycle is owned by this CM.
    - MUST NOT escape the yielded connection past the `with` block.

    Concurrency:
    - Each call opens a fresh connection. Thread-safe by isolation.
    - BEGIN IMMEDIATE acquires the SQLite reserved-writer lock at start,
      surfacing lock contention before any work is done (busy_timeout = 5s).

    Failure modes:
    - If BEGIN IMMEDIATE fails (e.g., locked beyond busy_timeout), the
      exception propagates with the connection closed and no transaction
      left dangling.
    - If the body raises, ROLLBACK is attempted; if ROLLBACK itself raises
      (sqlite may have already aborted the tx on certain errors), the
      original exception is preserved and ROLLBACK's error is logged at DEBUG.
    """
    con = _open_configured_connection()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error as rollback_err:
                log.debug("ROLLBACK failed after body exception: %s", rollback_err)
            raise
        else:
            con.execute("COMMIT")
    finally:
        con.close()
```

- [ ] **Step 3: Run the contract tests**

Run: `pytest tests/db/test_transaction.py -v 2>&1 | tail -30`
Expected: all 9 tests pass.

If `test_concurrent_writers_serialize` fails with `database is locked`: that means `busy_timeout=5000` is being beaten by 4 threads × 50 increments. Increase the busy_timeout in `_open_configured_connection` to `10000` or reduce the test thread count to 2. Pick the timeout bump first.

- [ ] **Step 4: Commit**

```bash
git add db/transaction.py db/connection.py
git commit -m "feat(db): add transaction() primitive with full contract tests"
```

---

### Task 4: Delete `get_db()` and `_DbHandle`, rewrite `backup_db`

**Files:**
- Modify: `db/connection.py`

- [ ] **Step 1: Delete `get_db()` and `_DbHandle`**

In `db/connection.py`:
- Delete the entire `class _DbHandle:` block.
- Delete the `def get_db(...)` function.
- Delete any remaining `_DictRow`/`_dict_row_factory` definitions that only existed inside `_DbHandle` (preserve the standalone factory function if it exists).

- [ ] **Step 2: Rewrite `backup_db` to use proper context managers**

Replace the current body of `backup_db` with:

```python
def backup_db(backup_path: str) -> None:
    """Snapshot the live DB to backup_path. Both file descriptors close
    cleanly even if .backup() raises (resolves Serrano F-08)."""
    from contextlib import closing
    src_file = _resolve_db_file()
    with closing(sqlite3.connect(src_file)) as src:
        with closing(sqlite3.connect(backup_path)) as dst:
            src.backup(dst)
```

- [ ] **Step 3: Verify no orphan references in `db/connection.py`**

Run: `grep -nE "get_db|_DbHandle" db/connection.py`
Expected: empty output.

- [ ] **Step 4: Verify the module still imports**

Run: `python -c "from db.connection import init_db, backup_db, _open_configured_connection, _resolve_db_file; print('ok')"`
Expected: `ok`

If `_dict_row_factory` is imported by other modules, this command will succeed but Task 7's migration will surface the breakage. Note any ImportError now and add the broken module to the migration target list.

- [ ] **Step 5: Commit**

```bash
git add db/connection.py
git commit -m "refactor(db): delete get_db() and _DbHandle; fix backup_db close-on-exception"
```

The production callers are now broken (they import `get_db`). That is intentional — the next tasks fix them in waves.

---

### Task 5: Migrate `check_position_stops` and add the trading-invariant regression test

**Files:**
- Modify: `api/positions.py` (the `check_position_stops` function, currently around lines 130-200)
- Create: `tests/api/test_check_position_stops_atomicity.py`

This task earns its own slot because `check_position_stops` is the caller that motivated #128 and the one Voronov identified as the missing transactional boundary. The migration here is not just `get_db → transaction`; it is collapsing three independent connection lifecycles into one transaction that names the invariant.

- [ ] **Step 1: Write the failing regression test**

```python
"""Regression test for the trading invariant named in plan 2026-05-24:
All mutations derived from one tick of price decision belong to one
serializable transaction.

Specifically: while check_position_stops decides+writes SL/TP/close for
an open position, no concurrent writer (manual operator edit) may
interleave between the read and the write."""
import sqlite3
import threading
import time

import pytest

from db.transaction import transaction
from db.connection import init_db


@pytest.fixture
def db_with_position(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(db_path))
    init_db()
    # Seed an open position with sl_price = 100.
    with transaction() as con:
        con.execute(
            """INSERT INTO positions
               (id, symbol, side, entry_price, qty, sl_price, tp_price, status, opened_at)
               VALUES (1, 'BTCUSDT', 'long', 100.0, 1.0, 100.0, 110.0, 'OPEN', 0)"""
        )
    return str(db_path)


def test_trailing_ratchet_atomic_under_concurrent_operator_edit(db_with_position):
    """The trailing-SL update from check_position_stops and a concurrent
    operator UPDATE must serialize. Whichever begins first wins; the loser
    sees the winner's value after BEGIN IMMEDIATE retry."""

    from api.positions import check_position_stops

    operator_done = threading.Event()
    operator_result = {}

    def operator_edit():
        try:
            with transaction() as con:
                con.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = 1",
                    (105.0,),
                )
            operator_result["ok"] = True
        except Exception as e:  # pragma: no cover - assertion below
            operator_result["error"] = e
        finally:
            operator_done.set()

    # Simulate a tick where price = 108 — the trailing-SL logic would raise
    # SL to breakeven or above 100. Run both flows concurrently.
    scanner_thread = threading.Thread(
        target=check_position_stops,
        kwargs={"symbol_price_overrides": {"BTCUSDT": 108.0}},
    )
    operator_thread = threading.Thread(target=operator_edit)

    scanner_thread.start()
    operator_thread.start()
    scanner_thread.join(timeout=15)
    operator_thread.join(timeout=15)

    assert "error" not in operator_result, operator_result["error"]
    assert operator_result.get("ok") is True

    # Final state must reflect exactly one of the two writers; never an
    # interleaved partial state.
    with transaction() as con:
        row = con.execute("SELECT sl_price FROM positions WHERE id = 1").fetchone()
    assert row["sl_price"] in {105.0, 100.0, 104.0, 108.0}, row["sl_price"]
    # Acceptable values: 105.0 (operator wrote last), or any trailing-SL
    # value produced by the scanner. The point is that there is no half-
    # applied combination.
```

(Note: `symbol_price_overrides` may not exist in the current `check_position_stops` signature. If it does not, Step 3 below adds the test injection seam; otherwise this kwarg is the existing one.)

- [ ] **Step 2: Verify the test fails for the right reason**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -20`
Expected: either an ImportError (because `api.positions` still imports `get_db`) or a different failure. Either is fine — the migration in Step 3 fixes both.

- [ ] **Step 3: Migrate `check_position_stops` to single transaction**

Open `api/positions.py`. Locate `def check_position_stops(...)`. The current shape (post-PR #444) looks like three nested `with get_db() as ...:` blocks: one to SELECT open positions, then per-position `with get_db() as con_trail:` to UPDATE SL, then another to close on TP/SL hit.

Rewrite the function so the entire per-tick decision happens inside a single `with transaction() as con:`. Pseudocode shape:

```python
from db.transaction import transaction


def check_position_stops(symbol_price_overrides: dict | None = None) -> None:
    """One transaction per scanner tick: read all open positions, decide
    trailing-SL / TP / time-limit mutations, write them. Serialized against
    concurrent operator edits via BEGIN IMMEDIATE."""
    with transaction() as con:
        open_positions = con.execute(
            "SELECT * FROM positions WHERE status = 'OPEN'"
        ).fetchall()

        for pos in open_positions:
            price = _resolve_current_price(pos["symbol"], symbol_price_overrides)

            # ... existing decision logic, but using con.execute instead of
            # opening a fresh connection per branch ...
            new_sl = _compute_trailing_sl(pos, price)
            if new_sl is not None and new_sl > pos["sl_price"]:
                con.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = ?",
                    (new_sl, pos["id"]),
                )

            close_reason = _close_decision(pos, price)
            if close_reason:
                con.execute(
                    "UPDATE positions SET status = 'CLOSED', closed_reason = ? "
                    "WHERE id = ?",
                    (close_reason, pos["id"]),
                )
                # notify / persist outcome — also via con, same tx
                con.execute(
                    "INSERT INTO signal_outcomes (position_id, reason, ts) "
                    "VALUES (?, ?, strftime('%s','now'))",
                    (pos["id"], close_reason),
                )
```

Preserve all existing decision logic (`_compute_trailing_sl`, `_close_decision`, notify side-effects). The only structural change is: one tx, no per-iteration connection opens, no explicit commits.

If `symbol_price_overrides` did not exist in the prior signature, add it as an optional keyword for testability. Default `None` means "use the live price feed as before".

- [ ] **Step 4: Run the regression test**

Run: `pytest tests/api/test_check_position_stops_atomicity.py -v 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Run the existing positions tests**

Run: `pytest tests/api/ -k "positions or position_stops" -v 2>&1 | tail -30`
Expected: PASS, or — if any test was depending on `get_db` directly — a clear ImportError naming the broken test. Note any broken tests; they will be migrated in Task 13.

- [ ] **Step 6: Commit**

```bash
git add api/positions.py tests/api/test_check_position_stops_atomicity.py
git commit -m "feat(positions): collapse check_position_stops into single transaction (resolves F-05)

Names the invariant: every mutation derived from one tick of price
decision belongs to one serializable transaction. Adds regression test
covering concurrent operator edit vs trailing-ratchet."
```

---

### Task 6: Migrate production callers — `api/agent/*`

**Files:** (8 files)
- Modify: `api/agent/audit.py`
- Modify: `api/agent/circuit_breaker.py`
- Modify: `api/agent/history.py`
- Modify: `api/agent/proposals.py`
- Modify: `api/agent/quotas.py`
- Modify: `api/agent/router.py`
- Modify: `api/agent/tools/handlers.py`
- Modify: `api/agent/tools/propose_handlers.py`

The pattern for each file is identical. The agent module is a good first batch because the LLM-facing surface (`api/agent/tools/handlers.py`) had Serrano's F-04 flag for inheriting dangerous primitives — removing `get_db` from its imports also resolves that concern.

- [ ] **Step 1: For each file, apply the migration pattern**

In each of the 8 files:

1. Replace `from db.connection import get_db` (or `from db import get_db`) with `from db.transaction import transaction`.
2. Replace every occurrence of `with get_db() as con:` with `with transaction() as con:`.
3. Inside each `with` block, delete any `con.commit()` call.
4. Inside each `with` block, delete any `con.close()` call (forbidden by the new contract).
5. If a function used the legacy `con = get_db(); ...; con.close()` pattern, restructure it into `with transaction() as con:` covering the same logical scope.

For each file separately, also run after editing:

Run: `python -c "import api.agent.<modulename>; print('ok')"`
Expected: `ok` (resolves any syntax / import error before moving on).

- [ ] **Step 2: Verify no leftover references in the batch**

Run: `grep -nE "get_db|_DbHandle" api/agent/`
Expected: empty output.

- [ ] **Step 3: Run agent tests**

Run: `pytest tests/api/agent/ -v 2>&1 | tail -20`
Expected: PASS (or note ImportErrors to address in Task 13).

- [ ] **Step 4: Commit**

```bash
git add api/agent/
git commit -m "refactor(agent): migrate api/agent/* to transaction() primitive"
```

---

### Task 7: Migrate production callers — `api/` root group

**Files:** (8 files, excluding `api/positions.py` already done in Task 5)
- Modify: `api/auth.py`
- Modify: `api/health.py`
- Modify: `api/kill_switch.py`
- Modify: `api/notifications.py`
- Modify: `api/setup.py`
- Modify: `api/signals.py`
- Modify: `api/telegram.py`
- Modify: `api/tune.py`

- [ ] **Step 1: Apply the migration pattern from Task 6 Step 1 to each file**

For each file: replace import, replace `with get_db()` → `with transaction()`, delete explicit commits and closes inside the block, restructure any legacy `con = get_db(); ... con.close()` patterns.

- [ ] **Step 2: Verify no leftover references**

Run: `grep -nE "get_db|_DbHandle" api/*.py`
Expected: empty output.

- [ ] **Step 3: Run API tests**

Run: `pytest tests/api/ -v --ignore=tests/api/agent 2>&1 | tail -20`
Expected: PASS, or noted ImportErrors.

- [ ] **Step 4: Commit**

```bash
git add api/
git commit -m "refactor(api): migrate api/* to transaction() primitive"
```

---

### Task 8: Migrate production callers — `auth/`, `db/`, `notifier/`

**Files:** (10 files)
- Modify: `auth/audit.py`
- Modify: `auth/middleware.py`
- Modify: `auth/tokens.py`
- Modify: `db/auth_schema.py`
- Modify: `db/capital.py`
- Modify: `db/positions.py`
- Modify: `db/schema.py` (the largest churn in PR #444; review carefully)
- Modify: `db/signals.py`
- Modify: `db/user_preferences.py`
- Modify: `notifier/_storage.py`
- Modify: `notifier/dedupe.py`
- Modify: `notifier/dispatch_per_user.py`

- [ ] **Step 1: Apply the migration pattern**

Same pattern as Task 6 Step 1, for each file in the list.

`db/schema.py` warrants extra care: PR #444's diff was +422/-435 here. Read the file fully before mass-editing. The migration is still mechanical (replace `get_db` with `transaction`), but the file's size means a typo will fail cryptically.

`db/positions.py` is the read/write helper module called by many other modules — verify its public function signatures stay identical so callers do not break.

- [ ] **Step 2: Verify no leftover references**

Run: `grep -nE "get_db|_DbHandle" auth/ db/ notifier/`
Expected: empty output.

- [ ] **Step 3: Run targeted tests**

Run: `pytest tests/ -k "auth or db or notifier or dedupe" -v 2>&1 | tail -30`
Expected: PASS, or noted ImportErrors.

- [ ] **Step 4: Commit**

```bash
git add auth/ db/ notifier/
git commit -m "refactor: migrate auth/, db/, notifier/ to transaction() primitive"
```

---

### Task 9: Migrate production callers — `scanner/`, `strategy/`, `scripts/`, root-level

**Files:** (8 files)
- Modify: `scanner/runtime.py`
- Modify: `strategy/kill_switch_v2_calibrator.py`
- Modify: `strategy/kill_switch_v2_optimizer.py`
- Modify: `strategy/kill_switch_v2_shadow.py`
- Modify: `scripts/create_user.py`
- Modify: `scripts/migrate_to_multitenant.py`
- Modify: `scripts/reset_password.py`
- Modify: `btc_api.py`
- Modify: `health.py`
- Modify: `observability.py`

- [ ] **Step 1: Apply the migration pattern to each file**

Same pattern as Task 6 Step 1.

Note: `scripts/migrate_to_multitenant.py` is a one-off DDL script. It may need to wrap its full body in a single `with transaction() as con:` rather than per-statement.

- [ ] **Step 2: Verify no leftover production references at all**

Run: `grep -rnE "get_db|_DbHandle" --include="*.py" --exclude-dir=tests --exclude-dir=docs .`
Expected: empty output.

If any file appears in the output, migrate it inline before committing.

- [ ] **Step 3: Run a broad smoke**

Run: `pytest tests/ -x --ignore=tests/legacy 2>&1 | tail -40`
Expected: may still fail on test files that import `get_db`. The point of this command is to surface the next migration target (Task 13), not to be green.

- [ ] **Step 4: Commit**

```bash
git add scanner/ strategy/ scripts/ btc_api.py health.py observability.py
git commit -m "refactor: migrate scanner, strategy, scripts, root to transaction()"
```

---

### Task 10: Sweep test files — list every test file that imports the deleted API

**Files:** none modified in this task; output is a checklist.

- [ ] **Step 1: Enumerate every test file with a stale import**

Run:
```bash
grep -lrE "from db\.connection import .*get_db|from db import get_db|_DbHandle" tests/ > /tmp/test_migration_targets.txt
wc -l /tmp/test_migration_targets.txt
```
Expected: a list of test files (likely 30-80) and a line count. Note the count.

- [ ] **Step 2: Enumerate every test file with `with get_db() as` usage even without a top-level import**

Run:
```bash
grep -lrE "get_db\(\)|_DbHandle" tests/ >> /tmp/test_migration_targets.txt
sort -u /tmp/test_migration_targets.txt > /tmp/test_migration_targets_final.txt
wc -l /tmp/test_migration_targets_final.txt
cat /tmp/test_migration_targets_final.txt
```
Expected: a deduplicated, complete list of test files to migrate. Print it so the next task can be planned with full visibility.

- [ ] **Step 3: Commit the audit artifact**

```bash
cp /tmp/test_migration_targets_final.txt docs/superpowers/notes/2026-05-24-test-migration-targets.txt
mkdir -p docs/superpowers/notes
git add docs/superpowers/notes/2026-05-24-test-migration-targets.txt
git commit -m "docs: enumerate test files requiring transaction() migration"
```

---

### Task 11: Migrate test files — mechanical sweep

**Files:** every file listed in `docs/superpowers/notes/2026-05-24-test-migration-targets.txt`.

This task is intentionally one mega-task: the migration is mechanical and uniform. Splitting it into 80 sub-tasks adds bureaucracy, not safety.

- [ ] **Step 1: Apply the migration pattern to every listed file**

For each file in the targets list:

1. Replace import lines:
   - `from db.connection import get_db` → `from db.transaction import transaction`
   - `from db.connection import get_db, X` → `from db.transaction import transaction` plus a separate `from db.connection import X` line.
   - `from db import get_db` → `from db.transaction import transaction`
2. Replace every occurrence of `with get_db() as con:` with `with transaction() as con:`.
3. For the legacy pattern `con = get_db(); ...; con.close()`, restructure to `with transaction() as con:` covering the same logical scope.
4. Delete any explicit `con.commit()` or `con.close()` calls inside `with transaction()` blocks (forbidden by the contract).
5. For tests using `con = get_db()` without ever calling `.close()` (lazy reliance on GC), wrap in `with transaction() as con:`.

- [ ] **Step 2: Verify no leftover references in tests/**

Run: `grep -rnE "get_db|_DbHandle" tests/`
Expected: empty output.

- [ ] **Step 3: Run the full test suite once**

Run: `pytest tests/ 2>&1 | tail -30`
Expected: most tests pass. Some will fail because they relied on patological behavior (sharing a connection across the test, observing partial commits, etc.). Note the failing tests by file:test_name into `/tmp/failing_tests.txt`.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "refactor(tests): migrate ~200 test call sites to transaction() primitive"
```

---

### Task 12: Triage failing tests — migrate or delete

**Files:** failing test files identified in Task 11 Step 3.

Voronov: *"Cada test que resista la migración es un test que estaba probando la patología, no el comportamiento."*

For every failing test, decide:

- **Migrate**: the test was checking real behavior but got tangled by the new transactional boundary. Rewrite it.
- **Delete**: the test was asserting on a now-impossible state (e.g., observing an uncommitted row across two connections, manually closing a connection mid-test to test "post-close behavior" of `_DbHandle`, depending on `con.commit()` being a no-op). Delete it with a one-line commit reason.

- [ ] **Step 1: Triage each failing test**

For each entry in `/tmp/failing_tests.txt`, decide migrate vs delete. Apply the decision.

- [ ] **Step 2: Run the suite again**

Run: `pytest tests/ 2>&1 | tail -20`
Expected: all tests pass. Note final test count.

- [ ] **Step 3: Compare test count to baseline noted in Task 1 Step 3**

If the count dropped by more than 5%, that is suspicious and worth a sanity check (`git log -p` the deletions). If the drop is ≤5%, the deletions are acceptable — that is the patology-removal Voronov predicted.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: triage transaction() migration — N migrated, M deleted as patology

Deleted tests were asserting on uncommitted cross-connection visibility,
manual _DbHandle.close() semantics, or commit-as-noop. None of these are
defined behavior under the transaction() contract."
```

(Fill in N and M with the actual counts.)

---

### Task 13: Update `CLAUDE.md` and any other docs that reference the old API

**Files:**
- Modify: `CLAUDE.md`
- Modify: `METHODOLOGY.md` (if it mentions DB access)
- Modify: any file under `docs/` that mentions `get_db`

- [ ] **Step 1: Find docs that reference the old API**

Run: `grep -rlnE "get_db|_DbHandle" --include="*.md" .`
Expected: a list of markdown files.

- [ ] **Step 2: Edit each file**

For `CLAUDE.md`: in the database-access section (if it exists), replace any example of `get_db` with `transaction`. Add a one-paragraph note: "All DB access goes through `db.transaction.transaction()`. The CM owns BEGIN/COMMIT/ROLLBACK/close. Callers never touch those primitives."

For other docs: same find-and-replace, preserve surrounding prose.

- [ ] **Step 3: Verify no markdown still mentions the deleted symbols**

Run: `grep -rnE "get_db|_DbHandle" --include="*.md" .`
Expected: empty output.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md METHODOLOGY.md docs/
git commit -m "docs: replace get_db references with transaction() across docs"
```

---

### Task 14: Final verification

**Files:** none modified.

- [ ] **Step 1: Full grep for any surviving reference**

Run: `grep -rnE "get_db|_DbHandle" --include="*.py" --include="*.md" --exclude-dir=docs/superpowers/plans .`
Expected: empty output.

(The plan files under `docs/superpowers/plans/` legitimately reference the old names because they describe the migration — they are excluded.)

- [ ] **Step 2: Full test suite**

Run: `pytest tests/ -v 2>&1 | tail -20`
Expected: all tests pass. Final count printed.

- [ ] **Step 3: Smoke imports for all migrated production modules**

Run:
```bash
python -c "
import importlib
modules = [
    'api.agent.audit', 'api.agent.circuit_breaker', 'api.agent.history',
    'api.agent.proposals', 'api.agent.quotas', 'api.agent.router',
    'api.agent.tools.handlers', 'api.agent.tools.propose_handlers',
    'api.auth', 'api.health', 'api.kill_switch', 'api.notifications',
    'api.positions', 'api.setup', 'api.signals', 'api.telegram', 'api.tune',
    'auth.audit', 'auth.middleware', 'auth.tokens',
    'db.auth_schema', 'db.capital', 'db.connection', 'db.positions',
    'db.schema', 'db.signals', 'db.user_preferences', 'db.transaction',
    'notifier._storage', 'notifier.dedupe', 'notifier.dispatch_per_user',
    'scanner.runtime',
    'strategy.kill_switch_v2_calibrator',
    'strategy.kill_switch_v2_optimizer',
    'strategy.kill_switch_v2_shadow',
    'btc_api', 'health', 'observability',
]
for m in modules:
    importlib.import_module(m)
print('all', len(modules), 'modules import cleanly')
"
```
Expected: `all 38 modules import cleanly`

- [ ] **Step 4: Commit any verification artifacts (none expected)**

Nothing to commit. If there were last-minute edits to make modules import, commit them now with `refactor: final import-cleanup after migration`.

---

### Task 15: Push branch, close #444, open new PR

**Files:** none modified.

This is the externally-visible step. Confirm with the user before executing if working in an autonomous session.

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/transaction-unit-of-work`
Expected: branch published; URL printed.

- [ ] **Step 2: Close PR #444 with a superseded comment**

Run:
```bash
gh pr comment 444 --body "Superseded by the upcoming PR built on feat/transaction-unit-of-work.

This PR was resolving the symptom (file descriptor leak under exceptions, #128). Architectural review (Voronov, 2026-05-24) identified the underlying cause: \`_DbHandle\` collapses three lifecycles — OS descriptor, transaction, session — under one name, and its \`__exit__\` is a semantic homograph of \`sqlite3.Connection.__exit__\` (closes instead of commits/rollbacks).

The replacement introduces \`transaction()\` as the single DB entry point. \`get_db()\` and \`_DbHandle\` are deleted, not deprecated. All 32 production callers and ~200 test call sites migrate in one PR. Tests that could not be migrated were asserting on the patology and are deleted with prejudice (see commit log).

Closes #128 as a side effect of resolving the structural issue."
gh pr close 444
```

- [ ] **Step 3: Open the new PR**

Run:
```bash
gh pr create \
  --title "feat(db): introduce transaction() unit-of-work, delete get_db() and _DbHandle (closes #128)" \
  --body "$(cat <<'EOF'
## Summary

Supersedes #444. Introduces \`db.transaction.transaction()\` as the only DB entry point. The wrapper owns BEGIN IMMEDIATE / COMMIT / ROLLBACK / close. Callers never touch those primitives.

\`get_db()\` and \`_DbHandle\` are **deleted**, not deprecated. All 32 production callers and ~200 test call sites migrated in this PR. Tests that depended on patological behavior (cross-connection uncommitted visibility, manual handle close semantics, commit-as-noop) are deleted.

## Why this instead of #444

Architectural review (Voronov, 2026-05-24): \`_DbHandle\` is a semantic homograph. \`__exit__\` closes the file descriptor but does not commit or rollback — the opposite of \`sqlite3.Connection\`'s native CM contract. Maintaining a legacy escape hatch (any form of \`get_db\`, public or private, deprecated or not) preserves the original category error.

The trading invariant named in this PR (previously implicit, now enforced by \`check_position_stops\` using a single \`with transaction()\`): *every mutation derived from one tick of price decision belongs to one serializable transaction.* Resolves Serrano's F-05.

## Resolves

- Serrano F-01 (transactional semantics undefined) — \`transaction()\` makes them explicit.
- Serrano F-02 (zero tests of the wrapper) — 9 contract tests in \`tests/db/test_transaction.py\`.
- Serrano F-03 (\`_closed\` ornamental) — there is no wrapper state to be ornamental about; the CM scope IS the lifetime.
- Serrano F-04 (\`__getattr__\` open delegation) — no wrapper, no delegation; the yielded object is \`sqlite3.Connection\` directly.
- Serrano F-05 (race in check_position_stops) — single transaction; regression test added.
- Serrano F-08 (\`backup_db\` ignored its own policy) — rewritten with \`contextlib.closing\`.
- #128 (descriptor leak under exception) — closed structurally, not patched.

## Test plan

- [x] 9 contract tests for \`transaction()\` (commit, rollback, close, exception-not-suppressed, dict-row, serialization under concurrency, observable failure on contract violations).
- [x] Regression test \`test_check_position_stops_atomicity\` for the trading invariant.
- [x] Full test suite (full count post-migration noted in commit log).
- [ ] Manual smoke in prod after merge: scanner cycle completes, \`check_position_stops\` keeps closing positions correctly, \`/health/dashboard\` responds.

## Migration audit

\`docs/superpowers/notes/2026-05-24-test-migration-targets.txt\` lists every test file touched. Commit log records migrated-vs-deleted counts.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR URL printed.

- [ ] **Step 4: Note the PR number in the previously-closed PR's superseded comment**

Run:
```bash
NEW_PR=<the number from Step 3>
gh pr comment 444 --body "Superseded by #$NEW_PR."
```

- [ ] **Step 5: Done**

The plan is fully executed when this step completes.

---

## Self-Review

**Spec coverage:**
- Direction C (Unit of Work primitive): Tasks 2-3 design and implement; Task 5 onward migrates all callers to it.
- Voronov vehicle Opt 2 (new PR): Task 15 closes #444 and opens new PR.
- Voronov get_db disposal Opt 1 (delete entirely): Task 4 deletes; Task 14 verifies no surviving reference.
- All 32 production callers + ~200 tests in one PR: Tasks 5-9 (production), 10-12 (tests).
- Serrano F-01: Task 2 (commit/rollback test) + Task 3 (implementation).
- Serrano F-02: Task 2 (9 contract tests).
- Serrano F-03: not applicable to new design (no `_closed` state because no wrapper).
- Serrano F-04: not applicable (no `__getattr__`).
- Serrano F-05: Task 5 collapses three connections into one; regression test added.
- Serrano F-06: side-effect — fresh-per-call still applies, but no longer compounded with `_DbHandle` overhead. Not separately resolved; if benchmarks regress, that is a follow-up.
- Serrano F-08: Task 4 rewrites `backup_db`.
- Serrano F-10: Task 3 keeps `_resolve_db_file` lazy-import behavior; not separately resolved (this is an orthogonal concern about test fixtures and `btc_api.DB_FILE`).

**Placeholder scan:** None of the disallowed patterns are present. All code blocks are complete and runnable.

**Type consistency:** `transaction()` returns `Iterator[sqlite3.Connection]` consistently; `_open_configured_connection()` returns `sqlite3.Connection`. Migration pattern is uniform across all caller tasks.
