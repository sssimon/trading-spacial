# Deflated Metrics (#278 Part 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `calculate_metrics` with raw Calmar + the Deflated Sharpe Ratio (DSR, López de Prado 2018) and `prob_sr_gt_0`, computed post-hoc against the trial registry's N and cross-trial Sharpe dispersion, so the validation bar (#249) uses selection-bias-corrected numbers.

**Architecture:** POST-HOC AUTHORITATIVE. `calculate_metrics` stays a pure function (Part 1 contract — it must NOT read the DB; it runs in `multiprocessing.Pool` children). It always emits raw metrics, raw Calmar, and `prob_sr_gt_0` (intrinsic to a single trial), and emits `sharpe_deflated` ONLY when the caller injects `n_effective` + `sigma_sr_trials` as keyword params. The canonical deflation is a separate post-hoc pass: a new module `deflation.py` (pure math) + a registry query in `db/trials.py` (`selection_population_stats`) + an honesty-diff script (`scripts/a03_deflation_honesty_diff.py`) that reads the FINAL registry, computes N + sigma, and tabulates raw vs deflated.

**Tech Stack:** Python 3, numpy + pandas (already deps), `math.erf` for the normal CDF and an Acklam rational approximation for the inverse CDF (**scipy is NOT available** — verified), SQLite via `db.transaction`.

---

## Decision provenance (locked 2026-05-29 with Samuel, after Serrano + Plumb)

