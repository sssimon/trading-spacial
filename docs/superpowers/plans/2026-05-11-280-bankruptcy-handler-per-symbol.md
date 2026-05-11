# #280 — Per-symbol Bankruptcy Handler (backtest simulator) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-symbol bankruptcy handler to `backtest.simulate_strategy` so that, once a symbol's simulated equity collapses below `0.1 × INITIAL_CAPITAL`, the simulator stops opening new positions for that symbol, emits a single first-class `exit_reason="BANKRUPT"` trade record at the breach bar, and `calculate_metrics` treats post-bankruptcy bars as "not traded" (excluded from win-rate / Sharpe / Sortino / trade-count, but reflected in drawdown and total-return).

**Architecture:** Strictly per-symbol, scoped inside `simulate_strategy` (the existing per-symbol simulator). A new module-level constant `BANKRUPTCY_THRESHOLD: Final[float] = 0.1 * INITIAL_CAPITAL` ($1000) defines the breach line. A local sticky flag `_bankrupt` initialized to `False` is set to `True` immediately after either of the two `capital += trade["pnl_usd"]` mutations (closing-side line ~728 and final-bar tail-close line ~1019) when `capital < BANKRUPTCY_THRESHOLD`. The bar loop's "Open position" path short-circuits to `continue` when `_bankrupt is True`. The synthetic BANKRUPT trade is appended to `trades` exactly once (the breach transition). `calculate_metrics` is amended to exclude `exit_reason in ("OPEN", "BANKRUPT")` from win/loss/PF/Sharpe/Sortino/streak/trade-tier aggregates but to leave the equity-curve-derived `max_drawdown` and `net_pnl/total_return_pct` untouched. Portfolio-level bankruptcy handling is explicitly **out of scope** (deferred per #280) — a separate future epic will own that when a portfolio-level simulator lands.

**Tech Stack:** Python 3.x, `pandas`, `pytest`, existing `backtest.py` / `tests/` layout. No new dependencies. No database schema changes. No frontend / API changes.

---

## Why this is needed NOW (context for zero-context agent)

Read these files in order before touching code:

