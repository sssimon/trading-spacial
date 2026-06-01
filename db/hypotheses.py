"""Frozen-hypothesis ledger + falsification gate (Capa 2, holdout gate).

A hypothesis is a frozen confirmatory study: the punctual claim a single holdout
shot will try to kill. Falsification is asymmetric — a shot can REFUTE, never
CONFIRM. Lifecycle: draft -> locked -> fired -> refuted/not_refuted.

Design (spec 2026-06-01-holdout-falsification-gate-design.md, Approach A):
- lock_hypothesis enforces FIVE criteria (provenance, deflation, walk-forward,
  drift, complete claim), captures N, and SEALS the frozen fields.
- authorize_fire is a SEPARATE deliberate act gated by HOLDOUT_FIRE_COOLDOWN —
  the gate against the impatient owner with the legitimate key.
- record_fire marks the fire BEFORE the holdout is read (claim-then-execute;
  a partial peek burns the bala unica just as surely as a full run, Caveat 5).
- Writes go through db.transaction.transaction() with bounded retry/backoff,
  mirroring db/trials.py. Storage: signals.db.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from db.schema import _set_wal_mode_idempotent_with_retry
from db.transaction import transaction

log = logging.getLogger("db.hypotheses")

_WRITE_BACKOFFS = (0.2, 0.6, 1.5)
_schema_ensured = False

# Config (spec Section 2). Conception 1 = one shot, deliberately fired.
HOLDOUT_FIRE_BUDGET = 1
HOLDOUT_FIRE_COOLDOWN = timedelta(hours=24)

# Fields frozen at lock; the seal covers exactly these (order matters).
_FROZEN_FIELDS = (
    "strategy_config_json", "config_hash", "symbols_json", "window_label",
    "metric", "threshold", "direction",
    "deflated_metric", "deflated_threshold", "n_at_lock",
    "cand_sharpe", "cand_n_returns", "cand_skew", "cand_kurt_raw",
    "preholdout_trial_ids_json", "walkforward_ref", "drift_check_ref",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_write_retry(label: str, fn):
    """Bounded backoff on 'database is locked'; non-lock errors propagate
    immediately; only backoff exhaustion raises. Mirrors db/trials.py."""
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
                log.warning("hypotheses.%s locked on attempt %d/%d; retrying",
                            label, attempt + 1, len(_WRITE_BACKOFFS) + 1)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def _ensure_schema() -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    _set_wal_mode_idempotent_with_retry()
    with transaction() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS hypotheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ts TEXT NOT NULL,
                locked_ts TEXT,
                fire_authorized_ts TEXT,
                fired_ts TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                strategy_config_json TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                symbols_json TEXT,
                window_label TEXT,
                metric TEXT,
                threshold REAL,
                direction TEXT,
                deflated_metric TEXT,
                deflated_threshold REAL,
                n_at_lock INTEGER,
                cand_sharpe REAL,
                cand_n_returns INTEGER,
                cand_skew REAL,
                cand_kurt_raw REAL,
                preholdout_trial_ids_json TEXT,
                walkforward_ref TEXT,
                drift_check_ref TEXT,
                realized_metric REAL,
                verdict TEXT,
                seal TEXT,
                source_note TEXT
            )
            """
        )
    _schema_ensured = True


def claim_hypothesis(
    *,
    strategy_config: dict,
    symbols: list[str],
    window_label: str,
    metric: str,
    threshold: float,
    direction: str,
    deflated_metric: str,
    deflated_threshold: float,
    cand_sharpe: float,
    cand_n_returns: int,
    cand_skew: float,
    cand_kurt_raw: float,
    source_note: str = "",
) -> int:
    """Create a DRAFT hypothesis. Mutable until lock. Returns the id."""
    _ensure_schema()
    cfg_json = json.dumps(strategy_config, default=str, sort_keys=True)
    config_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    now = _now()

    def _do() -> int:
        with transaction() as con:
            cur = con.execute(
                "INSERT INTO hypotheses "
                "(created_ts, status, strategy_config_json, config_hash, symbols_json, "
                " window_label, metric, threshold, direction, deflated_metric, "
                " deflated_threshold, cand_sharpe, cand_n_returns, cand_skew, "
                " cand_kurt_raw, source_note) "
                "VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, cfg_json, config_hash, json.dumps(symbols), window_label,
                 metric, threshold, direction, deflated_metric, deflated_threshold,
                 cand_sharpe, cand_n_returns, cand_skew, cand_kurt_raw, source_note),
            )
            return int(cur.lastrowid)

    return _with_write_retry("claim_hypothesis", _do)


