# Kill Switch v2 — B2: Portfolio-Level Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first feature of Kill Switch v2 Phase 2 — portfolio-level circuit breaker. Computes aggregate portfolio drawdown from closed + open positions, maps to portfolio tier (NORMAL/WARNED/REDUCED/FROZEN) via slider-adjusted thresholds. Runs in SHADOW MODE: writes to decision log with `engine="v2_shadow"` alongside the existing v1 path. No effect on real trades.

**Architecture:** New module `strategy/kill_switch_v2.py` with pure functions: `compute_portfolio_equity_curve(closed_trades, open_positions, capital_base)`, `compute_portfolio_dd(equity_curve)`, `evaluate_portfolio_tier(dd, concurrent_failures, slider, cfg)`. Extend `observability.compute_portfolio_aggregate` to use DD thresholds. Scanner writes a shadow decision per scan via a thin `kill_switch_v2_shadow.evaluate_and_log()` wrapper.

**Tech Stack:** Python 3.12, SQLite (existing `positions` + `kill_switch_decisions` tables), pytest.

---

## Scope audit (before writing plan)

- Phase 1 shipped `kill_switch_decisions` table + observability module + scanner wiring with `engine="v1"`.
- Existing `observability.compute_portfolio_aggregate(per_symbol_tiers, concurrent_alert_threshold=3)` returns `{"tier": "NORMAL"|"WARNED", "concurrent_failures": int}` — concurrent-failure-count only.
- B2 adds DD-based tiers (REDUCED/FROZEN) — the "real" portfolio circuit breaker.
- Config keys for v2 (from §7.1 of the spec):
  - `kill_switch.v2.aggressiveness: 50` (slider, default 50%)
  - `kill_switch.v2.thresholds.portfolio_dd_reduced: {min: -0.08, max: -0.03}`
  - `kill_switch.v2.thresholds.portfolio_dd_frozen: {min: -0.15, max: -0.06}`
  - `kill_switch.v2.concurrent_alert_threshold: 3`
- NOT in scope: per-symbol auto-calibration (B4), velocity triggers (B1), regime-aware (B3), PROBATION tier (B5), auto-calibrator daemon.

---

## File structure

```
strategy/kill_switch_v2.py                     (new)
tests/test_strategy_kill_switch_v2.py          (new)
observability.py                               (modified: extend compute_portfolio_aggregate)
btc_scanner.py                                 (modified: emit v2_shadow decision in scan())
tests/test_observability.py                    (modified: test new signature)
tests/test_scanner.py                          (modified: test v2_shadow emission)
config.defaults.json                           (modified: add v2 thresholds if not already present)
```

**Module responsibilities:**

- `strategy/kill_switch_v2.py` — pure functions: compute portfolio equity/DD, slider-to-threshold interpolation, evaluate portfolio tier. No I/O. Exports `PortfolioTierDecision` dataclass.
- `observability.compute_portfolio_aggregate` — extended to accept DD + config; returns richer tier info. Backward compatible: existing callers that pass only `per_symbol_tiers` still get WARNED/NORMAL as before.
- `btc_scanner.scan()` — after writing v1 decision to log, ALSO write a v2_shadow decision computing portfolio tier from current equity state. Fail-open try/except.

---

## Task 1: `strategy/kill_switch_v2.py` skeleton + slider interpolation

**Files:**
- Create: `strategy/kill_switch_v2.py`
- Create: `tests/test_strategy_kill_switch_v2.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_strategy_kill_switch_v2.py`:

