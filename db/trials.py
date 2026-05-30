"""Trial registry — the audit ledger for backtest trials (A.0.3, #278 Part 1).

Every backtest run inside an exploratory parameter/window SWEEP is a "trial".
The deflated-metrics work (#278 Part 2) deflates the best Sharpe by the number
of trials N that competed for selection (López de Prado 2018). For N to be
honest, the registry MUST record every selection trial BEFORE it runs, so a
crashed run still counts (its row survives as status='pending').

Design (Halberg runtime review 2026-05-29):
- Claim-then-execute: claim_trial() INSERTs status='pending' BEFORE the
  simulator runs; finalize_trial() UPDATEs to 'ok'/'failed' after. A process
  that dies before finalize leaves an orphan 'pending' row — that row IS the
  evidence the trial existed, preserving the N denominator. Registering INSIDE
  the running process AFTER the fact cannot count a process that crashes.
- Writes go through db.transaction.transaction() (configured connection,
  busy_timeout=5000, BEGIN IMMEDIATE), wrapped in bounded retry/backoff on
  'database is locked'. signals.db already has concurrent writers (scanner,
  API); a TRANSIENT lock must NOT abort a multi-hour sweep. Only durability
  exhaustion (backoff spent, disk full, corruption) aborts loudly.
  Real retry budget: each of the 4 attempts opens a connection with
  busy_timeout=5000, so each attempt can block up to 5s INSIDE SQLite before
  raising 'database is locked'. Total worst case is therefore ~22s
  (4 × 5s busy_timeout + the inter-attempt backoff sum 0.2 + 0.6 + 1.5 = 2.3s),
  NOT the 2.3s backoff sum alone — the busy_timeout dominates the budget.
- Storage: signals.db (same DB as tune_results; precedent
  auto_tune.save_tune_result). The sweep scripts do not call db.schema.init_db,
  so this module ensures its own table + WAL idempotently on first use.
- source + study_type columns let Part 2 compute N_effective by filtering:
  only 'exploratory' trials inflate selection bias; 'confirmatory'
  pre-registered studies (epic C) are recordable later WITHOUT schema change.

N-counting contract for #278 Part 2: compute N over DISTINCT
(source, combo_json, window_label) tuples, NOT raw COUNT(*). Some sweeps
legitimately re-run an identical configuration: e.g. regime_allocation_sweep's
sensitivity pass re-runs the vol_target=0.30 primary cell (0.30 is in both the
primary pass and SENSITIVITY_VOL_TARGETS), producing identical-config duplicate
rows. Those duplicates are the SAME selection candidate competing once, so a raw
COUNT(*) would over-inflate N (over-deflating the best Sharpe). De-duplicate on
the identity tuple before counting.

Scope (operator-confirmed 2026-05-29): wired into the 4 exploratory selection
sweeps — auto_tune, grid_search_tf, optimize_new_tokens, regime_allocation_sweep.
The pre-registered signal_calibration_* sweeps are confirmatory and excluded.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import time
from datetime import datetime, timezone

from db.schema import _set_wal_mode_idempotent_with_retry
from db.transaction import transaction

log = logging.getLogger("db.trials")

# Inter-attempt backoff (seconds). NOTE: these 2.3s are NOT the retry budget —
# each attempt also blocks up to busy_timeout=5000ms inside SQLite, so the real
# worst-case budget across the 4 attempts is ~22s (4 × 5s + 0.2 + 0.6 + 1.5).
_WRITE_BACKOFFS = (0.2, 0.6, 1.5)
_schema_ensured = False


def _ensure_trials_schema() -> None:
    """Idempotent: set WAL once + CREATE TABLE IF NOT EXISTS. Runs once/process."""
    global _schema_ensured
    if _schema_ensured:
        return
    _set_wal_mode_idempotent_with_retry()
    with transaction() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claimed_ts TEXT NOT NULL,
                finalized_ts TEXT,
                source TEXT NOT NULL,
                study_type TEXT NOT NULL DEFAULT 'exploratory',
                symbol TEXT,
                combo_json TEXT NOT NULL,
                window_label TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                sharpe REAL,
                metrics_json TEXT,
                error TEXT
            )
            """
        )
    _schema_ensured = True


