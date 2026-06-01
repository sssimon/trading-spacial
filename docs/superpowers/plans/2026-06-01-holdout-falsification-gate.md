# Holdout Falsification Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a frozen-hypothesis ledger (`db/hypotheses.py`) and a falsification gate (`open_holdout_for_falsification`) that makes access to the locked holdout impossible without a pre-registered, deflation-passing, deliberately-fired hypothesis — turning the #322 prose blockage into machine-verifiable state.

**Architecture:** A new `hypotheses` table in `signals.db` with lifecycle `draft → locked → fired → refuted/not_refuted`. `lock_hypothesis` enforces five criteria (provenance, deflation, walk-forward, drift, complete claim) and seals the row; `authorize_fire` is a separate deliberate act gated by a cooldown; `open_holdout_for_falsification` chains `assert_fireable → record_fire → open_holdout`, marking the fire BEFORE the read (claim-then-execute, honoring Caveat 5). Reuses `db/trials.py` (registry/N) and `deflation.py` untouched.

**Tech Stack:** Python 3, SQLite via `db/connection.py` + `db/transaction.py`, `deflation.py` (pure stats), pytest.

**Spec:** `docs/superpowers/specs/2026-06-01-holdout-falsification-gate-design.md` (Approach A, amended post-Voronov).

---

## Scope

This plan builds the gate MACHINERY only. It does **not** migrate the existing A.4-3 harness (`walk_forward.evaluate_winner_on_holdout`, gated today by `_HOLDOUT_322_CLOSED`) onto the new function — that is a separate `#322`-closure-adjacent change with its own review, deferred to a consuming PR (spec Section 6). This plan leaves `walk_forward._HOLDOUT_322_CLOSED` and its two tests (`test_a43_gate_*`) untouched and green.

## Verification points (the implementer MUST confirm BEFORE writing code)

1. **DB test fixture.** `tests/test_trials_registry.py` points the DB layer at a tmp file via `monkeypatch.setattr("btc_api.DB_FILE", str(db_path))` and resets the module's `_schema_ensured` flag. Confirm `btc_api.DB_FILE` is still the connection source (`db/connection.py::_open_configured_connection`) and reuse this exact fixture shape.
2. **Candidate deflation inputs.** This plan stores `cand_sharpe / cand_n_returns / cand_skew / cand_kurt_raw` ON the hypothesis row, filled by whoever assembles the draft from the winning exploratory trial. Confirm where those values live in the candidate trial: read `calculate_metrics` in `backtest.py` and check whether `metrics_json` persists skew / kurtosis / sample-size keys. If it does, the draft assembler reads them from `trials.metrics_json`; if not, the assembler computes them from the trade `pnl_pct` series. Either way the gate only reads the four columns — it does NOT depend on the metrics_json schema. (This is the same decoupling rationale the trial-registry plan used for its verification points.)
3. **`deflation.py` signatures** (confirmed at plan time, re-confirm): `deflated_sharpe_ratio(sr, n_trials, sigma_sr_trials, n_returns, skew, kurt_raw) -> float | None` returns a PROBABILITY in `[0,1]`; `selection_population_stats(study_type='exploratory') -> {"n_registered": int, "sigma_sr_trials": float | None}`; `n_effective(n_registered, *, today, decay_date=, floor=) -> int`. All in `deflation.py` / `db/trials.py`.

## File Structure

- **Create:** `db/hypotheses.py` — the frozen-hypothesis ledger. One responsibility: claim/lock/authorize/fire/outcome of hypothesis rows in `signals.db`, with the deflation gate and seal. ~220-260 lines.
- **Create:** `tests/test_hypotheses_gate.py` — unit tests for the ledger + gate chain.
- **Modify:** `data/holdout_access.py` — add `HoldoutFalsificationError` + `open_holdout_for_falsification`.
- **Modify:** `tests/test_holdout_isolation.py` — extend the whitelist sanity test to assert the new entry point exists; no new module to whitelist (`db/hypotheses.py` touches no holdout path).
- **Create:** `.mex/patterns/firing-the-holdout.md` + add to `.mex/patterns/INDEX.md` — the GROW step.

Naming follows repo convention (`db/trials.py`, `data/holdout_access.py`). `db/hypotheses.py` mirrors `db/trials.py`'s structure (own `_with_write_retry`, `_ensure_*_schema`, `transaction()` writes).

---

## Task 1: `hypotheses` table schema + `claim_hypothesis` (draft INSERT)

**Files:**
- Create: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hypotheses_gate.py`:

```python
import json
import sqlite3

import pytest

from db.transaction import transaction


@pytest.fixture
def hyp_db(tmp_path, monkeypatch):
    """Throwaway signals.db; reset both modules' once-per-process schema flags."""
    db_path = tmp_path / "signals_test.db"
    monkeypatch.setattr("btc_api.DB_FILE", str(db_path))
    import db.hypotheses
    import db.trials
    monkeypatch.setattr(db.hypotheses, "_schema_ensured", False)
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    return db_path


def _all_hyps():
    with transaction() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM hypotheses ORDER BY id"
        ).fetchall()]


