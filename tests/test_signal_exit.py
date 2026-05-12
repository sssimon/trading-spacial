"""SIGNAL_EXIT (Phase 2 R1, signal-reversal exit) — exit logic tests.

Pre-reg: docs/superpowers/plans/2026-05-12-r1-dynamic-exit-pre-reg.md
Audit:   docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md §6 R1

Coverage:
  - Per-direction logic (LONG ≥ thr ; SHORT ≤ 100-thr) per pre-reg §2.2.
  - Warmup safety: helper returns False for None/NaN LRC (defense-in-depth for §5.3).
  - Tie-break order SL > TP > SIGNAL_EXIT > TIME_LIMIT per §2.2.
  - Flag-off regression: cfg.dynamic_exit_enabled=False matches the no-cfg-field
    baseline byte-identical.

Note on flat-price LRC: `calc_lrc` short-circuits to lrc_pct = 50.0 when the
regression-band width collapses to ~0 (flat prices). This lets us trigger
SIGNAL_EXIT deterministically by choosing a threshold ≤ 50 (LONG) or ≥ 50
(SHORT) without engineering a custom price series.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from unittest.mock import patch

import pandas as pd
import pytest

from strategy.core import SignalDecision


@pytest.fixture(autouse=True)
def _reset_validator_throttle(monkeypatch):
    """Fresh shared-validator throttle state per test (mirrors the time-limit suite)."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _flat_bars(n_hours: int = 300, base: float = 100.0):
    """Synthetic OHLCV: flat closes ⇒ calc_lrc returns lrc_pct = 50.0."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx1h = pd.date_range(start, periods=n_hours, freq="1h")
    df1h = pd.DataFrame(
        {
            "open": [base] * n_hours,
            "high": [base + 0.5] * n_hours,
            "low": [base - 0.5] * n_hours,
            "close": [base] * n_hours,
            "volume": [1000.0] * n_hours,
        },
        index=idx1h,
    )
    df4h = df1h.iloc[::4].copy()
    df5m = df1h.iloc[0:1].copy()
    df1d = df1h.iloc[::24].copy()
    return df1h, df4h, df5m, df1d


def _force_first_signal_with_levels(
    entry: float, sl: float, tp: float, direction: str = "LONG",
):
    """Patch evaluate_signal to fire ONE wide-or-tight-level signal then go silent."""
    state = {"fired": False, "entry_bar_time": None}

    def fake(df1h, df4h, df5m, df1d, *, symbol, cfg, regime, health_state, now):
        if not state["fired"]:
            state["fired"] = True
            state["entry_bar_time"] = now
            return SignalDecision(
                direction=direction,
                score=5,
                score_label="PREMIUM",
                is_signal=True,
                entry_price=float(entry),
                sl_price=float(sl),
                tp_price=float(tp),
                reasons={
                    "atr_sl_mult": 1.0, "atr_tp_mult": 2.0, "atr_be_mult": 1.5,
                },
                indicators={"atr_1h": 0.5},
            )
        return SignalDecision(direction="NONE", is_signal=False)

    return fake, state


# ─────────────────────────────────────────────────────────────────────────────
# §2.2 — Per-direction logic + None/NaN guard (helper unit tests)
# ─────────────────────────────────────────────────────────────────────────────


def test_should_signal_exit_long_at_threshold_inclusive():
    """LONG exits when LRC% >= threshold (boundary inclusive per §2.2)."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("LONG", 50.0, 50.0) is True