```python
"""Tests for strategy.kill_switch_v2 — portfolio circuit breaker (#187 B2)."""
import pytest


def test_interpolate_threshold_at_slider_0():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=0 → t_min
    assert interpolate_threshold(0, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.08)


def test_interpolate_threshold_at_slider_100():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=100 → t_max (more strict)
    assert interpolate_threshold(100, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.03)


def test_interpolate_threshold_at_slider_50():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=50 → midpoint
    assert interpolate_threshold(50, t_min=-0.08, t_max=-0.03) == pytest.approx(-0.055)


def test_interpolate_threshold_linear():
    from strategy.kill_switch_v2 import interpolate_threshold
    # slider=25 → 25% of the way
    assert interpolate_threshold(25, t_min=0.0, t_max=100.0) == pytest.approx(25.0)


def test_get_thresholds_from_config_default_aggressiveness():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    cfg = {
        "kill_switch": {
            "v2": {
                "aggressiveness": 50,
                "thresholds": {
                    "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
                    "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
                },
            },
        },
    }
    thresholds = get_portfolio_thresholds(cfg)
    assert thresholds["reduced_dd"] == pytest.approx(-0.055)
    assert thresholds["frozen_dd"] == pytest.approx(-0.105)


def test_get_thresholds_from_config_aggressiveness_0():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    cfg = {
        "kill_switch": {
            "v2": {
                "aggressiveness": 0,
                "thresholds": {
                    "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
                    "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
                },
            },
        },
    }
    thresholds = get_portfolio_thresholds(cfg)
    assert thresholds["reduced_dd"] == pytest.approx(-0.08)
    assert thresholds["frozen_dd"] == pytest.approx(-0.15)


def test_get_thresholds_missing_config_returns_defaults():
    from strategy.kill_switch_v2 import get_portfolio_thresholds
    # No v2 config present — should return sensible defaults (slider=50)
    thresholds = get_portfolio_thresholds({})
    # With defaults t_min=-0.08/-0.15 t_max=-0.03/-0.06 and slider=50
    assert thresholds["reduced_dd"] == pytest.approx(-0.055)
    assert thresholds["frozen_dd"] == pytest.approx(-0.105)
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
python -m pytest tests/test_strategy_kill_switch_v2.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `interpolate_threshold` + `get_portfolio_thresholds`**

Create `strategy/kill_switch_v2.py`:

```python
"""Kill switch v2 shadow engine (#187 B2 — portfolio circuit breaker).

Pure functions computing portfolio-level state from equity curves. Runs in
shadow mode during Phase 2: writes to decision log with engine='v2_shadow';
does NOT affect real trading. The actual v1 kill switch continues operating
untouched.

Operator-facing slider (0-100) interpolates thresholds linearly between
tmin (laxo) and tmax (paranoid). Values come from config.defaults.json
under kill_switch.v2.thresholds.
"""
from __future__ import annotations

from typing import Any


# Defaults (match config.defaults.json). Used as fallback when config is incomplete.
_DEFAULT_AGGRESSIVENESS = 50.0
_DEFAULT_DD_REDUCED = {"min": -0.08, "max": -0.03}
_DEFAULT_DD_FROZEN = {"min": -0.15, "max": -0.06}


def interpolate_threshold(slider: float, t_min: float, t_max: float) -> float:
    """Linearly interpolate a threshold value from the slider (0-100).

    slider=0 → t_min (most permissive)
    slider=100 → t_max (most strict)
    """
    slider = max(0.0, min(100.0, float(slider)))
    return t_min + (slider / 100.0) * (t_max - t_min)


def get_portfolio_thresholds(cfg: dict[str, Any]) -> dict[str, float]:
    """Extract the slider-adjusted portfolio DD thresholds from config.

    Returns:
        {"reduced_dd": float, "frozen_dd": float}

    Both values are negative (drawdowns). Falls back to defaults when config
    keys are missing.
    """
    v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
    slider = v2_cfg.get("aggressiveness", _DEFAULT_AGGRESSIVENESS)
    thresholds_cfg = v2_cfg.get("thresholds", {}) or {}

    reduced_range = thresholds_cfg.get("portfolio_dd_reduced") or _DEFAULT_DD_REDUCED
    frozen_range = thresholds_cfg.get("portfolio_dd_frozen") or _DEFAULT_DD_FROZEN

    return {
        "reduced_dd": interpolate_threshold(
            slider, reduced_range["min"], reduced_range["max"]
        ),
        "frozen_dd": interpolate_threshold(
            slider, frozen_range["min"], frozen_range["max"]
        ),
    }
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
python -m pytest tests/test_strategy_kill_switch_v2.py -v
```
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add strategy/kill_switch_v2.py tests/test_strategy_kill_switch_v2.py
git commit -m "feat(kill-switch-v2): slider interpolation + threshold extraction (#187 B2)"
```

---

## Task 2: Compute portfolio equity curve from positions

**Files:**
- Modify: `strategy/kill_switch_v2.py` (add `compute_portfolio_equity_curve`)
- Modify: `tests/test_strategy_kill_switch_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_kill_switch_v2.py`:

