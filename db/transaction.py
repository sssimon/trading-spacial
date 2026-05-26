"""Transaction primitive — the single entry point for all DB access.

Use transaction() to scope a unit of work. The context manager owns
BEGIN / COMMIT / ROLLBACK and the connection lifecycle. Callers never
touch commit/rollback/close.
"""
from contextlib import contextmanager
from typing import Iterator, NewType
import logging
import sqlite3

from db.connection import _open_configured_connection

log = logging.getLogger("db.transaction")


# Move 'precheck != snapshot' from convención to tipo (#468, Voronov 2026-05-26).
#
# PrecheckConn is a connection authorized for reads that will FEED a follow-up
# write transaction. The caller is contractually obligated to extract any field
# the write-tx will need into an immutable snapshot (see
# operators.precheck.OwnershipValidatedSnapshot) BEFORE the with block exits.
#
# SnapshotConn is a connection authorized for TERMINAL reads — results
# serialize to an output (JSON file, HTTP response, log) and are NOT used to
# drive a subsequent mutation. No follow-up re-validation obligation.
#
# Both NewTypes wrap sqlite3.Connection. mypy treats them as incompatible;
# at runtime, both are no-ops (the wrapped object is the original Connection).
# The mechanism is identical (PRAGMA query_only=1); the contract is in the
# type, not in the docstring.
PrecheckConn = NewType("PrecheckConn", sqlite3.Connection)
SnapshotConn = NewType("SnapshotConn", sqlite3.Connection)


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
def precheck_connection() -> Iterator[PrecheckConn]:
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
        yield PrecheckConn(con)
    finally:
        con.close()


@contextmanager
def snapshot_connection() -> Iterator[SnapshotConn]:
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
        yield SnapshotConn(con)
    finally:
        con.close()
