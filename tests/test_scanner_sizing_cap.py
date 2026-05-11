"""btc_scanner.scan() participation cap tests.

Per-symbol `max_participation_rate` in cfg.symbol_overrides surfaces in
`sizing_1h.liquidity_cap` regardless of whether a signal fires. When a
direction setup IS present and the cap binds, scan() returns
`señal_activa=False` with an estado containing "BLOQUEADA — liquidity cap".

Pinned invariants:
- `sizing_1h.liquidity_cap` dict is ALWAYS present in scan output (frontend pin).
- `liquidity_cap.enabled` mirrors whether `max_participation_rate` is in cfg.
- Cap-hit estado branch takes precedence over `blocks_auto` (market-impact
  is structural, not signal-quality).
- Invalid cap values (validator rejects) → silent passthrough (no cap, +1
  throttled warning).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import btc_scanner as scanner
from strategy.core import SignalDecision


@pytest.fixture(autouse=True)
def _reset_validator_throttle(monkeypatch):
    """Fresh shared throttle state per test."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    """Inject a fake config.json into scanner's SCRIPT_DIR.

    Returns a setter callable; pass the cfg dict to install it.
    """
    def _set(cfg: dict):
        (tmp_path / "config.json").write_text(json.dumps(cfg))
        monkeypatch.setattr(scanner, "SCRIPT_DIR", str(tmp_path))

    return _set


@pytest.fixture(autouse=True)
def _stub_external_dependencies(monkeypatch):
    """Stub regime + health to avoid network calls and DB reads."""
    monkeypatch.setattr(
        scanner,
        "get_cached_regime",
        lambda: {"regime": "BULL", "score": 75.0, "details": {}},
    )
    monkeypatch.setattr(
        scanner,
        "detect_regime_for_symbol",
        lambda symbol, mode: {"regime": "BULL", "score": 75.0, "details": {}},
    )
    # health.get_symbol_state lookup — fail-open default in scan().
    # health.apply_reduce_factor is wrapped in try/except inside scan(); patch
    # both to be NORMAL-tier passthrough so risk_usd stays at 1% of capital.
    import health as _health_mod  # noqa: PLC0415
    monkeypatch.setattr(_health_mod, "apply_reduce_factor", lambda size, sym, cfg: size)
    monkeypatch.setattr(_health_mod, "get_symbol_state", lambda symbol: "NORMAL")


def _make_ohlcv(n: int = 210, base_price: float = 100.0, volume: float = 1000.0,
                trend: float = 0.0, noise: float = 0.5, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV with controllable bar volume.

    Default `volume=1000` × close=100 → bar_volume_usd = $100,000 → 24h median = $100,000.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    closes = base_price + trend * np.arange(n) + rng.normal(0, noise, n)
    closes = np.maximum(closes, 1.0)
    highs = closes + rng.uniform(0.1, 0.5, n)
    lows = closes - rng.uniform(0.1, 0.5, n)
    lows = np.maximum(lows, 0.5)
    opens = closes + rng.normal(0, 0.1, n)
    df = pd.DataFrame({
        "open":  opens,
        "high":  highs,
        "low":   lows,
        "close": closes,
        "volume": [volume] * n,
        "taker_buy_base":  [volume * 0.5] * n,
        "taker_buy_quote": [volume * 0.5 * base_price] * n,
    }, index=idx)
    return df


def _make_scan_mock_klines(volume: float = 1000.0, base_price: float = 100.0):
    """Build the (df5m, df1h, df4h, df1d) tuple for mock_klines.side_effect.

    Order matches scanner: get_klines("5m", 210), get_klines("1h", 210),
    get_klines("4h", 150), get_klines("1d", 150 — actually scan passes df1h).
    """
    df1h = _make_ohlcv(n=210, base_price=base_price, volume=volume)
    df4h = _make_ohlcv(n=150, base_price=base_price, volume=volume)
    df5m = _make_ohlcv(n=210, base_price=base_price, volume=volume)
    return df5m, df1h, df4h, df1h


# ─────────────────────────────────────────────────────────────────────────────
# Schema: liquidity_cap dict structure
# ─────────────────────────────────────────────────────────────────────────────


def test_sizing_cap_dict_always_present_in_output(fake_cfg):
    """Frontend pin — `sizing_1h.liquidity_cap` dict must be in every scan output."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.010}}})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    assert "sizing_1h" in rep
    assert "liquidity_cap" in rep["sizing_1h"]
    cap = rep["sizing_1h"]["liquidity_cap"]
    expected_keys = {
        "enabled", "max_participation_rate", "liquidity_24h_median_usd",
        "cap_threshold_usd", "desired_notional_usd", "passes_cap",
        "config_rejected", "max_participation_rate_raw",
    }
    assert set(cap.keys()) == expected_keys, (
        f"liquidity_cap key drift: got {set(cap.keys())}, expected {expected_keys}"
    )