```python
def test_compute_portfolio_equity_curve_empty():
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=[],
        capital_base=100_000.0,
        now_price_by_symbol={},
    )
    # Empty history — single snapshot at capital_base
    assert len(curve) == 1
    assert curve[0]["equity"] == pytest.approx(100_000.0)


def test_compute_portfolio_equity_curve_closed_trades_only():
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    # 2 closed trades: +200, -50 → cumulative equity steps
    closed_trades = [
        {"symbol": "BTCUSDT", "exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 200.0},
        {"symbol": "ETHUSDT", "exit_ts": "2026-04-21T14:00:00+00:00", "pnl_usd": -50.0},
    ]
    curve = compute_portfolio_equity_curve(
        closed_trades=closed_trades,
        open_positions=[],
        capital_base=100_000.0,
        now_price_by_symbol={},
    )
    # 3 points: start, after trade 1, after trade 2
    assert len(curve) == 3
    assert curve[0]["equity"] == pytest.approx(100_000.0)
    assert curve[1]["equity"] == pytest.approx(100_200.0)
    assert curve[2]["equity"] == pytest.approx(100_150.0)


def test_compute_portfolio_equity_curve_open_positions_mtm():
    """Open positions add an MTM point at the end using now_price_by_symbol."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    # 1 closed trade (+100), 1 open position entered at $50k, now $51k with 0.01 qty
    closed_trades = [
        {"symbol": "BTCUSDT", "exit_ts": "2026-04-20T12:00:00+00:00", "pnl_usd": 100.0},
    ]
    open_positions = [
        {
            "symbol": "BTCUSDT",
            "entry_price": 50_000.0,
            "qty": 0.01,
            "direction": "LONG",
        },
    ]
    now_prices = {"BTCUSDT": 51_000.0}
    curve = compute_portfolio_equity_curve(
        closed_trades=closed_trades,
        open_positions=open_positions,
        capital_base=100_000.0,
        now_price_by_symbol=now_prices,
    )
    # Start 100k → after trade +100 → +MTM of (51k-50k)*0.01 = 10
    # 3 points: [100_000, 100_100, 100_110]
    assert len(curve) == 3
    assert curve[-1]["equity"] == pytest.approx(100_110.0)


def test_compute_portfolio_equity_curve_short_mtm():
    """SHORT position MTM is (entry - current) * qty."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    open_positions = [
        {
            "symbol": "ETHUSDT",
            "entry_price": 3_000.0,
            "qty": 1.0,
            "direction": "SHORT",
        },
    ]
    now_prices = {"ETHUSDT": 2_950.0}
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=open_positions,
        capital_base=10_000.0,
        now_price_by_symbol=now_prices,
    )
    # SHORT won (+50 per coin × 1 coin = +50)
    # 2 points: start, end
    assert curve[-1]["equity"] == pytest.approx(10_050.0)


def test_compute_portfolio_equity_curve_missing_price_skips_mtm():
    """If now_price_by_symbol is missing the open position's symbol, skip MTM for it."""
    from strategy.kill_switch_v2 import compute_portfolio_equity_curve
    open_positions = [
        {
            "symbol": "UNKNOWNUSDT",
            "entry_price": 1.0,
            "qty": 100.0,
            "direction": "LONG",
        },
    ]
    now_prices = {}  # empty
    curve = compute_portfolio_equity_curve(
        closed_trades=[],
        open_positions=open_positions,
        capital_base=100_000.0,
        now_price_by_symbol=now_prices,
    )
    # Only the start point remains (no MTM applied)
    assert len(curve) == 1
    assert curve[0]["equity"] == pytest.approx(100_000.0)
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
python -m pytest tests/test_strategy_kill_switch_v2.py -v -k "equity_curve"
```
Expected: FAIL — `compute_portfolio_equity_curve` doesn't exist.

- [ ] **Step 3: Implement**

Append to `strategy/kill_switch_v2.py`:

```python
def compute_portfolio_equity_curve(
    closed_trades: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    capital_base: float,
    now_price_by_symbol: dict[str, float],
) -> list[dict[str, Any]]:
    """Compute a portfolio equity curve by applying closed trades + open MTM.

    Args:
        closed_trades: list of {"symbol", "exit_ts", "pnl_usd"} — pnl added cumulatively.
        open_positions: list of {"symbol", "entry_price", "qty", "direction"} — MTM'd at end.
        capital_base: starting equity.
        now_price_by_symbol: current price per symbol, used to MTM open positions.

    Returns:
        List of {"ts": str, "equity": float} points, time-ordered.
    """
    # Sort closed trades by exit_ts ascending
    sorted_closed = sorted(closed_trades, key=lambda t: t.get("exit_ts", ""))

    curve: list[dict[str, Any]] = []

    # Starting point
    start_ts = sorted_closed[0].get("exit_ts") if sorted_closed else "start"
    curve.append({"ts": start_ts, "equity": capital_base})

    # Apply each closed trade
    current_equity = capital_base
    for trade in sorted_closed:
        pnl = float(trade.get("pnl_usd") or 0)
        current_equity += pnl
        curve.append({"ts": trade.get("exit_ts", ""), "equity": current_equity})

    # Add MTM point for open positions
    mtm_total = 0.0
    for pos in open_positions:
        sym = pos.get("symbol")
        if sym not in now_price_by_symbol:
            continue
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("qty") or 0)
        direction = pos.get("direction", "LONG")
        current_price = now_price_by_symbol[sym]
        if direction == "SHORT":
            mtm_total += (entry - current_price) * qty
        else:
            mtm_total += (current_price - entry) * qty

    if mtm_total != 0.0:
        curve.append({"ts": "now_mtm", "equity": current_equity + mtm_total})

    return curve
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
python -m pytest tests/test_strategy_kill_switch_v2.py -v
```
Expected: all tests PASS (previous + 5 new = 12 total).