def test_claim_hypothesis_inserts_draft_row(hyp_db):
    from db.hypotheses import claim_hypothesis

    hid = claim_hypothesis(
        strategy_config={"atr_sl_mult": 1.0},
        symbols=["BTCUSDT"],
        window_label="2025-04-30..2026-04-30",
        metric="net_pnl",
        threshold=0.0,
        direction=">",
        deflated_metric="sharpe_deflated",
        deflated_threshold=0.95,
        cand_sharpe=1.4,
        cand_n_returns=120,
        cand_skew=0.1,
        cand_kurt_raw=3.5,
        source_note="from grid_search_tf sweep 2026-05",
    )
    rows = _all_hyps()
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == hid
    assert r["status"] == "draft"
    assert r["locked_ts"] is None
    assert r["fired_ts"] is None
    assert r["config_hash"]                      # computed at draft
    assert json.loads(r["strategy_config_json"])["atr_sl_mult"] == 1.0
    assert json.loads(r["symbols_json"]) == ["BTCUSDT"]
    assert r["deflated_threshold"] == pytest.approx(0.95)
    assert r["created_ts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hypotheses_gate.py::test_claim_hypothesis_inserts_draft_row -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.hypotheses'`

- [ ] **Step 3: Write minimal implementation**

Create `db/hypotheses.py`:

```python
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
            if "is locked" in str(exc).lower():
                last_exc = exc
                log.warning("hypotheses.%s locked on attempt %d; retrying", label, attempt + 1)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hypotheses_gate.py::test_claim_hypothesis_inserts_draft_row -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): table schema + claim_hypothesis draft (holdout gate)"
```

---

## Task 2: `lock_hypothesis` — provenance (4a) + complete-claim (4e) + seal

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
from db.trials import claim_trial, finalize_trial


def _draft(**overrides):
    """A complete, lockable draft. Override single fields per test."""
    from db.hypotheses import claim_hypothesis
    kw = dict(
        strategy_config={"atr_sl_mult": 1.0}, symbols=["BTCUSDT"],
        window_label="2025-04-30..2026-04-30", metric="net_pnl",
        threshold=0.0, direction=">", deflated_metric="sharpe_deflated",
        deflated_threshold=0.95, cand_sharpe=3.0, cand_n_returns=200,
        cand_skew=0.0, cand_kurt_raw=3.0, source_note="t",
    )
    kw.update(overrides)
    return claim_hypothesis(**kw)


def _register_matching_ok_trial(strategy_config):
    """Register an exploratory ok trial whose combo == strategy_config so the
    candidate's config_hash has provenance, plus enough sibling trials that N
    is meaningful."""
    tid = claim_trial(source="grid_search_tf", combo=strategy_config,
                      symbol="BTCUSDT", window_label="w")
    finalize_trial(tid, status="ok", metrics={"sharpe_ratio": 3.0})
    # siblings so sigma_sr_trials is defined
    for s in (0.5, 1.0, 1.5, 2.0):
        t = claim_trial(source="grid_search_tf", combo={"atr_sl_mult": s},
                        symbol="BTCUSDT", window_label="w")
        finalize_trial(t, status="ok", metrics={"sharpe_ratio": s})


def test_lock_refuses_without_provenance(hyp_db):
    """4a: config_hash must match an ok exploratory trial."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _draft(strategy_config={"atr_sl_mult": 9.9})  # no matching trial
    with pytest.raises(HypothesisLockError, match="provenance"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_incomplete_claim(hyp_db):
    """4e: deflated_threshold missing -> refuse. Build a draft then NULL it."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET deflated_threshold=NULL WHERE id=?", (hid,))
    with pytest.raises(HypothesisLockError, match="claim"):
        lock_hypothesis(hid, today=_T())


def test_lock_succeeds_and_seals(hyp_db):
    from db.hypotheses import lock_hypothesis
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg)
    lock_hypothesis(hid, today=_T())
    r = _all_hyps()[0]
    assert r["status"] == "locked"
    assert r["locked_ts"]
    assert r["seal"]                         # sealed
    assert r["n_at_lock"] is not None        # N captured
```

Add this helper near the top of the test file (after imports):

```python
from datetime import datetime, timezone


def _T():
    """Fixed 'today' before the deflation decay date — keeps the floor active."""
    return datetime(2026, 6, 1, tzinfo=timezone.utc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k lock -v`
Expected: FAIL with `ImportError: cannot import name 'lock_hypothesis'`

- [ ] **Step 3: Write minimal implementation**

Append to `db/hypotheses.py`:

```python
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


def _has_provenance(con, config_hash: str) -> bool:
    """4a: the config_hash must match >=1 exploratory ok trial. We recompute the
    hash from each ok trial's combo_json the same way claim_hypothesis did."""
    rows = con.execute(
        "SELECT combo_json FROM trials WHERE study_type='exploratory' AND status='ok'"
    ).fetchall()
    for r in rows:
        h = hashlib.sha256(r["combo_json"].encode("utf-8")).hexdigest()
        if h == config_hash:
            return True
    return False
```

> **Provenance hashing note:** `claim_hypothesis` hashes `json.dumps(strategy_config, sort_keys=True)`. `claim_trial` stores `combo_json = json.dumps(combo, default=str, sort_keys=True)`. For the hashes to match, the candidate's `strategy_config` passed to `claim_hypothesis` MUST be the same dict the winning trial registered as its `combo`. The draft assembler is responsible for this (verification point 2). The test `_register_matching_ok_trial` exercises the match.

Now add `lock_hypothesis` (provenance + complete-claim only for this task; deflation/walk-forward/drift land in Tasks 3-4):

```python
def lock_hypothesis(hid: int, *, today: datetime) -> None:
    """Freeze a draft after enforcing all lock criteria. Idempotent only on the
    transition; refuses if not in 'draft'."""
    _ensure_schema()

    def _do() -> None:
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

            # 4a: provenance
            if not _has_provenance(con, row["config_hash"]):
                raise HypothesisLockError(
                    "provenance: config_hash matches no exploratory ok trial — "
                    "the candidate did not emerge from registered search")

            # (4b deflation, 4c walk-forward, 4d drift land in Tasks 3-4)

            row["n_at_lock"] = row.get("n_at_lock")  # set in Task 3
            locked_ts = _now()
            sealed = dict(row)
            sealed["locked_ts"] = locked_ts
            seal = _compute_seal(sealed)
            con.execute(
                "UPDATE hypotheses SET status='locked', locked_ts=?, seal=? WHERE id=?",
                (locked_ts, seal, hid),
            )

    _with_write_retry("lock_hypothesis", _do)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k lock -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): lock provenance (4a) + complete-claim (4e) + seal"