def test_sizing_cap_disabled_when_no_config(fake_cfg):
    """No `max_participation_rate` in cfg → enabled=False, all numeric fields None."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"atr_sl_mult": 1.0}}})  # no max_pov

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["enabled"] is False
    assert cap["max_participation_rate"] is None
    assert cap["cap_threshold_usd"] is None
    # passes_cap=True when no cap exists (no constraint to violate).
    assert cap["passes_cap"] is True


def test_sizing_cap_disabled_when_no_symbol_overrides(fake_cfg):
    """Empty cfg → no symbol_overrides → no cap → enabled=False."""
    fake_cfg({})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["enabled"] is False
    assert cap["passes_cap"] is True


def test_sizing_cap_enabled_with_valid_config(fake_cfg):
    """`max_participation_rate=0.010` in cfg → enabled=True, fields populated."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.010}}})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["enabled"] is True
    assert cap["max_participation_rate"] == 0.010
    assert cap["liquidity_24h_median_usd"] is not None
    assert cap["liquidity_24h_median_usd"] > 0
    assert cap["cap_threshold_usd"] is not None
    assert cap["cap_threshold_usd"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Cap math — passes vs hits
# ─────────────────────────────────────────────────────────────────────────────


def test_cap_passes_when_volume_high_and_cap_loose(fake_cfg):
    """volume=1000 × close=100 → liq=$100,000. max_pov=0.5 → cap=$50,000.
    Capital=$1K, risk_usd=$10 → val_pos clamped at $980 (98% leverage cap).
    $980 << $50,000 → passes_cap=True."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.5}}})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["passes_cap"] is True
    assert cap["desired_notional_usd"] <= cap["cap_threshold_usd"]


def test_cap_hits_when_volume_low_relative_to_cap(fake_cfg):
    """Tight cap forces failure even with normal volume.

    volume=10 × close=100 → liq=$1000. max_pov=0.0001 → cap=$0.10.
    val_pos > $0.10 → cap hit → passes_cap=False.
    """
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.0001}}})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=10.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["passes_cap"] is False
    assert cap["desired_notional_usd"] > cap["cap_threshold_usd"]


# Note: degenerate liquidity (volume=0 → cap_threshold_usd=None) can't be
# exercised here because volume=0 triggers a pre-existing ZeroDivisionError in
# the legacy vol_ratio scoring (line ~394). The NaN-liquidity branch IS
# exercised via test_scanner_pre_warmup_skips_cap_active_symbol below by
# patching evaluate_signal so the legacy scoring path isn't reached.


def test_scanner_pre_warmup_skips_cap_active_symbol(fake_cfg):
    """Scanner with cap-active symbol + NaN volume → BLOQUEADA — liquidity cap.

    Regression net for the OR-vs-AND skip condition (mirror of the backtest
    test): a refactor that changes `pd.isna(_liq) or _liq <= 0` to AND would
    silently allow NaN-liquidity entries.
    """
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.5}}})

    df5m, df1h, df4h, df1d = _make_scan_mock_klines(volume=1000.0)
    df1h.loc[:, "volume"] = float("nan")

    patches = _patch_signal_path(_make_long_signal())
    for p in patches:
        p.start()
    try:
        with patch("btc_scanner.md.get_klines") as mock_klines:
            mock_klines.side_effect = (df5m, df1h, df4h, df1h)
            rep = scanner.scan("BTCUSDT")
    finally:
        for p in patches:
            p.stop()

    assert rep["señal_activa"] is False, f"NaN-liquidity must block signal; estado={rep['estado']}"
    assert "BLOQUEADA" in rep["estado"]
    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["passes_cap"] is False
    # Degenerate liquidity → no threshold computed (different from cap-exceeded
    # case where cap_threshold_usd is a real number).
    assert cap["cap_threshold_usd"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Estado wiring — BLOQUEADA branch when cap-hit AND direction set
# ─────────────────────────────────────────────────────────────────────────────


def _full_long_indicators(price: float = 100.0) -> dict:
    """Indicators dict that satisfies the legacy LONG scoring path in scan().

    The legacy block reads vol_1h / vol_avg_1h, cvd_1h, sma10/20, BB lower —
    all must be finite + non-zero (vol_avg) to avoid ZeroDivisionError in
    the unrelated vol_ratio computation.
    """
    return {
        "atr_1h": 0.5, "lrc_pct": 15.0, "lrc_upper": 110, "lrc_lower": 90,
        "lrc_mid": 100, "rsi_1h": 30, "adx_1h": 20, "sma100_4h": 100,
        "price_above_sma100_4h": True,
        "vol_1h": 100.0, "vol_avg_1h": 100.0, "cvd_1h": 0.5,
        "sma10_1h": price, "sma20_1h": price,
        "bb_upper_1h": price + 2.0, "bb_lower_1h": price - 2.0,
        "bull_div_1h": False, "bear_div_1h": False,
    }


def _make_long_signal(price: float = 100.0) -> SignalDecision:
    """SignalDecision that surfaces a LONG signal with full legacy indicators."""
    return SignalDecision(
        direction="LONG", score=5, score_label="PREMIUM", is_signal=True,
        entry_price=price, sl_price=price * 0.5, tp_price=price * 2.0,
        reasons={"atr_sl_mult": 1.0, "atr_tp_mult": 2.0, "atr_be_mult": 1.5},
        indicators=_full_long_indicators(price),
    )


def _patch_signal_path(fake_decision: SignalDecision):
    """Composite patch context: forces direction + neutral engulfing + active 5M trigger.

    Engulfing detectors are recomputed from df1h regardless of evaluate_signal
    output; we silence them so the LONG `blocks` list stays empty (else
    "BulgEngulfing" might fire and steal the BLOQUEADA estado branch).
    """
    return [
        patch("strategy.core.evaluate_signal", return_value=fake_decision),
        patch("btc_scanner.detect_bull_engulfing", return_value=False),
        patch("btc_scanner.detect_bear_engulfing", return_value=False),
        patch("btc_scanner.check_trigger_5m", return_value=(True, {})),
    ]


def test_estado_blocked_with_liquidity_cap_when_signal_fires(fake_cfg):
    """When evaluate_signal returns a valid LONG and cap is hit, estado must
    contain 'BLOQUEADA' + 'liquidity cap' AND señal_activa=False.

    Cap math: volume=10 × close=100 → liq=$1000. max_pov=0.0001 → cap=$0.10.
    val_pos in scanner ≈ $980 (98% capital cap) >> $0.10 → cap hit.
    """
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.0001}}})

    patches = _patch_signal_path(_make_long_signal())
    for p in patches:
        p.start()
    try:
        with patch("btc_scanner.md.get_klines") as mock_klines:
            mock_klines.side_effect = _make_scan_mock_klines(volume=10.0)
            rep = scanner.scan("BTCUSDT")
    finally:
        for p in patches:
            p.stop()

    assert rep["señal_activa"] is False, f"cap hit must override signal; estado={rep['estado']}"
    assert "BLOQUEADA" in rep["estado"], f"unexpected estado: {rep['estado']}"
    assert "liquidity cap" in rep["estado"].lower(), (
        f"estado must surface liquidity cap reason; got: {rep['estado']}"
    )


def test_estado_passes_when_cap_loose(fake_cfg):
    """Loose cap → liquidity_cap_hit=False → estado does NOT mention cap.

    With volume=1000 + max_pov=0.5, cap=$50,000 >> val_pos $980 → no cap-hit.
    """
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.5}}})

    patches = _patch_signal_path(_make_long_signal())
    for p in patches:
        p.start()
    try:
        with patch("btc_scanner.md.get_klines") as mock_klines:
            mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
            rep = scanner.scan("BTCUSDT")
    finally:
        for p in patches:
            p.stop()

    assert "liquidity cap" not in rep["estado"].lower(), (
        f"loose cap must not produce cap-block estado; got: {rep['estado']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Validator integration
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_cap_value_logged_and_treated_as_no_cap(fake_cfg, caplog):
    """Negative max_pov → validator returns None → liquidity_cap.enabled=False."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": -0.005}}})

    caplog.set_level(logging.WARNING)
    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["enabled"] is False, "invalid value must be treated as no cap"
    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) >= 1, "validator must warn on invalid cap value"


