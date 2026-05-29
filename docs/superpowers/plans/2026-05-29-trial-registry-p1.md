# Trial Registry (#278 Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a trial registry (`db/trials.py`) that records every backtest run inside an exploratory parameter/window sweep as a row in a new `trials` table, using claim-then-execute so a crashed run still counts toward the trial count N.

**Architecture:** A new module `db/trials.py` exposes `claim_trial(...) -> int` (INSERT a `status='pending'` row BEFORE the simulator runs) and `finalize_trial(id, status, metrics|error)` (UPDATE to `ok`/`failed` after). Writes go through the existing `db.transaction.transaction()` primitive (configured connection, `busy_timeout=5000`, `BEGIN IMMEDIATE`) wrapped in bounded retry/backoff on `database is locked`. A transient lock NEVER aborts a sweep; only durability exhaustion (backoff spent, disk full, corruption) raises. The four exploratory selection sweeps — `auto_tune`, `grid_search_tf`, `optimize_new_tokens`, `regime_allocation_sweep` — get claim/finalize wiring at their natural chokepoints. The pre-registered confirmatory `signal_calibration_*` sweeps are out of scope (recordable later via `study_type='confirmatory'` without schema change). The deflated-metrics consumption of N is **#278 Part 2**, a separate PR.

**Tech Stack:** Python 3, SQLite via `db/connection.py` + `db/transaction.py`, pytest.

---

## Why this design (decision provenance)

This plan is the output of a pre-coding investigation on 2026-05-29:

- **Plumb** (sizing, ×2): #278 Part 1 is size **M**, mode **refactor**. The split Part 1 → Part 2 is clean (Part 2 adds a keyword-default param to `calculate_metrics`, touching 0 mandatory callsites). Storage = `signals.db` by precedent (`auto_tune.save_tune_result`). Naming for Part 2 metrics already reserved in `tests/test_backtest_with_costs.py:244-248`. **0 open architectural decisions.**
- **Halberg** (runtime): the spec's literal design — register INSIDE `calculate_metrics` with "abort loudly on insert failure" — does NOT run under load. `signals.db` already has concurrent writers (scanner `BEGIN IMMEDIATE` at `db/transaction.py:63`, API). `tools/regime_allocation_sweep.py` runs `calculate_metrics` in N child processes via `multiprocessing.Pool`. And a process that dies before the INSERT cannot register itself → silent N corruption. The corrected design (this plan): claim-then-execute at the ORCHESTRATOR level, single serial writer, configured connection + retry/backoff, abort only on durability exhaustion.
- **Operator (Samuel)**: scope confirmed = core 4 sweeps + `source`/`study_type` provenance columns from day 1; `signal_calibration_*` (epic C, pre-registered confirmatory) excluded for now.

**Governance:**
- Non-Negotiable #2/#3: this work touches `signals.db` only. It does NOT touch `data/holdout/`, does NOT call `open_holdout`, does NOT add to the holdout allow-list. `tests/test_holdout_isolation.py` must stay 15/15.
- Non-Negotiable #5: no baseline numbers are produced or cited here.
- The registry is guardrail-critical (it is the N denominator for deflation). Per the project's adversarial-audit pattern: after the implementation commits are green locally, run an independent adversarial audit before pushing.

---

## File Structure

- **Create:** `db/trials.py` — the registry module. One responsibility: claim/finalize trial rows in `signals.db` with retry/backoff. ~90-120 lines.
- **Create:** `tests/test_trials_registry.py` — unit tests for the registry module.
- **Modify:** `grid_search_tf.py` — wire claim/finalize into the `grid_search_symbol` loop.
- **Modify:** `optimize_new_tokens.py` — wire claim/finalize into the `optimize_symbol` loop.
- **Modify:** `auto_tune.py` — wire claim/finalize into `run_backtest_with_params` (the single chokepoint; 3 callers).
- **Modify:** `tools/regime_allocation_sweep.py` — wire claim/finalize into `_run_jobs_parallel` (parent-side, around `pool.map`).
- **Modify:** `tests/test_grid_search_tf.py` (or create if absent) — wiring test.
- **Modify:** `tests/test_regime_allocation_sweep.py` — wiring test for `_run_jobs_parallel`.
- **Create:** `.mex/patterns/registering-a-trial.md` + add to `.mex/patterns/INDEX.md` — the GROW step.

### Verification points (the implementer MUST confirm before writing the wiring code)

1. **`auto_tune.run_backtest_with_params` signature.** This plan assumes `run_backtest_with_params(symbol, params, sim_start, sim_end, *, cutoff=None, app_config=None)` (inferred from the call at `auto_tune.py:303`). Read the actual `def` (just above `auto_tune.py:225`) and confirm the parameter names before wiring. Confirm it has no callers outside `auto_tune.py` that would create spurious trials (grep `run_backtest_with_params`).
2. **`regime_allocation_sweep` job dict keys.** This plan extracts `combo = {k: job[k] for k in ("symbol", "sub_window", "vol_target") if k in job}`. Read `_build_primary_jobs` (`tools/regime_allocation_sweep.py:741`) and `_build_sensitivity_jobs` (`:763`) to confirm the actual identity keys, and adjust the extraction set if they differ.
3. **Trial-producing worker set.** This plan gates trial registration to `worker_fn in {_process_regime_allocation_cell, _process_lrc_archived_baseline_cell}`. Confirm the btc_bh / hubrich call-sites of `_run_jobs_parallel` pass a DIFFERENT `worker_fn` (those baselines do not run `calculate_metrics` and must NOT produce trials). Grep the `_run_jobs_parallel(` call-sites.

