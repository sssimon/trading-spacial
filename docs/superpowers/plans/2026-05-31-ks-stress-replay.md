# KS Stress-Replay v1-vs-v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a holdout-safe harness that replays the strategy over pre-holdout history and compares kill-switch v1 vs v2 (slider sweep) on a portfolio-level DD/P&L frontier, emitting a pre-registered STRONG/PASS/FAIL verdict.

**Architecture:** Approach C (hybrid two-pass). Pass 1 reuses `backtest.simulate_strategy(apply_kill_switch=False)` per symbol to generate a base trade stream. Pass 2 merges all symbols chronologically and replays them through a common-interface overlay (none/v1/v2@slider) that maintains shared portfolio equity, so v2's cross-symbol portfolio breaker is exercised. Pass 3 computes metrics + gate.

**Tech Stack:** Python 3, pandas, sqlite3, pytest. Reuses `backtest.py`, `backtest_kill_switch.KillSwitchSimulator` (v1), `strategy.kill_switch_v2_simulator.V2KillSwitchSimulator` (v2), and the loader patterns in `tools/regime_retune_pre_holdout.py`.

**Spec:** `docs/superpowers/specs/es/2026-05-31-ks-stress-replay-v1-vs-v2-design.md`

---

## Reference facts (verified against source 2026-05-31)

- `simulate_strategy(...)` returns `(trades, equity)`. Each `trade` is a dict with keys including: `entry_time` (datetime), `exit_time` (datetime), `exit_reason` (str, e.g. `"SL"`, `"TP"`, `"BANKRUPT"`), `pnl_usd` (float), `size_mult` (float), `direction` (str). Timestamps are **datetime objects, not ISO strings**.
- `KillSwitchSimulator(cfg)` (v1): `get_tier(symbol) -> str`; `on_trade_close(symbol, exit_ts_iso: str, pnl_usd: float, now: datetime) -> str`. Tiers: NORMAL/ALERT/REDUCED/PAUSED.
- `V2KillSwitchSimulator(cfg, regime_score=None, capital_base=1000.0)`: `should_skip_or_reduce(symbol, entry_ts: str) -> (skip: bool, size_factor: float)`; `on_trade_close(symbol, exit_ts: str, pnl_usd: float, exit_reason: str) -> None`. Reads slider from `cfg["kill_switch"]["v2"]["aggressiveness"]`.
- Loader helpers to mirror (do NOT import private names; re-implement or import the module): `tools/regime_retune_pre_holdout.py::_slice_below_cutoff`, `_load_frames`, `_load_config`.
- Holdout starts `2025-04-30T00:00:00Z`; cutoff for this harness is `2025-04-30T00:00:00Z` exclusive (i.e., bars strictly `< cutoff`). Curated symbols: BTCUSDT, ETHUSDT, ADAUSDT, AVAXUSDT, DOGEUSDT, UNIUSDT, XLMUSDT, PENDLEUSDT, JUPUSDT, RUNEUSDT.
- Portfolio capital base is identical across all engines, so its absolute value cancels in the relative v1-vs-v2 comparison. Default `1000.0` (matches V2 sim default).
- v2 regime adjustment is applied once at simulator construction; this replay passes `regime_score=None` (NEUTRAL). Per-bar regime would require Approach B — documented simplification.

---

## Prerequisites (verify BEFORE Task 1)

- **`config.json` with `symbol_overrides` at repo root.** `generate_base_stream` →
  `tools.regime_retune_pre_holdout._load_config()` hard-errors if `config.json` is
  missing or has empty `symbol_overrides`. This workspace currently has only
  `config.defaults.json` (verified 2026-05-31); the production `config.json` lives on
  the server (`/var/www/trading/config.json`). Obtain it (scp from prod, or whatever the
  canonical source is) and place it at repo root before running Pass 1. The unit tests
  for Tasks 1-5 do NOT need it (they mock or avoid `generate_base_stream`); only the
  Task 7 integration runs do.
- **`data/ohlcv.db` present** (verified present locally, ~462 MB).

## File Structure

