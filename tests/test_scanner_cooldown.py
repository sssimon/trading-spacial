"""btc_scanner cooldown — _build_e5_cooldown paths A-E + scan() verdict integration."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import btc_scanner as scanner
from strategy.core import SignalDecision


@pytest.fixture(autouse=True)
def _reset_throttles(monkeypatch):
    """Reset both the validator-warning throttle and the scanner's
    DB-failure throttle so each test starts clean."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())
    monkeypatch.setattr(scanner, "_db_fail_warned", set())


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    def _set(cfg: dict):
        (tmp_path / "config.json").write_text(json.dumps(cfg))
        monkeypatch.setattr(scanner, "SCRIPT_DIR", str(tmp_path))
    return _set


@pytest.fixture(autouse=True)
def _stub_external(monkeypatch):
    monkeypatch.setattr(scanner, "get_cached_regime",
                        lambda: {"regime": "BULL", "score": 75.0, "details": {}})
    monkeypatch.setattr(scanner, "detect_regime_for_symbol",
                        lambda sym, mode: {"regime": "BULL", "score": 75.0, "details": {}})
    import health as _h
    monkeypatch.setattr(_h, "apply_reduce_factor", lambda size, sym, cfg: size)
    monkeypatch.setattr(_h, "get_symbol_state", lambda sym: "NORMAL")


def _make_ohlcv(n=210, base=100.0, volume=1000.0, seed=42):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    closes = base + rng.normal(0, 0.5, n)
    closes = np.maximum(closes, 1.0)
    return pd.DataFrame({
        "open":  closes + rng.normal(0, 0.1, n),
        "high":  closes + rng.uniform(0.1, 0.5, n),
        "low":   np.maximum(closes - rng.uniform(0.1, 0.5, n), 0.5),
        "close": closes,
        "volume": [volume] * n,
        "taker_buy_base":  [volume * 0.5] * n,
        "taker_buy_quote": [volume * 0.5 * base] * n,
    }, index=idx)


def _make_scan_klines(volume=1000.0, base=100.0):
    df1h = _make_ohlcv(n=210, base=base, volume=volume)
    df4h = _make_ohlcv(n=150, base=base, volume=volume)
    df5m = _make_ohlcv(n=210, base=base, volume=volume)
    return df5m, df1h, df4h, df1h


# ── Direct unit tests for _build_e5_cooldown (paths A-E) ───────────────────


def test_e5_path_a_no_prior_exits_returns_activo_false_with_null_hours(monkeypatch):
    """Path A: db_last_exit_ts → None. activo=False, hours_since_last_exit=None."""
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: None)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is False
    assert e5["hours_since_last_exit"] is None
    assert e5["cooldown_hours_required"] == 6.0
    assert "Sin trades previos" in e5["nota"]


def test_e5_path_b_hours_since_exceeds_cooldown_returns_activo_false(monkeypatch):
    """Path B: 10h since exit, 6h required → activo=False."""
    last_exit = datetime.now(timezone.utc) - timedelta(hours=10)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is False
    assert e5["hours_since_last_exit"] is not None
    assert e5["hours_since_last_exit"] >= 9.99  # ~10
    assert e5["cooldown_hours_required"] == 6.0


def test_e5_path_c_hours_since_below_cooldown_returns_activo_true(monkeypatch):
    """Path C: 1h since exit, 6h required → activo=True (the core blocking path)."""
    last_exit = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is True
    assert e5["hours_since_last_exit"] < 6.0
    assert e5["cooldown_hours_required"] == 6.0


def test_e5_path_c_uses_per_symbol_override_not_global(monkeypatch):
    """Per-symbol override binds when global cd would not (8h since exit, cd=14)."""
    last_exit = datetime.now(timezone.utc) - timedelta(hours=8)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 14}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is True
    assert e5["cooldown_hours_required"] == 14.0