def test_should_signal_exit_long_below_threshold_no_fire():
    """LONG does not exit when LRC% strictly below threshold."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("LONG", 49.99, 50.0) is False


def test_should_signal_exit_short_at_complement_inclusive():
    """SHORT exits when LRC% <= (100 - threshold) (boundary inclusive)."""
    from backtest import _should_signal_exit
    # threshold=50 ⇒ SHORT exits when lrc ≤ 50
    assert _should_signal_exit("SHORT", 50.0, 50.0) is True


def test_should_signal_exit_short_above_complement_no_fire():
    """SHORT does not exit when LRC% strictly above (100 - threshold)."""
    from backtest import _should_signal_exit
    # threshold=50 ⇒ no fire when lrc > 50
    assert _should_signal_exit("SHORT", 50.01, 50.0) is False


def test_should_signal_exit_long_degenerate_fast_threshold_35():
    """Pre-reg §5.1 boundary: threshold=35 ⇒ LONG exits at lrc≥35."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("LONG", 35.0, 35.0) is True
    assert _should_signal_exit("LONG", 34.99, 35.0) is False


def test_should_signal_exit_short_threshold_35_complement_is_65():
    """Pre-reg §2.2 SHORT semantics: threshold=35 ⇒ SHORT exits when lrc≤65."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("SHORT", 65.0, 35.0) is True
    assert _should_signal_exit("SHORT", 65.01, 35.0) is False


def test_should_signal_exit_long_greedy_threshold_55():
    """Pre-reg §5.2 boundary: threshold=55 ⇒ LONG exits at lrc≥55."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("LONG", 55.0, 55.0) is True
    assert _should_signal_exit("LONG", 54.99, 55.0) is False


def test_should_signal_exit_unknown_direction_returns_false():
    """Defensive: NONE / empty direction never triggers SIGNAL_EXIT."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("NONE", 80.0, 50.0) is False
    assert _should_signal_exit("", 80.0, 50.0) is False


def test_should_signal_exit_none_lrc_returns_false():
    """Pre-reg §5.3 warmup guard: lrc_pct=None ⇒ False."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("LONG", None, 50.0) is False
    assert _should_signal_exit("SHORT", None, 50.0) is False


def test_should_signal_exit_nan_lrc_returns_false():
    """Pre-reg §5.3 warmup guard: NaN lrc_pct ⇒ False."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("LONG", math.nan, 50.0) is False
    assert _should_signal_exit("SHORT", float("nan"), 50.0) is False


def test_should_signal_exit_lowercase_direction_case_insensitive():
    """Defensive: accept lowercase/mixed-case direction strings (operator config drift)."""
    from backtest import _should_signal_exit
    assert _should_signal_exit("long", 80.0, 50.0) is True
    assert _should_signal_exit("Short", 20.0, 50.0) is True
    assert _should_signal_exit("LoNg", 80.0, 50.0) is True


def test_should_signal_exit_none_direction_returns_false():
    """Defensive: None direction (e.g., position lacking a side) never triggers exit."""
    from backtest import _should_signal_exit
    assert _should_signal_exit(None, 80.0, 50.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# §2.2 — SIGNAL_EXIT fires via the simulator (integration)
# ─────────────────────────────────────────────────────────────────────────────


def test_signal_exit_fires_for_long_on_flat_bars_threshold_50():
    """LONG + flat bars (lrc=50) + threshold=50 ⇒ SIGNAL_EXIT fires (lrc≥thr)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    # Wide SL/TP so SL/TP cannot intercept SIGNAL_EXIT in the same bar.
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 50.0,
                "symbol_overrides": {
                    "BTCUSDT": {"time_limit_hours": 100},  # well past sim end
                },
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    se_trades = [t for t in trades if t["exit_reason"] == "SIGNAL_EXIT"]
    assert len(se_trades) == 1, f"expected 1 SIGNAL_EXIT trade, got {len(se_trades)}: {trades}"
    assert se_trades[0]["direction"] == "LONG"
    assert se_trades[0]["exit_price"] == pytest.approx(100.0)