1. **`CLAUDE.md`** — start at "Validation Methodology — Holdout Dataset (epic #246, ticket #247)" and read through "Caveats heredados — A.4 (#250) MUST honor". The 4th caveat ("per-symbol vs portfolio aggregation gap") is the framing for this work.
2. **`data/retune/2026-05-06-pre-holdout/regime_report.md`** (currently untracked in the worktree) — section "Simulation Artifact Caveats (A.4-1.5 Validation)" documents a concrete in-the-wild instance of the bug: in the A.4-1.5 regime threshold sweep, JUPUSDT went bankrupt early under `no_detector`, after which the simulator continued processing trades with `risk_amount = 0` (the `effective_capital = max(0.0, capital)` floor added in A.0.2 / #277 prevents NaN math but keeps the loop running). The result: `no_detector` "won" the raw sum-of-`net_pnl` aggregate while the technically-correct config (`60_40`) lost more nominal dollars because its losses landed BEFORE the bankruptcy bar instead of after. The reviewer had to do an operator override (commit `11b6f2f`) to ship the right winner.
3. **GitHub issue #280** — read the issue body in full. The acceptance criteria there are authoritative; this plan implements them at v1 (per-symbol).

**Why the bug matters for A.4:** A.4-3 holdout evaluation is single-shot. If we run it with #280 unresolved, every interpretation will inherit Bankruptcy Bias and require operator override. That spends the holdout shot on noisy evidence. Shipping #280 first means A.4-1 (ATR re-tune) and A.4-1.5 (regime re-tune) can be re-run cleanly, and A.4-3 produces a defensible report on first contact.

**What this is NOT:** This is not the portfolio-level pooled-capital fix referenced in CLAUDE.md caveat #4. K=10 cap (PR #309) bounded per-trade overshoot; this PR bounds simulator behavior after a per-symbol equity collapse. Pooled-portfolio capital management is a separate epic, deferred.

---

## Pre-execution constraints (non-negotiable)

- **NO read of `data/holdout/`** anywhere in this PR. The bankruptcy handler runs inside `simulate_strategy` which reads `data/ohlcv.db` only. If you need to add the module to `HOLDOUT_LEGITIMATE_MODULES` in `tests/test_holdout_isolation.py`, you don't — `backtest.py` already lives in the whitelist for unrelated reasons; don't touch the whitelist.
- **NO chmod / monkey-patch / Guard B suppression** at any point.
- **NO `--no-verify`, `--no-gpg-sign`, `push --force`** (use `--force-with-lease` only if absolutely needed — not expected on a fresh branch).
- **NO `gh pr merge --auto`**. Open as draft for review.
- **NO `Closes #N` / `Fixes #N` / `Resolves #N`** in commit body or PR body — use a `## References` section listing `#280` and `#277` only.
- **NO re-running** any A.4 sweep in this PR. Sweep re-runs are downstream consumers, not part of this work. Do not touch `data/retune/`.
- **NO modification** of `_close_position`, the K=10 cap (`MAX_OVERSHOOT_RATIO`), or the `effective_capital = max(0.0, capital)` floor. Those are the existing safety nets; #280 layers on top of them, it does not replace them.
- **NO touching** `strategy/regime.py`, `tools/regime_retune_pre_holdout.py`, `api/`, `frontend/`, `config.json`, `config.defaults.json`. Out of scope.
- The `BANKRUPTCY_THRESHOLD` value (`0.1 * INITIAL_CAPITAL` = $1000 with current `INITIAL_CAPITAL = 10_000.0`) is rule-derived (the 90%-drawdown convention from the issue body); do not tune it against any backtest. If a future PR proposes changing it, that PR must pre-register the change.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backtest.py` | Modify (3 sites: header constants, `simulate_strategy` body, `calculate_metrics`) | Define `BANKRUPTCY_THRESHOLD`; thread `_bankrupt` sticky flag; emit synthetic BANKRUPT trade; gate entry path; filter BANKRUPT from aggregate metrics |
| `tests/test_backtest_bankruptcy.py` | Create | Unit + integration tests for all four acceptance criteria from #280 |
| `CLAUDE.md` | Modify (caveat #4 paragraph) | Append one short paragraph noting that #280 lands per-symbol scope; portfolio-level remains deferred |
| `docs/superpowers/plans/2026-05-11-280-bankruptcy-handler-per-symbol.md` | Create (this file) | Executable plan, committed alongside the work |

**No new modules.** All logic stays inside `backtest.py` next to the capital arithmetic it guards.

---

## Phase 1 — Implementation

### Task 1: Add the threshold constant and helper

**Files:**
- Modify: `backtest.py:80-88` (constants block, just below `RISK_PER_TRADE` / `MAX_OVERSHOOT_RATIO`)

- [ ] **Step 1: Add the constant directly under `MAX_OVERSHOOT_RATIO`**

Open `backtest.py`, locate line 88 (`MAX_OVERSHOOT_RATIO: Final[float] = 10.0`), and add immediately below:

```python
# Per-symbol bankruptcy threshold (#280). Rule-derived 90%-drawdown convention
# from the issue body: once simulated capital falls below 10% of INITIAL_CAPITAL,
# any real account would be force-liquidated and the kill switch would have
# fired in production. In simulation, the existing effective_capital = max(0,
# capital) floor (A.0.2 / #277) prevented NaN math but kept the bar loop
# running — those subsequent zero-risk_amount trades distort aggregate metrics
# (Bankruptcy Bias, demonstrated in data/retune/2026-05-06-pre-holdout
# regime_report.md). This constant + the _bankrupt sticky flag wired below
# halt new entries for the affected symbol. Portfolio-level bankruptcy is
# deferred to its own epic when a portfolio-level simulator lands.
BANKRUPTCY_THRESHOLD: Final[float] = 0.1 * INITIAL_CAPITAL  # $1000 at INITIAL_CAPITAL=10_000
```

- [ ] **Step 2: Commit**

```bash
git add backtest.py
git commit -m "chore(backtest): add BANKRUPTCY_THRESHOLD constant for #280"
```

---

### Task 2: TDD — bankruptcy is detected exactly when capital crosses the threshold

**Files:**
- Create: `tests/test_backtest_bankruptcy.py`
- Modify (in Task 3): `backtest.py:619` (init), `backtest.py:~728` (closing-side detection), `backtest.py:~1019` (tail-close detection)

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_bankruptcy.py`:

```python
"""Regression tests for the per-symbol bankruptcy handler (#280).

The handler trips when simulate_strategy's `capital` falls below
BANKRUPTCY_THRESHOLD (0.1 × INITIAL_CAPITAL). Once tripped:
  - A synthetic trade with exit_reason="BANKRUPT" is appended exactly once.
  - No new positions open for the rest of the run.
  - Existing open positions can still close naturally (SL/TP/TIME_LIMIT).
  - calculate_metrics excludes BANKRUPT records from win/loss/PF/Sharpe/Sortino.

Layering: this sits on top of the effective_capital = max(0, capital) floor
(A.0.2 / #277) and the K=10 overshoot cap (#309). It does not replace either.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from backtest import (
    BANKRUPTCY_THRESHOLD,
    INITIAL_CAPITAL,
    calculate_metrics,
)


def _trade(entry_h: int, exit_h: int, pnl_usd: float, exit_reason: str = "SL",
           score: int = 4, size_mult: float = 1.0) -> dict:
    base = pd.Timestamp("2024-01-01", tz="UTC")
    return {
        "entry_time": base + pd.Timedelta(hours=entry_h),
        "exit_time": base + pd.Timedelta(hours=exit_h),
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl_usd / 100,
        "exit_reason": exit_reason,
        "direction": "LONG",
        "pnl_pct": pnl_usd / 100,
        "pnl_usd": pnl_usd,
        "overshoot_clamped": False,
        "score": score,
        "size_mult": size_mult,
        "duration_hours": float(exit_h - entry_h),
        "atr_sl_mult_used": 1.0,
        "atr_tp_mult_used": 4.0,
        "atr_be_mult_used": 1.5,
    }


def test_threshold_constant_is_ten_percent_of_initial_capital():
    assert BANKRUPTCY_THRESHOLD == pytest.approx(0.1 * INITIAL_CAPITAL)
    assert BANKRUPTCY_THRESHOLD == pytest.approx(1_000.0)
```

- [ ] **Step 2: Run test to verify it fails (it should pass — sanity check)**

Run: `pytest tests/test_backtest_bankruptcy.py::test_threshold_constant_is_ten_percent_of_initial_capital -v`
Expected: PASS (the constant landed in Task 1).

- [ ] **Step 3: Commit**

```bash
git add tests/test_backtest_bankruptcy.py
git commit -m "test(backtest): scaffold #280 bankruptcy test module"
```

---

### Task 3: TDD — BANKRUPT trade record is emitted exactly once

**Files:**
- Modify: `backtest.py` (add `_bankrupt` flag init at line 619 area; add detection blocks after the two `capital +=` sites)
- Modify: `tests/test_backtest_bankruptcy.py`

- [ ] **Step 1: Add the failing test for BANKRUPT-record emission**

Append to `tests/test_backtest_bankruptcy.py`:

```python
def test_bankrupt_record_is_emitted_when_capital_crosses_threshold():
    """If capital drops below threshold mid-run, exactly one BANKRUPT
    record is appended at the breach bar."""
    from backtest import simulate_strategy

    # Construct a 1H frame guaranteed to fire one losing trade per bar by
    # gap-down candles after a clean LONG signal. The exact data plumbing
    # is heavy (signal generation + indicators), so we instead test the
    # narrower invariant via the synthetic-trades helper below.
    trades = [
        _trade(0, 1, pnl_usd=-2_000),  # capital: 10_000 → 8_000
        _trade(2, 3, pnl_usd=-3_000),  # 8_000 → 5_000
        _trade(4, 5, pnl_usd=-4_500),  # 5_000 → 500  ← BREACH here
        _trade(6, 7, pnl_usd=-100),    # would-be: 500 → 400; must NOT appear
    ]
    # Mock the capital accumulator the way simulate_strategy does it.
    # When #280 ships, simulate_strategy itself will emit the BANKRUPT
    # record + halt entries; this test asserts the contract any reasonable
    # implementation must satisfy.
    from backtest import _emit_bankrupt_if_breached  # to be created

    capital = INITIAL_CAPITAL
    bankrupt = False
    emitted = []
    for t in trades:
        if bankrupt:
            break  # would-be entry skipped — mirror simulate_strategy gate
        capital += t["pnl_usd"]
        rec = _emit_bankrupt_if_breached(capital, t["exit_time"])
        if rec is not None:
            emitted.append(rec)
            bankrupt = True
    assert len(emitted) == 1, f"expected exactly one BANKRUPT record, got {len(emitted)}"
    assert emitted[0]["exit_reason"] == "BANKRUPT"
    assert emitted[0]["pnl_usd"] == 0.0
    assert emitted[0]["pnl_pct"] == 0.0
    # The breach capital should be carried for forensic visibility.
    assert "breach_capital" in emitted[0]
    assert emitted[0]["breach_capital"] == pytest.approx(500.0)
```

- [ ] **Step 2: Run — must FAIL on import (`_emit_bankrupt_if_breached` not defined)**

Run: `pytest tests/test_backtest_bankruptcy.py::test_bankrupt_record_is_emitted_when_capital_crosses_threshold -v`
Expected: FAIL with `ImportError: cannot import name '_emit_bankrupt_if_breached' from 'backtest'`.

- [ ] **Step 3: Add the helper to `backtest.py`**

Locate `backtest.py` line 619 (`capital = INITIAL_CAPITAL`). Above the `for i in range(warmup, len(df1h)):` loop (which begins around line 658), but below the simulator wiring block (after line 650 `_simulator = KillSwitchSimulator(...)`), the file currently has the warmup definition at line 653. We will not change that yet — first add the standalone helper at module scope, near the other module-level helpers like `_close_position`.

Open `backtest.py` and add this function immediately above `def _close_position(` (around line 337):

```python
def _emit_bankrupt_if_breached(capital: float, bar_time) -> dict | None:
    """Return a synthetic BANKRUPT trade record when `capital` falls below
    BANKRUPTCY_THRESHOLD; None otherwise. Stateless — callers own the
    sticky flag that prevents re-emission.

    The record carries a zero pnl payload (the event is a marker, not a
    trade) plus `breach_capital` for forensic visibility. exit_time and
    entry_time both point at the breach bar; duration is zero by design
    (this is an event, not a held position).
    """
    if capital >= BANKRUPTCY_THRESHOLD:
        return None
    return {
        "entry_time": bar_time,
        "exit_time": bar_time,
        "entry_price": 0.0,
        "exit_price": 0.0,
        "exit_reason": "BANKRUPT",
        "direction": "NONE",
        "pnl_pct": 0.0,
        "pnl_usd": 0.0,
        "overshoot_clamped": False,
        "score": 0,
        "size_mult": 0.0,
        "duration_hours": 0.0,
        "atr_sl_mult_used": None,
        "atr_tp_mult_used": None,
        "atr_be_mult_used": None,
        "breach_capital": round(float(capital), 2),
    }
```

- [ ] **Step 4: Re-run — must PASS**

Run: `pytest tests/test_backtest_bankruptcy.py::test_bankrupt_record_is_emitted_when_capital_crosses_threshold -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_bankruptcy.py
git commit -m "feat(backtest): _emit_bankrupt_if_breached helper (#280)"
```

---

### Task 4: Wire the sticky flag and entry gate into `simulate_strategy`

**Files:**
- Modify: `backtest.py` — three sites inside `simulate_strategy`:
  1. Init `_bankrupt = False` near `capital = INITIAL_CAPITAL` (line 619).
  2. Gate the entry path so that when `_bankrupt is True`, the open-position block is skipped.
  3. After both `capital += trade["pnl_usd"]` mutations (line ~728 and line ~1019), call `_emit_bankrupt_if_breached` and append the record + set `_bankrupt = True` on first breach.

- [ ] **Step 1: Add the failing integration test**

Append to `tests/test_backtest_bankruptcy.py`:

```python
def test_simulate_strategy_halts_entries_after_bankruptcy(monkeypatch):
    """End-to-end: when a losing-streak data fixture drives capital below
    threshold, simulate_strategy emits exactly one BANKRUPT record and
    opens no further positions, but the open position at the breach bar
    is allowed to close naturally."""
    from tests._fakes import build_bankruptcy_fixture  # to be added in Step 2
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d, df_fng, df_funding = build_bankruptcy_fixture()
    trades, equity_curve, metrics = simulate_strategy(
        df1h=df1h, df4h=df4h, df5m=df5m, df1d=df1d,
        df_fng=df_fng, df_funding=df_funding,
        symbol="TESTUSDT",
        enable_slippage=False, enable_spread=False, enable_fees=False,
        regime_disabled=True,
        cfg={"symbol_overrides": {}},
    )
    bankrupt_records = [t for t in trades if t["exit_reason"] == "BANKRUPT"]
    assert len(bankrupt_records) == 1, (
        f"expected exactly 1 BANKRUPT record, got {len(bankrupt_records)}: "
        f"{[t['exit_time'] for t in bankrupt_records]}"
    )
    breach_time = bankrupt_records[0]["exit_time"]
    # No new entries after the breach.
    later_entries = [
        t for t in trades
        if t["exit_reason"] not in ("BANKRUPT", "OPEN")
        and t["entry_time"] > breach_time
    ]
    assert later_entries == [], (
        f"simulator opened {len(later_entries)} entries after bankruptcy"
    )
```

- [ ] **Step 2: Add the fixture builder**

Open `tests/_fakes.py`. At the bottom (after the existing helpers), append:

```python
def build_bankruptcy_fixture():
    """Return (df1h, df4h, df5m, df1d, df_fng, df_funding) wired to drive
    a backtest into bankruptcy within a small number of bars. The fixture
    is intentionally minimal — pure losing streaks under a clean LONG-
    biased regime — so it does not depend on signal-generation realism.

    Bars span 2024-01-01 → 2024-01-15 hourly. Price walks downward in
    ~3% steps every 12 bars, which combined with size_mult=1.5 (premium
    score) and ATR-SL on a tight stop produces consecutive SL losses that
    cross BANKRUPTCY_THRESHOLD within the window.
    """
    import numpy as np
    import pandas as pd

    start = pd.Timestamp("2024-01-01", tz="UTC")
    hours = pd.date_range(start, periods=24 * 14, freq="1h", tz="UTC")
    # Monotonic downtrend, 1% per bar — produces clean SL hits.
    close = 100.0 * (0.99 ** np.arange(len(hours)))
    df1h = pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.99,
         "close": close, "volume": np.full(len(hours), 1_000_000.0)},
        index=hours,
    )
    df4h = df1h.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    ).dropna()
    df5m = df1h.resample("5min").ffill()
    df1d = df1h.resample("1d").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    ).dropna()
    df_fng = pd.DataFrame({"value": np.full(len(df1d), 50)}, index=df1d.index)
    df_funding = pd.DataFrame({"funding_rate": np.zeros(len(df1d))}, index=df1d.index)
    return df1h, df4h, df5m, df1d, df_fng, df_funding
```

- [ ] **Step 3: Run — must FAIL (sticky flag + gate not yet wired)**

Run: `pytest tests/test_backtest_bankruptcy.py::test_simulate_strategy_halts_entries_after_bankruptcy -v`
Expected: FAIL — either no BANKRUPT record emitted, or extra entries appear after the breach.

- [ ] **Step 4: Init `_bankrupt = False` in `simulate_strategy`**

In `backtest.py`, immediately below line 619 (`capital = INITIAL_CAPITAL`), add:

```python
    capital = INITIAL_CAPITAL
    _bankrupt = False  # #280: sticky flag — once True, no further entries open
    equity_curve = []
```

- [ ] **Step 5: Wire the closing-side detection (line ~728)**

Locate line 728 (`capital += trade["pnl_usd"]`). This site closes a position from inside the bar loop. Replace this single line with the three-line block below (preserving indentation — this is inside the for-loop):

```python
                capital += trade["pnl_usd"]
                _bk_rec = _emit_bankrupt_if_breached(capital, bar_time)
                if _bk_rec is not None and not _bankrupt:
                    trades.append(_bk_rec)
                    _bankrupt = True
```

- [ ] **Step 6: Wire the tail-close detection (line ~1019)**

Locate line 1019 (`capital += trade["pnl_usd"]`). This site closes the surviving open position after the bar loop has finished. Replace this line with:

```python
        capital += trade["pnl_usd"]
        _bk_rec = _emit_bankrupt_if_breached(capital, trade["exit_time"])
        if _bk_rec is not None and not _bankrupt:
            trades.append(_bk_rec)
            _bankrupt = True
```

- [ ] **Step 7: Gate the entry path**

Locate line 755 (`if position is not None: continue`). Immediately AFTER that block (at the same indentation as the existing `if position is not None:`), add:

```python
        if _bankrupt:
            continue  # #280: no new entries after per-symbol bankruptcy
```

- [ ] **Step 8: Run — must PASS**

Run: `pytest tests/test_backtest_bankruptcy.py::test_simulate_strategy_halts_entries_after_bankruptcy -v`
Expected: PASS.

- [ ] **Step 9: Run the full bankruptcy test module + the existing close-position regression tests**

Run: `pytest tests/test_backtest_bankruptcy.py tests/test_backtest_close_position.py tests/test_backtest_close_position_overshoot_cap.py -v`
Expected: ALL PASS. The existing tests must remain green — the new code only adds behavior past a threshold that those tests never cross.

- [ ] **Step 10: Commit**

```bash
git add backtest.py tests/test_backtest_bankruptcy.py tests/_fakes.py
git commit -m "feat(backtest): per-symbol bankruptcy halt + BANKRUPT trade record (#280)"
```

---

### Task 5: TDD — `calculate_metrics` filters BANKRUPT from aggregate metrics

**Files:**
- Modify: `backtest.py:1028-1140` (`calculate_metrics`)
- Modify: `tests/test_backtest_bankruptcy.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_backtest_bankruptcy.py`:

```python
def test_calculate_metrics_excludes_bankrupt_from_win_pf_sharpe():
    """BANKRUPT records are event markers, not trades. They must not
    contribute to win_rate / profit_factor / Sharpe / Sortino / streaks
    / score-tier breakdowns.

    The equity-curve-derived max_drawdown and net_pnl are computed from
    the equity_curve list and reflect the actual capital path — those
    are unaffected by this filter."""
    trades = [
        _trade(0, 1, pnl_usd=+100, exit_reason="TP"),
        _trade(2, 3, pnl_usd=-50, exit_reason="SL"),
        # Synthetic BANKRUPT event — must be filtered.
        {
            **_trade(4, 4, pnl_usd=0, exit_reason="BANKRUPT", score=0, size_mult=0.0),
            "breach_capital": 500.0,
        },
    ]
    equity_curve = [
        {"time": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=h),
         "equity": eq}
        for h, eq in [(0, 10_000), (1, 10_100), (3, 10_050), (4, 500)]
    ]
    metrics = calculate_metrics(trades, equity_curve)

    # With 1 win + 1 loss (BANKRUPT excluded), win_rate must be 0.5,
    # NOT 1/3 (which would mean BANKRUPT was counted as a loss).
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert metrics["total_trades"] == 2, (
        f"BANKRUPT leaked into total_trades: got {metrics['total_trades']}"
    )
    # max_drawdown still reflects the bankruptcy via equity_curve.
    assert metrics["max_drawdown"] < -90, (
        f"equity curve dropped 95% but max_drawdown reads {metrics['max_drawdown']}"
    )
```

- [ ] **Step 2: Run — must FAIL (BANKRUPT currently counted as a "loss")**

Run: `pytest tests/test_backtest_bankruptcy.py::test_calculate_metrics_excludes_bankrupt_from_win_pf_sharpe -v`
Expected: FAIL — either `total_trades == 3` or `win_rate ≈ 0.333`.

- [ ] **Step 3: Update the `closed` filter in `calculate_metrics`**

In `backtest.py`, locate line 1036:

```python
    closed = df[df["exit_reason"] != "OPEN"]
```

Replace with:

```python
    # #280: BANKRUPT records are event markers (capital fell below
    # BANKRUPTCY_THRESHOLD), not trades. Exclude them from every aggregate
    # that consumes per-trade pnl — win-rate, PF, Sharpe, Sortino, streaks,
    # score-tier breakdowns. The equity_curve still reflects the bankruptcy
    # path so drawdown and total_return_pct are unaffected.
    closed = df[~df["exit_reason"].isin(["OPEN", "BANKRUPT"])]
```

- [ ] **Step 4: Add a `bankruptcy_count` field to the metrics dict for forensic visibility**

In `calculate_metrics`, locate the `clamped_trade_count` assignment near the end of the function (search for `clamped_trade_count`). Above that assignment (or alongside it — same dict-building section), add:

```python
    # #280: surface how many BANKRUPT records this run produced. In a
    # symbol-level call this is 0 or 1; in a portfolio-aggregated metrics
    # call (future work) it could be up to N (one per symbol). Operators
    # consuming the metrics dict use this to decide whether the run is
    # interpretable as strategy edge or as cap-binding behavior.
    bankruptcy_count = int((df["exit_reason"] == "BANKRUPT").sum())
```

Then include `"bankruptcy_count": bankruptcy_count` in the final returned dict (same place `clamped_trade_count` is returned).

- [ ] **Step 5: Update the empty-trades early return to include the new field**

Locate line 1033:

```python
        return {"error": "No trades generated", "clamped_trade_count": 0}
```

Replace with:

```python
        return {
            "error": "No trades generated",
            "clamped_trade_count": 0,
            "bankruptcy_count": 0,
        }
```

- [ ] **Step 6: Re-run — must PASS**

Run: `pytest tests/test_backtest_bankruptcy.py::test_calculate_metrics_excludes_bankrupt_from_win_pf_sharpe -v`
Expected: PASS.

- [ ] **Step 7: Run the full backtest test suite to catch any consumer that depended on BANKRUPT-counted-as-trade**

Run: `pytest tests/test_backtest_close_position.py tests/test_backtest_close_position_overshoot_cap.py tests/test_backtest_bankruptcy.py tests/test_backtest_phantom_profit_guard.py tests/test_backtest_costs.py tests/test_backtest_with_costs.py tests/test_backtest_sizing_cap.py tests/test_backtest_time_limit.py tests/test_backtest_refactor_parity.py -v`
Expected: ALL PASS.

If any pre-existing test fails: STOP, do not "fix" the test by adding BANKRUPT to its expectations. Read the failure carefully. The test may be asserting on the old `clamped_trade_count`-only shape — adding the new `bankruptcy_count` field to the metrics dict is a shape change. If a pre-existing test asserts `metrics == {exact dict}`, that test needs the new field added. That is the only legitimate test edit; any other failure means the new code touched a path it shouldn't have.

- [ ] **Step 8: Commit**

```bash
git add backtest.py tests/test_backtest_bankruptcy.py
git commit -m "feat(backtest): calculate_metrics excludes BANKRUPT from trade aggregates (#280)"
```

---

### Task 6: Smoke — full simulator run completes cleanly on the bankruptcy fixture

**Files:**
- Modify: `tests/test_backtest_bankruptcy.py`

- [ ] **Step 1: Add the smoke test**

Append to `tests/test_backtest_bankruptcy.py`:

```python
def test_simulate_strategy_smoke_bankruptcy_no_errors():
    """End-to-end smoke: simulate_strategy completes without exception
    on the bankruptcy fixture; metrics carry bankruptcy_count >= 1 and
    win_rate is computed only over real trades."""
    from tests._fakes import build_bankruptcy_fixture
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d, df_fng, df_funding = build_bankruptcy_fixture()
    trades, equity_curve, metrics = simulate_strategy(
        df1h=df1h, df4h=df4h, df5m=df5m, df1d=df1d,
        df_fng=df_fng, df_funding=df_funding,
        symbol="TESTUSDT",
        enable_slippage=False, enable_spread=False, enable_fees=False,
        regime_disabled=True,
        cfg={"symbol_overrides": {}},
    )
    assert isinstance(metrics, dict)
    assert metrics.get("bankruptcy_count", 0) >= 1
    if metrics.get("total_trades", 0) > 0:
        assert 0.0 <= metrics["win_rate"] <= 1.0
```

- [ ] **Step 2: Run — must PASS**

Run: `pytest tests/test_backtest_bankruptcy.py::test_simulate_strategy_smoke_bankruptcy_no_errors -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_backtest_bankruptcy.py
git commit -m "test(backtest): smoke test for bankruptcy handler end-to-end (#280)"
```

---

## Phase 2 — Documentation + PR

### Task 7: Update CLAUDE.md caveat #4 to note partial mitigation

**Files:**
- Modify: `CLAUDE.md` (the "4. Per-symbol vs portfolio aggregation gap" paragraph under "Caveats heredados — A.4 (#250) MUST honor")

- [ ] **Step 1: Locate the existing caveat #4 block**

Open `CLAUDE.md`. Find the paragraph beginning with:

```
4. **Per-symbol vs portfolio aggregation gap.** The backtest simulator computes `sum(net_pnl)` ...
```

This caveat currently documents two unfixed sub-gaps: (a) per-trade overshoot via amplification, and (b) the silent-continued-fictional-trading after capital exhaustion. PR #309 fixed (a) via K=10. This PR fixes (b) per-symbol.

- [ ] **Step 2: Append one paragraph at the end of caveat #4, just before "Discovered during A.4-1.5 sweep halt..."**

Insert:

```
**Per-symbol bankruptcy halt (PR #XXX, #280) addresses the silent-continued-fictional-trading sub-gap at the per-symbol level.** Once a symbol's simulated equity falls below `BANKRUPTCY_THRESHOLD = 0.1 × INITIAL_CAPITAL` ($1000), `simulate_strategy` emits a single `exit_reason="BANKRUPT"` trade record and halts new entries for that symbol. `calculate_metrics` excludes BANKRUPT records from win-rate / PF / Sharpe / Sortino / streaks / score-tier aggregates; `max_drawdown` and `total_return_pct` are unaffected (they derive from `equity_curve`). The metrics dict carries `bankruptcy_count` for operator visibility. **Portfolio-level bankruptcy handling remains deferred** — a portfolio-level simulator (when it lands) will need its own ticket to pool capital across symbols and decide whether one symbol's bankruptcy should halt the whole portfolio or just that symbol's stream. For A.4-1, A.4-1.5, A.4-2, and A.4-3, the per-symbol fix is sufficient: each symbol's $10K stream is now bounded both per-trade (K=10 cap) and at the bankruptcy floor.
```

Replace `#XXX` with the actual PR number after `gh pr create` (Task 8 will return it).

- [ ] **Step 3: Commit (PR number placeholder is OK at this stage)**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): caveat #4 — per-symbol bankruptcy halt mitigates (b) gap (#280)"
```

---

### Task 8: Commit the plan, push the branch, open the draft PR

**Files:**
- Already-committed across tasks 1–7.
- This plan file (`docs/superpowers/plans/2026-05-11-280-bankruptcy-handler-per-symbol.md`) must also be committed.

- [ ] **Step 1: Stage the plan file and commit**

```bash
git add docs/superpowers/plans/2026-05-11-280-bankruptcy-handler-per-symbol.md
git commit -m "docs(plans): #280 bankruptcy handler — executable plan"
```

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/280-bankruptcy-handler-per-symbol
```