- Create `tools/ks_stress_replay/__init__.py` — package marker.
- Create `tools/ks_stress_replay/overlays.py` — common `Overlay` interface + `NoneOverlay`, `V1Overlay`, `V2Overlay` adapters. One responsibility: map each engine to `(skip, size_factor)` decisions + close feedback.
- Create `tools/ks_stress_replay/replay.py` — chronological event-loop replay engine. One responsibility: turn a base stream + overlay into a portfolio result.
- Create `tools/ks_stress_replay/base_stream.py` — Pass 1: per-symbol base trade generation + bankruptcy flagging.
- Create `tools/ks_stress_replay/metrics.py` — Pass 3: gate evaluation + frontier.
- Create `tools/ks_stress_replay/run.py` — CLI driver + report writers + holdout assertion.
- Create tests: `tests/test_ks_stress_replay_overlays.py`, `tests/test_ks_stress_replay_replay.py`, `tests/test_ks_stress_replay_metrics.py`, `tests/test_ks_stress_replay_base_stream.py`.

---

## Task 1: Package skeleton + Overlay interface + NoneOverlay

**Files:**
- Create: `tools/ks_stress_replay/__init__.py`
- Create: `tools/ks_stress_replay/overlays.py`
- Test: `tests/test_ks_stress_replay_overlays.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ks_stress_replay_overlays.py
from tools.ks_stress_replay.overlays import NoneOverlay


def test_none_overlay_always_takes_full_size():
    ov = NoneOverlay()
    assert ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00") == (False, 1.0)
    # record_close is a no-op and must not raise
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -50.0, "SL")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ks_stress_replay_overlays.py::test_none_overlay_always_takes_full_size -v`
Expected: FAIL with `ModuleNotFoundError: tools.ks_stress_replay`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/ks_stress_replay/__init__.py
"""Holdout-safe kill-switch v1-vs-v2 stress-replay harness (#187 promotion gate)."""
```

```python
# tools/ks_stress_replay/overlays.py
"""Common-interface overlays mapping each kill-switch engine to per-trade
decisions for the chronological replay engine.

Interface (duck-typed; all overlays implement it):
    decide(symbol: str, entry_ts: str) -> tuple[bool, float]
        Returns (skip, size_factor) for a hypothetical entry at entry_ts (ISO).
    record_close(symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str) -> None
        Feed the realized (already size-scaled) close back to the engine so its
        internal portfolio-DD / tier state evolves.
"""
from __future__ import annotations


class NoneOverlay:
    """No kill switch: every trade taken at full size. The unprotected reference."""

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        return (False, 1.0)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ks_stress_replay_overlays.py::test_none_overlay_always_takes_full_size -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/ks_stress_replay/__init__.py tools/ks_stress_replay/overlays.py tests/test_ks_stress_replay_overlays.py
git commit -m "feat(ks-replay): package skeleton + NoneOverlay reference"
```

---

## Task 2: V1Overlay + V2Overlay adapters

**Files:**
- Modify: `tools/ks_stress_replay/overlays.py`
- Test: `tests/test_ks_stress_replay_overlays.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_ks_stress_replay_overlays.py
from tools.ks_stress_replay.overlays import V1Overlay, V2Overlay


def _v1_cfg():
    # KillSwitchSimulator reads cfg["kill_switch"]; defaults are fine for a
    # fresh symbol (no closed trades => NORMAL => full size).
    return {"kill_switch": {}}


def test_v1_overlay_normal_tier_full_size():
    ov = V1Overlay(_v1_cfg())
    skip, factor = ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00")
    assert skip is False and factor == 1.0
    # feeding a close must not raise (exit_ts is ISO; now derived internally)
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -10.0, "SL")


def test_v2_overlay_fresh_symbol_full_size_and_slider_injected():
    ov = V2Overlay({}, slider=50.0, capital_base=1000.0)
    skip, factor = ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00")
    # Fresh portfolio (no closed trades): DD=0, tier NORMAL => full size.
    assert skip is False and factor == 1.0
    # slider was injected into the cfg the simulator built
    assert ov.sim.cfg_eff["kill_switch"]["v2"]["aggressiveness"] == 50.0


def test_v2_overlay_record_close_accumulates_portfolio_dd():
    ov = V2Overlay({}, slider=50.0, capital_base=1000.0)
    # A large loss should push the simulator's internal portfolio DD negative.
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -200.0, "SL")
    assert ov.sim._current_portfolio_dd() < 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ks_stress_replay_overlays.py -k "v1_overlay or v2_overlay" -v`
Expected: FAIL with `ImportError: cannot import name 'V1Overlay'`

- [ ] **Step 3: Write the implementation**

```python
# append to tools/ks_stress_replay/overlays.py
import copy
from datetime import datetime, timezone