def test_e5_path_d_invalid_override_falls_back_to_global_cooldown_h(monkeypatch, caplog):
    """Path D: invalid override → fallback to COOLDOWN_H. Validator emits 1 warn."""
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: None)
    caplog.set_level(logging.WARNING)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": -5}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["cooldown_hours_required"] == float(scanner.COOLDOWN_H)
    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) >= 1


def test_e5_path_d_invalid_override_fallback_USED_in_comparison(monkeypatch, caplog):
    """Combo: invalid override AND last_exit 1h ago → activo=True from fallback.

    Catches a regression where the fallback COOLDOWN_H is only stored in the
    dict but not actually used in the `hours_since < cd` comparison. Without
    this test, a refactor that resolved the value via a different path could
    return cooldown_hours_required=6.0 in the dict yet compare against 0
    (missing/None) and let the trade through.
    """
    last_exit = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)
    caplog.set_level(logging.WARNING)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": -5}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["cooldown_hours_required"] == float(scanner.COOLDOWN_H)
    # 1h since exit < 6h fallback → activo MUST be True.
    assert e5["activo"] is True, (
        "fallback value must be USED in comparison, not just stored in the dict"
    )


def test_e5_path_e_db_exception_fail_open_logs_warning(monkeypatch, caplog):
    """DB query raises → activo=False (fail-open), warning logged."""
    def _boom(sym):
        raise RuntimeError("DB connection lost")
    monkeypatch.setattr("db.positions.db_last_exit_ts", _boom)
    caplog.set_level(logging.WARNING)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is False
    assert e5["hours_since_last_exit"] is None
    matching = [r for r in caplog.records if "db_last_exit_ts" in r.getMessage()]
    assert len(matching) >= 1, "fail-open path must log a warning"


def test_e5_path_e_sqlite_operational_error_logs_at_error_level(monkeypatch, caplog):
    """SQLite OperationalError escalates to log.error (not warning)."""
    import sqlite3
    def _boom(sym):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr("db.positions.db_last_exit_ts", _boom)
    caplog.set_level(logging.WARNING)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is False  # fail-open behavior unchanged
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "db_last_exit_ts" in r.getMessage()
    ]
    assert len(error_records) >= 1, "SQLite OperationalError must escalate to ERROR level"


def test_e5_path_e_oserror_logs_at_error_level(monkeypatch, caplog):
    """OSError (disk-full, permission-denied, missing parent dir) escalates to error."""
    def _boom(sym):
        raise OSError("disk full")
    monkeypatch.setattr("db.positions.db_last_exit_ts", _boom)
    caplog.set_level(logging.WARNING)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["activo"] is False
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "db_last_exit_ts" in r.getMessage()
    ]
    assert len(error_records) >= 1, "OSError must escalate to ERROR level"


# ── Schema invariants ──────────────────────────────────────────────────────


def test_e5_dict_always_present_keys_match_baseline_shape(monkeypatch):
    """Frontend pin: 4 keys, types correct. Drift here breaks downstream consumers."""
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: None)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)

    expected_keys = {"activo", "nota", "hours_since_last_exit", "cooldown_hours_required"}
    assert set(e5.keys()) == expected_keys
    assert isinstance(e5["activo"], bool)
    assert isinstance(e5["nota"], str)
    assert e5["hours_since_last_exit"] is None or isinstance(e5["hours_since_last_exit"], (int, float))
    assert isinstance(e5["cooldown_hours_required"], float)


def test_e5_hours_since_rounded_to_2_decimals(monkeypatch):
    """Round contract: hours_since_last_exit must be limited to 2 decimals."""
    last_exit = datetime.now(timezone.utc) - timedelta(hours=2, minutes=37, seconds=18)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    h = e5["hours_since_last_exit"]
    # Verify at most 2 decimal places (round(x, 2) yields finite resolution)
    assert h == round(h, 2)


def test_e5_cooldown_hours_required_reflects_per_symbol_when_present(monkeypatch):
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: None)
    cfg = {"symbol_overrides": {"ETHUSDT": {"cooldown_hours": 14}}}
    e5 = scanner._build_e5_cooldown("ETHUSDT", cfg)
    assert e5["cooldown_hours_required"] == 14.0