```

---

## Task 3: `lock_hypothesis` — deflation gate (4b)

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
def test_lock_refuses_when_deflated_below_threshold(hyp_db):
    """4b: a weak candidate (low Sharpe vs best-of-N) fails the deflation gate."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    # cand_sharpe barely positive, large selection population -> low DSR
    hid = _draft(strategy_config=cfg, cand_sharpe=0.05, cand_n_returns=30,
                 deflated_threshold=0.95)
    with pytest.raises(HypothesisLockError, match="deflat"):
        lock_hypothesis(hid, today=_T())


def test_lock_captures_n_at_lock_on_success(hyp_db):
    from db.hypotheses import lock_hypothesis
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)            # 5 distinct ok trials registered
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)
    lock_hypothesis(hid, today=_T())
    r = _all_hyps()[0]
    assert r["status"] == "locked"
    assert r["n_at_lock"] >= 50               # floor active before decay date
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k deflat_or_n_at_lock -v`
(or `-k "deflated or n_at_lock"`)
Expected: FAIL — the weak candidate currently locks (no deflation gate yet).

- [ ] **Step 3: Write minimal implementation**

Add the deflation helper to `db/hypotheses.py`:

```python
from db.trials import selection_population_stats, n_effective
from deflation import deflated_sharpe_ratio


def _deflation_probability(row: dict, *, today: datetime) -> tuple[float | None, int]:
    """Compute the candidate's deflated-Sharpe probability over the FULL registry N.
    Returns (probability_or_None, n_at_lock)."""
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
```

Insert the 4b check into `lock_hypothesis`, replacing the placeholder lines
`# (4b deflation ...)` and `row["n_at_lock"] = row.get("n_at_lock")`:

```python
            # 4b: deflation gate — selection bias, in the machine
            dsr, n_at_lock = _deflation_probability(row, today=today)
            if dsr is None or dsr < float(row["deflated_threshold"]):
                raise HypothesisLockError(
                    f"deflation gate: deflated probability {dsr} < threshold "
                    f"{row['deflated_threshold']} over N={n_at_lock} — "
                    "candidate does not survive the best-of-N selection penalty")
            row["n_at_lock"] = n_at_lock
```

And persist `n_at_lock` in the UPDATE (extend the existing UPDATE in `lock_hypothesis`):

```python
            con.execute(
                "UPDATE hypotheses SET status='locked', locked_ts=?, seal=?, "
                "n_at_lock=? WHERE id=?",
                (locked_ts, seal, n_at_lock, hid),
            )
```

> Note: `sealed` must include the final `n_at_lock` before `_compute_seal` (it is in `_FROZEN_FIELDS`). Ensure `sealed["n_at_lock"] = n_at_lock` is set before computing the seal.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k "lock or deflated or n_at_lock" -v`
Expected: PASS (the earlier lock tests with `cand_sharpe=3.0`/`deflated_threshold=0.95` must still pass — if they now fail the deflation gate, raise their `cand_sharpe` or lower their `deflated_threshold` so a genuine winner locks; the intent of those tests is provenance/claim, not deflation).

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): deflation gate (4b) inside lock + capture n_at_lock"
```

---

## Task 4: `lock_hypothesis` — walk-forward (4c) + drift (4d) attested refs

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
def _attest(verdict="pass"):
    return json.dumps({"ref": "data/retune/x/report.md", "verdict": verdict,
                       "ts": "2026-05-30T00:00:00+00:00"})


def _lockable(**overrides):
    """A draft that passes provenance + deflation, with both attested refs set
    to 'pass'. Override to break one ref."""
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)
    wf = overrides.get("walkforward_ref", _attest())
    dr = overrides.get("drift_check_ref", _attest())
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=?, drift_check_ref=? WHERE id=?",
                    (wf, dr, hid))
    return hid