# v1 tier -> size factor, matching production (btc_scanner.py:264).
_V1_TIER_FACTOR = {
    "NORMAL": 1.0, "ALERT": 1.0, "REDUCED": 0.5, "PAUSED": 0.0, "PROBATION": 0.5,
}


def _ensure_aware(ts_iso: str) -> datetime:
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class V1Overlay:
    """Wraps the health-based v1 KillSwitchSimulator behind the common interface."""

    def __init__(self, cfg: dict):
        from backtest_kill_switch import KillSwitchSimulator
        self.sim = KillSwitchSimulator(cfg)

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        tier = self.sim.get_tier(symbol)
        factor = _V1_TIER_FACTOR.get(tier, 1.0)
        return (factor == 0.0, factor)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        self.sim.on_trade_close(symbol, exit_ts, pnl_usd, _ensure_aware(exit_ts))


class V2Overlay:
    """Wraps V2KillSwitchSimulator at a fixed slider behind the common interface."""

    def __init__(
        self, cfg: dict, slider: float, capital_base: float,
        regime_score: float | None = None,
    ):
        from strategy.kill_switch_v2_simulator import V2KillSwitchSimulator
        cfg2 = copy.deepcopy(cfg) if cfg else {}
        cfg2.setdefault("kill_switch", {}).setdefault("v2", {})
        cfg2["kill_switch"]["v2"]["aggressiveness"] = float(slider)
        self.sim = V2KillSwitchSimulator(
            cfg2, regime_score=regime_score, capital_base=capital_base,
        )

    def decide(self, symbol: str, entry_ts: str) -> tuple[bool, float]:
        return self.sim.should_skip_or_reduce(symbol, entry_ts)

    def record_close(
        self, symbol: str, exit_ts: str, pnl_usd: float, exit_reason: str,
    ) -> None:
        self.sim.on_trade_close(symbol, exit_ts, pnl_usd, exit_reason)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ks_stress_replay_overlays.py -v`
Expected: PASS (all overlay tests)

- [ ] **Step 5: Commit**

```bash
git add tools/ks_stress_replay/overlays.py tests/test_ks_stress_replay_overlays.py
git commit -m "feat(ks-replay): v1 + v2 overlay adapters on common interface"
```

---

## Task 3: Chronological replay engine

**Files:**
- Create: `tools/ks_stress_replay/replay.py`
- Test: `tests/test_ks_stress_replay_replay.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ks_stress_replay_replay.py
from datetime import datetime, timezone
from tools.ks_stress_replay.overlays import NoneOverlay
from tools.ks_stress_replay.replay import replay


def _trade(entry, exit_, pnl, reason="TP"):
    return {
        "entry_time": datetime.fromisoformat(entry).replace(tzinfo=timezone.utc),
        "exit_time": datetime.fromisoformat(exit_).replace(tzinfo=timezone.utc),
        "pnl_usd": pnl, "exit_reason": reason,
    }


def test_none_overlay_realizes_all_pnl_and_tracks_dd():
    base = {
        "BTCUSDT": [
            _trade("2022-01-01T00:00:00", "2022-01-02T00:00:00", 100.0),
            _trade("2022-01-03T00:00:00", "2022-01-04T00:00:00", -300.0, "SL"),
        ],
    }
    res = replay(base, NoneOverlay(), capital_base=1000.0)
    assert res["total_pnl"] == -200.0
    assert res["final_equity"] == 800.0
    # peak 1100 after first close, trough 800 => DD = (800-1100)/1100
    assert abs(res["max_dd"] - ((800.0 - 1100.0) / 1100.0)) < 1e-9
    assert res["taken"] == 2 and res["skipped"] == 0


def test_closes_processed_in_timestamp_order_across_symbols():
    # ETH closes between BTC's entry and close => interleaving matters.
    base = {
        "BTCUSDT": [_trade("2022-01-01T00:00:00", "2022-01-10T00:00:00", 50.0)],
        "ETHUSDT": [_trade("2022-01-02T00:00:00", "2022-01-03T00:00:00", -100.0, "SL")],
    }
    res = replay(base, NoneOverlay(), capital_base=1000.0)
    # Equity path: -100 (ETH close on 01-03) then +50 (BTC close on 01-10).
    # Trough at 900 after ETH close => max_dd = (900-1000)/1000 = -0.1
    assert abs(res["max_dd"] - (-0.1)) < 1e-9
    assert res["final_equity"] == 950.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ks_stress_replay_replay.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# tools/ks_stress_replay/replay.py