- [ ] **Step 5: Commit**

```bash
git add strategy/kill_switch_v2.py tests/test_strategy_kill_switch_v2.py
git commit -m "feat(kill-switch-v2): compute_portfolio_equity_curve (#187 B2)"
```

---

## Task 3: Compute portfolio DD + evaluate portfolio tier

**Files:**
- Modify: `strategy/kill_switch_v2.py` (add `compute_portfolio_dd` + `evaluate_portfolio_tier`)
- Modify: `tests/test_strategy_kill_switch_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_kill_switch_v2.py`:

```python
def test_compute_portfolio_dd_from_flat_curve():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 100_000.0},
    ]
    assert compute_portfolio_dd(curve) == pytest.approx(0.0)


def test_compute_portfolio_dd_only_gains():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 105_000.0},
        {"ts": "c", "equity": 110_000.0},
    ]
    assert compute_portfolio_dd(curve) == pytest.approx(0.0)


def test_compute_portfolio_dd_drawdown_from_peak():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    # Peak 110k, valley 99k → DD = (99-110)/110 = -0.10
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 110_000.0},
        {"ts": "c", "equity": 105_000.0},
        {"ts": "d", "equity": 99_000.0},
    ]
    assert compute_portfolio_dd(curve) == pytest.approx(-0.10)


def test_compute_portfolio_dd_current_at_peak_zero_dd():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    # Went down then back up to peak
    curve = [
        {"ts": "a", "equity": 100_000.0},
        {"ts": "b", "equity": 110_000.0},
        {"ts": "c", "equity": 95_000.0},
        {"ts": "d", "equity": 110_000.0},
    ]
    # DD is measured at LAST point vs running peak. Last == peak → 0.
    assert compute_portfolio_dd(curve) == pytest.approx(0.0)


def test_compute_portfolio_dd_empty_curve():
    from strategy.kill_switch_v2 import compute_portfolio_dd
    assert compute_portfolio_dd([]) == 0.0


def test_evaluate_portfolio_tier_normal():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD -0.01 → well above -0.055 reduced threshold → NORMAL
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.01,
        concurrent_failures=0,
        cfg=cfg,
    )
    assert result["tier"] == "NORMAL"
    assert result["dd"] == pytest.approx(-0.01)


def test_evaluate_portfolio_tier_warned_by_concurrent_failures():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "concurrent_alert_threshold": 3,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD safe, but 3 concurrent failures → WARNED
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.01,
        concurrent_failures=3,
        cfg=cfg,
    )
    assert result["tier"] == "WARNED"


def test_evaluate_portfolio_tier_reduced_by_dd():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD -0.07 crosses reduced threshold -0.055 → REDUCED
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.07,
        concurrent_failures=0,
        cfg=cfg,
    )
    assert result["tier"] == "REDUCED"


def test_evaluate_portfolio_tier_frozen_by_dd():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    # DD -0.12 crosses frozen threshold -0.105 → FROZEN
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.12,
        concurrent_failures=0,
        cfg=cfg,
    )
    assert result["tier"] == "FROZEN"


def test_evaluate_portfolio_tier_frozen_takes_priority_over_concurrent():
    from strategy.kill_switch_v2 import evaluate_portfolio_tier
    cfg = {"kill_switch": {"v2": {
        "aggressiveness": 50,
        "concurrent_alert_threshold": 3,
        "thresholds": {
            "portfolio_dd_reduced": {"min": -0.08, "max": -0.03},
            "portfolio_dd_frozen": {"min": -0.15, "max": -0.06},
        },
    }}}
    result = evaluate_portfolio_tier(
        portfolio_dd=-0.15,
        concurrent_failures=5,  # also WARNED eligible
        cfg=cfg,
    )
    # FROZEN is the most severe; takes priority
    assert result["tier"] == "FROZEN"
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
python -m pytest tests/test_strategy_kill_switch_v2.py -v -k "compute_portfolio_dd or evaluate_portfolio_tier"
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `strategy/kill_switch_v2.py`:

```python
def compute_portfolio_dd(equity_curve: list[dict[str, Any]]) -> float:
    """Peak-to-current drawdown % from an equity curve.

    Returns negative value if in drawdown; 0.0 otherwise.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]["equity"]
    current = peak
    for point in equity_curve:
        eq = float(point["equity"])
        if eq > peak:
            peak = eq
        current = eq
    if peak <= 0:
        return 0.0
    return (current - peak) / peak


def evaluate_portfolio_tier(
    portfolio_dd: float,
    concurrent_failures: int,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Compose portfolio tier from DD + concurrent failure count.

    Tier precedence (most severe wins):
        FROZEN > REDUCED > WARNED > NORMAL

    Returns:
        {"tier": str, "dd": float, "concurrent_failures": int,
         "reduced_threshold": float, "frozen_threshold": float}
    """
    thresholds = get_portfolio_thresholds(cfg)
    v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
    concurrent_alert_threshold = int(
        v2_cfg.get("concurrent_alert_threshold", 3)
    )

    # FROZEN check (most severe)
    if portfolio_dd <= thresholds["frozen_dd"]:
        tier = "FROZEN"
    elif portfolio_dd <= thresholds["reduced_dd"]:
        tier = "REDUCED"
    elif concurrent_failures >= concurrent_alert_threshold:
        tier = "WARNED"
    else:
        tier = "NORMAL"

    return {
        "tier": tier,
        "dd": portfolio_dd,
        "concurrent_failures": concurrent_failures,
        "reduced_threshold": thresholds["reduced_dd"],
        "frozen_threshold": thresholds["frozen_dd"],
    }
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
python -m pytest tests/test_strategy_kill_switch_v2.py -v
```
Expected: all 21 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add strategy/kill_switch_v2.py tests/test_strategy_kill_switch_v2.py
git commit -m "feat(kill-switch-v2): compute_portfolio_dd + evaluate_portfolio_tier (#187 B2)"
```

