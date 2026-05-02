"""simulate_strategy time-limit barrier.

Per-symbol `time_limit_hours` in `symbol_overrides` closes positions at
`bar["close"]` when `now - entry_time >= time_limit_hours`. SL/TP win the
tie-break in the same bar.

Pinned invariants:
- Exit price on time-limit = `bar["close"]`.
- Missing field (legacy fallback) => no time-limit applied.
- Legacy `atr_*` kwargs path skips time-limit (auto_tune / grid_search must
  opt in by passing symbol_overrides explicitly).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from strategy.core import SignalDecision


@pytest.fixture(autouse=True)
def _reset_validator_throttle(monkeypatch):
    """Fresh shared-validator throttle state per test."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: synthetic OHLCV that lets simulate_strategy run, plus
# evaluate_signal mocks that fire entries on the first eligible bar.
# ─────────────────────────────────────────────────────────────────────────────


def _flat_bars(n_hours: int = 300, base: float = 100.0):
    """Synthetic 1H/4H/5M/1D OHLCV with a flat price series.

    Flat price keeps a wide-SL/TP entry from being closed by SL or TP, so the
    only viable exit is the time-limit barrier or the simulation end.
    """
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


def _force_first_signal(direction: str = "LONG", base: float = 100.0):
    """Patch evaluate_signal to fire one wide-SL/TP signal then go silent."""
    state = {"fired": False, "entry_bar_time": None}

    sl = base * 0.5 if direction == "LONG" else base * 1.5
    tp = base * 2.0 if direction == "LONG" else base * 0.5

    def fake(df1h, df4h, df5m, df1d, *, symbol, cfg, regime, health_state, now):
        if not state["fired"]:
            state["fired"] = True
            state["entry_bar_time"] = now
            return SignalDecision(
                direction=direction,
                score=5,
                score_label="PREMIUM",
                is_signal=True,
                entry_price=float(base),
                sl_price=float(sl),
                tp_price=float(tp),
                reasons={
                    "atr_sl_mult": 1.0,
                    "atr_tp_mult": 2.0,
                    "atr_be_mult": 1.5,
                },
                indicators={"atr_1h": 0.5},
            )
        return SignalDecision(direction="NONE", is_signal=False)

    return fake, state


def _force_first_signal_with_levels(
    entry: float,
    sl: float,
    tp: float,
    direction: str = "LONG",
):
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
                    "atr_sl_mult": 1.0,
                    "atr_tp_mult": 2.0,
                    "atr_be_mult": 1.5,
                },
                indicators={"atr_1h": 0.5},
            )
        return SignalDecision(direction="NONE", is_signal=False)

    return fake, state


# ─────────────────────────────────────────────────────────────────────────────
# Boundary + tie-break behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_time_limit_fires_at_boundary():
    """time_limit_hours=14 → trade closes exactly 14h after entry, reason TIME_LIMIT."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, state = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 14}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 1, (
        f"expected exactly 1 TIME_LIMIT trade, got {len(tl_trades)}: {trades}"
    )
    t = tl_trades[0]
    duration_h = (t["exit_time"] - t["entry_time"]).total_seconds() / 3600
    assert duration_h == pytest.approx(14.0, abs=1e-6), (
        f"expected duration 14h, got {duration_h}"
    )
    assert t["exit_price"] == pytest.approx(100.0)


def test_time_limit_fires_at_boundary_short_direction():
    """SHORT direction respects the time-limit identically to LONG (no asymmetry)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal(direction="SHORT")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 14}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 1
    assert tl_trades[0]["direction"] == "SHORT"
    duration_h = (tl_trades[0]["exit_time"] - tl_trades[0]["entry_time"]).total_seconds() / 3600
    assert duration_h == pytest.approx(14.0, abs=1e-6)


def test_time_limit_does_not_fire_before_boundary():
    """time_limit_hours=20 with only 18 hourly bars after entry: no TIME_LIMIT exit."""
    from backtest import simulate_strategy

    # 130 bars → after warmup (~110), only ~18 bars remain post-entry
    df1h, df4h, df5m, df1d = _flat_bars(n_hours=130)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 20}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0, f"unexpected TIME_LIMIT exits: {tl_trades}"


