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

from data.holdout_access import HoldoutAccessError
from db.schema import _set_wal_mode_idempotent_with_retry
from db.transaction import transaction
from db.trials import (
    _ensure_trials_schema,
    claim_trial,
    n_effective,
    selection_population_stats,
)
from deflation import deflated_sharpe_ratio

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
        con.execute(
            """
            CREATE TRIGGER IF NOT EXISTS hypotheses_frozen_after_lock
            BEFORE UPDATE ON hypotheses
            FOR EACH ROW
            WHEN OLD.status IN ('locked', 'fired', 'refuted', 'not_refuted')
              AND (
                NEW.strategy_config_json IS NOT OLD.strategy_config_json
                OR NEW.config_hash         IS NOT OLD.config_hash
                OR NEW.symbols_json        IS NOT OLD.symbols_json
                OR NEW.window_label        IS NOT OLD.window_label
                OR NEW.metric              IS NOT OLD.metric
                OR NEW.threshold           IS NOT OLD.threshold
                OR NEW.direction           IS NOT OLD.direction
                OR NEW.deflated_metric     IS NOT OLD.deflated_metric
                OR NEW.deflated_threshold  IS NOT OLD.deflated_threshold
                OR NEW.n_at_lock           IS NOT OLD.n_at_lock
                OR NEW.cand_sharpe         IS NOT OLD.cand_sharpe
                OR NEW.cand_n_returns      IS NOT OLD.cand_n_returns
                OR NEW.cand_skew           IS NOT OLD.cand_skew
                OR NEW.cand_kurt_raw       IS NOT OLD.cand_kurt_raw
                OR NEW.preholdout_trial_ids_json IS NOT OLD.preholdout_trial_ids_json
                OR NEW.walkforward_ref     IS NOT OLD.walkforward_ref
                OR NEW.drift_check_ref     IS NOT OLD.drift_check_ref
                OR NEW.seal                IS NOT OLD.seal
              )
            BEGIN
                -- RAISE(ABORT, ...) surfaces as sqlite3.IntegrityError in Python
                -- (SQLITE_CONSTRAINT_TRIGGER), NOT OperationalError. Do not "fix" the
                -- exception type in tests back to OperationalError.
                SELECT RAISE(ABORT, 'hypothesis frozen fields are immutable after lock');
            END
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


class HoldoutFalsificationError(HoldoutAccessError):
    """The falsification gate refused: not lockable/authorized/in-budget, or sealed-tamper."""


def _fetch(con, hid: int, *, exc: type[Exception] = HypothesisLockError) -> dict:
    """Fetch a hypothesis row or raise. Callers pass their own `exc` so the
    missing-id error matches their boundary (lock vs authorize vs gate)."""
    row = con.execute("SELECT * FROM hypotheses WHERE id=?", (hid,)).fetchone()
    if row is None:
        raise exc(f"hypothesis {hid} does not exist")
    return dict(row)


def _compute_seal(row: dict) -> str:
    # ordering is fixed by _FROZEN_FIELDS; values are scalars (no dict keys to sort)
    payload = json.dumps([row.get(f) for f in _FROZEN_FIELDS], default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _has_provenance(config_hash: str) -> bool:
    """4a: the config_hash must match >=1 exploratory ok trial. Recomputes the
    hash from each ok trial's combo_json the same way claim_hypothesis did.

    Opens its OWN transaction() (BEGIN IMMEDIATE) and returns before
    lock_hypothesis opens its write transaction — they never nest (nesting two
    BEGIN IMMEDIATE connections in one call would deadlock on busy_timeout)."""
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


def _deflation_probability(row: dict, *, today: datetime) -> tuple[float | None, int]:
    """Candidate's deflated-Sharpe probability over the FULL registry N.
    Returns (probability_or_None, n_at_lock). Opens its own read of the registry
    (selection_population_stats) and MUST run outside any write transaction."""
    stats = selection_population_stats(study_type="exploratory")
    n_at_lock = n_effective(stats["n_registered"], today=today)
    sigma = stats["sigma_sr_trials"] or 0.0
    dsr = deflated_sharpe_ratio(
        sr=float(row["cand_sharpe"]),
        n_trials=n_at_lock,
        sigma_sr_trials=sigma,
        n_returns=int(row["cand_n_returns"]),
        skew=float(row["cand_skew"]),
        kurt_raw=float(row["cand_kurt_raw"]),
    )
    return dsr, n_at_lock


def _attested_pass(raw: str | None, label: str) -> None:
    """Refuse unless raw is JSON object {verdict:'pass', ...}. label names the criterion."""
    if not raw:
        raise HypothesisLockError(f"{label}: evidence ref is missing")
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        raise HypothesisLockError(f"{label}: evidence ref is not valid JSON")
    if not isinstance(obj, dict):
        raise HypothesisLockError(f"{label}: evidence ref is not a JSON object")
    if obj.get("verdict") != "pass":
        raise HypothesisLockError(f"{label}: verdict is {obj.get('verdict')!r}, not 'pass'")


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

    # 4b: deflation gate — single call yields BOTH n_at_lock AND DSR
    # (selection_population_stats opens its own transaction; must stay outside
    # the write transaction below to avoid nested BEGIN IMMEDIATE deadlock)
    dsr, n_at_lock = _deflation_probability(row, today=today)
    if dsr is None:
        raise HypothesisLockError(
            f"deflation gate: DSR undefined for this candidate "
            f"(cand_n_returns={row['cand_n_returns']}, cand_skew={row['cand_skew']}, "
            f"cand_kurt_raw={row['cand_kurt_raw']}) — degenerate inputs, refused")
    if dsr < float(row["deflated_threshold"]):
        raise HypothesisLockError(
            f"deflation gate: deflated probability {dsr:.4f} < threshold "
            f"{row['deflated_threshold']} over N={n_at_lock} — "
            "candidate does not survive the best-of-N selection penalty")

    # 4c / 4d: attested lower-tier evidence
    _attested_pass(row.get("walkforward_ref"), "walk-forward")
    _attested_pass(row.get("drift_check_ref"), "drift check")

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


# ---------------------------------------------------------------------------
# Task 6: authorize_fire — cooldown gate + deliberate act
# ---------------------------------------------------------------------------

class FireAuthorizationError(RuntimeError):
    """authorize_fire refused — not locked, or cooldown not elapsed."""


def authorize_fire(hid: int, *, now: datetime) -> None:
    """The SEPARATE deliberate act: lock decides WHAT to falsify, this decides
    that the moment is NOW. Refuses unless locked and the cooldown elapsed.
    Logs the authorization (no silent fire)."""
    _ensure_schema()

    def _do() -> None:
        with transaction() as con:
            row = _fetch(con, hid, exc=FireAuthorizationError)
            if row["status"] != "locked":
                raise FireAuthorizationError(
                    f"hypothesis {hid} is '{row['status']}', not 'locked' — "
                    "cannot authorize a fire")
            locked_at = datetime.fromisoformat(row["locked_ts"])
            if now - locked_at < HOLDOUT_FIRE_COOLDOWN:
                raise FireAuthorizationError(
                    f"cooldown not elapsed: {now - locked_at} < {HOLDOUT_FIRE_COOLDOWN} "
                    "— deliberation window between lock and fire is mandatory")
            con.execute("UPDATE hypotheses SET fire_authorized_ts=? WHERE id=?",
                        (now.isoformat(), hid))

    _with_write_retry("authorize_fire", _do)
    log.warning("HOLDOUT FIRE AUTHORIZED for hypothesis %d at %s — the bala unica "
                "is now spendable for this hypothesis. Log via `mex log`.", hid, now.isoformat())


# ---------------------------------------------------------------------------
# Task 7: assert_fireable (gate chain) + record_fire (fire-before-read)
# ---------------------------------------------------------------------------

def _fired_count(con, window_label: str) -> int:
    return con.execute(
        "SELECT COUNT(*) c FROM hypotheses WHERE window_label=? AND fired_ts IS NOT NULL",
        (window_label,),
    ).fetchone()["c"]


def assert_fireable(hid: int) -> None:
    """The all-or-nothing gate chain. Raises HoldoutFalsificationError on any
    failure; the holdout is untouched on any failure."""
    _ensure_schema()
    with transaction() as con:
        row = _fetch(con, hid, exc=HoldoutFalsificationError)
        # 2: status + no outcome
        if row["status"] == "draft":
            raise HoldoutFalsificationError(f"hypothesis {hid} is draft — lock it first")
        if row["status"] in ("refuted", "not_refuted"):
            raise HoldoutFalsificationError(
                f"hypothesis {hid} already resolved ({row['status']}) — read window closed")
        # 3: deliberate authorization
        if not row.get("fire_authorized_ts"):
            raise HoldoutFalsificationError(
                f"hypothesis {hid} not authorized — call authorize_fire (the shot is a "
                "deliberate act, not a function side-effect)")
        # 4: seal intact
        if _compute_seal(row) != row["seal"]:
            raise HoldoutFalsificationError(
                f"hypothesis {hid} seal mismatch — frozen fields were tampered")
        # 5: budget (a re-read of an already-fired hypothesis is allowed)
        if row["fired_ts"] is None and _fired_count(con, row["window_label"]) >= HOLDOUT_FIRE_BUDGET:
            raise HoldoutFalsificationError(
                f"fire budget {HOLDOUT_FIRE_BUDGET} exhausted for window "
                f"{row['window_label']!r} — override only via `mex log`")


def record_fire(hid: int) -> None:
    """Mark the fire BEFORE the holdout is read (claim-then-execute / Caveat 5).
    Idempotent within the crash-recovery window. On the FIRST fire, writes a
    confirmatory trial so deflation's N sees it."""
    _ensure_schema()

    def _do() -> None:
        with transaction() as con:
            row = _fetch(con, hid, exc=HoldoutFalsificationError)
            if row["fired_ts"] is not None:
                return  # already fired — re-read, do not double count
            now = _now()
            con.execute(
                "UPDATE hypotheses SET status='fired', fired_ts=? WHERE id=?",
                (now, hid),
            )

    _with_write_retry("record_fire", _do)
    # confirmatory trial only if this call actually set fired_ts (first fire)
    with transaction() as con:
        row = _fetch(con, hid, exc=HoldoutFalsificationError)
    if row["fired_ts"]:
        with transaction() as con:
            already = con.execute(
                "SELECT COUNT(*) c FROM trials WHERE study_type='confirmatory' "
                "AND combo_json = ?",
                (row["strategy_config_json"],),
            ).fetchone()["c"]
        if not already:
            claim_trial(
                source="holdout_falsification",
                combo=json.loads(row["strategy_config_json"]),
                window_label=row["window_label"],
                study_type="confirmatory",
            )