"""Chronological event-loop replay: base stream + overlay -> portfolio result.

Decisions are made at ENTRY (using only state from trades CLOSED so far);
realized scaled PnL and DD updates happen at CLOSE. Events at the same
timestamp process CLOSE before ENTRY so a fresh decision sees freed capital.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _to_iso(t) -> str:
    if isinstance(t, str):
        dt = datetime.fromisoformat(t)
    else:
        dt = t
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def replay(base_stream: dict, overlay, capital_base: float = 1000.0) -> dict:
    """Replay every symbol's trades chronologically through `overlay`.

    base_stream: dict[symbol -> list[trade]], trade has entry_time, exit_time,
    pnl_usd, exit_reason. Returns dict with max_dd (negative fraction),
    total_pnl, final_equity, taken, skipped, engagements.
    """
    # ord: CLOSE=0, ENTRY=1 so closes settle before same-ts entries.
    events = []
    for symbol, trades in base_stream.items():
        for idx, tr in enumerate(trades):
            key = (symbol, idx)
            events.append((_to_iso(tr["entry_time"]), 1, "ENTRY", symbol, key, tr))
            events.append((_to_iso(tr["exit_time"]), 0, "CLOSE", symbol, key, tr))
    events.sort(key=lambda e: (e[0], e[1]))

    decisions: dict = {}
    equity = peak = float(capital_base)
    max_dd = 0.0
    total_pnl = 0.0
    taken = skipped = engagements = 0

    for ts, _ord, kind, symbol, key, tr in events:
        if kind == "ENTRY":
            skip, size_factor = overlay.decide(symbol, ts)
            decisions[key] = (skip, float(size_factor))
            if skip:
                skipped += 1
            else:
                taken += 1
            if skip or float(size_factor) < 1.0:
                engagements += 1
        else:  # CLOSE
            skip, size_factor = decisions.get(key, (False, 1.0))
            scaled = 0.0 if skip else float(tr["pnl_usd"]) * size_factor
            overlay.record_close(
                symbol, ts, scaled, tr.get("exit_reason", "") or "",
            )
            equity += scaled
            peak = max(peak, equity)
            dd = (equity - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
            total_pnl += scaled

    return {
        "max_dd": max_dd,
        "total_pnl": total_pnl,
        "final_equity": equity,
        "taken": taken,
        "skipped": skipped,
        "engagements": engagements,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ks_stress_replay_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/ks_stress_replay/replay.py tests/test_ks_stress_replay_replay.py
git commit -m "feat(ks-replay): chronological event-loop portfolio replay engine"
```

---

## Task 4: Base stream generation (Pass 1) + bankruptcy flag + holdout guard

**Files:**
- Create: `tools/ks_stress_replay/base_stream.py`
- Test: `tests/test_ks_stress_replay_base_stream.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ks_stress_replay_base_stream.py
from datetime import datetime, timezone
from unittest.mock import patch
from tools.ks_stress_replay.base_stream import (
    HOLDOUT_CUTOFF, truncate_at_bankruptcy, flag_bankruptcies,
)


def _tr(entry, reason="TP", pnl=10.0):
    return {
        "entry_time": datetime.fromisoformat(entry).replace(tzinfo=timezone.utc),
        "exit_time": datetime.fromisoformat(entry).replace(tzinfo=timezone.utc),
        "pnl_usd": pnl, "exit_reason": reason,
    }


def test_holdout_cutoff_is_holdout_start():
    assert HOLDOUT_CUTOFF == datetime(2025, 4, 30, tzinfo=timezone.utc)


def test_truncate_at_bankruptcy_drops_post_bankrupt_trades():
    trades = [_tr("2022-01-01"), _tr("2022-01-02", "BANKRUPT", 0.0), _tr("2022-01-03")]
    out = truncate_at_bankruptcy(trades)
    assert len(out) == 2
    assert out[-1]["exit_reason"] == "BANKRUPT"


def test_flag_bankruptcies_lists_affected_symbols():
    stream = {
        "BTCUSDT": [_tr("2022-01-01")],
        "JUPUSDT": [_tr("2022-01-01"), _tr("2022-01-02", "BANKRUPT", 0.0)],
    }
    assert flag_bankruptcies(stream) == ["JUPUSDT"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ks_stress_replay_base_stream.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# tools/ks_stress_replay/base_stream.py
"""Pass 1: generate the per-symbol base trade stream (no kill switch) over the
pre-holdout window, with a hard holdout cutoff and bankruptcy flagging.

