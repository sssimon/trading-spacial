"""simulate_strategy participation cap tests.

Per-symbol `max_participation_rate` in `symbol_overrides` skips an entry
when the desired notional would exceed `max_pov × _liquidity_24h_median`.
SL/TP and time-limit semantics are unaffected.

Pinned invariants:
- Skip on STRICT `desired > cap × liq` (allow on equality).
- Skip on degenerate liquidity (≤0 dead bar; NaN at 24h warmup).
- Legacy `atr_*` kwargs path (auto_tune / grid_search) bypasses cap.
- Cap is entry-time only — no cap re-check on already-open positions.

Notional math (with default mock):
    entry=100, sl=50         → sl_pct_actual=50%
    capital=10K, RISK=0.01,
    size_mult=1.5 (PREMIUM)  → risk_amount=$150
    desired_notional         = 150 * 100 / 50 = $300
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from strategy.core import SignalDecision


@pytest.fixture(autouse=True)
def _reset_validator_throttle(monkeypatch):
    """Fresh shared throttle state per test."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())


def _flat_bars(n_hours: int = 300, base: float = 100.0, volume: float = 1000.0):
    """Synthetic 1H/4H/5M/1D OHLCV with flat price + controllable volume.

    `volume` controls the 24h-rolling-median liquidity proxy used by the cap.
    Bar volume USD = base * volume; rolling-24-median = same constant after warmup.
    """
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx1h = pd.date_range(start, periods=n_hours, freq="1h")
    df1h = pd.DataFrame(
        {
            "open":   [base] * n_hours,
            "high":   [base + 0.5] * n_hours,
            "low":    [base - 0.5] * n_hours,
            "close":  [base] * n_hours,
            "volume": [volume] * n_hours,
        },
        index=idx1h,
    )
    df4h = df1h.iloc[::4].copy()
    df5m = df1h.iloc[0:1].copy()
    df1d = df1h.iloc[::24].copy()
    return df1h, df4h, df5m, df1d


def _force_first_signal(direction: str = "LONG", base: float = 100.0):
    """Patch evaluate_signal to fire one wide-SL/TP signal then go silent.

    Wide SL/TP keeps the trade open until time-limit (or sim-end). Tests pin
    cap behavior at entry — exit semantics are deferred to other test files.
    """
    state = {"fired": False}

    sl = base * 0.5 if direction == "LONG" else base * 1.5
    tp = base * 2.0 if direction == "LONG" else base * 0.5

    def fake(df1h, df4h, df5m, df1d, *, symbol, cfg, regime, health_state, now):
        if not state["fired"]:
            state["fired"] = True
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


# ─────────────────────────────────────────────────────────────────────────────
# Cap behavior — within and over
# ─────────────────────────────────────────────────────────────────────────────


def test_sizing_cap_skips_when_notional_exceeds_cap():
    """max_pov=0.001, volume=1000 → cap=$100 < desired=$300 → SKIP."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.001, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert trades == [], f"expected zero trades (cap-skip), got {trades}"


def test_sizing_cap_allows_when_notional_within_cap():
    """max_pov=0.010, volume=1000 → cap=$1000 > desired=$300 → ALLOW."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.010, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, f"expected 1 trade (cap-allow), got {len(trades)}: {trades}"