def test_e5_cooldown_hours_required_reflects_global_when_overrides_missing(monkeypatch):
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: None)
    cfg = {"symbol_overrides": {}}  # no per-symbol entry
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["cooldown_hours_required"] == float(scanner.COOLDOWN_H)


def test_e5_payload_json_serializable(monkeypatch):
    """The dict ends up in scan() report which is JSON-serialized for /symbols."""
    last_exit = datetime.now(timezone.utc) - timedelta(hours=3)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)
    cfg = {"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 6}}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    # Must not raise.
    s = json.dumps(e5, ensure_ascii=False)
    assert len(s) > 0


# ── C2 disabled-symbol guard (cfg=False) ───────────────────────────────────


def test_e5_disabled_symbol_does_not_crash(monkeypatch):
    """cfg.symbol_overrides[BTC] = False → no AttributeError on .get('cooldown_hours')."""
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: None)
    cfg = {"symbol_overrides": {"BTCUSDT": False}}
    e5 = scanner._build_e5_cooldown("BTCUSDT", cfg)
    assert e5["cooldown_hours_required"] == float(scanner.COOLDOWN_H)
    assert e5["activo"] is False


# ── End-to-end scan() integration: verdict ladder must read E5 ────────────


def _full_long_indicators(price=100.0):
    return {
        "atr_1h": 0.5, "lrc_pct": 15.0, "lrc_upper": 110, "lrc_lower": 90,
        "lrc_mid": 100, "rsi_1h": 30, "adx_1h": 20, "sma100_4h": 100,
        "price_above_sma100_4h": True,
        "vol_1h": 100.0, "vol_avg_1h": 100.0, "cvd_1h": 0.5,
        "sma10_1h": price, "sma20_1h": price,
        "bb_upper_1h": price + 2.0, "bb_lower_1h": price - 2.0,
        "bull_div_1h": False, "bear_div_1h": False,
    }


def _make_long_signal(price=100.0):
    return SignalDecision(
        direction="LONG", score=5, score_label="PREMIUM", is_signal=True,
        entry_price=price, sl_price=price * 0.5, tp_price=price * 2.0,
        reasons={"atr_sl_mult": 1.0, "atr_tp_mult": 2.0, "atr_be_mult": 1.5},
        indicators=_full_long_indicators(price),
    )


def test_scan_blocks_signal_when_cooldown_active(fake_cfg, monkeypatch):
    """When E5_Cooldown.activo=True, scan() sets señal_activa=False with a 'BLOQUEADA' estado containing 'cooldown'."""
    fake_cfg({"symbol_overrides": {"BTCUSDT": {"cooldown_hours": 14}}})

    # Last exit 1h ago — cooldown 14h NOT elapsed → activo=True
    last_exit = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr("db.positions.db_last_exit_ts", lambda sym: last_exit)

    fake_decision = _make_long_signal()

    with patch("btc_scanner.md.get_klines") as mock_klines, \
         patch("strategy.core.evaluate_signal", return_value=fake_decision), \
         patch("btc_scanner.detect_bull_engulfing", return_value=False), \
         patch("btc_scanner.detect_bear_engulfing", return_value=False), \
         patch("btc_scanner.check_trigger_5m", return_value=(True, {})):
        mock_klines.side_effect = _make_scan_klines(volume=1000.0)
        rep = scanner.scan("BTCUSDT")

    assert rep["señal_activa"] is False, (
        f"cooldown active must block signal; got estado={rep['estado']}"
    )
    assert "BLOQUEADA" in rep["estado"]
    assert "cooldown" in rep["estado"].lower()
    # E5 also surfaced in blocks_auto for CLI/frontend visibility.
    assert any("E5: cooldown activo" in b for b in rep["blocks_auto"]), (
        f"E5 must surface in blocks_auto; got {rep['blocks_auto']}"
    )