Reuses the loader + config helpers from tools.regime_retune_pre_holdout and
backtest.simulate_strategy (apply_kill_switch=False).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("ks_stress_replay.base_stream")

# Holdout starts 2025-04-30T00:00:00Z (.mex/context/decisions.md:39). Bars used
# by this harness are strictly < this cutoff. NON-NEGOTIABLE #3.
HOLDOUT_CUTOFF = datetime(2025, 4, 30, tzinfo=timezone.utc)

CURATED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
]


def truncate_at_bankruptcy(trades: list[dict]) -> list[dict]:
    """Drop every trade after the first BANKRUPT marker (inclusive of it).

    The post-#280 simulator emits a BANKRUPT record then keeps processing
    zero-risk trades; including them saturates portfolio DD (caveat #2 in
    .mex/context/decisions.md). Truncating at the first BANKRUPT stops that.
    """
    out: list[dict] = []
    for tr in trades:
        out.append(tr)
        if tr.get("exit_reason") == "BANKRUPT":
            break
    return out


def flag_bankruptcies(stream: dict) -> list[str]:
    """Return sorted symbols whose base stream contains a BANKRUPT trade."""
    flagged = [
        sym for sym, trades in stream.items()
        if any(tr.get("exit_reason") == "BANKRUPT" for tr in trades)
    ]
    return sorted(flagged)


def generate_base_stream(
    symbols: list[str] | None = None,
    cutoff: datetime = HOLDOUT_CUTOFF,
) -> dict:
    """Run simulate_strategy(apply_kill_switch=False) per symbol; return
    dict[symbol -> truncated trade list]. Holdout cutoff enforced.
    """
    if cutoff > HOLDOUT_CUTOFF:
        raise AssertionError(
            f"cutoff {cutoff} exceeds holdout start {HOLDOUT_CUTOFF} — "
            "NON-NEGOTIABLE #3 forbids reading holdout-window frames."
        )
    syms = symbols if symbols is not None else CURATED_SYMBOLS

    from backtest import simulate_strategy
    from tools.regime_retune_pre_holdout import _load_frames, _load_config

    app_config = _load_config()
    overrides = app_config.get("symbol_overrides", {}) or {}

    stream: dict = {}
    for sym in syms:
        frames = _load_frames(sym, cutoff)
        if frames["df1h"].empty or frames["df4h"].empty or frames["df5m"].empty:
            log.warning("empty OHLCV below cutoff for %s — skipping", sym)
            stream[sym] = []
            continue
        trades, _equity = simulate_strategy(
            df1h=frames["df1h"], df4h=frames["df4h"], df5m=frames["df5m"],
            df1d=frames["df1d"], df1d_btc=frames["df1d_btc"],
            df_fng=frames["df_fng"], df_funding=frames["df_funding"],
            symbol=sym, sl_mode="atr", symbol_overrides=overrides,
            cfg=app_config, enable_slippage=True, enable_spread=True,
            enable_fees=True,
        )
        stream[sym] = truncate_at_bankruptcy(trades)
    return stream
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ks_stress_replay_base_stream.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/ks_stress_replay/base_stream.py tests/test_ks_stress_replay_base_stream.py
git commit -m "feat(ks-replay): pass-1 base stream + holdout cutoff + bankruptcy flag"
```

---

## Task 5: Metrics + gate evaluation (Pass 3)

**Files:**
- Create: `tools/ks_stress_replay/metrics.py`
- Test: `tests/test_ks_stress_replay_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ks_stress_replay_metrics.py
from tools.ks_stress_replay.metrics import evaluate_gate


def test_pareto_dominance_is_strong():
    v1 = {"max_dd": -0.30, "total_pnl": 1000.0}
    v2 = {50: {"max_dd": -0.20, "total_pnl": 1100.0}}  # lower DD AND higher PnL
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "STRONG" and slider == 50


def test_dd_first_pass_within_pnl_floor():
    v1 = {"max_dd": -0.30, "total_pnl": 1000.0}
    # DD 0.30 -> 0.22 = 8pp absolute reduction; PnL 950 = 95% of v1 (>=90%).
    v2 = {50: {"max_dd": -0.22, "total_pnl": 950.0}}
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "PASS" and slider == 50


