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
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for read-only work (DEPRECATED in this PR).

    This helper carries two semantic contracts under one name (precheck +
    snapshot) — see PR #463 review (Voronov, 2026-05-25). It will be removed
    later in this plan, replaced by precheck_connection and snapshot_connection.

    Threat model (cooperative latch — NOT a sandbox):
    - Detects accidental writes from helpers contracted as read-only.
      A SQL helper that mistakenly mutates state inside this block fails loudly.
    - Does NOT protect against PRAGMA query_only=0, executescript with embedded
      PRAGMA, writes to temp.* tables, or AFTER triggers.
    - The semantic invariant "this phase does not mutate the world" lives at
      the CALL SITE, not in this primitive.

    Caller contract:
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError (cooperative).
    - MUST NOT escape the connection past the `with` block.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()


@contextmanager
def precheck_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for a PRECHECK READ that will feed a
    follow-up write transaction.

    Use when an operator needs to read state to decide whether (and how) to
    open a write transaction in a later step. The connection closes on exit;
    no BEGIN/COMMIT is issued.

    Contract (cooperative — see threat model below):
    - MAY use con.execute for SELECT.
    - INSERT/UPDATE/DELETE raise sqlite3.OperationalError via PRAGMA query_only=1.
    - MUST extract any field the write-tx will need into an immutable snapshot
      value (see operators.precheck.PositionSnapshot) BEFORE this block exits.
    - MUST NOT escape the connection past the `with` block.

    Threat model:
    - Detects accidental writes from helpers contracted as read-only.
      A SQL helper that mistakenly mutates state inside this block fails LOUDLY.
    - NOT a sandbox: callers can re-enable writes via PRAGMA query_only=0,
      executescript, or writes to temp.* tables. SQLite does not provide an
      ontologically read-only connection; this is a cooperative latch.
    - The semantic invariant "this phase does not mutate the world" lives at
      the CALL SITE (extract → snapshot → terminate), not in this primitive.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()


@contextmanager
def snapshot_connection() -> Iterator[sqlite3.Connection]:
    """Open a configured connection for a TERMINAL READ (no follow-up write).

    Use for snapshot generation, dashboard queries, audit reads — operations
    whose result is serialized to an output (JSON file, HTTP response, log)
    and NOT used to drive a subsequent mutation.

    Contract: same mechanism as precheck_connection, same threat model
    (cooperative latch, detector-not-sandbox). The distinct name encodes a
    distinct CALL SITE OBLIGATION: terminal reads do not need to produce a
    snapshot for hand-off to a write-tx, so there is no follow-up re-validation
    contract.

    See precheck_connection for the threat model and mechanism details.
    """
    con = _open_configured_connection()
    try:
        con.execute("PRAGMA query_only = 1")
        yield con
    finally:
        con.close()