def test_lock_refuses_failed_walkforward(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _lockable(walkforward_ref=_attest(verdict="fail"))
    with pytest.raises(HypothesisLockError, match="walk.?forward"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_failed_drift(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _lockable(drift_check_ref=_attest(verdict="fail"))
    with pytest.raises(HypothesisLockError, match="drift"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_missing_refs(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)  # refs never set -> NULL
    with pytest.raises(HypothesisLockError, match="walk.?forward|drift"):
        lock_hypothesis(hid, today=_T())


def test_lock_succeeds_with_passing_refs(hyp_db):
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    assert _all_hyps()[0]["status"] == "locked"
```

> Update the Task 2/3 lockable-draft tests (`test_lock_succeeds_and_seals`, `test_lock_captures_n_at_lock_on_success`) to also set both attested refs to `pass` (use `_lockable()` or add the same UPDATE), since 4c/4d now run inside lock. Re-run them after this task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k "walkforward or drift or missing_refs or passing_refs" -v`
Expected: FAIL — refs are not yet checked, so failing/missing refs still lock.

- [ ] **Step 3: Write minimal implementation**

Add the attested-ref helper to `db/hypotheses.py`:

```python
def _attested_pass(raw: str | None, label: str) -> None:
    """Refuse unless raw is JSON {verdict:'pass', ...}. label names the criterion."""
    if not raw:
        raise HypothesisLockError(f"{label}: evidence ref is missing")
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        raise HypothesisLockError(f"{label}: evidence ref is not valid JSON")
    if obj.get("verdict") != "pass":
        raise HypothesisLockError(f"{label}: verdict is {obj.get('verdict')!r}, not 'pass'")
```

Insert the 4c/4d checks into `lock_hypothesis`, after the 4b deflation block and before computing the seal:

```python
            # 4c / 4d: attested lower-tier evidence
            _attested_pass(row.get("walkforward_ref"), "walk-forward")
            _attested_pass(row.get("drift_check_ref"), "drift check")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k lock -v`
Expected: PASS (all lock tests, including the updated Task 2/3 ones).

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): walk-forward (4c) + drift (4d) attested-ref lock criteria"
```

---

## Task 5: Immutability enforcement — mutation refusal + seal + SQLite trigger

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
def test_locked_row_frozen_field_update_is_aborted_by_trigger(hyp_db):
    """Direct UPDATE of a frozen field on a locked row must RAISE (DB trigger)."""
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    with pytest.raises(sqlite3.OperationalError):
        with transaction() as con:
            con.execute("UPDATE hypotheses SET threshold=999 WHERE id=?", (hid,))


def test_locked_row_lifecycle_field_update_is_allowed(hyp_db):
    """Lifecycle columns (fire_authorized_ts, fired_ts, status, verdict,
    realized_metric) must remain writable after lock — the trigger guards only
    frozen fields."""
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    with transaction() as con:                       # must not raise
        con.execute("UPDATE hypotheses SET fire_authorized_ts=? WHERE id=?",
                    (_now_str(), hid))
    assert _all_hyps()[0]["fire_authorized_ts"]


def test_seal_detects_tamper_via_recompute(hyp_db):
    """If a frozen field is changed by a path that bypasses the trigger, the
    recomputed seal no longer matches the stored seal."""
    from db.hypotheses import lock_hypothesis, _compute_seal
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    row = _all_hyps()[0]
    tampered = dict(row)
    tampered["threshold"] = 999
    assert _compute_seal(tampered) != row["seal"]
```

Add this helper near the test imports:

```python
def _now_str():
    from db.hypotheses import _now
    return _now()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k "trigger or lifecycle_field or tamper" -v`
Expected: FAIL — no trigger yet, so the frozen-field UPDATE succeeds instead of raising.

- [ ] **Step 3: Write minimal implementation**

In `db/hypotheses.py`, extend `_ensure_schema` to create the trigger right after the table (inside the same `with transaction() as con:` block):

```python
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
                SELECT RAISE(ABORT, 'hypothesis frozen fields are immutable after lock');
            END
            """
        )
```

> The trigger guards frozen columns only. Lifecycle columns (`fire_authorized_ts`, `fired_ts`, `status`, `realized_metric`, `verdict`) are absent from the `WHEN` clause, so `authorize_fire` / `record_fire` / `record_outcome` updates pass. The `lock_hypothesis` write itself transitions `OLD.status='draft'`, excluded from the `WHEN` guard. `_compute_seal` (Task 2) is already importable for the recompute test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k "trigger or lifecycle_field or tamper" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): immutability — frozen-field trigger + seal recompute"
```

---

## Task 6: `authorize_fire` — cooldown + mex-logged deliberate act

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
from datetime import timedelta


def test_authorize_fire_refuses_before_cooldown(hyp_db):
    from db.hypotheses import lock_hypothesis, authorize_fire, FireAuthorizationError
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    locked_at = datetime.fromisoformat(_all_hyps()[0]["locked_ts"])
    with pytest.raises(FireAuthorizationError, match="cooldown"):
        authorize_fire(hid, now=locked_at + timedelta(hours=1))


def test_authorize_fire_refuses_unlocked(hyp_db):
    from db.hypotheses import authorize_fire, FireAuthorizationError
    hid = _draft(strategy_config={"atr_sl_mult": 1.0})  # still draft
    with pytest.raises(FireAuthorizationError, match="locked"):
        authorize_fire(hid, now=_T())


def test_authorize_fire_sets_timestamp_after_cooldown(hyp_db):
    from db.hypotheses import lock_hypothesis, authorize_fire
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    locked_at = datetime.fromisoformat(_all_hyps()[0]["locked_ts"])
    authorize_fire(hid, now=locked_at + timedelta(hours=25))
    assert _all_hyps()[0]["fire_authorized_ts"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k authorize -v`
Expected: FAIL with `ImportError: cannot import name 'authorize_fire'`

- [ ] **Step 3: Write minimal implementation**

Append to `db/hypotheses.py`:

```python
class FireAuthorizationError(RuntimeError):
    """authorize_fire refused — not locked, or cooldown not elapsed."""


def authorize_fire(hid: int, *, now: datetime) -> None:
    """The SEPARATE deliberate act: lock decides WHAT to falsify, this decides
    that the moment is NOW. Refuses unless locked and the cooldown elapsed.
    Logs the authorization (no silent fire)."""
    _ensure_schema()

    def _do() -> None:
        with transaction() as con:
            row = _fetch(con, hid)
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
```

> `HypothesisFetchError` reuse: `_fetch` raises `HypothesisLockError` on a missing id; `authorize_fire` is fine surfacing that. The `mex log` step is operator discipline (documented in the runbook, Task 12), reinforced by the loud log line; it is not auto-invoked from library code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k authorize -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): authorize_fire — cooldown gate against the impatient owner"
```

---

## Task 7: `assert_fireable` + `record_fire` — the gate chain + fire-before-read + confirmatory trial

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
def _locked_and_authorized(hyp_id_overrides=None):
    """Helper: lock + authorize past cooldown. Returns hid."""
    from db.hypotheses import lock_hypothesis, authorize_fire
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    locked_at = datetime.fromisoformat(_all_hyps()[0]["locked_ts"])
    authorize_fire(hid, now=locked_at + timedelta(hours=25))
    return hid


def test_assert_fireable_refuses_unauthorized(hyp_db):
    """Locked + sealed + in budget, but NOT authorized -> refuse (impatient-owner gate)."""
    from db.hypotheses import lock_hypothesis, assert_fireable, HoldoutFalsificationError
    hid = _lockable()
    lock_hypothesis(hid, today=_T())            # no authorize_fire
    with pytest.raises(HoldoutFalsificationError, match="authorized"):
        assert_fireable(hid)


def test_assert_fireable_refuses_draft(hyp_db):
    from db.hypotheses import assert_fireable, HoldoutFalsificationError
    hid = _draft(strategy_config={"atr_sl_mult": 1.0})
    with pytest.raises(HoldoutFalsificationError, match="locked|lock it"):
        assert_fireable(hid)


def test_record_fire_sets_fired_ts_and_writes_confirmatory_trial(hyp_db):
    from db.hypotheses import assert_fireable, record_fire
    from db.trials import selection_population_stats
    hid = _locked_and_authorized()
    assert_fireable(hid)
    record_fire(hid)
    r = _all_hyps()[0]
    assert r["status"] == "fired"
    assert r["fired_ts"]
    # a confirmatory trial row now exists
    with transaction() as con:
        confirmatory = con.execute(
            "SELECT COUNT(*) c FROM trials WHERE study_type='confirmatory'"
        ).fetchone()["c"]
    assert confirmatory == 1


def test_record_fire_idempotent_within_window(hyp_db):
    from db.hypotheses import assert_fireable, record_fire
    hid = _locked_and_authorized()
    record_fire(hid)
    first = _all_hyps()[0]["fired_ts"]
    record_fire(hid)                              # re-read, same test
    assert _all_hyps()[0]["fired_ts"] == first    # unchanged
    with transaction() as con:
        c = con.execute("SELECT COUNT(*) c FROM trials WHERE study_type='confirmatory'").fetchone()["c"]
    assert c == 1                                 # not double-counted


def test_budget_blocks_second_distinct_fire(hyp_db):
    """With HOLDOUT_FIRE_BUDGET=1, a second DISTINCT hypothesis over the same
    window cannot fire."""
    from db.hypotheses import assert_fireable, record_fire, HoldoutFalsificationError
    hid1 = _locked_and_authorized()
    record_fire(hid1)
    # second hypothesis, same window, distinct config
    cfg2 = {"atr_sl_mult": 2.0}
    _register_matching_ok_trial(cfg2)
    from db.hypotheses import claim_hypothesis, lock_hypothesis, authorize_fire
    hid2 = _draft(strategy_config=cfg2, cand_sharpe=4.0, cand_n_returns=500,
                  deflated_threshold=0.50)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=?, drift_check_ref=? WHERE id=?",
                    (_attest(), _attest(), hid2))
    lock_hypothesis(hid2, today=_T())
    locked_at = datetime.fromisoformat(
        [r for r in _all_hyps() if r["id"] == hid2][0]["locked_ts"])
    authorize_fire(hid2, now=locked_at + timedelta(hours=25))
    with pytest.raises(HoldoutFalsificationError, match="budget"):
        assert_fireable(hid2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k "fireable or record_fire or budget" -v`
Expected: FAIL with `ImportError: cannot import name 'assert_fireable'`

- [ ] **Step 3: Write minimal implementation**

Append to `db/hypotheses.py`:

```python
from db.trials import claim_trial


class HoldoutFalsificationError(RuntimeError):
    """The falsification gate refused: not lockable/authorized/in-budget, or sealed-tamper."""


def _fired_count(con, window_label: str) -> int:
    return con.execute(
        "SELECT COUNT(*) c FROM hypotheses WHERE window_label=? AND fired_ts IS NOT NULL",
        (window_label,),
    ).fetchone()["c"]


def assert_fireable(hid: int) -> None:
    """The all-or-nothing gate chain (spec Section 3). Raises if anything fails;
    the holdout is untouched on any failure."""
    _ensure_schema()
    with transaction() as con:
        row = _fetch(con, hid)
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
            row = _fetch(con, hid)
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
        row = _fetch(con, hid)
    # write outside the gate transaction; claim_trial has its own retry
    if row["fired_ts"]:
        already = None
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
```

> **Idempotency of the confirmatory trial:** the guard `combo_json = ?` against `study_type='confirmatory'` ensures a re-read (Task 7 `test_record_fire_idempotent_within_window`) does not register a second confirmatory trial. `claim_trial` stores `combo_json = json.dumps(combo, sort_keys=True)`; `row["strategy_config_json"]` was written the same way, so the equality holds.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k "fireable or record_fire or budget" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): assert_fireable chain + record_fire (fire-before-read)"
```

---

## Task 8: `record_outcome` — verdict asymmetry + close the read window

**Files:**
- Modify: `db/hypotheses.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hypotheses_gate.py`:

```python
def test_record_outcome_refuted_when_threshold_not_met(hyp_db):
    from db.hypotheses import record_fire, record_outcome
    hid = _locked_and_authorized()        # metric net_pnl, threshold 0.0, direction '>'
    record_fire(hid)
    record_outcome(hid, realized_metric=-50.0)
    r = _all_hyps()[0]
    assert r["verdict"] == "refuted"
    assert r["status"] == "refuted"
    assert r["realized_metric"] == pytest.approx(-50.0)