# ---------------------------------------------------------------------------
# Task 2: lock_hypothesis — provenance (4a) + complete-claim (4e) + seal
# ---------------------------------------------------------------------------

class HypothesisLockError(RuntimeError):
    """A lock criterion (provenance / deflation / walk-forward / drift / claim) failed."""


def _fetch(con, hid: int) -> dict:
    row = con.execute("SELECT * FROM hypotheses WHERE id=?", (hid,)).fetchone()
    if row is None:
        raise HypothesisLockError(f"hypothesis {hid} does not exist")
    return dict(row)


def _compute_seal(row: dict) -> str:
    payload = json.dumps([row.get(f) for f in _FROZEN_FIELDS],
                         default=str, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _has_provenance(config_hash: str) -> bool:
    """4a: the config_hash must match >=1 exploratory ok trial. We recompute the
    hash from each ok trial's combo_json the same way claim_hypothesis did.
    Opens its own read connection so it can be called outside a write transaction."""
    from db.trials import _ensure_trials_schema
    _ensure_trials_schema()
    with transaction() as con:
        rows = con.execute(
            "SELECT combo_json FROM trials WHERE study_type='exploratory' AND status='ok'"
        ).fetchall()
    for r in rows:
        h = hashlib.sha256(r["combo_json"].encode("utf-8")).hexdigest()
        if h == config_hash:
            return True
    return False


from db.trials import selection_population_stats, n_effective


def lock_hypothesis(hid: int, *, today: datetime) -> None:
    """Freeze a draft after enforcing lock criteria. Refuses if not in 'draft'.

    All cross-table reads (provenance, population stats) are done BEFORE the
    hypotheses write transaction opens, avoiding nested BEGIN IMMEDIATE deadlocks.
    """
    _ensure_schema()

    # --- Pre-read phase (outside the write transaction) ---
    # Read the draft row first to run checks before acquiring the writer lock.
    with transaction() as con:
        row = _fetch(con, hid)

    if row["status"] != "draft":
        raise HypothesisLockError(
            f"hypothesis {hid} is '{row['status']}', not 'draft' — cannot re-lock")

    # 4e: complete claim
    required = ("metric", "threshold", "direction",
                "deflated_metric", "deflated_threshold")
    missing = [f for f in required if row.get(f) is None]
    if missing:
        raise HypothesisLockError(f"incomplete claim — missing {missing}")

    # 4a: provenance (opens its own transaction internally)
    if not _has_provenance(row["config_hash"]):
        raise HypothesisLockError(
            "provenance: config_hash matches no exploratory ok trial — "
            "the candidate did not emerge from registered search")

    # (4b deflation THRESHOLD, 4c walk-forward, 4d drift land in Tasks 3-4)
    n_at_lock = n_effective(
        selection_population_stats()["n_registered"], today=today)

    # --- Write phase ---
    def _do() -> None:
        with transaction() as con:
            # Re-fetch under the write lock to guard against concurrent mutations.
            current = _fetch(con, hid)
            if current["status"] != "draft":
                raise HypothesisLockError(
                    f"hypothesis {hid} status changed to '{current['status']}' "
                    "between pre-check and write — concurrent lock attempt?")

            locked_ts = _now()
            sealed = dict(current)
            sealed["locked_ts"] = locked_ts
            sealed["n_at_lock"] = n_at_lock
            seal = _compute_seal(sealed)
            con.execute(
                "UPDATE hypotheses SET status='locked', locked_ts=?, seal=?, "
                "n_at_lock=? WHERE id=?",
                (locked_ts, seal, n_at_lock, hid),
            )

    _with_write_retry("lock_hypothesis", _do)