---

## Task 1: Trials table schema + `claim_trial`

**Files:**
- Create: `db/trials.py`
- Test: `tests/test_trials_registry.py`

- [ ] **Step 0: Branch**

```bash
git checkout main
git pull upstream main          # base = upstream/main @ 3763f0b
git checkout -b feat/278-trial-registry
```

- [ ] **Step 1: Write the failing test (claim inserts a pending row)**

Create `tests/test_trials_registry.py`:

```python
import json
import sqlite3

import pytest

from db.transaction import transaction


@pytest.fixture
def trials_db(tmp_path, monkeypatch):
    """Point the DB layer at a throwaway file and reset the module's
    once-per-process schema flag so each test starts clean."""
    db_path = tmp_path / "signals_test.db"
    monkeypatch.setattr("btc_api.DB_FILE", str(db_path))
    import db.trials
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    return db_path


def _all_trials():
    with transaction() as con:
        return [dict(r._mapping) for r in con.execute(
            "SELECT * FROM trials ORDER BY id"
        ).fetchall()]


def test_claim_trial_inserts_pending_row(trials_db):
    from db.trials import claim_trial

    tid = claim_trial(
        source="grid_search_tf",
        symbol="BTCUSDT",
        combo={"atr_sl_mult": 1.0, "atr_tp_mult": 3.0},
        window_label="2022-01-01..2022-04-01",
    )

    rows = _all_trials()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == tid
    assert row["status"] == "pending"
    assert row["source"] == "grid_search_tf"
    assert row["symbol"] == "BTCUSDT"
    assert row["study_type"] == "exploratory"
    assert row["finalized_ts"] is None
    assert json.loads(row["combo_json"])["atr_sl_mult"] == 1.0
    assert row["claimed_ts"]  # non-empty ISO timestamp


def test_claim_trial_study_type_can_be_confirmatory(trials_db):
    from db.trials import claim_trial

    claim_trial(
        source="signal_calibration_sweep",
        combo={"vol_target": 0.30},
        study_type="confirmatory",
    )
    assert _all_trials()[0]["study_type"] == "confirmatory"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trials_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.trials'`

- [ ] **Step 3: Write minimal implementation**

Create `db/trials.py`:

```python
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
- Storage: signals.db (same DB as tune_results; precedent
  auto_tune.save_tune_result). The sweep scripts do not call db.schema.init_db,
  so this module ensures its own table + WAL idempotently on first use.
- source + study_type columns let Part 2 compute N_effective by filtering:
  only 'exploratory' trials inflate selection bias; 'confirmatory'
  pre-registered studies (epic C) are recordable later WITHOUT schema change.

Scope (operator-confirmed 2026-05-29): wired into the 4 exploratory selection
sweeps — auto_tune, grid_search_tf, optimize_new_tokens, regime_allocation_sweep.
The pre-registered signal_calibration_* sweeps are confirmatory and excluded.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

from db.schema import _set_wal_mode_idempotent_with_retry
from db.transaction import transaction

log = logging.getLogger("db.trials")

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
```

Also add the retry helper (used by claim now, finalize in Task 2). Insert it directly above `claim_trial`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trials_registry.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add db/trials.py tests/test_trials_registry.py
git commit -m "feat(trials): trials table + claim_trial (Advances #278)"
```

---

## Task 2: `finalize_trial`

**Files:**
- Modify: `db/trials.py`
- Test: `tests/test_trials_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trials_registry.py`:

```python
def test_finalize_trial_ok_records_metrics_and_sharpe(trials_db):
    from db.trials import claim_trial, finalize_trial

    tid = claim_trial(source="auto_tune", combo={"x": 1}, symbol="ETHUSDT")
    finalize_trial(tid, status="ok", metrics={"sharpe_ratio": 1.42, "net_pnl": 100})

    row = _all_trials()[0]
    assert row["status"] == "ok"
    assert row["finalized_ts"]
    assert row["sharpe"] == pytest.approx(1.42)
    assert json.loads(row["metrics_json"])["net_pnl"] == 100
    assert row["error"] is None


def test_finalize_trial_failed_records_error(trials_db):
    from db.trials import claim_trial, finalize_trial

    tid = claim_trial(source="auto_tune", combo={"x": 1})
    finalize_trial(tid, status="failed", error="No data")

    row = _all_trials()[0]
    assert row["status"] == "failed"
    assert row["error"] == "No data"
    assert row["sharpe"] is None