- [ ] **Step 3: Open the draft PR**

```bash
gh pr create --draft \
  --title "feat(backtest): per-symbol bankruptcy handler" \
  --body "$(cat <<'EOF'
## Summary

- Adds `BANKRUPTCY_THRESHOLD = 0.1 × INITIAL_CAPITAL` and a sticky `_bankrupt` flag inside `simulate_strategy`. Once a symbol's simulated capital falls below the threshold, the simulator emits a single `exit_reason="BANKRUPT"` trade record and halts new entries for the rest of the run.
- `calculate_metrics` excludes BANKRUPT records from per-trade aggregates (win-rate, PF, Sharpe, Sortino, streaks, score-tier breakdowns) and surfaces `bankruptcy_count` for forensic visibility. `max_drawdown` and `total_return_pct` derive from `equity_curve` and are unchanged.
- Per-symbol scope only. Portfolio-level pooled-capital handling remains deferred as called out in the issue.

## Motivation

The Bankruptcy Bias was demonstrated concretely in the A.4-1.5 regime threshold sweep on 2026-05-06: `no_detector` "won" the nominal sum-of-net-PnL aggregate only because JUPUSDT went bankrupt under it, after which the simulator continued processing trades with `risk_amount = 0` (the `effective_capital = max(0, capital)` floor from A.0.2 / #277 prevented NaN math but kept the bar loop running). The reviewer had to operator-override the nominal winner to ship `60_40`. With this PR, the simulator stops processing entries past the bankruptcy floor, so the aggregate trusts itself again.

## Scope decision

Per-symbol bankruptcy handling matches the current per-symbol simulator architecture. Portfolio-level bankruptcy needs its own ticket once a portfolio-level simulator lands — flagged in the issue body and in CLAUDE.md caveat #4 (this PR updates that paragraph).

## Test plan

- [x] `pytest tests/test_backtest_bankruptcy.py -v` — all green
- [x] `pytest tests/test_backtest_close_position.py tests/test_backtest_close_position_overshoot_cap.py tests/test_backtest_phantom_profit_guard.py tests/test_backtest_costs.py tests/test_backtest_with_costs.py tests/test_backtest_sizing_cap.py tests/test_backtest_time_limit.py tests/test_backtest_refactor_parity.py -v` — all green (no regressions in adjacent backtest behavior)
- [x] `pytest tests/test_holdout_isolation.py -v` — Guard B green (no holdout reads added)

## References

- #280 — methodology: bankruptcy handling in backtest simulator
- #277 — A.0.2 (introduced `effective_capital = max(0, capital)` floor this builds on)
- `data/retune/2026-05-06-pre-holdout/regime_report.md` — concrete in-the-wild evidence of Bankruptcy Bias

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Capture the PR number and amend CLAUDE.md if needed**

After `gh pr create` returns the URL, extract the PR number. If the `#XXX` placeholder is still in `CLAUDE.md` from Task 7 Step 2, replace it now with a fresh commit (not amend):