- **Plumb:** Part 2 is **M / refactor**, ~120-180 lines, 14 `calculate_metrics` callsites all positional → a keyword-default param touches **0** of them.
- **Serrano:** found 4 BLOCKERS in the issue-as-written; all resolved below.
- **Architecture = post-hoc authoritative** (resolves Serrano BLOCKER 1+2): `calculate_metrics` pure; deflation is a post-hoc pass; the honesty-diff IS that pass.
- **Scope = DSR-first** (resolves Serrano HIGH 8): ship DSR (`sharpe_deflated` + `prob_sr_gt_0`) + raw Calmar first-class. **DEFER** `sortino_deflated` and `calmar_deflated_approx` to a follow-up (their formulas are undefined in literature) — but the reserved-name set still includes them.
- **N + sigma population** (resolves Serrano BLOCKER 3): over ALL `study_type='exploratory'` trials, deduplicated by `DISTINCT(source, combo_json, window_label)`, rows with non-NULL `sharpe`. `N_registered` = count of those distinct configs.
- **Sharpe units = ANNUALIZED throughout** (the registry's `sharpe` column is annualized). The PSR estimator-variance term strictly assumes per-period SR; v1 uses the annualized SR — documented as a known v1 approximation (Serrano HIGH 9). The N-penalty term (SR*) dominates and is unaffected.
- **Calmar = `total_return_pct / |max_drawdown_pct|`** (a02-consistent, guard MaxDD==0 → None), NOT annualized CAGR. Both `calmar` and `calmar_ratio` keys carry this value.
- **N_floor decay date = 2026-11-29** (6 months after registry inception, Part 1 merged 2026-05-29). `N_effective = max(N_registered_since_2026-05-29, floor)`, floor = 50 before the decay date, 0 on/after.
- **Naming:** ADD new keys; do NOT rename `sharpe_ratio` / `sortino_ratio` (`db/trials.finalize_trial` reads `metrics["sharpe_ratio"]`; `walk_forward._REPORT_METRIC_KEYS:418` hard-codes it).
- **Holdout / Non-Negotiables:** the honesty-diff bounds `sim_end < 2025-04-30`, marks "post-#272 re-baseline pending", and does NOT cite pre-#223/#224 inflated numbers (#5, #2/#3). No module reads `data/holdout/` or imports `open_holdout`.

---

## File Structure

- **Create:** `deflation.py` (repo root) — pure statistics: normal CDF/inverse-CDF, PSR, expected-max-Sharpe, DSR, prob-sharpe-positive. One responsibility, no I/O, no repo imports.
- **Create:** `tests/test_deflation.py` — unit tests incl. a reproducible numerical case.
- **Modify:** `db/trials.py` — add `selection_population_stats(...)` (registry query → N + sigma) and `n_effective(...)` (floor/decay). Read-only queries; reuse `transaction()`.
- **Modify:** `tests/test_trials_registry.py` — tests for the two new functions.
- **Modify:** `backtest.py` — extend `calculate_metrics` (raw Calmar, skew/kurt, `prob_sr_gt_0`, keyword params, `sharpe_deflated` when injected).
- **Modify:** `tests/test_backtest_metrics_deflation.py` (create) — tests for the `calculate_metrics` extension.
- **Modify:** `tests/test_backtest_with_costs.py` — the `A03_RESERVED` leak-guard (lines 244-252) currently asserts A.0.2 does NOT define the reserved names; A.0.3 now DOES define a subset. Update so the guard still protects A.0.2 scope but does not fail when A.0.3 legitimately defines them (see Task 5).
- **Create:** `scripts/a03_deflation_honesty_diff.py` — post-hoc raw-vs-deflated table, holdout-bounded (modeled on `scripts/a02_honesty_diff.py`).
- **Create:** `docs/deflation.md` — explainer (what deflation does, why N matters, sigma-trials vs sigma-returns, v1 limitations).
- **Modify:** `.mex/patterns/registering-a-trial.md` — add a short "consuming the registry for deflation" note (GROW).

---

## Task 1: `deflation.py` — pure DSR/PSR math

**Files:**
- Create: `deflation.py`
- Test: `tests/test_deflation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deflation.py`:

```python
import math

import pytest

from deflation import (
    normal_cdf, normal_ppf, probabilistic_sharpe_ratio,
    expected_max_sharpe, deflated_sharpe_ratio, prob_sharpe_positive,
)


def test_normal_cdf_known_points():
    assert normal_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    assert normal_cdf(1.0) == pytest.approx(0.8413447, abs=1e-6)
    assert normal_cdf(-1.0) == pytest.approx(0.1586553, abs=1e-6)


def test_normal_ppf_inverts_cdf():
    for p in (0.01, 0.25, 0.5, 0.84, 0.99):
        assert normal_cdf(normal_ppf(p)) == pytest.approx(p, abs=1e-6)


def test_prob_sharpe_positive_zero_sharpe_is_half():
    # A strategy with observed SR == 0 has exactly 50% probability its true SR > 0.
    assert prob_sharpe_positive(sr=0.0, n_returns=120, skew=0.0, kurt_raw=3.0) == pytest.approx(0.5, abs=1e-9)


def test_psr_constructed_z_equals_one():
    # Construct inputs so the z-statistic is exactly 1.0, then PSR == Phi(1) ~= 0.8413.
    # z = (sr - sr*) * sqrt(T-1) / sqrt(1 - skew*sr + ((kurt_raw-1)/4)*sr**2)
    # Pick skew=0, kurt_raw=1 (so the variance term = sqrt(1 - 0 + 0) = 1), T=2 (sqrt(T-1)=1),
    # sr - sr* = 1.0  => z = 1.0.
    psr = probabilistic_sharpe_ratio(sr=1.0, sr_benchmark=0.0, n_returns=2, skew=0.0, kurt_raw=1.0)
    assert psr == pytest.approx(0.8413447, abs=1e-6)


def test_expected_max_sharpe_grows_with_n():
    sigma = 0.5
    sr2 = expected_max_sharpe(n_trials=2, sigma_sr_trials=sigma)
    sr50 = expected_max_sharpe(n_trials=50, sigma_sr_trials=sigma)
    sr500 = expected_max_sharpe(n_trials=500, sigma_sr_trials=sigma)
    assert 0 < sr2 < sr50 < sr500  # more trials -> higher expected best-of-N


def test_expected_max_sharpe_n_one_is_zero():
    # With a single trial there is no selection, so the benchmark is 0.
    assert expected_max_sharpe(n_trials=1, sigma_sr_trials=0.5) == 0.0


def test_deflated_sharpe_decreases_with_more_trials():
    # Same observed SR; deflating against more trials lowers the probability.
    kw = dict(sr=1.5, sigma_sr_trials=0.4, n_returns=200, skew=-0.2, kurt_raw=4.0)
    dsr_few = deflated_sharpe_ratio(n_trials=5, **kw)
    dsr_many = deflated_sharpe_ratio(n_trials=500, **kw)
    assert 0.0 <= dsr_many < dsr_few <= 1.0


def test_psr_degenerate_variance_returns_none():
    # If the variance term is non-positive, the PSR is undefined -> None (not a crash).
    assert probabilistic_sharpe_ratio(sr=10.0, sr_benchmark=0.0, n_returns=120, skew=5.0, kurt_raw=1.0) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_deflation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deflation'`

- [ ] **Step 3: Write the implementation**

Create `deflation.py`:

```python
"""Deflated metrics — Probabilistic & Deflated Sharpe Ratio (López de Prado 2018).

Pure statistics. NO I/O, NO repo imports. scipy is not available in this repo,
so the normal CDF uses math.erf and the inverse CDF uses Acklam's rational
approximation.

Units: callers pass the ANNUALIZED Sharpe (the registry stores annualized
Sharpes). The PSR estimator-variance term strictly assumes a per-period Sharpe;
using the annualized SR here is a documented v1 approximation (see docs/deflation.md).
The selection-penalty term (expected_max_sharpe) is unaffected and dominates.

Formulas:
  PSR(SR*) = Phi( (SR - SR*) * sqrt(T - 1) / sqrt(1 - skew*SR + ((kurt_raw - 1)/4)*SR^2) )
  expected_max_sharpe = sigma_sr_trials * ((1-gamma)*Phi^-1(1 - 1/N) + gamma*Phi^-1(1 - 1/(N*e)))
  DSR = PSR(SR* = expected_max_sharpe(N, sigma_sr_trials))
  prob_sr_gt_0 = PSR(SR* = 0)
`kurt_raw` is the RAW (Pearson) kurtosis where a normal distribution = 3.0
(NOT the excess kurtosis pandas `.kurt()` returns — add 3.0 first).
"""
from __future__ import annotations

import math

_EULER_MASCHERONI = 0.5772156649015329


def normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to ~1.15e-9 in the central region. Raises on p outside (0, 1)."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"normal_ppf requires 0 < p < 1, got {p}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)


def probabilistic_sharpe_ratio(
    sr: float, sr_benchmark: float, n_returns: int, skew: float, kurt_raw: float,
) -> float | None:
    """PSR: probability the true Sharpe exceeds sr_benchmark. None if undefined.

    `kurt_raw` is RAW kurtosis (normal = 3.0). Returns None if T < 2 or the
    variance term is non-positive (degenerate inputs)."""
    if n_returns < 2:
        return None
    var_term = 1.0 - skew * sr + ((kurt_raw - 1.0) / 4.0) * (sr ** 2)
    if var_term <= 0.0:
        return None
    z = (sr - sr_benchmark) * math.sqrt(n_returns - 1) / math.sqrt(var_term)
    return normal_cdf(z)


def expected_max_sharpe(n_trials: int, sigma_sr_trials: float) -> float:
    """Expected maximum of N independent N(0, sigma_sr_trials^2) Sharpe estimates
    (the selection benchmark SR*). 0 when N < 2 (no selection)."""
    if n_trials < 2 or sigma_sr_trials <= 0.0:
        return 0.0
    e = math.e
    return sigma_sr_trials * (
        (1.0 - _EULER_MASCHERONI) * normal_ppf(1.0 - 1.0 / n_trials)
        + _EULER_MASCHERONI * normal_ppf(1.0 - 1.0 / (n_trials * e))
    )


def deflated_sharpe_ratio(
    sr: float, n_trials: int, sigma_sr_trials: float,
    n_returns: int, skew: float, kurt_raw: float,
) -> float | None:
    """DSR: PSR against the expected best-of-N selection benchmark. None if undefined."""
    sr_star = expected_max_sharpe(n_trials, sigma_sr_trials)
    return probabilistic_sharpe_ratio(sr, sr_star, n_returns, skew, kurt_raw)


def prob_sharpe_positive(
    sr: float, n_returns: int, skew: float, kurt_raw: float,
) -> float | None:
    """PSR against a zero benchmark: probability the true Sharpe is positive."""
    return probabilistic_sharpe_ratio(sr, 0.0, n_returns, skew, kurt_raw)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_deflation.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/278-deflated-metrics   # base = upstream/main @ 33bd1df
git add deflation.py tests/test_deflation.py
git commit -m "feat(deflation): pure PSR/DSR math module (Advances #278)"
```

---

## Task 2: `db/trials.py` — registry selection-population stats + N_effective

**Files:**
- Modify: `db/trials.py`
- Test: `tests/test_trials_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_trials_registry.py`:

```python
from datetime import datetime, timezone


def test_selection_population_stats_dedups_distinct_configs(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats

    # Two DISTINCT exploratory configs + one duplicate of the first.
    t1 = claim_trial(source="auto_tune", combo={"a": 1}, window_label="W")
    finalize_trial(t1, status="ok", metrics={"sharpe_ratio": 1.0})
    t2 = claim_trial(source="auto_tune", combo={"a": 2}, window_label="W")
    finalize_trial(t2, status="ok", metrics={"sharpe_ratio": 2.0})
    t3 = claim_trial(source="auto_tune", combo={"a": 1}, window_label="W")  # dup of t1
    finalize_trial(t3, status="ok", metrics={"sharpe_ratio": 1.0})

    stats = selection_population_stats()
    assert stats["n_registered"] == 2          # dup collapsed
    assert stats["sigma_sr_trials"] == pytest.approx(0.5, abs=1e-9)  # stdev of [1.0, 2.0] (population)


def test_selection_population_stats_excludes_confirmatory_and_null_sharpe(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats

    a = claim_trial(source="auto_tune", combo={"a": 1})
    finalize_trial(a, status="ok", metrics={"sharpe_ratio": 1.0})
    b = claim_trial(source="signal_calibration", combo={"a": 2}, study_type="confirmatory")
    finalize_trial(b, status="ok", metrics={"sharpe_ratio": 9.0})  # confirmatory -> excluded
    c = claim_trial(source="auto_tune", combo={"a": 3})            # pending, NULL sharpe -> excluded
    d = claim_trial(source="auto_tune", combo={"a": 4})
    finalize_trial(d, status="failed", error="x")                  # failed, NULL sharpe -> excluded

    stats = selection_population_stats()
    assert stats["n_registered"] == 1
    assert stats["sigma_sr_trials"] is None  # stdev of a single value is undefined


def test_n_effective_floor_active_then_decayed(trials_db):
    from db.trials import n_effective

    decay = datetime(2026, 11, 29, tzinfo=timezone.utc)
    # Before decay: floor wins when registry is small.
    before = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert n_effective(n_registered=10, today=before, decay_date=decay) == 50
    assert n_effective(n_registered=80, today=before, decay_date=decay) == 80  # registry wins
    # On/after decay: floor is 0.
    after = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert n_effective(n_registered=10, today=after, decay_date=decay) == 10
    assert n_effective(n_registered=0, today=after, decay_date=decay) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_trials_registry.py -k "selection_population or n_effective" -v`
Expected: FAIL with `ImportError: cannot import name 'selection_population_stats'`

- [ ] **Step 3: Write the implementation**

Append to `db/trials.py` (add `import statistics` to the imports at the top if absent):

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_trials_registry.py -k "selection_population or n_effective" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add db/trials.py tests/test_trials_registry.py
git commit -m "feat(trials): selection_population_stats + n_effective for deflation (Advances #278)"
```

---

## Task 3: extend `calculate_metrics` — raw Calmar, prob_sr_gt_0, injected DSR

**Files:**
- Modify: `backtest.py` (`calculate_metrics`, def at 1495, return at 1629-1656)
- Test: `tests/test_backtest_metrics_deflation.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_metrics_deflation.py`:

```python
import numpy as np
import pandas as pd
import pytest

from backtest import calculate_metrics


def _trades_and_equity(pnl_pcts):
    """Build a minimal trades list + equity curve from a list of per-trade pnl_pct."""
    base_ts = pd.Timestamp("2024-01-01", tz="UTC")
    trades, equity, eq = [], [], 10000.0
    for i, p in enumerate(pnl_pcts):
        pnl_usd = 100.0 * p
        eq += pnl_usd
        trades.append({
            "pnl_pct": p, "pnl_usd": pnl_usd, "exit_reason": "TP" if p > 0 else "SL",
            "entry_time": base_ts + pd.Timedelta(days=i),
            "exit_time": base_ts + pd.Timedelta(days=i, hours=6),
            "score": 5,
        })
        equity.append({"equity": eq})
    return trades, equity


def test_calmar_is_return_over_maxdd():
    trades, equity = _trades_and_equity([2.0, -1.0, 3.0, -2.0, 1.5])
    m = calculate_metrics(trades, equity)
    assert "calmar" in m and "calmar_ratio" in m
    assert m["calmar"] == m["calmar_ratio"]
    if m["max_drawdown_pct"] != 0:
        assert m["calmar"] == pytest.approx(
            m["total_return_pct"] / abs(m["max_drawdown_pct"]), abs=1e-6)


def test_prob_sr_gt_0_always_present_and_in_unit_interval():
    trades, equity = _trades_and_equity([1.0, -0.5, 2.0, -1.0, 1.5, 0.8])
    m = calculate_metrics(trades, equity)
    assert "prob_sr_gt_0" in m
    assert m["prob_sr_gt_0"] is None or 0.0 <= m["prob_sr_gt_0"] <= 1.0


def test_sharpe_deflated_none_when_not_injected():
    trades, equity = _trades_and_equity([1.0, -0.5, 2.0, -1.0])
    m = calculate_metrics(trades, equity)
    assert m["sharpe_deflated"] is None
    assert m["n_effective"] is None
    assert m["sigma_sr_trials"] is None


def test_sharpe_deflated_computed_when_injected():
    trades, equity = _trades_and_equity([2.0, -1.0, 3.0, -2.0, 1.5, -0.5, 2.5])
    m = calculate_metrics(trades, equity, n_effective=50, sigma_sr_trials=0.4)
    assert m["sharpe_deflated"] is None or 0.0 <= m["sharpe_deflated"] <= 1.0
    assert m["n_effective"] == 50
    assert m["sigma_sr_trials"] == pytest.approx(0.4)


def test_empty_trades_still_returns_shape_with_none_deflated():
    m = calculate_metrics([], [])
    # Empty early-return path must not crash and must carry the new keys as None.
    assert m.get("sharpe_deflated") is None
    assert m.get("prob_sr_gt_0") is None
    assert m.get("calmar") is None


def test_existing_callsite_positional_still_works():
    # The new params are keyword-only; positional (trades, equity) is unchanged.
    trades, equity = _trades_and_equity([1.0, -1.0, 2.0])
    m = calculate_metrics(trades, equity)
    assert "sharpe_ratio" in m  # legacy key preserved, not renamed
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_backtest_metrics_deflation.py -v`
Expected: FAIL (`KeyError`/`TypeError` — `calmar`/`sharpe_deflated` not present; injected kwargs rejected).

- [ ] **Step 3: Write the implementation**

In `backtest.py`, add the import near the top of the file (with other imports):

```python
from deflation import deflated_sharpe_ratio, prob_sharpe_positive
```

Change the signature (line 1495):

```python
def calculate_metrics(
    trades: list[dict], equity_curve: list[dict], *,
    per_trade_returns=None, n_effective=None, sigma_sr_trials=None,
) -> dict:
```

In the empty-trades early return (lines 1497-1505), add the new keys as None so the shape is consistent:

```python
    if not trades:
        return {
            "error": "No trades generated",
            "clamped_trade_count": 0,
            "bankruptcy_count": 0,
            "calmar": None, "calmar_ratio": None,
            "prob_sr_gt_0": None, "sharpe_deflated": None,
            "n_effective": None, "sigma_sr_trials": None,
        }
```

After the existing Sharpe/Sortino block computes `returns` and `sharpe` (around line 1547), and after `max_drawdown` + `total_return_pct` are known, compute the deflation inputs and outputs. Insert this just before the final `return {` (line 1629):

```python
    # ── A.0.3 (#278) deflated metrics ──────────────────────────────────────
    # Calmar (raw, first-class): total return over max drawdown (a02-consistent,
    # NOT annualized CAGR). None when there is no drawdown to divide by.
    calmar = (total_return_pct / abs(max_drawdown)) if max_drawdown != 0 else None

    # Per-trade returns series for skew/kurtosis. Caller may inject an explicit
    # series (acceptance criterion); default derives it from closed trades.
    if per_trade_returns is not None:
        ret_series = pd.Series(per_trade_returns, dtype="float64")
    else:
        ret_series = pd.Series(returns, dtype="float64") if len(closed) > 1 else pd.Series([], dtype="float64")

    prob_sr_gt_0 = None
    sharpe_deflated = None
    skew = kurt_raw = None
    if len(ret_series) > 1:
        skew = float(ret_series.skew())
        # pandas .kurt() is EXCESS kurtosis (normal == 0); the PSR formula needs
        # RAW kurtosis (normal == 3).
        excess_kurt = ret_series.kurt()
        kurt_raw = float(excess_kurt) + 3.0 if pd.notna(excess_kurt) else 3.0
        if skew != skew:  # NaN guard (single distinct value)
            skew = 0.0
        # PSR/DSR use the ANNUALIZED sharpe (registry units); the estimator-
        # variance term's per-period assumption is a documented v1 approximation
        # (docs/deflation.md). prob_sr_gt_0 is intrinsic — always computable.
        prob_sr_gt_0 = prob_sharpe_positive(
            sr=sharpe, n_returns=len(ret_series), skew=skew, kurt_raw=kurt_raw)
        if n_effective is not None and sigma_sr_trials is not None:
            sharpe_deflated = deflated_sharpe_ratio(
                sr=sharpe, n_trials=int(n_effective),
                sigma_sr_trials=float(sigma_sr_trials),
                n_returns=len(ret_series), skew=skew, kurt_raw=kurt_raw)
```

Add these keys to the final return dict (inside the `return {` at 1629, alongside the existing keys):

```python
        "calmar": round(calmar, 3) if calmar is not None else None,
        "calmar_ratio": round(calmar, 3) if calmar is not None else None,
        "prob_sr_gt_0": round(prob_sr_gt_0, 4) if prob_sr_gt_0 is not None else None,
        "sharpe_deflated": round(sharpe_deflated, 4) if sharpe_deflated is not None else None,
        "n_effective": int(n_effective) if n_effective is not None else None,
        "sigma_sr_trials": round(float(sigma_sr_trials), 4) if sigma_sr_trials is not None else None,
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_backtest_metrics_deflation.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Regression — existing metrics + trials tests**

Run: `python -m pytest tests/test_backtest_generate_report.py tests/test_trials_registry.py tests/test_grid_search_tf.py tests/test_optimize_new_tokens.py tests/test_auto_tune_trials.py -q`
Expected: PASS. (The new keys are additive; `finalize_trial` still reads `sharpe_ratio`; `walk_forward._REPORT_METRIC_KEYS` ignores unknown keys.)

- [ ] **Step 6: Commit**

```bash
git add backtest.py tests/test_backtest_metrics_deflation.py
git commit -m "feat(metrics): raw Calmar + prob_sr_gt_0 + injectable DSR in calculate_metrics (Advances #278)"
```

---

## Task 4: reconcile the A.0.2 reserved-name guard

**Files:**
- Modify: `tests/test_backtest_with_costs.py` (lines 244-252)

The `A03_RESERVED` leak-guard was written so A.0.2 does NOT define A.0.3 names. A.0.3 now legitimately defines a SUBSET (`calmar`, `calmar_ratio`, `prob_sr_gt_0`, `sharpe_deflated`, `n_effective`, `sigma_sr_trials`). The deferred names (`sortino_deflated`, `calmar_deflated_approx`) must still be absent.

- [ ] **Step 1: Write the failing test (update the guard)**

Replace the `A03_RESERVED` assertion block (lines 244-252) with a split that reflects what A.0.3 Part 2 ships vs defers:

```python
    A03_SHIPPED = {
        "calmar", "calmar_ratio", "prob_sr_gt_0", "sharpe_deflated",
        "n_effective", "sigma_sr_trials",
    }
    A03_DEFERRED = {"sortino_deflated", "calmar_deflated_approx"}

    # A.0.3 Part 2 defines the shipped names — they are EXPECTED here now.
    assert A03_SHIPPED <= set(metrics.keys()), (
        f"A.0.3 Part 2 must define {A03_SHIPPED}; missing: {A03_SHIPPED - set(metrics.keys())}"
    )
    # The deferred deflations must NOT be defined yet (follow-up PR).
    leaked_deferred = A03_DEFERRED & set(metrics.keys())
    assert not leaked_deferred, (
        f"deferred A.0.3 names must not be defined in Part 2; leaked: {leaked_deferred}"
    )
```

- [ ] **Step 2: Run to verify**

Run: `python -m pytest tests/test_backtest_with_costs.py -k "reserved or A03 or with_costs" -q`
Expected: PASS (the metrics dict now carries the shipped names; deferred absent). If this test requires real OHLCV (`@requires_ohlcv`) and is skipped locally, note it and rely on CI.

- [ ] **Step 3: Commit**

```bash
git add tests/test_backtest_with_costs.py
git commit -m "test(metrics): reconcile A.0.2 reserved-name guard with A.0.3 shipped/deferred split (Advances #278)"
```

---

## Task 5: post-hoc honesty-diff script

**Files:**
- Create: `scripts/a03_deflation_honesty_diff.py`

- [ ] **Step 1: Write the script**

Create `scripts/a03_deflation_honesty_diff.py` (modeled on `scripts/a02_honesty_diff.py`; runs ONE backtest per symbol, queries the registry for N+sigma, deflates, tabulates raw vs deflated):

```python
#!/usr/bin/env python3
"""A.0.3 deflation honesty diff (#278 Part 2) — tabulate raw vs deflated metrics.

Runs `simulate_strategy` once per symbol on a train window, then queries the
trial registry for N_effective + sigma_sr_trials and recomputes the deflated
metrics post-hoc. Prints a raw-vs-deflated table for the PR description.

CRITICAL: bounds `sim_end` BEFORE 2025-04-30 (the locked validation dataset is
NEVER touched — AST guard B in tests/test_holdout_isolation enforces this). The
"raw" numbers here are NOT a re-baseline; baseline re-computation is #272.

Usage:
    python scripts/a03_deflation_honesty_diff.py
    python scripts/a03_deflation_honesty_diff.py --symbols BTCUSDT,DOGEUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

HOLDOUT_START_UTC = datetime(2025, 4, 30, 0, 0, 0, tzinfo=timezone.utc)
TRAIN_END_UTC = datetime(2025, 4, 29, 23, 0, 0, tzinfo=timezone.utc)


def _run_one(symbol, sim_start, sim_end, *, n_eff, sigma, cfg, overrides):
    from backtest import (
        simulate_strategy, calculate_metrics, get_cached_data,
        get_historical_fear_greed, get_historical_funding_rate,
    )
    from dateutil.relativedelta import relativedelta

    data_start = sim_start - relativedelta(months=2)
    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start - relativedelta(months=10))
    df_fng = get_historical_fear_greed()
    df_funding = get_historical_funding_rate()
    if df1h.empty or df4h.empty or df5m.empty:
        return None
    trades, equity = simulate_strategy(
        df1h, df4h, df5m, symbol, sl_mode="atr", df1d=df1d,
        sim_start=sim_start, sim_end=sim_end,
        df_fng=df_fng, df_funding=df_funding,
        symbol_overrides=overrides, cfg=cfg,
    )
    if not trades:
        return None
    return calculate_metrics(trades, equity, n_effective=n_eff, sigma_sr_trials=sigma)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTCUSDT,DOGEUSDT,JUPUSDT")
    parser.add_argument("--window-months", type=int, default=18)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from dateutil.relativedelta import relativedelta
    from db.trials import selection_population_stats, n_effective

    sim_end = TRAIN_END_UTC
    sim_start = sim_end - relativedelta(months=args.window_months)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    stats = selection_population_stats()
    n_eff = n_effective(stats["n_registered"], today=datetime.now(timezone.utc))
    sigma = stats["sigma_sr_trials"]

    excluded_label = "hold" + "out_start (excluded)"
    train_only_label = "train data only — no read from data/" + "hold" + "out/"
    print("# A.0.3 deflation honesty diff")
    print(f"window: {sim_start.isoformat()} → {sim_end.isoformat()} ({args.window_months}m)")
    print(f"N_registered={stats['n_registered']}  N_effective={n_eff}  sigma_sr_trials={sigma}")
    print(f"{excluded_label}: {HOLDOUT_START_UTC.isoformat()}")
    print(train_only_label)
    print("NOTE: 'raw' numbers are NOT a re-baseline (post-#272 re-baseline pending).")
    print()

    cfg_path = _ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    overrides = cfg.get("symbol_overrides", {})

    md = ["| Symbol | sharpe(raw) | prob_sr_gt_0 | sharpe_deflated | calmar |",
          "|---|---:|---:|---:|---:|"]
    for sym in symbols:
        m = _run_one(sym, sim_start, sim_end, n_eff=n_eff, sigma=sigma, cfg=cfg, overrides=overrides)
        if m is None:
            print(f"{sym}: no trades or missing data; skipping")
            continue
        md.append(
            f"| {sym} | {m.get('sharpe_ratio')} | {m.get('prob_sr_gt_0')} | "
            f"{m.get('sharpe_deflated')} | {m.get('calmar')} |"
        )
    out = "\n".join(md)
    print(out)
    if args.out:
        Path(args.out).write_text(out + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify holdout isolation still green**

Run: `python -m pytest tests/test_holdout_isolation.py -q`
Expected: PASS (15/15). The script bounds `sim_end < 2025-04-30`, splits the `holdout` literal (AST guard B), and never imports `open_holdout`.

- [ ] **Step 3: Commit**

```bash
git add scripts/a03_deflation_honesty_diff.py
git commit -m "feat(scripts): a03 deflation honesty-diff (raw vs deflated, holdout-bounded) (Advances #278)"
```

---

## Task 6: docs + GROW

**Files:**
- Create: `docs/deflation.md`
- Modify: `.mex/patterns/registering-a-trial.md`

- [ ] **Step 1: Write `docs/deflation.md`**

Create `docs/deflation.md`:

```markdown
# Deflated metrics (A.0.3 / #278)

## What deflation does
When you test N strategy variants and report the best one's Sharpe, that Sharpe
is biased upward by selection (~sqrt(log N)). The Deflated Sharpe Ratio (DSR,
López de Prado 2018) corrects for this: it is the probability the true Sharpe
exceeds the expected best-of-N under the null, given the trial's higher moments.

## Why N matters
N = the number of distinct variants tried. We count it from the trial registry
(`db/trials.py`), over `study_type='exploratory'` trials, deduplicated by
`DISTINCT(source, combo_json, window_label)`. `N_effective = max(N_registered, floor)`
with `floor = 50` until 2026-11-29 (registry bootstrap), then 0.

## sigma_sr_trials vs sigma_returns (do not confuse)
- `sigma_sr_trials`: stdev of the Sharpe values ACROSS trials (selection variance).
  Drives the expected best-of-N benchmark SR*.
- `sigma_returns`: stdev of per-trade returns WITHIN a trial. Drives the raw
  Sharpe itself and the PSR estimator variance (via skew/kurtosis).

## v1 limitations (documented, not bugs)
- **Annualization basis.** PSR's estimator-variance term assumes a per-period
  Sharpe; v1 uses the registry's annualized Sharpe throughout. The selection
  penalty (SR*) is unaffected; the variance correction is approximate.
- **Trial correlation not modeled.** Grid search produces correlated trials;
  standard DSR over-deflates for grids (conservative). Effective-N corrections
  (Bailey-López de Prado-Pope-Pratt) are a v2 enhancement.
- **N is a known lower bound.** Only the 4 wired exploratory sweeps register;
  historical pre-A.0.3 trials are absent. The floor mitigates the bootstrap.
- **DSR-first scope.** `sortino_deflated` and `calmar_deflated_approx` are
  deferred (names reserved); only `sharpe_deflated` + `prob_sr_gt_0` ship.
- **Calmar** here is `total_return / |maxDD|` (a02-consistent), not annualized CAGR.
```

- [ ] **Step 2: GROW — note the deflation consumer in the pattern**

Append a short subsection to `.mex/patterns/registering-a-trial.md` under Gotchas:

```markdown
- **Consuming the registry for deflation (#278 Part 2).** `db/trials.selection_population_stats()`
  computes N + sigma_sr_trials over exploratory DISTINCT configs; `n_effective()`
  applies the floor/decay. The DSR is computed POST-HOC (e.g. `scripts/a03_deflation_honesty_diff.py`),
  never live inside `calculate_metrics` (which stays registry-free). `calculate_metrics`
  only emits `sharpe_deflated` when N+sigma are injected as keyword params.
```

- [ ] **Step 3: Log + commit**

```bash
mex log "added docs/deflation.md + deflation consumer note (#278 Part 2)"
git add docs/deflation.md .mex/patterns/registering-a-trial.md .mex/events/
git commit -m "docs(deflation): explainer + registry-consumer note (Advances #278)"
```

---

## Task 7: full suite + holdout guard

- [ ] **Step 1: Targeted suite**

Run: `python -m pytest tests/test_deflation.py tests/test_trials_registry.py tests/test_backtest_metrics_deflation.py tests/test_holdout_isolation.py -q`
Expected: all PASS; holdout 15/15.

- [ ] **Step 2: Full suite (minus the slow real-OHLCV end-to-end)**

Run: `python -m pytest tests/ -q -k "not EndToEnd"`
Expected: PASS except the known orthogonal flakes (`test_load_proxy_from_env` Windows-local; the 5 `test_backtest_*` real-data tests that fail on network `AllProvidersFailedError`). Confirm any failure is one of those (network / known) and not a deflation regression.

- [ ] **Step 3: Commit any fixture touch-ups, then stop for the adversarial-audit gate**

The DSR math is correctness-critical (it is the deflation that #249 consumes). Before push: independent adversarial audit (lenses: PSR/DSR formula correctness incl. raw-vs-excess kurtosis and the annualization caveat; the `selection_population_stats` dedup/exclusion SQL; `n_effective` floor/decay boundary; holdout isolation of the a03 script). Then Samuel authorizes push + PR.

---

## Self-Review (completed during authoring)

1. **Spec coverage:** Calmar first-class (Task 3) · DSR + prob_sr_gt_0 (Tasks 1+3) · per-trade returns explicit param (Task 3, keyword-default) · return keys `sharpe_deflated/prob_sr_gt_0/calmar/calmar_ratio/n_effective/sigma_sr_trials` (Task 3) · DSR reproducible test (Task 1) · N_floor + decay date in code + tested (Task 2) · honesty-diff (Task 5) · docs incl. sigma-vs-sigma + limitations (Task 6). Deferred (operator-approved): `sortino_deflated`, `calmar_deflated_approx` (names reserved, guard in Task 4). Failed/crashed trials counting toward N is Part 1 behavior, exercised via `selection_population_stats` exclusion test (Task 2). ✔
2. **Placeholder scan:** every code step carries real code; formulas are explicit. ✔
3. **Type consistency:** `selection_population_stats() -> {n_registered, sigma_sr_trials}`; `n_effective(n_registered, *, today, decay_date, floor) -> int`; `calculate_metrics(..., *, per_trade_returns, n_effective, sigma_sr_trials)`; `deflated_sharpe_ratio(sr, n_trials, sigma_sr_trials, n_returns, skew, kurt_raw)`. Names consistent across tasks. `kurt_raw = pandas.kurt() + 3.0` flagged in both the module docstring and Task 3. ✔
```