---

## Task 4: Shadow integration — scan() emits v2_shadow decision

**Files:**
- Modify: `btc_scanner.py` — after the v1 decision log, compute + log a v2_shadow decision
- Create: `strategy/kill_switch_v2_shadow.py` — glue that reads DB state + calls pure functions + writes to decision log
- Modify: `tests/test_scanner.py` — add test for v2_shadow emission

- [ ] **Step 1: Write failing test**

Append to `tests/test_scanner.py` (new class):

```python
class TestScanEmitsV2ShadowDecision:
    def test_scan_writes_v2_shadow_row(self, tmp_path, monkeypatch):
        """scan() writes BOTH engine='v1' AND engine='v2_shadow' rows to the log."""
        import btc_api, btc_scanner, observability
        db_path = str(tmp_path / "signals.db")
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        if hasattr(btc_api, "_db_conn"):
            delattr(btc_api, "_db_conn")
        btc_api.init_db()

        # Mock market data fetch (Task 3 A6 pattern)
        import pandas as pd
        import numpy as np
        def fake_klines(*a, **kw):
            n = 250
            idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
            prices = 50_000 + np.cumsum(
                np.random.default_rng(42).standard_normal(n) * 100
            )
            return pd.DataFrame({
                "open": prices, "high": prices * 1.005, "low": prices * 0.995,
                "close": prices, "volume": np.full(n, 1000.0),
                "taker_buy_base": np.full(n, 500.0),
            }, index=idx)

        monkeypatch.setattr(btc_scanner.md, "get_klines", fake_klines)
        try:
            btc_scanner.scan("BTCUSDT")
        except Exception:
            pass  # let exceptions in the indicator path happen — the decision log
                   # writes come BEFORE any possible crash

        v1_rows = observability.query_decisions(symbol="BTCUSDT", engine="v1")
        shadow_rows = observability.query_decisions(symbol="BTCUSDT", engine="v2_shadow")
        assert len(v1_rows) >= 1, "v1 row must still be logged"
        assert len(shadow_rows) >= 1, "v2_shadow row must be logged alongside v1"
        # The shadow row should have a valid portfolio_tier
        assert shadow_rows[0]["portfolio_tier"] in (
            "NORMAL", "WARNED", "REDUCED", "FROZEN",
        )
```