def test_dd_reduction_too_small_or_pnl_floor_broken_is_fail():
    v1 = {"max_dd": -0.30, "total_pnl": 1000.0}
    v2 = {
        30: {"max_dd": -0.29, "total_pnl": 1000.0},   # only 1pp / 3.3% reduction
        70: {"max_dd": -0.10, "total_pnl": 700.0},    # big DD cut but PnL 70% < 90%
    }
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "FAIL" and slider is None


def test_negative_v1_pnl_floor_uses_absolute_band():
    v1 = {"max_dd": -0.40, "total_pnl": -100.0}
    # v2 gives up <=10% of |v1 pnl| (-110 floor) and cuts DD >=3pp.
    v2 = {50: {"max_dd": -0.30, "total_pnl": -105.0}}
    verdict, slider = evaluate_gate(v1, v2)
    assert verdict == "PASS" and slider == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ks_stress_replay_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# tools/ks_stress_replay/metrics.py
"""Pass 3: gate evaluation on the DD/P&L frontier.

Pre-registered gate (spec 2026-05-31, §1, §6):
  STRONG  - some v2 slider Pareto-dominates v1 (|DD| <= and PnL >=).
  PASS    - some v2 slider cuts |DD| by >=3pp absolute OR >=15% relative
            AND keeps PnL within 10% of v1 (absolute band, sign-safe).
  FAIL    - otherwise.
DD values are negative fractions (e.g. -0.30 = 30% drawdown).
"""
from __future__ import annotations

DD_ABS_MARGIN = 0.03    # 3 percentage points (fraction terms)
DD_REL_MARGIN = 0.15    # 15% relative
PNL_FLOOR_FRAC = 0.10   # v2 may give up at most 10% of |v1 PnL|