def test_crashed_trial_leaves_pending_row(trials_db):
    """A claim with no finalize (process died) must remain countable."""
    from db.trials import claim_trial

    claim_trial(source="grid_search_tf", combo={"x": 1})
    row = _all_trials()[0]
    assert row["status"] == "pending"
    assert row["finalized_ts"] is None


def test_finalize_invalid_status_raises(trials_db):
    from db.trials import claim_trial, finalize_trial

    tid = claim_trial(source="auto_tune", combo={"x": 1})
    with pytest.raises(ValueError, match="ok.*failed"):
        finalize_trial(tid, status="done")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trials_registry.py -v`
Expected: FAIL with `ImportError: cannot import name 'finalize_trial'`

- [ ] **Step 3: Write minimal implementation**

Append to `db/trials.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trials_registry.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add db/trials.py tests/test_trials_registry.py
git commit -m "feat(trials): finalize_trial ok/failed (Advances #278)"
```

---

## Task 3: Retry/backoff semantics (abort only on durability exhaustion)

This task adds tests pinning Halberg's runtime requirement: a transient `database is locked` is retried (never aborts a sweep); a non-lock error propagates immediately; only backoff exhaustion raises.

**Files:**
- Test: `tests/test_trials_registry.py`
- (No new implementation — `_with_write_retry` was written in Task 1; this task verifies its semantics.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trials_registry.py`:

```python
def test_with_write_retry_succeeds_after_transient_lock(monkeypatch):
    import db.trials
    monkeypatch.setattr(db.trials.time, "sleep", lambda s: None)  # skip real backoff
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert db.trials._with_write_retry("test", flaky) == "ok"
    assert calls["n"] == 2


def test_with_write_retry_exhaustion_raises(monkeypatch):
    import db.trials
    monkeypatch.setattr(db.trials.time, "sleep", lambda s: None)

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        db.trials._with_write_retry("test", always_locked)


def test_with_write_retry_non_lock_error_propagates_immediately(monkeypatch):
    import db.trials
    monkeypatch.setattr(db.trials.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: trials")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db.trials._with_write_retry("test", boom)
    assert calls["n"] == 1  # no retry on non-lock errors
```

- [ ] **Step 2: Run tests to verify they pass (implementation already exists)**

Run: `python -m pytest tests/test_trials_registry.py -v`
Expected: PASS (9 passed). If `test_with_write_retry_succeeds_after_transient_lock` fails because the backoff loop has the wrong number of attempts, fix `_with_write_retry` so the iteration is over `(0.0, *_WRITE_BACKOFFS)` (4 total attempts).

- [ ] **Step 3: Commit**

```bash
git add tests/test_trials_registry.py
git commit -m "test(trials): pin retry/backoff abort-loudly semantics (Advances #278)"
```

---

## Task 4: Wire `grid_search_tf.grid_search_symbol`

**Files:**
- Modify: `grid_search_tf.py:80-160` (the `grid_search_symbol` loop)
- Test: `tests/test_grid_search_tf.py` (create if absent)

- [ ] **Step 1: Write the failing wiring test**

Create or append to `tests/test_grid_search_tf.py`:

```python
from datetime import datetime, timezone


def test_grid_search_claims_and_finalizes_each_trial(monkeypatch):
    import grid_search_tf as gst

    # Stub data loaders so no real data is needed.
    monkeypatch.setattr(gst, "get_cached_data", lambda *a, **k: ["bar"])
    monkeypatch.setattr(gst, "get_historical_fear_greed", lambda: None)
    monkeypatch.setattr(gst, "get_historical_funding_rate", lambda: None)

    # First combo succeeds, second produces no trades.
    seq = iter([(["t"], ["e"]), ([], [])])
    monkeypatch.setattr(gst, "simulate_strategy", lambda *a, **k: next(seq))
    monkeypatch.setattr(gst, "calculate_metrics", lambda *a, **k: {
        "total_trades": 3, "win_rate": 0.6, "net_pnl": 10, "profit_factor": 1.5,
        "max_drawdown_pct": -5, "sharpe_ratio": 1.1, "final_equity": 110,
        "trades_per_month": 2,
    })

    claims, finals = [], []
    monkeypatch.setattr(gst, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(gst, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    tiny_grid = {
        "tf_ema_fast": [10], "tf_ema_slow": [20],
        "tf_adx_min": [20], "tf_atr_mult": [2.0], "tf_rsi_entry_long": [55],
    }
    gst.grid_search_symbol(
        "BTCUSDT", tiny_grid,
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        datetime(2022, 4, 1, tzinfo=timezone.utc),
    )

    # Both combos claimed; first finalized ok, second finalized failed.
    assert len(claims) == 2
    assert all(c["source"] == "grid_search_tf" for c in claims)
    statuses = [kw["status"] for _, kw in finals]
    assert statuses == ["ok", "failed"]
```