- [ ] **Step 2: Run test — confirm failure**

```bash
python -m pytest tests/test_scanner.py::TestScanEmitsV2ShadowDecision -v
```
Expected: FAIL — no v2_shadow row.

- [ ] **Step 3: Create shadow glue**

Create `strategy/kill_switch_v2_shadow.py`:

```python
"""Shadow-mode glue for kill switch v2 (#187 B2).

Reads state from DB (closed trades + open positions + current prices),
calls the pure functions in strategy.kill_switch_v2, writes a decision
to the observability log with engine='v2_shadow'.

Fail-open: any exception is logged; v1 keeps operating untouched.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("kill_switch_v2_shadow")


def _load_closed_trades() -> list[dict[str, Any]]:
    """Load closed positions from DB for portfolio equity computation."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT symbol, exit_ts, pnl_usd
               FROM positions
               WHERE status = 'closed' AND exit_ts IS NOT NULL
               ORDER BY exit_ts"""
        ).fetchall()
    finally:
        conn.close()
    return [
        {"symbol": r[0], "exit_ts": r[1], "pnl_usd": r[2] or 0.0}
        for r in rows
    ]


def _load_open_positions() -> list[dict[str, Any]]:
    """Load open positions from DB for MTM."""
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT symbol, entry_price, qty, direction
               FROM positions
               WHERE status = 'open'"""
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "symbol": r[0],
            "entry_price": r[1] or 0.0,
            "qty": r[2] or 0.0,
            "direction": r[3] or "LONG",
        }
        for r in rows
    ]


def _count_concurrent_failures() -> int:
    """Count symbols whose latest v1 decision is ALERT/REDUCED/PAUSED/PROBATION."""
    import observability
    state = observability.get_current_state(engine="v1")
    return state["portfolio"]["concurrent_failures"]


def emit_shadow_decision(
    symbol: str,
    cfg: dict[str, Any],
    now_price_by_symbol: dict[str, float] | None = None,
) -> None:
    """Compute portfolio tier, write a v2_shadow row to the decision log.

    Fail-open: any exception is caught and logged.
    """
    from strategy.kill_switch_v2 import (
        compute_portfolio_equity_curve,
        compute_portfolio_dd,
        evaluate_portfolio_tier,
        get_portfolio_thresholds,
    )
    import observability

    try:
        capital_base = float(cfg.get("capital_usd", 100_000.0))
        closed = _load_closed_trades()
        opens = _load_open_positions()
        prices = now_price_by_symbol or {}

        equity_curve = compute_portfolio_equity_curve(
            closed_trades=closed,
            open_positions=opens,
            capital_base=capital_base,
            now_price_by_symbol=prices,
        )
        portfolio_dd = compute_portfolio_dd(equity_curve)
        concurrent = _count_concurrent_failures()

        portfolio = evaluate_portfolio_tier(
            portfolio_dd=portfolio_dd,
            concurrent_failures=concurrent,
            cfg=cfg,
        )

        v2_cfg = (cfg.get("kill_switch", {}) or {}).get("v2", {}) or {}
        slider = float(v2_cfg.get("aggressiveness", 50.0))

        observability.record_decision(
            symbol=symbol,
            engine="v2_shadow",
            per_symbol_tier="NORMAL",  # v2 per-symbol tier lands with B4 auto-cal
            portfolio_tier=portfolio["tier"],
            size_factor=1.0,  # v2 sizing lands later
            skip=False,
            reasons={
                "portfolio_dd": portfolio_dd,
                "reduced_threshold": portfolio["reduced_threshold"],
                "frozen_threshold": portfolio["frozen_threshold"],
                "concurrent_failures": concurrent,
            },
            scan_id=None,
            slider_value=slider,
            velocity_active=False,
        )
    except Exception as e:
        log.warning("kill_switch_v2_shadow.emit_shadow_decision failed for %s: %s", symbol, e)
```

- [ ] **Step 4: Wire into `scan()`**

In `btc_scanner.py`, find the observability log block added in phase 1 (around line 1035-1060). **After** the `observability.record_decision(..., engine="v1", ...)` call, add:

```python
    # Shadow mode for kill switch v2 (#187 B2): compute + log portfolio tier
    # as engine='v2_shadow' alongside the v1 row. No effect on trading.
    try:
        from strategy.kill_switch_v2_shadow import emit_shadow_decision
        current_price = float(df1h["close"].iloc[-1]) if not df1h.empty else 0.0
        emit_shadow_decision(
            symbol=symbol,
            cfg=_cfg if _cfg else {},
            now_price_by_symbol={symbol: current_price},
        )
    except Exception as _shadow_err:
        log.warning("kill_switch_v2_shadow emission failed for %s: %s", symbol, _shadow_err)
```

Fail-open: if shadow emission crashes, v1 log and trading continue.

- [ ] **Step 5: Run test — confirm pass**

```bash
python -m pytest tests/test_scanner.py::TestScanEmitsV2ShadowDecision -v
```
Expected: PASS.

- [ ] **Step 6: Full scanner suite**

```bash
python -m pytest tests/test_scanner.py -v
```
Expected: all scanner tests still PASS (baseline preserved).

- [ ] **Step 7: Commit**

```bash
git add strategy/kill_switch_v2_shadow.py btc_scanner.py tests/test_scanner.py
git commit -m "feat(scanner): emit v2_shadow portfolio tier decision alongside v1 (#187 B2)"
```

---

## Task 5: Update observability dashboard to show portfolio DD

**Files:**
- Modify: `observability.py` — `get_current_state` already returns the latest per-symbol + portfolio aggregate. We only need to expose v2_shadow retrievability.
- Modify: `tests/test_observability.py` — smoke test confirming v2_shadow rows are queryable

- [ ] **Step 1: Write smoke test**

Append to `tests/test_observability.py`:

```python
def test_get_current_state_engine_v2_shadow(tmp_db):
    from observability import record_decision, get_current_state
    # Record a v1 decision and a v2_shadow decision for the same symbol
    record_decision(
        symbol="BTCUSDT", engine="v1",
        per_symbol_tier="NORMAL", portfolio_tier="NORMAL",
        size_factor=1.0, skip=False, reasons={},
        scan_id=None, slider_value=None, velocity_active=False,
    )
    record_decision(
        symbol="BTCUSDT", engine="v2_shadow",
        per_symbol_tier="NORMAL", portfolio_tier="REDUCED",
        size_factor=1.0, skip=False,
        reasons={"portfolio_dd": -0.06},
        scan_id=None, slider_value=50.0, velocity_active=False,
    )

    v1_state = get_current_state(engine="v1")
    shadow_state = get_current_state(engine="v2_shadow")

    assert v1_state["symbols"]["BTCUSDT"]["portfolio_tier"] == "NORMAL"
    assert shadow_state["symbols"]["BTCUSDT"]["portfolio_tier"] == "REDUCED"
```

Run: `python -m pytest tests/test_observability.py::test_get_current_state_engine_v2_shadow -v`
Expected: PASS (feature already works because query_decisions accepts engine filter).

- [ ] **Step 2: Full regression**

```bash
python -m pytest tests/ -q -m "not network"
```
Expected: 676 baseline + ~25 new = ~701 passing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_observability.py
git commit -m "test(observability): smoke test engine=v2_shadow querying (#187 B2)"
```

---

## Task 6: Full regression + PR

- [ ] **Step 1: Python suite**

```bash
python -m pytest tests/ -q -m "not network"
```
Expected: ≥ 701 passing.

- [ ] **Step 2: Frontend sanity**

```bash
cd frontend && npm test && cd ..
```
Expected: 21 passing unchanged.

- [ ] **Step 3: Push**

```bash
git push -u origin feat/kill-switch-v2-b2-portfolio-breaker
```

- [ ] **Step 4: Open PR**

```bash
gh pr create --base main --head feat/kill-switch-v2-b2-portfolio-breaker \
  --title "feat(kill-switch-v2): B2 portfolio-level circuit breaker in shadow mode (#187)" \
  --body "$(cat <<'BODY'
## Summary