def test_signal_exit_fires_for_short_on_flat_bars_threshold_50():
    """SHORT + flat bars (lrc=50) + threshold=50 ⇒ SIGNAL_EXIT fires (lrc≤100−thr)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    # SHORT: SL above entry, TP below entry. Wide so neither intercepts.
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=200.0, tp=50.0, direction="SHORT")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 50.0,
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 100}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    se_trades = [t for t in trades if t["exit_reason"] == "SIGNAL_EXIT"]
    assert len(se_trades) == 1, f"expected 1 SIGNAL_EXIT trade, got {len(se_trades)}: {trades}"
    assert se_trades[0]["direction"] == "SHORT"


def test_signal_exit_does_not_fire_when_threshold_above_lrc_long():
    """LONG + flat lrc=50 + threshold=55 ⇒ no SIGNAL_EXIT (50 < 55)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 55.0,
                # Short TL so the trade does close (via TIME_LIMIT) — proves no SIGNAL_EXIT fired.
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 5}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    se_trades = [t for t in trades if t["exit_reason"] == "SIGNAL_EXIT"]
    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(se_trades) == 0, f"unexpected SIGNAL_EXIT exits: {se_trades}"
    assert len(tl_trades) == 1, f"expected 1 TIME_LIMIT fallback, got {len(tl_trades)}: {trades}"


def test_signal_exit_does_not_fire_when_threshold_below_lrc_short():
    """SHORT + flat lrc=50 + threshold=55 ⇒ no SIGNAL_EXIT (lrc=50 > 100-55=45)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=200.0, tp=50.0, direction="SHORT")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 55.0,
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 5}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    se_trades = [t for t in trades if t["exit_reason"] == "SIGNAL_EXIT"]
    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(se_trades) == 0
    assert len(tl_trades) == 1


# ─────────────────────────────────────────────────────────────────────────────
# §2.2 — Tie-break order (SL > TP > SIGNAL_EXIT > TIME_LIMIT)
# ─────────────────────────────────────────────────────────────────────────────


def test_tie_break_sl_wins_over_signal_exit_same_bar():
    """Same bar: SL hits (low ≤ SL) AND SIGNAL_EXIT eligible (lrc ≥ thr) ⇒ SL wins."""
    from backtest import simulate_strategy

    # Flat base=100, default low = base-0.5 = 99.5.
    # SL exactly at 99.5 ⇒ hit_sl = (99.5 ≤ 99.5) = True on every bar.
    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200, base=100.0)
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=99.5, tp=200.0, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 50.0,  # flat lrc=50 ≥ 50 ⇒ also eligible
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 100}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    closed = [t for t in trades if t.get("exit_reason") not in (None, "OPEN")]
    assert closed, f"expected a closed trade, got {trades}"
    assert closed[0]["exit_reason"] == "SL", (
        f"tie-break violated: SL must win over SIGNAL_EXIT in same bar; got {closed[0]['exit_reason']}"
    )


def test_tie_break_tp_wins_over_signal_exit_same_bar():
    """Same bar: TP hits (high ≥ TP) AND SIGNAL_EXIT eligible ⇒ TP wins."""
    from backtest import simulate_strategy

    # Flat base=100, default high = 100.5. TP at 100.5 ⇒ hit_tp = True on every bar.
    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200, base=100.0)
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=100.5, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 50.0,
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 100}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    closed = [t for t in trades if t.get("exit_reason") not in (None, "OPEN")]
    assert closed, f"expected a closed trade, got {trades}"
    assert closed[0]["exit_reason"] == "TP", (
        f"tie-break violated: TP must win over SIGNAL_EXIT in same bar; got {closed[0]['exit_reason']}"
    )


def test_tie_break_signal_exit_wins_over_time_limit_same_bar():
    """Same bar: SIGNAL_EXIT eligible AND hours_open ≥ TL ⇒ SIGNAL_EXIT wins."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200, base=100.0)
    # Wide SL/TP so neither intercepts.
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": True,
                "lrc_exit_threshold": 50.0,
                # Very short TL — both SIGNAL_EXIT (lrc≥50) and TL would fire at hour 1.
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 1}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    closed = [t for t in trades if t.get("exit_reason") not in (None, "OPEN")]
    assert closed, f"expected a closed trade, got {trades}"
    assert closed[0]["exit_reason"] == "SIGNAL_EXIT", (
        f"tie-break violated: SIGNAL_EXIT must win over TIME_LIMIT in same bar; got {closed[0]['exit_reason']}"
    )