def test_record_outcome_not_refuted_when_threshold_met(hyp_db):
    from db.hypotheses import record_fire, record_outcome
    hid = _locked_and_authorized()
    record_fire(hid)
    record_outcome(hid, realized_metric=120.0)
    r = _all_hyps()[0]
    assert r["verdict"] == "not_refuted"   # NEVER 'confirmed'
    assert r["status"] == "not_refuted"


def test_no_reread_after_outcome(hyp_db):
    from db.hypotheses import record_fire, record_outcome, assert_fireable, HoldoutFalsificationError
    hid = _locked_and_authorized()
    record_fire(hid)
    record_outcome(hid, realized_metric=120.0)
    with pytest.raises(HoldoutFalsificationError, match="resolved|closed"):
        assert_fireable(hid)               # read window closed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hypotheses_gate.py -k outcome -v`
Expected: FAIL with `ImportError: cannot import name 'record_outcome'`

- [ ] **Step 3: Write minimal implementation**

Append to `db/hypotheses.py`:

```python
def _passes(realized: float, threshold: float, direction: str) -> bool:
    if direction == ">":
        return realized > threshold
    if direction == "<":
        return realized < threshold
    raise ValueError(f"unknown direction {direction!r}")


def record_outcome(hid: int, *, realized_metric: float) -> None:
    """Resolve a fired hypothesis. A single shot can only 'refuted' or
    'not_refuted' — NEVER 'confirmed' (the future distribution stays
    unobserved). Closes the read window."""
    _ensure_schema()

    def _do() -> None:
        with transaction() as con:
            row = _fetch(con, hid)
            if row["status"] != "fired":
                raise HoldoutFalsificationError(
                    f"hypothesis {hid} is '{row['status']}', not 'fired' — "
                    "cannot record an outcome")
            passed = _passes(float(realized_metric), float(row["threshold"]), row["direction"])
            verdict = "not_refuted" if passed else "refuted"
            con.execute(
                "UPDATE hypotheses SET status=?, verdict=?, realized_metric=? WHERE id=?",
                (verdict, verdict, float(realized_metric), hid),
            )

    _with_write_retry("record_outcome", _do)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hypotheses_gate.py -k outcome -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(hypotheses): record_outcome — refuted|not_refuted, close read window"
```