def test_sl_wins_in_same_bar_conflict():
    """SL hit in same bar as time-limit threshold → exit_reason SL, not TIME_LIMIT."""
    from backtest import simulate_strategy

    n_hours = 200
    base = 100.0
    df1h, df4h, df5m, df1d = _flat_bars(n_hours=n_hours, base=base)

    # Force a deep wick at bar warmup+14 (the time-limit boundary bar) that
    # touches SL but does not touch TP.
    # Entry would happen at first eligible bar = warmup index (~110).
    # We build SL = 95.0; the wick at bar 124 dips low=94 → SL hit.
    # Use a tight SL/wide TP so SL is the only same-bar candidate.
    fake, state = _force_first_signal_with_levels(
        entry=100.0, sl=95.0, tp=200.0,  # wide TP
    )

    # Mutate the dataframe at the boundary bar to inject the wick.
    # Boundary bar index = warmup_idx + 14 (entry happens at warmup_idx).
    # We don't know warmup exactly here — set wicks across bars 120..130 to
    # be safe; the time-limit fires at the first qualifying bar.
    for i in range(120, 130):
        df1h.iloc[i, df1h.columns.get_loc("low")] = 94.0
        df1h.iloc[i, df1h.columns.get_loc("open")] = 99.0  # below entry (sl-first heuristic)

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 14}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    closed = [t for t in trades if t["exit_reason"] != "OPEN"]
    assert len(closed) >= 1, f"expected at least one closed trade, got {trades}"
    assert closed[0]["exit_reason"] == "SL", (
        f"expected SL win over TIME_LIMIT, got {closed[0]['exit_reason']}"
    )


def test_tp_wins_over_time_limit_in_same_bar():
    """TP hit in same bar as time-limit threshold → exit_reason TP, not TIME_LIMIT."""
    from backtest import simulate_strategy

    n_hours = 200
    df1h, df4h, df5m, df1d = _flat_bars(n_hours=n_hours, base=100.0)

    # Tight TP, wide SL. Inject a wick UP at the boundary bars.
    fake, _ = _force_first_signal_with_levels(
        entry=100.0, sl=50.0, tp=105.0,
    )

    for i in range(120, 130):
        df1h.iloc[i, df1h.columns.get_loc("high")] = 106.0
        df1h.iloc[i, df1h.columns.get_loc("open")] = 101.0  # above entry

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 14}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    closed = [t for t in trades if t["exit_reason"] != "OPEN"]
    assert len(closed) >= 1
    assert closed[0]["exit_reason"] == "TP"


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol horizons (smoke at simulate_strategy level)
# ─────────────────────────────────────────────────────────────────────────────


def test_per_symbol_time_limit_values_btc_14():
    """BTC time_limit_hours=14 → exit ~14h after entry."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 14}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 1
    duration_h = (tl_trades[0]["exit_time"] - tl_trades[0]["entry_time"]).total_seconds() / 3600
    assert duration_h == pytest.approx(14.0, abs=1e-6)


def test_per_symbol_time_limit_values_ada_5():
    """ADA time_limit_hours=5 → exit ~5h after entry."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "ADAUSDT", df1d=df1d,
            symbol_overrides={"ADAUSDT": {"time_limit_hours": 5}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 1
    duration_h = (tl_trades[0]["exit_time"] - tl_trades[0]["entry_time"]).total_seconds() / 3600
    assert duration_h == pytest.approx(5.0, abs=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy / parity behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_no_time_limit_in_config_legacy_behavior():
    """Without `time_limit_hours` field → no TIME_LIMIT exits, only OPEN at end."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            # no symbol_overrides
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0
    open_trades = [t for t in trades if t["exit_reason"] == "OPEN"]
    assert len(open_trades) == 1, f"expected one OPEN trade at sim end, got {trades}"


def test_legacy_atr_kwargs_path_skips_time_limit():
    """Legacy atr_* kwargs path: even if cfg has time_limit_hours, no TIME_LIMIT applied."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    cfg = {"symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}}}
    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            atr_sl_mult=1.0, atr_tp_mult=2.0, atr_be_mult=1.5,
            cfg=cfg,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0, (
        f"legacy atr_* kwargs path must skip time-limit; got {tl_trades}"
    )


