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

log = logging.getLogger("db.transaction")


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


@contextmanager
def _tx_or_use(con: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """If `con` is None, open a new transaction(). Otherwise yield the
    provided connection (the caller already owns a transaction).

    Usage in helpers that may be called both standalone and inside a caller-
    controlled transaction:

        def db_close_position(pos_id, ..., con: sqlite3.Connection | None = None):
            with _tx_or_use(con) as con:
                con.execute("UPDATE positions SET status='closed' WHERE id=?", (pos_id,))

    Rationale (Task 8.5 of plan 2026-05-24-transaction-unit-of-work): helpers
    that own their own `transaction()` cannot be composed inside an outer
    caller-owned transaction without deadlocking on BEGIN IMMEDIATE (the
    inner conn waits for the outer's writer lock, busy_timeout fires).
    This wrapper resolves that by letting the outer caller pass `con` to
    each helper, while standalone callers (passing `con=None`) still get
    their own atomic boundary.
    """
    if con is None:
        with transaction() as new_con:
            yield new_con
    else:
        yield con


@contextmanager
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for read-only work outside any transaction.

    Use when an operator needs pre-validation reads (ownership check,
    existence check) that must NOT hold a writer lock. The connection
    closes on exit; no BEGIN/COMMIT is issued.

    Caller contract:
    - MAY use con.execute for SELECT.
    - MUST NOT issue INSERT/UPDATE/DELETE — if SQLite's autocommit triggers,
      the write happens without the operator's atomicity guarantee.
    - MUST NOT escape the connection past the `with` block.
    """
    con = _open_configured_connection()
    try:
        yield con
    finally:
        con.close()