---

## Task 9: `open_holdout_for_falsification` in `holdout_access.py`

**Files:**
- Modify: `data/holdout_access.py`
- Test: `tests/test_hypotheses_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hypotheses_gate.py`:

```python
def test_open_for_falsification_records_fire_before_read(hyp_db, tmp_path, monkeypatch):
    """The gate must mark the fire BEFORE resolving the path. We sabotage
    open_holdout so the read explodes; fired_ts must already be set."""
    import data.holdout_access as ha
    from db.hypotheses import open_holdout_for_falsification

    hid = _locked_and_authorized()

    def _explode(rel_path, *, evaluation_mode):
        raise AssertionError("read reached")

    monkeypatch.setattr(ha, "open_holdout", _explode)
    with pytest.raises(AssertionError, match="read reached"):
        open_holdout_for_falsification("fng.parquet", hypothesis_id=hid)
    # fire was recorded before the read blew up
    assert _all_hyps()[0]["fired_ts"]


def test_open_for_falsification_refuses_unauthorized(hyp_db):
    from db.hypotheses import open_holdout_for_falsification, HoldoutFalsificationError
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())          # not authorized
    with pytest.raises(HoldoutFalsificationError):
        open_holdout_for_falsification("fng.parquet", hypothesis_id=hid)
```