First feature of Kill Switch v2 Phase 2 — **portfolio-level circuit breaker** (#187 B2). Computes aggregate portfolio drawdown from positions, maps to portfolio tier (NORMAL/WARNED/REDUCED/FROZEN) via slider-adjusted thresholds. Runs in SHADOW MODE: writes to decision log with \`engine='v2_shadow'\` alongside existing v1 path. **Zero effect on real trades.**

## What ships

- \`strategy/kill_switch_v2.py\` (new) — pure functions: \`interpolate_threshold\`, \`get_portfolio_thresholds\`, \`compute_portfolio_equity_curve\`, \`compute_portfolio_dd\`, \`evaluate_portfolio_tier\`.
- \`strategy/kill_switch_v2_shadow.py\` (new) — DB-glue that reads closed + open positions, calls pure functions, writes v2_shadow decision log row.
- \`btc_scanner.scan()\` — after v1 log write, emits parallel v2_shadow row. Fail-open.
- ~25 new tests (21 pure unit tests + 1 shadow integration + 1 observability smoke).

## Shadow mode behavior

- Every scan cycle now writes TWO rows to \`kill_switch_decisions\`: one \`engine='v1'\` (unchanged) and one \`engine='v2_shadow'\` (new).
- \`v1\` decides what actually happens in production (size factor, skip, etc.).
- \`v2_shadow\` decides what v2 WOULD do — portfolio tier based on real DD. Dashboard can compare side by side.
- Frontend dashboard (KillSwitchDashboard.tsx from #205) already queries via \`engine=\` filter — it can show v2_shadow without any frontend change.

## Threshold math

\`kill_switch.v2.aggressiveness\` (slider 0-100) interpolates thresholds linearly:
- \`portfolio_dd_reduced\`: min=-0.08 (slider=0, laxo) → max=-0.03 (slider=100, paranoid).
- \`portfolio_dd_frozen\`: min=-0.15 (laxo) → max=-0.06 (paranoid).

Default slider=50 → reduced=-0.055, frozen=-0.105.

## Intentionally NOT shipped

- Per-symbol v2 tier (will come with **B4** auto-calibration).
- Velocity triggers (**B1**).
- Regime-aware threshold modulation (**B3**).
- PROBATION tier (**B5**).
- Auto-calibrator daemon.
- Frontend display of v2_shadow vs v1 diff — dashboard already handles v2_shadow via existing engine filter; richer comparison UI lands later.

## Test plan

- [x] Pure unit tests: 21 covering interpolation, equity curve, DD computation, tier mapping, priority (FROZEN > REDUCED > WARNED > NORMAL).
- [x] Shadow integration: \`TestScanEmitsV2ShadowDecision\` verifies scan writes both v1 and v2_shadow rows.
- [x] Observability: smoke test confirms \`engine='v2_shadow'\` is queryable independently.
- [x] Full backend suite: ~701 passing, no regressions.
- [x] Frontend suite: 21 passing unchanged.

## Closes

Addresses #196 (B2 portfolio-level circuit breaker). First feature of Phase 2 of Epic #187.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 5: Watch CI**

```bash
sleep 12 && gh pr checks --watch --interval 15
```
Expected: backend-tests PASS + frontend-typecheck PASS.

---

## Self-review

**Spec coverage (§8.2 of kill switch v2 design spec):**

- ✅ Track `portfolio_equity_peak` and `portfolio_equity_current` — `compute_portfolio_dd` does this from the curve.
- ✅ Trigger REDUCED on `portfolio_dd < portfolio_dd_reduced_threshold` — `evaluate_portfolio_tier`.
- ✅ Trigger FROZEN on `portfolio_dd < portfolio_dd_frozen_threshold` — `evaluate_portfolio_tier`.
- ✅ Trigger WARNED on `count_symbols_in_alert >= concurrent_alert_threshold` — `evaluate_portfolio_tier`.
- ✅ Priority (FROZEN > REDUCED > WARNED > NORMAL) — explicit test + code.
- ⚠️ Recovery (auto when DD mejora con cooldown) — NOT in MVP; tier recomputed every scan so recovery is immediate if the portfolio recovers. Cooldown lands with B5 PROBATION tier work.

**Placeholder scan:** searched. Zero TBD / TODO / "similar to".

**Type consistency:**
- `equity_curve: list[dict]` with `ts`/`equity` keys — consistent across Tasks 2, 3, 5.
- `evaluate_portfolio_tier` return shape — consistent between definition + tests + shadow glue.
- `cfg` dict schema — matches `config.defaults.json` §7.1 of the spec.

**Scope:** B2 only. Does not touch v1 logic, does not change trading, does not modify frontend components. Strictly additive.

**Known follow-ups (NOT this PR):**
- Real portfolio DD computation uses only closed trades + open MTM — doesn't include live-market equity (no fetch of every symbol's current price each scan). Accepted trade-off: the current scan IS computing the price for the scanned symbol; MTM only applies to that one. Full-portfolio MTM on every scan would require price fetch of all open positions' symbols each cycle.
- Equity curve sorting by `exit_ts` string works for ISO format but not mixed formats — all positions come from same DB format, safe.

Plan ready for execution.