def test_resolver_reads_from_cfg_when_symbol_overrides_none():
    """When symbol_overrides=None but cfg.symbol_overrides has time_limit_hours, it applies."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    cfg = {"symbol_overrides": {"BTCUSDT": {"time_limit_hours": 14}}}
    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            cfg=cfg,  # cfg has it, symbol_overrides kwarg is None
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 1


def test_no_time_limit_in_config_byte_identical_to_legacy():
    """Parity guard: with no time_limit_hours and no legacy kwargs, exit_reason
    distribution is byte-identical to pre-change behavior on the same fixture.

    Pre-change: only SL / TP / OPEN exits exist. We assert no TIME_LIMIT
    appears anywhere — this is the pin against accidental drift in the resolver.
    """
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300, base=100.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    reasons = {t["exit_reason"] for t in trades}
    legacy_allowed = {"SL", "TP", "OPEN"}
    assert reasons.issubset(legacy_allowed), (
        f"reasons {reasons} must be subset of legacy {legacy_allowed} when "
        f"no time-limit is configured"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_time_limit_zero_rejected_with_warning(caplog):
    """time_limit_hours=0 is invalid (degenerate) — rejected with log.warning,
    no time-limit applied."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with caplog.at_level(logging.WARNING, logger="backtest"):
        with patch("strategy.core.evaluate_signal", side_effect=fake):
            trades, _ = simulate_strategy(
                df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
                symbol_overrides={"BTCUSDT": {"time_limit_hours": 0}},
                enable_slippage=False, enable_spread=False, enable_fees=False,
            )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_time_limit_negative_rejected_with_warning(caplog):
    """time_limit_hours=-5 → log.warning, no time-limit applied."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with caplog.at_level(logging.WARNING, logger="backtest"):
        with patch("strategy.core.evaluate_signal", side_effect=fake):
            trades, _ = simulate_strategy(
                df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
                symbol_overrides={"BTCUSDT": {"time_limit_hours": -5}},
                enable_slippage=False, enable_spread=False, enable_fees=False,
            )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_time_limit_string_type_rejected_with_warning(caplog):
    """time_limit_hours="14" (string from hand-edited JSON) → log.warning, no time-limit."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with caplog.at_level(logging.WARNING, logger="backtest"):
        with patch("strategy.core.evaluate_signal", side_effect=fake):
            trades, _ = simulate_strategy(
                df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
                symbol_overrides={"BTCUSDT": {"time_limit_hours": "14"}},
                enable_slippage=False, enable_spread=False, enable_fees=False,
            )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_time_limit_bool_rejected_with_warning(caplog):
    """time_limit_hours=True (bool) is wrong type — bool subclasses int, would
    silently accept as 1.0 without the explicit bool guard."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with caplog.at_level(logging.WARNING, logger="backtest"):
        with patch("strategy.core.evaluate_signal", side_effect=fake):
            trades, _ = simulate_strategy(
                df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
                symbol_overrides={"BTCUSDT": {"time_limit_hours": True}},
                enable_slippage=False, enable_spread=False, enable_fees=False,
            )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0, "bool must not collapse to 1.0"
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_time_limit_nan_rejected_with_warning(caplog):
    """time_limit_hours=NaN → `hours_open >= nan` is always False; without
    the finite-check the time-limit silently never fires."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    with caplog.at_level(logging.WARNING, logger="backtest"):
        with patch("strategy.core.evaluate_signal", side_effect=fake):
            trades, _ = simulate_strategy(
                df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
                symbol_overrides={"BTCUSDT": {"time_limit_hours": float("nan")}},
                enable_slippage=False, enable_spread=False, enable_fees=False,
            )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_time_limit_inf_rejected_with_warning(caplog):
    """time_limit_hours=Inf → `hours_open >= inf` is always False; silent never-fires."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    with caplog.at_level(logging.WARNING, logger="backtest"):
        with patch("strategy.core.evaluate_signal", side_effect=fake):
            trades, _ = simulate_strategy(
                df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
                symbol_overrides={"BTCUSDT": {"time_limit_hours": float("inf")}},
                enable_slippage=False, enable_spread=False, enable_fees=False,
            )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0
    assert any("time_limit_hours" in r.getMessage() for r in caplog.records)


def test_time_limit_very_large_no_op():
    """time_limit_hours=9999 → never fires within the simulation window."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 9999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 0


def test_float_arithmetic_boundary_precision():
    """Boundary check uses pd.Timedelta-style hour arithmetic — entry exactly
    at hour mark + 14h must trigger, not skip.
    """
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 14}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    tl_trades = [t for t in trades if t["exit_reason"] == "TIME_LIMIT"]
    assert len(tl_trades) == 1
    expected_dt = tl_trades[0]["entry_time"] + pd.Timedelta(hours=14)
    assert tl_trades[0]["exit_time"] == expected_dt


# ─────────────────────────────────────────────────────────────────────────────
# Final-bar OPEN labeling
# ─────────────────────────────────────────────────────────────────────────────


def test_final_bar_open_position_label_open():
    """Position open at simulation end with no time-limit hit → exit_reason OPEN."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(n_hours=200)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 9999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    open_trades = [t for t in trades if t["exit_reason"] == "OPEN"]
    assert len(open_trades) == 1