> `open_holdout_for_falsification` lives in `data/holdout_access.py`, but the test imports it from `db.hypotheses` too — re-export it there for ergonomics (the gate logic is in `db.hypotheses`; the holdout function delegates). Implement the function in `holdout_access.py` and add `from data.holdout_access import open_holdout_for_falsification` to the bottom of `db/hypotheses.py`. (If a circular import appears, keep the import local inside a thin wrapper in `db.hypotheses` instead.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hypotheses_gate.py -k falsification -v`
Expected: FAIL — `open_holdout_for_falsification` not defined.

- [ ] **Step 3: Write the implementation**

In `data/holdout_access.py`, add below `open_holdout`:

```python
class HoldoutFalsificationError(HoldoutAccessError):
    """Falsification access without a locked+authorized hypothesis, or budget exhausted."""


def open_holdout_for_falsification(rel_path: str, *, hypothesis_id: int) -> Path:
    """The ONLY path to a falsification read of the holdout. Verifies the gate
    chain, marks the fire BEFORE reading (claim-then-execute / Caveat 5), then
    delegates path resolution to open_holdout."""
    from db.hypotheses import assert_fireable, record_fire
    assert_fireable(hypothesis_id)                       # 1-5: refuse before touching anything
    record_fire(hypothesis_id)                           # mark the fire BEFORE the read
    return open_holdout(rel_path, evaluation_mode=True)  # reused path resolution
```

> `HoldoutFalsificationError` is defined in BOTH `holdout_access.py` (subclassing `HoldoutAccessError`) and was referenced in `db/hypotheses.py` Task 7. Resolve the duplication: keep the canonical class in `holdout_access.py` (it must subclass `HoldoutAccessError`), and in `db/hypotheses.py` import it (`from data.holdout_access import HoldoutFalsificationError`) instead of redefining. Adjust the Task 7 code: replace the `class HoldoutFalsificationError(RuntimeError)` definition with the import. If that import is circular at module load, define the class once in `db/hypotheses.py` as `class HoldoutFalsificationError(RuntimeError)` and have `holdout_access.py` import it and re-raise as needed — pick whichever import direction loads cleanly, and pin the choice with `test_open_for_falsification_refuses_unauthorized`. Run the test to confirm the chosen direction imports.

At the bottom of `db/hypotheses.py`, re-export for ergonomic imports:

```python
# Ergonomic re-export — the holdout function lives in data/holdout_access.py
# but is logically part of this gate.
def open_holdout_for_falsification(rel_path: str, *, hypothesis_id: int):
    from data.holdout_access import open_holdout_for_falsification as _impl
    return _impl(rel_path, hypothesis_id=hypothesis_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hypotheses_gate.py -k falsification -v`
Expected: PASS

- [ ] **Step 5: Run the full new-test file**

Run: `python -m pytest tests/test_hypotheses_gate.py -v`
Expected: PASS (all tasks 1-9).

- [ ] **Step 6: Commit**

```bash
git add data/holdout_access.py db/hypotheses.py tests/test_hypotheses_gate.py
git commit -m "feat(holdout): open_holdout_for_falsification gate (fire-before-read)"
```

---

## Task 10: Holdout isolation stays green + AST scanner recognizes the new entry point

**Files:**
- Modify: `tests/test_holdout_isolation.py`
- Verify: full suite

- [ ] **Step 1: Write the failing test**

Append to `tests/test_holdout_isolation.py` (in the "Whitelist sanity" section):

```python
def test_wrapper_exposes_falsification_entry_point():
    """The gate's falsification entry point must exist in the access wrapper and
    be recognized as a legitimate way to reach the holdout."""
    wrapper_path = REPO_ROOT / "data/holdout_access.py"
    text = wrapper_path.read_text()
    assert "def open_holdout_for_falsification(" in text
    assert "class HoldoutFalsificationError" in text


def test_hypotheses_module_does_not_reference_holdout_path():
    """db/hypotheses.py must touch NO holdout path — it only manages signals.db
    state, so it needs no whitelist entry. If this fails, the gate module grew a
    direct holdout reference and the isolation contract is at risk."""
    findings = _scan(REPO_ROOT / "db/hypotheses.py")
    assert findings == [], f"db/hypotheses.py references holdout: {findings}"
```

- [ ] **Step 2: Run tests to verify they pass or fail meaningfully**

Run: `python -m pytest tests/test_holdout_isolation.py -k "falsification or hypotheses_module" -v`
Expected:
- `test_wrapper_exposes_falsification_entry_point` PASS (Task 9 added the function).
- `test_hypotheses_module_does_not_reference_holdout_path` PASS (the module imports `data.holdout_access` symbols but never names a holdout *path* — the AST scanner keys on path segments / `holdout` tokens in strings, not on import statements). If it FAILS, the gate module accidentally embedded a holdout path string — remove it; the module must stay holdout-path-free.

- [ ] **Step 3: Run the full holdout-isolation suite**

Run: `python -m pytest tests/test_holdout_isolation.py -v`
Expected: PASS. The pre-existing count was 15+ tests including the two `test_a43_gate_*`; this task adds 2 more. No module was added to `HOLDOUT_LEGITIMATE_MODULES` (the new function lives in the already-whitelisted `data/holdout_access.py`; `db/hypotheses.py` is holdout-path-free). `walk_forward._HOLDOUT_322_CLOSED` is untouched, so `test_a43_gate_constant_defaults_to_blocked` stays green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_holdout_isolation.py
git commit -m "test(holdout): isolation green + recognize open_holdout_for_falsification"
```

---

## Task 11: Full suite green

**Files:**
- Verify only

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. Pre-existing orthogonal flakes per `.mex/context/ci-discipline.md` may appear (e.g. the Windows-local `test_load_proxy_from_env` flake in `test_http_reexport.py`); these do NOT fail CI Linux. If a NON-flake test fails, STOP and fix before proceeding — a gate that breaks an unrelated test is not shippable.

- [ ] **Step 2: Commit (only if a fixture touch-up was needed)**

```bash
git add -A
git commit -m "test(hypotheses): full-suite green"
```

---

## Task 12: GROW — `.mex` pattern for firing the holdout

**Files:**
- Create: `.mex/patterns/firing-the-holdout.md`
- Modify: `.mex/patterns/INDEX.md`

- [ ] **Step 1: Write the pattern**

Create `.mex/patterns/firing-the-holdout.md`:

```markdown
# Firing the holdout (the falsification gate)

## Contract

A falsification read of `data/holdout/` is reachable ONLY via
`data.holdout_access.open_holdout_for_falsification(rel_path, hypothesis_id=…)`,
which refuses unless a `hypotheses` row (see `db/hypotheses.py`) is locked,
authorized, sealed, and within the fire budget. Custodial reads (MANIFEST,
drift, integrity) keep using `open_holdout(rel_path, evaluation_mode=True)` and
need no hypothesis.

## Sequence

1. `claim_hypothesis(...)` — create a DRAFT with the frozen claim
   (`metric/threshold/direction`), the selection gate (`deflated_metric/
   deflated_threshold`), and the candidate's deflation inputs
   (`cand_sharpe/cand_n_returns/cand_skew/cand_kurt_raw`, sourced from the
   winning exploratory trial).
2. Attach lower-tier evidence: `preholdout_trial_ids`, `walkforward_ref` and
   `drift_check_ref` (each `{ref, verdict, ts}` with `verdict='pass'`).
3. `lock_hypothesis(hid, today=…)` — enforces the FIVE criteria
   (provenance 4a, deflation 4b, walk-forward 4c, drift 4d, complete claim 4e),
   captures `n_at_lock`, seals the row. The row is now immutable (seal + DB
   trigger). This is what "#322 closure criteria met" means in machine terms.
4. **Cooldown.** Wait `HOLDOUT_FIRE_COOLDOWN` between lock and authorization.
5. `authorize_fire(hid, now=…)` — the SEPARATE deliberate act. Refuses before
   the cooldown. `mex log` the authorization (no silent fire).
6. `open_holdout_for_falsification(rel_path, hypothesis_id=hid)` — marks the
   fire BEFORE the read (a partial peek burns the bala unica, Caveat 5), then
   reads.
7. `record_outcome(hid, realized_metric=…)` — `refuted` or `not_refuted`
   (NEVER `confirmed` — a single shot cannot confirm a future distribution).
   Closes the read window; further reads of this hypothesis are refused.

## Why each guard exists

- **Provenance (4a)** closes naive post-peek hand-tuning; it is necessary, not
  sufficient. **Deflation (4b)** closes competent selection bias (best-of-N).
- **Cooldown + authorize_fire** guard the most likely cause of death: the
  authorized owner firing a perfectly legitimate hypothesis one day too early.
- **Fire-before-read** makes a partial peek count as a fire.
- **Budget = 1** (conception 1): the locked holdout is a one-shot gate; the
  renewable validation is live shadow (epic B).

## Known limits (documented, not hidden)

Deflation N is a LOWER bound (`docs/deflation.md`): crashed trials and sweeps
outside the four wired ones do not enter N, and `n_effective = max(N, 50)`
until 2026-11-29. The gate raises the floor of rigor; it does not make N
omniscient. Mitigation is registry discipline ([[registering-a-trial]]).

## Out of scope

Migrating `walk_forward.evaluate_winner_on_holdout` (today gated by
`_HOLDOUT_322_CLOSED`) onto this function is a separate #322-closure PR.
Shadow→active code-enforcement is epic B.

See the spec: `docs/superpowers/specs/2026-06-01-holdout-falsification-gate-design.md`.
```

- [ ] **Step 2: Add to the pattern index**

Add this line to `.mex/patterns/INDEX.md` (in the appropriate section):

```markdown
- [firing-the-holdout](firing-the-holdout.md) — the falsification gate: locked+authorized hypothesis required before any holdout falsification read (Capa 2).
```

- [ ] **Step 3: Log the event**

```bash
mex log "added pattern: firing-the-holdout (Capa 2 holdout falsification gate)"
```

- [ ] **Step 4: Commit**

```bash
git add .mex/patterns/firing-the-holdout.md .mex/patterns/INDEX.md
git commit -m "docs(mex): add firing-the-holdout pattern (Capa 2 gate)"
```

---

## Pre-push: adversarial audit (guardrail-critical)

The gate guards the irreplaceable resource. With all commits green locally, dispatch an independent adversarial audit (separate from the implementer) on the diff before pushing. Lenses (from the spec's pre-push section):
1. Can falsification access succeed without a locked hypothesis?
2. Does a partial peek truly record a fire (fire-before-read)?
3. Can the seal/trigger be bypassed by a direct UPDATE?
4. Does the budget count fires correctly across distinct hypotheses?
5. Does `test_holdout_isolation` stay green and does the scanner recognize the new entry point?
6. Can a fire be executed without `authorize_fire` / before the cooldown (the impatient-owner path)?
7. Can the deflation gate (4b) be passed with an under-registered N, and is that limit documented rather than hidden?
8. Can any path write `verdict='confirmed'` or re-read the holdout after the outcome is recorded?

Amend on findings, then push and open the PR (base `upstream/main`). Per the subagent-parallel antipattern: do NOT run subagent implementers in parallel — sequential implement → capture SHA → review → next.

---

## Self-Review (completed during authoring)

1. **Spec coverage:** Section 1 custodial/falsification split (Task 9) · Section 2 schema + lifecycle incl. `authorize_fire`, verdict asymmetry, seal, budget, cooldown (Tasks 1,5,6,8) · Section 3 gate chain fire-before-read (Tasks 7,9) · Section 4 five lock criteria a-e incl. deflation-in-machine (Tasks 2,3,4) · Section 5 protocol/epic-B left as runbook (Task 12) · Section 6 testing without touching the bullet + isolation green + scanner recognition (Tasks 9,10,11). ✔
2. **Placeholder scan:** every code step carries real code; verification points are directed confirmations with fallbacks, not TBDs. The one explicit follow-up (walk_forward migration) is scoped OUT with rationale, not deferred silently. ✔
3. **Type consistency:** `claim_hypothesis(...) -> int`; `lock_hypothesis(hid, *, today)`; `authorize_fire(hid, *, now)`; `record_fire(hid)`; `record_outcome(hid, *, realized_metric)`; `assert_fireable(hid)`; `open_holdout_for_falsification(rel_path, *, hypothesis_id)`. `_FROZEN_FIELDS` matches the trigger's `WHEN` columns and `_compute_seal`. `HoldoutFalsificationError` single canonical definition (Task 9 resolves the duplication). Deflation signature matches `deflation.py`. ✔