def test_time_limit_fires_when_signal_exit_disabled():
    """Same construction as the previous test but flag-off ⇒ TIME_LIMIT fires (control)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200, base=100.0)
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg={
                "dynamic_exit_enabled": False,
                "lrc_exit_threshold": 50.0,  # ignored when disabled
                "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 1}},
            },
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    closed = [t for t in trades if t.get("exit_reason") not in (None, "OPEN")]
    assert closed, f"expected a closed trade, got {trades}"
    assert closed[0]["exit_reason"] == "TIME_LIMIT"


# ─────────────────────────────────────────────────────────────────────────────
# §6 — Flag-off regression (byte-identical to baseline without dynamic_exit field)
# ─────────────────────────────────────────────────────────────────────────────


def _trade_signature(trade: dict) -> tuple:
    """Comparable tuple of trade fields (drops volatile/floating diffs)."""
    return (
        trade.get("direction"),
        trade.get("exit_reason"),
        round(float(trade.get("entry_price", 0.0)), 8),
        round(float(trade.get("exit_price", 0.0)), 8),
        round(float(trade.get("pnl_usd", 0.0)), 8),
        trade.get("entry_time"),
        trade.get("exit_time"),
    )


def test_flag_off_byte_identical_to_no_field_baseline():
    """cfg.dynamic_exit_enabled=False ⇒ trade list identical to no-field baseline."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200, base=100.0)
    fake_a, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")
    fake_b, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")

    cfg_baseline = {
        # NO dynamic_exit_enabled, NO lrc_exit_threshold — purely existing API.
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 5}},
    }
    cfg_flag_off = {
        "dynamic_exit_enabled": False,
        "lrc_exit_threshold": 50.0,
        "symbol_overrides": {"BTCUSDT": {"time_limit_hours": 5}},
    }

    with patch("strategy.core.evaluate_signal", side_effect=fake_a):
        trades_baseline, _ = simulate_strategy(
            df1h.copy(), df4h.copy(), df5m.copy(), "BTCUSDT", df1d=df1d.copy(),
            cfg=cfg_baseline,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    with patch("strategy.core.evaluate_signal", side_effect=fake_b):
        trades_flag_off, _ = simulate_strategy(
            df1h.copy(), df4h.copy(), df5m.copy(), "BTCUSDT", df1d=df1d.copy(),
            cfg=cfg_flag_off,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades_baseline) == len(trades_flag_off), (
        f"trade count differs: baseline={len(trades_baseline)} flag_off={len(trades_flag_off)}"
    )
    for tb, tf in zip(trades_baseline, trades_flag_off):
        assert _trade_signature(tb) == _trade_signature(tf), (
            f"trade differs:\n  baseline={tb}\n  flag_off={tf}"
        )
    # No SIGNAL_EXIT must appear in either path.
    assert not any(t.get("exit_reason") == "SIGNAL_EXIT" for t in trades_flag_off)
    assert not any(t.get("exit_reason") == "SIGNAL_EXIT" for t in trades_baseline)


def test_legacy_atr_kwargs_path_does_not_signal_exit():
    """Legacy `atr_*` kwargs path (cfg=None) ⇒ SIGNAL_EXIT never fires (mirrors TL bypass)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200, base=100.0)
    fake, _ = _force_first_signal_with_levels(entry=100.0, sl=50.0, tp=200.0, direction="LONG")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            atr_sl_mult=1.0, atr_tp_mult=4.0, atr_be_mult=1.5,  # legacy kwargs path
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    se_trades = [t for t in trades if t.get("exit_reason") == "SIGNAL_EXIT"]
    assert se_trades == [], (
        f"legacy atr_* kwargs path must not engage SIGNAL_EXIT; got {se_trades}"
    )