> Note: `tiny_grid` has 1 value per key → `itertools.product` yields 1 combo. To exercise both the ok and no-trades paths, widen one key to 2 values so 2 combos run, e.g. `"tf_ema_fast": [10, 11]`. Adjust the grid above to `"tf_ema_fast": [10, 11]` so `len(claims) == 2` holds.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_grid_search_tf.py::test_grid_search_claims_and_finalizes_each_trial -v`
Expected: FAIL — `AttributeError: module 'grid_search_tf' has no attribute 'claim_trial'`

- [ ] **Step 3: Write the implementation**

In `grid_search_tf.py`, add the import near the top (with the other imports):

```python
from db.trials import claim_trial, finalize_trial
```

In `grid_search_symbol`, just before the combos loop (after `results = []` near line 103), build the window label:

```python
    window_label = f"{sim_start}..{sim_end}"
```

Replace the loop body (lines 106-154 region) so each combo claims a trial before simulating and finalizes on every exit path:

```python
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        # Skip invalid combos (fast >= slow) — these never run, not trials.
        if params["tf_ema_fast"] >= params["tf_ema_slow"]:
            continue

        config = {
            "symbol_overrides": {
                symbol: {
                    "strategy": "trend_following",
                    "use_5m_trigger": use_5m_trigger,
                    **params,
                    "tf_rsi_entry_short": 100 - params["tf_rsi_entry_long"],
                }
            }
        }

        trial_id = claim_trial(
            source="grid_search_tf",
            symbol=symbol,
            combo=params,
            window_label=window_label,
        )
        try:
            trades, equity = simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m,
                symbol=symbol,
                df1d=df1d,
                sim_start=sim_start, sim_end=sim_end,
                df_fng=df_fng, df_funding=df_funding,
                backtest_config=config,
            )

            if not trades:
                finalize_trial(trial_id, status="failed", error="no trades")
                continue

            metrics = calculate_metrics(trades, equity)
            if "error" in metrics:
                finalize_trial(trial_id, status="failed", error=str(metrics["error"]))
                continue

            finalize_trial(trial_id, status="ok", metrics=metrics)

            result = {
                "symbol": symbol,
                **params,
                "use_5m_trigger": use_5m_trigger,
                "trades": metrics["total_trades"],
                "win_rate": metrics["win_rate"],
                "net_pnl": metrics["net_pnl"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown_pct"],
                "sharpe": metrics["sharpe_ratio"],
                "final_equity": metrics["final_equity"],
                "trades_per_month": metrics["trades_per_month"],
            }
            results.append(result)
        except Exception as e:
            finalize_trial(trial_id, status="failed", error=str(e))
            log.warning(f"  Error: {e}")
            continue
```

> If the existing loop already wrapped the body in `try/except`, preserve any code after the `results.append(...)` (e.g. the progress-print block at line 102 of `optimize_new_tokens` has an analog here) by keeping it after the `except` block, unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_grid_search_tf.py::test_grid_search_claims_and_finalizes_each_trial -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add grid_search_tf.py tests/test_grid_search_tf.py
git commit -m "feat(trials): register trials in grid_search_tf sweep (Advances #278)"
```

---

## Task 5: Wire `optimize_new_tokens.optimize_symbol`

**Files:**
- Modify: `optimize_new_tokens.py:46-100` (the `optimize_symbol` loop)
- Test: `tests/test_optimize_new_tokens.py` (create if absent)

- [ ] **Step 1: Write the failing wiring test**

Create or append to `tests/test_optimize_new_tokens.py`:

```python
from datetime import datetime, timezone


def test_optimize_new_tokens_claims_and_finalizes(monkeypatch):
    import optimize_new_tokens as ont

    monkeypatch.setattr(ont, "get_cached_data", lambda *a, **k: ["bar"])
    monkeypatch.setattr(ont, "get_historical_fear_greed", lambda: None)
    monkeypatch.setattr(ont, "get_historical_funding_rate", lambda: None)

    monkeypatch.setattr(ont, "simulate_strategy", lambda *a, **k: (["t"], ["e"]))
    monkeypatch.setattr(ont, "calculate_metrics", lambda *a, **k: {
        "total_trades": 3, "win_rate": 0.6, "net_pnl": 10, "profit_factor": 1.5,
        "max_drawdown_pct": -5, "sharpe_ratio": 1.1, "final_equity": 110,
    })

    claims, finals = [], []
    monkeypatch.setattr(ont, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(ont, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    # Shrink the grid to 2 combos for a fast deterministic test.
    monkeypatch.setattr(ont, "GRID", {
        "atr_sl_mult": [0.5, 1.0], "atr_tp_mult": [3.0], "atr_be_mult": [2.0],
    })

    ont.optimize_symbol(
        "NEWUSDT",
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 4, 1, tzinfo=timezone.utc),
    )

    assert len(claims) == 2
    assert all(c["source"] == "optimize_new_tokens" for c in claims)
    assert [kw["status"] for _, kw in finals] == ["ok", "ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_optimize_new_tokens.py::test_optimize_new_tokens_claims_and_finalizes -v`
Expected: FAIL — `AttributeError: module 'optimize_new_tokens' has no attribute 'claim_trial'`

- [ ] **Step 3: Write the implementation**

In `optimize_new_tokens.py`, add the import near the top:

```python
from db.trials import claim_trial, finalize_trial
```

In `optimize_symbol`, build the window label before the loop (after `results = []` near line 62):

```python
    window_label = f"{sim_start}..{sim_end}"
```

Replace the loop body (lines 65-100) so each combo claims/finalizes:

```python
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        trial_id = claim_trial(
            source="optimize_new_tokens",
            symbol=symbol,
            combo=params,
            window_label=window_label,
        )
        try:
            trades, equity = simulate_strategy(
                df1h=df1h, df4h=df4h, df5m=df5m,
                symbol=symbol, sl_mode="atr",
                atr_sl_mult=params["atr_sl_mult"],
                atr_tp_mult=params["atr_tp_mult"],
                atr_be_mult=params["atr_be_mult"],
                df1d=df1d,
                sim_start=sim_start, sim_end=sim_end,
                df_fng=df_fng, df_funding=df_funding,
            )

            if not trades:
                finalize_trial(trial_id, status="failed", error="no trades")
                continue

            metrics = calculate_metrics(trades, equity)
            if "error" in metrics:
                finalize_trial(trial_id, status="failed", error=str(metrics["error"]))
                continue

            finalize_trial(trial_id, status="ok", metrics=metrics)

            results.append({
                "symbol": symbol,
                **params,
                "trades": metrics["total_trades"],
                "win_rate": metrics["win_rate"],
                "net_pnl": metrics["net_pnl"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown_pct"],
                "sharpe": metrics["sharpe_ratio"],
                "final_equity": metrics["final_equity"],
            })
        except Exception as e:
            finalize_trial(trial_id, status="failed", error=str(e))
            log.warning(f"  Error: {e}")
            continue

        if (idx + 1) % 20 == 0:
            elapsed = time.time() - start_time
            rate = (idx + 1) / elapsed
            log.info(f"  {idx + 1}/{len(combos)} ({rate:.1f}/s)")
```

> Preserve the exact body of the progress-print block (lines 102-104+) as it exists in the file; the snippet above shows its shape but copy the real lines.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_optimize_new_tokens.py::test_optimize_new_tokens_claims_and_finalizes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add optimize_new_tokens.py tests/test_optimize_new_tokens.py
git commit -m "feat(trials): register trials in optimize_new_tokens sweep (Advances #278)"
```

---

## Task 6: Wire `auto_tune.run_backtest_with_params`

> **AMENDED 2026-05-29 (operator-confirmed):** Verification point 1 surfaced that `run_backtest_with_params` has 5 EXTERNAL callers — `walk_forward.py:847` (EVALUATION phase, not selection) + 4 research tools (`r1`/`r2`/`r3`/`q2`). Registering unconditionally would pull them into N as `auto_tune` trials — out of the agreed "core 4" scope AND a methodology bug (counting evaluation runs as selection trials corrupts the N denominator). FIX: registration is now OPT-IN via a keyword-only `trial_source: str | None = None`. The function claims/finalizes ONLY when `trial_source is not None` (using it as the `source`). auto_tune's 3 internal callers pass `trial_source="auto_tune"`; the 5 external callers keep the default `None` and register nothing. `_finalize_from_metrics(trial_id, ...)` early-returns `(trades, metrics)` when `trial_id is None`; the `except` finalizes only `if trial_id is not None`, then re-raises. Implemented in commit `794747e`.

**Files:**
- Modify: `auto_tune.py` (`run_backtest_with_params` — the single chokepoint, 3 callers)
- Test: `tests/test_auto_tune_trials.py` (create)

> **Confirm verification point 1 first** (the `run_backtest_with_params` signature and that it has no external callers).

- [ ] **Step 1: Write the failing wiring tests**

Create `tests/test_auto_tune_trials.py`:

```python
from datetime import datetime, timezone


def _stub_loaders(monkeypatch, at):
    # Make data load succeed with non-empty frames.
    import pandas as pd
    frame = pd.DataFrame({"x": [1, 2, 3]})
    monkeypatch.setattr(at, "get_cached_data", lambda *a, **k: frame, raising=False)


def test_run_backtest_with_params_finalizes_ok(monkeypatch):
    import auto_tune as at

    monkeypatch.setattr(at, "simulate_strategy", lambda *a, **k: (["t"], ["e"]))
    monkeypatch.setattr(at, "calculate_metrics", lambda *a, **k: {
        "total_trades": 5, "net_pnl": 50, "profit_factor": 1.3, "sharpe_ratio": 0.9,
    })

    claims, finals = [], []
    monkeypatch.setattr(at, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(at, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    # Provide non-empty frames so the "No data" early return is not taken.
    import pandas as pd
    df = pd.DataFrame({"a": [1, 2]})
    monkeypatch.setattr(at, "get_cached_data", lambda *a, **k: df, raising=False)

    at.run_backtest_with_params(
        "BTCUSDT",
        {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
        datetime(2022, 1, 1, tzinfo=timezone.utc),
        datetime(2022, 4, 1, tzinfo=timezone.utc),
    )

    assert len(claims) == 1
    assert claims[0]["source"] == "auto_tune"
    assert finals[0][1]["status"] == "ok"


def test_run_backtest_with_params_finalizes_failed_on_exception(monkeypatch):
    import auto_tune as at

    def boom(*a, **k):
        raise RuntimeError("simulator exploded")

    monkeypatch.setattr(at, "simulate_strategy", boom)

    claims, finals = [], []
    monkeypatch.setattr(at, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(at, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    import pandas as pd
    df = pd.DataFrame({"a": [1, 2]})
    monkeypatch.setattr(at, "get_cached_data", lambda *a, **k: df, raising=False)

    import pytest
    with pytest.raises(RuntimeError, match="exploded"):
        at.run_backtest_with_params(
            "BTCUSDT",
            {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_be_mult": 2.0},
            datetime(2022, 1, 1, tzinfo=timezone.utc),
            datetime(2022, 4, 1, tzinfo=timezone.utc),
        )

    assert len(claims) == 1
    assert finals[0][1]["status"] == "failed"
    assert "exploded" in finals[0][1]["error"]
```

> The loader stubs may need adjustment to match how `run_backtest_with_params` loads data (it may receive frames as args rather than calling `get_cached_data`). Read the function head and adapt the stub so the body reaches `simulate_strategy`. If the function takes frames directly, drop the `get_cached_data` monkeypatch and pass small `pd.DataFrame`s as those args.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_auto_tune_trials.py -v`
Expected: FAIL — `AttributeError: module 'auto_tune' has no attribute 'claim_trial'`

- [ ] **Step 3: Write the implementation**

In `auto_tune.py`, add the import near the top:

```python
from db.trials import claim_trial, finalize_trial
```

Add a small module-level helper above `run_backtest_with_params`:

```python
def _finalize_from_metrics(trial_id, trades, metrics):
    """Finalize a trial from a (trades, metrics) result and return it unchanged
    so call sites can `return _finalize_from_metrics(...)`."""
    if not trades or "error" in metrics:
        finalize_trial(
            trial_id, status="failed",
            error=str(metrics.get("error", "no trades")),
        )
    else:
        finalize_trial(trial_id, status="ok", metrics=metrics)
    return trades, metrics
```

Then wrap `run_backtest_with_params`. At the very top of the function body, claim the trial:

```python
def run_backtest_with_params(symbol, params, sim_start, sim_end, *, cutoff=None, app_config=None):
    trial_id = claim_trial(
        source="auto_tune",
        symbol=symbol,
        combo=params,
        window_label=f"{sim_start.date()}..{sim_end.date()}",
    )
    try:
        # ... existing body unchanged, EXCEPT every `return trades, metrics`
        #     (and the two early `return [], {"error": ...}` returns) becomes
        #     `return _finalize_from_metrics(trial_id, <first>, <second>)`.
        ...
    except Exception as e:
        finalize_trial(trial_id, status="failed", error=str(e))
        raise
```

Concretely, change the three return statements:
- Line ~231 `return [], {"error": "No data", ...}` → `return _finalize_from_metrics(trial_id, [], {"error": "No data", "total_trades": 0, "net_pnl": 0, "profit_factor": 0})`
- Line ~277 `return [], {"error": "No trades", ...}` → `return _finalize_from_metrics(trial_id, [], {"error": "No trades", "total_trades": 0, "net_pnl": 0, "profit_factor": 0})`
- Line ~280 `return trades, metrics` → `return _finalize_from_metrics(trial_id, trades, metrics)`

Keep the existing dict contents byte-identical — only wrap the return.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auto_tune_trials.py -v`
Expected: PASS

- [ ] **Step 5: Run the existing auto_tune tests to confirm no regression**

Run: `python -m pytest tests/test_auto_tune_max_date.py -v`
Expected: PASS (the `*a, **kw` monkeypatches there tolerate the wrapped function; if a test that asserts an exact `(trades, metrics)` tuple fails, it is because `_finalize_from_metrics` must return the SAME tuple — verify it returns `(trades, metrics)` unchanged).

- [ ] **Step 6: Commit**

```bash
git add auto_tune.py tests/test_auto_tune_trials.py
git commit -m "feat(trials): register trials in auto_tune run_backtest_with_params (Advances #278)"
```

---

## Task 7: Wire `tools/regime_allocation_sweep._run_jobs_parallel` (parent-side)

This is the parallel path. The trial WRITE happens in the parent, never in the `pool.map` child. The parent claims each job before dispatch and finalizes by index after `pool.map` (which preserves order).

**Files:**
- Modify: `tools/regime_allocation_sweep.py:1114-1132` (`_run_jobs_parallel`)
- Test: `tests/test_regime_allocation_sweep.py`

> **Confirm verification points 2 and 3 first** (job dict keys; trial-producing worker set).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regime_allocation_sweep.py`:

```python
def test_run_jobs_parallel_registers_trials_for_cell_workers(monkeypatch):
    import tools.regime_allocation_sweep as ras

    # Avoid real multiprocessing: run the worker in-process, order-preserving.
    class _FakePool:
        def __init__(self, n):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def map(self, fn, jobs):
            return [fn(j) for j in jobs]

    monkeypatch.setattr(ras, "Pool", _FakePool)

    claims, finals = [], []
    monkeypatch.setattr(ras, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(ras, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    # A trial-producing worker: first job ok, second errored.
    seq = iter([{"sharpe_ratio": 1.0}, {"error": "boom"}])

    def fake_cell(job):
        return next(seq)

    monkeypatch.setattr(ras, "_process_regime_allocation_cell", fake_cell)

    jobs = [
        {"symbol": "BTCUSDT", "sub_window": "A", "vol_target": 0.30},
        {"symbol": "ETHUSDT", "sub_window": "A", "vol_target": 0.30},
    ]
    ras._run_jobs_parallel(jobs, workers=1, label="test", worker_fn=fake_cell)

    assert len(claims) == 2
    assert all(c["source"] == "regime_allocation_sweep" for c in claims)
    assert [kw["status"] for _, kw in finals] == ["ok", "failed"]


def test_run_jobs_parallel_skips_trials_for_non_cell_workers(monkeypatch):
    import tools.regime_allocation_sweep as ras

    class _FakePool:
        def __init__(self, n): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, fn, jobs): return [fn(j) for j in jobs]

    monkeypatch.setattr(ras, "Pool", _FakePool)

    claims = []
    monkeypatch.setattr(ras, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(ras, "finalize_trial", lambda tid, **kw: None)

    def bh_baseline(job):  # NOT a trial-producing worker
        return {"baseline": True}

    ras._run_jobs_parallel(
        [{"symbol": "BTCUSDT"}], workers=1, label="bh", worker_fn=bh_baseline,
    )
    assert claims == []  # baselines do not produce trials
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regime_allocation_sweep.py::test_run_jobs_parallel_registers_trials_for_cell_workers -v`
Expected: FAIL — `AttributeError: module 'tools.regime_allocation_sweep' has no attribute 'claim_trial'`

- [ ] **Step 3: Write the implementation**

In `tools/regime_allocation_sweep.py`, add the import near the top (after the `sys.path.insert` block so `db` is importable):

```python
from db.trials import claim_trial, finalize_trial
```

Replace `_run_jobs_parallel`:

```python
def _run_jobs_parallel(
    jobs: list[dict], workers: int, label: str, worker_fn=None,
) -> list[dict]:
    """Run jobs in parallel via multiprocessing.Pool. Progress on stderr.

    Trial registration (claim-then-execute) happens in THIS parent process,
    never in the pool child: child crashes leave a 'pending' row that still
    counts toward N. pool.map preserves order, so results[i] <-> jobs[i] <->
    trial_ids[i]. Only cell workers that run calculate_metrics produce trials;
    arithmetic baselines (btc_bh / hubrich) are gated out.
    """
    if not jobs:
        return []
    worker_fn = worker_fn or _process_regime_allocation_cell

    produces_trials = worker_fn in (
        _process_regime_allocation_cell,
        _process_lrc_archived_baseline_cell,
    )
    trial_ids: list[int | None] = [None] * len(jobs)
    if produces_trials:
        for i, job in enumerate(jobs):
            combo = {k: job[k] for k in ("symbol", "sub_window", "vol_target") if k in job}
            trial_ids[i] = claim_trial(
                source="regime_allocation_sweep",
                symbol=job.get("symbol"),
                combo=combo,
                window_label=str(job.get("sub_window") or job.get("window") or ""),
            )

    sys.stderr.write(
        f"[regime_allocation_sweep] {label}: {len(jobs)} jobs × {workers} workers...\n"
    )
    t0 = time.monotonic()
    with Pool(workers) as pool:
        results = pool.map(worker_fn, jobs)
    elapsed = time.monotonic() - t0
    sys.stderr.write(
        f"[regime_allocation_sweep] {label}: completed in {elapsed:.1f}s\n"
    )

    if produces_trials:
        for tid, res in zip(trial_ids, results):
            if tid is None:
                continue
            err = res.get("error") if isinstance(res, dict) else None
            if err:
                finalize_trial(tid, status="failed", error=str(err))
            else:
                finalize_trial(
                    tid, status="ok",
                    metrics=res if isinstance(res, dict) else None,
                )

    _emit_worker_error_summary(results)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_regime_allocation_sweep.py -v`
Expected: PASS (existing tests + 2 new). If an existing test calls `_run_jobs_parallel` with the real `Pool` and a real worker, confirm `claim_trial`/`finalize_trial` are monkeypatched there or that the real `signals.db` write is acceptable in that test (prefer monkeypatching).

- [ ] **Step 5: Commit**

```bash
git add tools/regime_allocation_sweep.py tests/test_regime_allocation_sweep.py
git commit -m "feat(trials): register trials parent-side in regime_allocation_sweep (Advances #278)"
```

---

## Task 8: Holdout-isolation guard + full suite green

**Files:**
- Verify: `tests/test_holdout_isolation.py` (no change expected)

- [ ] **Step 1: Confirm the registry does not touch the holdout**

Run: `python -m pytest tests/test_holdout_isolation.py -v`
Expected: PASS (15/15). `db/trials.py` does NOT import `open_holdout` and writes only to `signals.db`, so no allow-list edit is required. If this test fails, STOP — something wired a sweep against holdout frames, which is a Non-Negotiable #2/#3 violation.

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (pre-existing orthogonal flakes per `.mex/context/ci-discipline.md` may appear; the Windows-local `test_load_proxy_from_env` flake in `test_http_reexport.py` is known and does NOT fail CI Linux).

- [ ] **Step 3: Commit (if any test fixtures needed touch-ups)**

```bash
git add -A
git commit -m "test(trials): full-suite green + holdout isolation intact (Advances #278)"
```

---

## Task 9: GROW — write the `.mex` pattern

**Files:**
- Create: `.mex/patterns/registering-a-trial.md`
- Modify: `.mex/patterns/INDEX.md`

- [ ] **Step 1: Write the pattern**

Create `.mex/patterns/registering-a-trial.md`:

```markdown
# Registering a trial

## Contract

Every backtest run inside an EXPLORATORY parameter/window sweep must be recorded
in the `trials` table via `db/trials.py`, using claim-then-execute:

1. `trial_id = claim_trial(source=..., combo=..., symbol=..., window_label=...)`
   BEFORE invoking the simulator. This INSERTs a `status='pending'` row.
2. Run the simulator + `calculate_metrics`.
3. `finalize_trial(trial_id, status='ok', metrics=...)` on success, or
   `finalize_trial(trial_id, status='failed', error=...)` on no-trades / error.
4. On an uncaught exception, finalize as `failed` then re-raise. A process that
   dies before finalize leaves an orphan `pending` row — that row IS the
   evidence the trial existed, preserving the deflation denominator N.

## Why claim-then-execute (not register-after)

A process that crashes mid-run cannot register itself after the fact. Recording
BEFORE the run is the only way a crashed trial still counts toward N. (Halberg
runtime review, #278, 2026-05-29.)

## Concurrency

Writes go through `db.transaction.transaction()` with bounded retry/backoff on
`database is locked`. `signals.db` already has concurrent writers (scanner, API).
A TRANSIENT lock is retried and must NEVER abort a multi-hour sweep. Only
durability exhaustion (backoff spent, disk full, corruption) raises.

For a `multiprocessing.Pool` sweep, the trial write happens in the PARENT
(serial), never in the child worker. `pool.map` preserves order, so
`results[i] <-> jobs[i] <-> trial_ids[i]`.

## Scope

Wired sweeps (exploratory): `auto_tune`, `grid_search_tf`, `optimize_new_tokens`,
`regime_allocation_sweep`. Pre-registered CONFIRMATORY studies (e.g. epic C
`signal_calibration_*`) are recorded with `study_type='confirmatory'` and are
filtered out of the selection-bias N by #278 Part 2.

## Steps

See `db/trials.py::claim_trial` / `finalize_trial`. Add `from db.trials import
claim_trial, finalize_trial` at the orchestrator top; wire at the loop body
(serial) or around `pool.map` (parallel).
```

- [ ] **Step 2: Add to the pattern index**

Add this line to `.mex/patterns/INDEX.md` (in the appropriate section):

```markdown
- [registering-a-trial](registering-a-trial.md) — claim-then-execute trial registry contract for exploratory sweeps (#278).
```

- [ ] **Step 3: Log the event**

```bash
mex log "added pattern: registering-a-trial (#278 Part 1 trial registry)"
```

- [ ] **Step 4: Commit**

```bash
git add .mex/patterns/registering-a-trial.md .mex/patterns/INDEX.md
git commit -m "docs(mex): add registering-a-trial pattern (Advances #278)"
```

---

## Pre-push: adversarial audit (guardrail-critical)

The registry is the N denominator for deflation — a guardrail-critical component.
Per the project's adversarial-audit-before-push pattern: with all commits green
locally, dispatch an independent adversarial audit (separate from the implementer)
on the diff before pushing. Lenses: (1) does a crashed trial truly leave a
countable row? (2) can a transient lock abort a sweep? (3) does the parallel path
ever write from a child? (4) are confirmatory `signal_calibration_*` trials
correctly excluded? Amend on findings, then push and open the PR.

---

## Self-Review (completed during authoring)

1. **Spec coverage:** trial table (Task 1), claim-then-execute (Tasks 1-2), failed/crashed runs count (Task 2 `test_crashed_trial_leaves_pending_row`), abort-loudly only on durability (Task 3), per (combo, window) granularity (window_label in every claim), provenance + study_type columns (Task 1 schema), 4 exploratory sweeps wired (Tasks 4-7), signal_calibration_* excluded (documented, Task 9). N_floor and deflated metrics are Part 2 — out of scope here. ✔
2. **Placeholder scan:** the three verification points are explicit confirmations, not placeholders; all code steps carry real code. ✔
3. **Type consistency:** `claim_trial(...) -> int`; `finalize_trial(trial_id, *, status, metrics=None, error=None)`; metrics key read is `sharpe_ratio` (matches `grid_search_tf.py:151`). Same names used in every wiring task. ✔