def test_sizing_cap_capacity_exactly_at_cap_allows():
    """Convention pin: equality allowed, only STRICT exceedance skips.

    max_pov=0.003, volume=1000 → cap=$300 == desired=$300 → ALLOW.
    """
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.003, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, (
        f"equality (desired==cap) must allow per spec; got {trades}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol caps
# ─────────────────────────────────────────────────────────────────────────────


def test_per_symbol_cap_btc_passes_pendle_skips():
    """Same notional context, different per-symbol caps → divergent outcomes.

    BTC max_pov=0.010 → cap=$1000 > desired=$300 → ALLOW.
    PENDLE max_pov=0.0015 → cap=$150 < desired=$300 → SKIP.
    """
    from backtest import simulate_strategy

    overrides = {
        "BTCUSDT":    {"max_participation_rate": 0.010,  "time_limit_hours": 999},
        "PENDLEUSDT": {"max_participation_rate": 0.0015, "time_limit_hours": 999},
    }

    btc_df1h, btc_df4h, btc_df5m, btc_df1d = _flat_bars(volume=1000.0)
    pendle_df1h, pendle_df4h, pendle_df5m, pendle_df1d = _flat_bars(volume=1000.0)

    btc_fake, _ = _force_first_signal()
    with patch("strategy.core.evaluate_signal", side_effect=btc_fake):
        btc_trades, _ = simulate_strategy(
            btc_df1h, btc_df4h, btc_df5m, "BTCUSDT", df1d=btc_df1d,
            symbol_overrides=overrides,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    pendle_fake, _ = _force_first_signal()
    with patch("strategy.core.evaluate_signal", side_effect=pendle_fake):
        pendle_trades, _ = simulate_strategy(
            pendle_df1h, pendle_df4h, pendle_df5m, "PENDLEUSDT", df1d=pendle_df1d,
            symbol_overrides=overrides,
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(btc_trades) == 1, f"BTC must pass cap; got {btc_trades}"
    assert pendle_trades == [], f"PENDLE must skip cap; got {pendle_trades}"


def test_per_symbol_cap_short_direction_same_logic():
    """SHORT direction respects cap identically (no asymmetry).

    SHORT entry=100, sl=150 (above) → sl_pct = 50% → desired=$300 same.
    """
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal(direction="SHORT")

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.001, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert trades == [], f"SHORT must skip the cap-violating entry; got {trades}"


# ─────────────────────────────────────────────────────────────────────────────
# Degenerate liquidity
# ─────────────────────────────────────────────────────────────────────────────


def test_zero_volume_skips_position():
    """volume=0 → liq_24h=0 → cap-skip even with permissive max_pov."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=0.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.5, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert trades == [], f"zero-liquidity bars must skip; got {trades}"


def test_nan_liquidity_skips_position():
    """NaN volume → rolling 24h median = NaN → cap-skip.

    Regression net for the OR-vs-AND skip condition: a refactor that changes
    `pd.isna(_liq) or _liq <= 0` to `pd.isna(_liq) and _liq <= 0` would let
    NaN-liquidity entries through (NaN comparisons evaluate False, so the AND
    short-circuits to False → no skip). This test fails on that regression.
    """
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    # Inject NaN across the full volume column → rolling median NaN for every bar.
    df1h.loc[:, "volume"] = float("nan")

    fake, _ = _force_first_signal()
    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.5, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert trades == [], f"NaN-liquidity bars must skip; got {trades}"


# ─────────────────────────────────────────────────────────────────────────────
# Cap-disabled paths — passthrough behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_cap_field_missing_passthrough():
    """When max_participation_rate is absent from cfg, no cap applied —
    cap is opt-in per-symbol via symbol_overrides."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"time_limit_hours": 999}},  # no max_pov
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, f"missing cap field must passthrough; got {trades}"


def test_cap_explicit_none_passthrough():
    """cfg with explicit max_participation_rate=None → passthrough (no cap)."""
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": None, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, f"None cap must passthrough; got {trades}"


def test_legacy_kwargs_path_skips_sizing_cap():
    """Legacy `atr_*` kwargs (auto_tune / grid_search direct callers) bypass
    the cap entirely — even with a tight cap configured in symbol_overrides.

    Mirrors the time-limit barrier's `legacy_override_active` gating: direct
    callers without symbol_overrides opt out of structural barriers (cap +
    time-limit) so their tuning runs aren't surprised by them.
    """
    from backtest import simulate_strategy

    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            atr_sl_mult=1.0, atr_tp_mult=2.0, atr_be_mult=1.5,  # legacy kwargs ON
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 0.0001, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, (
        f"legacy_override_active must bypass cap (parity with auto_tune); got {trades}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validator integration — invalid cap value treated as no-cap
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_negative_cap_treated_as_no_cap(caplog):
    """Negative max_pov → validator returns None → no cap → trade allowed.
    Validator emits 1 throttled warning."""
    from backtest import simulate_strategy

    caplog.set_level(logging.WARNING)
    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": -0.005, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, (
        f"invalid cap value treated as no-cap (validator returned None); got {trades}"
    )
    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) >= 1, "validator must warn at least once on negative cap"


def test_above_one_cap_rejected_treated_as_no_cap(caplog):
    """max_pov=1.5 (operator typo: meant 0.015) → validator rejects → no cap."""
    from backtest import simulate_strategy

    caplog.set_level(logging.WARNING)
    df1h, df4h, df5m, df1d = _flat_bars(volume=1000.0)
    fake, _ = _force_first_signal()

    with patch("strategy.core.evaluate_signal", side_effect=fake):
        trades, _ = simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": 1.5, "time_limit_hours": 999}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert len(trades) == 1, f"above-one cap rejected → no cap → trade allowed; got {trades}"
    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) >= 1


def test_validator_warning_throttled_per_symbol_in_simulation(caplog):
    """In a long simulation, the same misconfig must warn at most once
    per (caller, symbol, error_kind) — critical for runtime log-spam control.
    """
    from backtest import simulate_strategy

    caplog.set_level(logging.WARNING)
    df1h, df4h, df5m, df1d = _flat_bars(n_hours=300, volume=1000.0)

    # Force MANY signals across the simulation (one per bar attempt) — but
    # because each entry would normally open a position, we wrap the fake
    # to return a signal at every call. The validator throttling must keep
    # warnings to 1.
    state = {"calls": 0}
    def always_signal(df1h, df4h, df5m, df1d, *, symbol, cfg, regime, health_state, now):
        state["calls"] += 1
        return SignalDecision(
            direction="LONG", score=5, score_label="PREMIUM", is_signal=True,
            entry_price=100.0, sl_price=50.0, tp_price=200.0,
            reasons={"atr_sl_mult": 1.0, "atr_tp_mult": 2.0, "atr_be_mult": 1.5},
            indicators={"atr_1h": 0.5},
        )

    with patch("strategy.core.evaluate_signal", side_effect=always_signal):
        simulate_strategy(
            df1h, df4h, df5m, "BTCUSDT", df1d=df1d,
            symbol_overrides={"BTCUSDT": {"max_participation_rate": -0.005, "time_limit_hours": 5}},
            enable_slippage=False, enable_spread=False, enable_fees=False,
        )

    assert state["calls"] > 0, "evaluate_signal must have been called at least once"
    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) == 1, (
        f"throttle broken: {state['calls']} signal attempts produced "
        f"{len(matching)} warnings (expected exactly 1)"
    )