def test_above_one_cap_rejected(fake_cfg, caplog):
    """max_pov=1.5 (operator typo) → validator rejects → no cap."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 1.5}}})

    caplog.set_level(logging.WARNING)
    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["enabled"] is False
    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) >= 1


def test_config_rejected_distinguishable_from_no_config(fake_cfg, caplog):
    """Operator-rejected cap value surfaces as config_rejected=True with the
    raw value preserved — distinguishable from "operator did not configure"
    (config_rejected=False, raw=None). Both look like enabled=False otherwise.
    """
    # Case A: cap was configured with an invalid string value, validator rejected.
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": "0.005"}}})
    caplog.set_level(logging.WARNING)
    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep_rejected = scanner.scan("BTCUSDT")

    cap_a = rep_rejected["sizing_1h"]["liquidity_cap"]
    assert cap_a["enabled"] is False
    assert cap_a["config_rejected"] is True
    assert cap_a["max_participation_rate_raw"] == "0.005"

    # Case B: no cap key at all → not rejected, raw None.
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"atr_sl_mult": 1.0}}})
    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep_not_configured = scanner.scan("BTCUSDT")

    cap_b = rep_not_configured["sizing_1h"]["liquidity_cap"]
    assert cap_b["enabled"] is False
    assert cap_b["config_rejected"] is False
    assert cap_b["max_participation_rate_raw"] is None


def test_config_rejected_false_when_valid_value(fake_cfg):
    """A valid cap value → config_rejected=False, raw passthrough None
    (raw is only surfaced when validator rejects)."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.010}}})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    cap = rep["sizing_1h"]["liquidity_cap"]
    assert cap["enabled"] is True
    assert cap["config_rejected"] is False
    assert cap["max_participation_rate_raw"] is None


# ─────────────────────────────────────────────────────────────────────────────
# JSON serialization regression
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_with_cap_remains_json_serializable(fake_cfg):
    """The new `liquidity_cap` block must not break JSON serialization
    (it's surfaced to /symbols and consumed by the frontend)."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"max_participation_rate": 0.010}}})

    with patch("btc_scanner.md.get_klines") as mock_klines:
        mock_klines.side_effect = _make_scan_mock_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    # Should not raise.
    serialized = json.dumps(rep, ensure_ascii=False)
    assert len(serialized) > 0