def evaluate_gate(v1_point: dict, v2_points: dict) -> tuple[str, object]:
    """Return (verdict, winning_slider | None). v2_points: {slider -> point}."""
    v1_dd = abs(v1_point["max_dd"])
    v1_pnl = float(v1_point["total_pnl"])
    pnl_floor = v1_pnl - PNL_FLOOR_FRAC * abs(v1_pnl)

    pass_slider = None
    for slider in sorted(v2_points):
        pt = v2_points[slider]
        v2_dd = abs(pt["max_dd"])
        v2_pnl = float(pt["total_pnl"])

        if v2_dd <= v1_dd and v2_pnl >= v1_pnl:
            return ("STRONG", slider)

        dd_abs_red = v1_dd - v2_dd
        dd_rel_red = (dd_abs_red / v1_dd) if v1_dd > 0 else 0.0
        dd_ok = dd_abs_red >= DD_ABS_MARGIN or dd_rel_red >= DD_REL_MARGIN
        if dd_ok and v2_pnl >= pnl_floor and pass_slider is None:
            pass_slider = slider

    if pass_slider is not None:
        return ("PASS", pass_slider)
    return ("FAIL", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ks_stress_replay_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/ks_stress_replay/metrics.py tests/test_ks_stress_replay_metrics.py
git commit -m "feat(ks-replay): pre-registered DD-first gate evaluation"
```

---

## Task 6: CLI driver + report writers

**Files:**
- Create: `tools/ks_stress_replay/run.py`
- Test: extend `tests/test_ks_stress_replay_metrics.py` with a frontier-assembly test (pure, no I/O)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ks_stress_replay_metrics.py
from tools.ks_stress_replay.run import assemble_frontier


def test_assemble_frontier_groups_points_by_engine():
    results = {
        "none": {"max_dd": -0.40, "total_pnl": 1200.0},
        "v1": {"max_dd": -0.30, "total_pnl": 1000.0},
        "v2@30": {"max_dd": -0.28, "total_pnl": 1010.0},
        "v2@50": {"max_dd": -0.22, "total_pnl": 950.0},
        "v2@70": {"max_dd": -0.15, "total_pnl": 800.0},
    }
    v1_point, v2_points = assemble_frontier(results)
    assert v1_point["max_dd"] == -0.30
    assert set(v2_points) == {30, 50, 70}
    assert v2_points[50]["total_pnl"] == 950.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ks_stress_replay_metrics.py::test_assemble_frontier_groups_points_by_engine -v`
Expected: FAIL with `ModuleNotFoundError: tools.ks_stress_replay.run`

- [ ] **Step 3: Write the implementation**

```python
# tools/ks_stress_replay/run.py
"""CLI driver: Pass 1 (base stream) -> Pass 2 (replay per engine/slider) ->
Pass 3 (gate) -> write report.md + results.json + derivation_audit.md.

Read-only on OHLCV; never touches signals.db or production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

from tools.ks_stress_replay.base_stream import (
    generate_base_stream, flag_bankruptcies, HOLDOUT_CUTOFF, CURATED_SYMBOLS,
)
from tools.ks_stress_replay.overlays import NoneOverlay, V1Overlay, V2Overlay
from tools.ks_stress_replay.replay import replay
from tools.ks_stress_replay.metrics import evaluate_gate

SLIDER_GRID = [30, 50, 70]
CAPITAL_BASE = 1000.0
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def assemble_frontier(results: dict) -> tuple[dict, dict]:
    """Split a flat {engine_label -> point} dict into (v1_point, {slider -> point})."""
    v1_point = results["v1"]
    v2_points = {
        int(label.split("@")[1]): pt
        for label, pt in results.items()
        if label.startswith("v2@")
    }
    return v1_point, v2_points


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        ).decode().strip()
    except Exception:
        return "unknown"


def run(out_dir: str, symbols: list[str], ran_at_iso: str) -> dict:
    base = generate_base_stream(symbols)
    bankruptcies = flag_bankruptcies(base)

    results: dict = {}
    results["none"] = replay(base, NoneOverlay(), CAPITAL_BASE)
    results["v1"] = replay(base, V1Overlay({"kill_switch": {}}), CAPITAL_BASE)
    for slider in SLIDER_GRID:
        ov = V2Overlay({}, slider=float(slider), capital_base=CAPITAL_BASE)
        results[f"v2@{slider}"] = replay(base, ov, CAPITAL_BASE)

    v1_point, v2_points = assemble_frontier(results)
    verdict, winning_slider = evaluate_gate(v1_point, v2_points)

    payload = {
        "ran_at": ran_at_iso,
        "cutoff": HOLDOUT_CUTOFF.isoformat(),
        "capital_base": CAPITAL_BASE,
        "slider_grid": SLIDER_GRID,
        "symbols": symbols,
        "bankruptcies": bankruptcies,
        "results": results,
        "verdict": verdict,
        "winning_slider": winning_slider,
        "code_commit": _git_commit(),
        "ohlcv_sha256": _sha256(os.path.join(REPO_ROOT, "data", "ohlcv.db")),
    }

    os.makedirs(out_dir, exist_ok=True)
    _atomic_write_json(os.path.join(out_dir, "results.json"), payload)
    _atomic_write_text(os.path.join(out_dir, "report.md"), _render_report(payload))
    _atomic_write_text(
        os.path.join(out_dir, "derivation_audit.md"), _render_audit(payload),
    )
    return payload


def _atomic_write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _atomic_write_text(path: str, content: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def _render_report(p: dict) -> str:
    lines = [
        "# KS Stress-Replay v1 vs v2 — report",
        "",
        f"- Ran at: {p['ran_at']}",
        f"- Cutoff (holdout-safe): {p['cutoff']}",
        f"- Verdict: **{p['verdict']}**"
        + (f" (winning slider: {p['winning_slider']})" if p["winning_slider"] is not None else ""),
        f"- Bankruptcies (flagged, post-bankrupt trades truncated): {p['bankruptcies'] or 'none'}",
        "",
        "> Absolute P&L is NOT a baseline (pre-#223/#224 inflation, NON-NEGOTIABLE #5).",
        "> Only the RELATIVE v1-vs-v2 comparison on the shared base stream is the conclusion.",
        "",
        "| engine | max_dd | total_pnl | taken | skipped | engagements |",
        "|---|---|---|---|---|---|",
    ]
    for label in ["none", "v1"] + [f"v2@{s}" for s in p["slider_grid"]]:
        r = p["results"][label]
        lines.append(
            f"| {label} | {r['max_dd']:.4f} | {r['total_pnl']:.2f} | "
            f"{r['taken']} | {r['skipped']} | {r['engagements']} |"
        )
    return "\n".join(lines) + "\n"


def _render_audit(p: dict) -> str:
    return (
        "# KS Stress-Replay — derivation audit\n\n"
        f"- ran_at: {p['ran_at']}\n"
        f"- cutoff: {p['cutoff']} (bars strictly < cutoff; NON-NEGOTIABLE #3)\n"
        f"- capital_base: {p['capital_base']} (identical across engines; cancels in relative comparison)\n"
        f"- slider_grid: {p['slider_grid']}\n"
        f"- symbols: {p['symbols']}\n"
        f"- bankruptcies: {p['bankruptcies']}\n"
        f"- code_commit: {p['code_commit']}\n"
        f"- ohlcv_sha256: {p['ohlcv_sha256']}\n"
        f"- verdict: {p['verdict']}; winning_slider: {p['winning_slider']}\n\n"
        "Gate (pre-registered): STRONG=Pareto dominance; "
        "PASS=|DD| cut >=3pp OR >=15% rel AND PnL within 10% band of v1; else FAIL.\n"
        "regime_score=None (NEUTRAL) for v2 replay — per-bar regime is Approach B, out of scope.\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="KS stress-replay v1 vs v2")
    ap.add_argument("--out-dir", default=os.path.join(
        REPO_ROOT, "data", "retune", "2026-05-31-ks-stress-replay"))
    ap.add_argument("--symbols", nargs="*", default=CURATED_SYMBOLS)
    ap.add_argument("--ran-at", required=True,
                    help="ISO timestamp (passed in; Date.now is not available in scripts)")
    args = ap.parse_args()
    payload = run(args.out_dir, args.symbols, args.ran_at)
    print(f"verdict={payload['verdict']} winning_slider={payload['winning_slider']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ks_stress_replay_metrics.py::test_assemble_frontier_groups_points_by_engine -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/ks_stress_replay/run.py tests/test_ks_stress_replay_metrics.py
git commit -m "feat(ks-replay): CLI driver + report/audit writers"
```

---

## Task 7: Integration smoke run on real data (2-symbol subset)

**Files:**
- No new source. Run the driver on a small subset to validate end-to-end before the full 10-symbol run.

- [ ] **Step 1: Run the full unit suite**

Run: `python -m pytest tests/test_ks_stress_replay_*.py -v`
Expected: PASS (all tasks 1-6 tests green)

- [ ] **Step 2: Smoke run on 2 symbols**

Run:
```bash
python -m tools.ks_stress_replay.run --symbols BTCUSDT ETHUSDT \
  --out-dir data/retune/2026-05-31-ks-stress-replay-smoke \
  --ran-at 2026-05-31T00:00:00+00:00
```
Expected: prints `verdict=... winning_slider=...`; creates `report.md`, `results.json`, `derivation_audit.md` under the smoke dir. No exceptions. `derivation_audit.md` shows `cutoff: 2025-04-30T00:00:00+00:00`.

- [ ] **Step 3: Sanity-check the smoke output**

Verify in `report.md`:
- `none` engine has the most negative (or equal) `max_dd` of the three families (unprotected).
- `v1` and `v2@*` rows are present with plausible `taken`/`skipped` counts.
- If any symbol bankrupted, it appears under "Bankruptcies".

- [ ] **Step 4: Full 10-symbol run**

Run:
```bash
python -m tools.ks_stress_replay.run \
  --out-dir data/retune/2026-05-31-ks-stress-replay \
  --ran-at 2026-05-31T00:00:00+00:00
```
Expected: full frontier + verdict. Review `report.md`.

- [ ] **Step 5: Commit the artefacts**

```bash
git add data/retune/2026-05-31-ks-stress-replay/
git commit -m "chore(ks-replay): full 10-symbol stress-replay artefacts + verdict"
```

---

## Self-review notes

- **Spec coverage:** §1 verdict → Task 5 (`evaluate_gate`) + Task 6 (frontier). §2 Pass 1 → Task 4; Pass 2 → Task 3 + overlays Tasks 1-2; Pass 3 → Tasks 5-6. §3 portfolio metrics → Task 3 (`max_dd`, `total_pnl`, `taken`, `skipped`, `engagements`). §4 guards: holdout → Task 4 (`generate_base_stream` assertion + `HOLDOUT_CUTOFF`); bankruptcy → Task 4 (`truncate_at_bankruptcy`/`flag_bankruptcies`); inflated-numbers caveat → Task 6 report banner. §5 outputs → Task 6 writers. §6 params (10 symbols, grid 30/50/70, gate thresholds) → Tasks 4-6 constants.
- **Per-symbol breakdown (§3):** the current plan reports portfolio-level aggregates; a per-symbol P&L/DD contribution table is a follow-up enhancement to `_render_report` (low risk, additive) — noted, not gating the verdict.
- **Known simplifications carried from spec §2/§7:** position-occupancy feedback approximation (Approach C) and `regime_score=None`. Both documented in `derivation_audit.md`.