```bash
# Replace #XXX with the real number, e.g., #320
sed -i.bak 's/(PR #XXX, #280)/(PR #320, #280)/' CLAUDE.md && rm CLAUDE.md.bak
git add CLAUDE.md
git commit -m "docs(claude-md): fill PR number for #280 caveat update"
git push
```

- [ ] **Step 5: Report**

Print to the user:
- PR URL
- Test summary (counts: passed / failed)
- Confirmation that no `data/holdout/` paths were read
- Confirmation that no A.4 sweep was re-run in this PR

---

## Self-review checklist (run BEFORE handing back to operator)

- [ ] All eight tasks above are checked off.
- [ ] `BANKRUPTCY_THRESHOLD` value matches the issue body (`0.1 × INITIAL_CAPITAL`); no tuning against any backtest.
- [ ] The sticky flag is initialized once and never reset within `simulate_strategy`.
- [ ] BANKRUPT is emitted exactly once per simulate_strategy call (verified by `test_bankrupt_record_is_emitted_when_capital_crosses_threshold` and the smoke test).
- [ ] No new holdout reads. `pytest tests/test_holdout_isolation.py -v` green.
- [ ] No `Closes #N` / `Fixes #N` / `Resolves #N` in any commit body or PR body. References section only.
- [ ] No `--no-verify` / `--no-gpg-sign` / `push --force` used.
- [ ] No A.4 sweep artefacts under `data/retune/` were touched in this branch.
- [ ] CLAUDE.md caveat #4 updated with PR number filled in.
- [ ] Plan file (`docs/superpowers/plans/2026-05-11-280-bankruptcy-handler-per-symbol.md`) committed.

## Out-of-scope (do NOT do in this PR)

- Re-running A.4-1 (ATR) re-tune sweep — separate downstream PR after this lands.
- Re-running A.4-1.5 (regime) re-tune sweep — separate downstream PR after this lands.
- Portfolio-level bankruptcy handling (pooled capital across symbols) — separate future epic, gated by the existence of a portfolio-level simulator.
- Removing the K=10 cap (`MAX_OVERSHOOT_RATIO`) or the `effective_capital = max(0, capital)` floor — both remain in place; this PR layers on top of them.
- Changing `INITIAL_CAPITAL`, `RISK_PER_TRADE`, or any other per-symbol money constant.
- Adding bankruptcy handling to the live (production) code paths (`api/`, `btc_scanner.py`, `strategy/`) — the live kill switch already handles bankruptcy in production; this is a backtest-only fix.
- Promoting any new regime threshold or ATR parameter — promotion PRs are downstream consumers of the re-run sweeps and live on their own branches.