def _with_write_retry(label: str, fn):
    """Run fn() (a write through transaction()) with bounded backoff on
    'database is locked'. Mirrors db.schema._set_wal_mode_idempotent_with_retry.
    A transient lock is retried; a non-lock error propagates immediately; only
    backoff exhaustion raises (abort loudly — durability failure only)."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt, delay in enumerate((0.0, *_WRITE_BACKOFFS)):
        if delay > 0:
            time.sleep(delay)
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "database is locked" in msg or "database table is locked" in msg:
                last_exc = exc
                log.warning(
                    "trials.%s locked on attempt %d/%d; retrying",
                    label, attempt + 1, len(_WRITE_BACKOFFS) + 1,
                )
                continue
            raise
    assert last_exc is not None
    raise last_exc


def claim_trial(
    *,
    source: str,
    combo: dict,
    symbol: str | None = None,
    window_label: str | None = None,
    study_type: str = "exploratory",
) -> int:
    """Record a trial as 'pending' BEFORE running it. Returns the trial id.

    Call immediately before invoking the simulator. If the process dies before
    finalize_trial(), the 'pending' row remains as evidence the trial existed
    (preserves N). Raises sqlite3.OperationalError only on DB durability failure
    after retries are exhausted (abort loudly)."""
    _ensure_trials_schema()
    now = datetime.now(timezone.utc).isoformat()
    combo_json = json.dumps(combo, default=str, sort_keys=True)

    def _do() -> int:
        with transaction() as con:
            cur = con.execute(
                "INSERT INTO trials "
                "(claimed_ts, source, study_type, symbol, combo_json, window_label, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (now, source, study_type, symbol, combo_json, window_label),
            )
            return int(cur.lastrowid)

    return _with_write_retry("claim_trial", _do)


def finalize_trial(
    trial_id: int,
    *,
    status: str,
    metrics: dict | None = None,
    error: str | None = None,
) -> None:
    """Mark a claimed trial 'ok' or 'failed'. Extracts sharpe + full metrics
    JSON for convenience. Raises on DB durability failure (abort loudly)."""
    if status not in ("ok", "failed"):
        raise ValueError(f"finalize_trial status must be 'ok' or 'failed', got {status!r}")
    now = datetime.now(timezone.utc).isoformat()
    sharpe = None
    metrics_json = None
    if metrics is not None:
        metrics_json = json.dumps(metrics, default=str)
        raw = metrics.get("sharpe_ratio", metrics.get("sharpe"))
        if isinstance(raw, (int, float)):
            sharpe = float(raw)

    def _do() -> None:
        with transaction() as con:
            con.execute(
                "UPDATE trials SET finalized_ts=?, status=?, sharpe=?, "
                "metrics_json=?, error=? WHERE id=?",
                (now, status, sharpe, metrics_json, error, trial_id),
            )

    _with_write_retry("finalize_trial", _do)


# Registry inception (Part 1 merged) — anchor for "N registered since A.0.3".
A03_DECAY_DATE = datetime(2026, 11, 29, tzinfo=timezone.utc)
A03_N_FLOOR = 50


def selection_population_stats(*, study_type: str = "exploratory") -> dict:
    """Aggregate the selection-trial population for deflation.

    Over all trials of the given study_type with a non-NULL sharpe, deduplicated
    by DISTINCT (source, combo_json, window_label) — identical configs re-run by
    a sensitivity pass count once (see registering-a-trial.md). Returns
    {"n_registered": int, "sigma_sr_trials": float | None}. sigma is the
    population stdev of the per-distinct-config annualized Sharpes (None if < 2)."""
    _ensure_trials_schema()
    with transaction() as con:
        rows = con.execute(
            "SELECT AVG(sharpe) AS s FROM trials "
            "WHERE study_type = ? AND sharpe IS NOT NULL "
            "GROUP BY source, combo_json, window_label",
            (study_type,),
        ).fetchall()
    sharpes = [float(r["s"]) for r in rows if r["s"] is not None]
    n = len(sharpes)
    sigma = statistics.pstdev(sharpes) if n >= 2 else None
    return {"n_registered": n, "sigma_sr_trials": sigma}


def n_effective(
    n_registered: int, *, today: datetime, decay_date: datetime = A03_DECAY_DATE,
    floor: int = A03_N_FLOOR,
) -> int:
    """N_effective = max(n_registered, floor) until decay_date, then n_registered.

    The floor avoids deflating against an artificially small N during the
    registry's bootstrap. It decays to 0 on/after decay_date (#278 spec)."""
    active_floor = floor if today < decay_date else 0
    return max(n_registered, active_floor)
