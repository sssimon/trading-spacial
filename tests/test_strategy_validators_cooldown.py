"""validated_cooldown_hours — boundary, throttle, default-fallback unit tests."""
from __future__ import annotations

import logging

import pytest

from strategy._validators import validated_cooldown_hours


@pytest.fixture(autouse=True)
def _reset_validator_throttle(monkeypatch):
    """Fresh shared throttle state per test — avoids cross-test bleed."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())


def _logger():
    return logging.getLogger("test_validator_cooldown")


# ── Pass-through for valid values ───────────────────────────────────────────


def test_none_returns_default_silent(caplog):
    """None → default fallback silently (caller decides default per-call)."""
    caplog.set_level(logging.WARNING)
    assert validated_cooldown_hours(None, caller="test", symbol="BTC", logger=_logger()) == 6.0
    assert validated_cooldown_hours(
        None, caller="test", symbol="BTC", logger=_logger(), default=10.0
    ) == 10.0
    assert len(caplog.records) == 0, "None passthrough must not log"


def test_valid_floats_passthrough():
    log = _logger()
    assert validated_cooldown_hours(1.0, caller="test", symbol="BTC", logger=log) == 1.0
    assert validated_cooldown_hours(6.0, caller="test", symbol="BTC", logger=log) == 6.0
    assert validated_cooldown_hours(14.0, caller="test", symbol="BTC", logger=log) == 14.0
    assert validated_cooldown_hours(48.5, caller="test", symbol="FOO", logger=log) == 48.5


def test_valid_int_returns_float():
    """int values get coerced to float."""
    assert validated_cooldown_hours(6, caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_boundary_168_inclusive():
    """168 is the inclusive upper bound — exactly 168 passes."""
    assert validated_cooldown_hours(168, caller="test", symbol="FOO", logger=_logger()) == 168.0
    assert validated_cooldown_hours(168.0, caller="test", symbol="FOO", logger=_logger()) == 168.0


def test_just_below_168_passes():
    assert validated_cooldown_hours(167.999, caller="test", symbol="BTC", logger=_logger()) == 167.999


# ── Type rejections — return default + warn ─────────────────────────────────


def test_bool_rejected_returns_default(caplog):
    """bool subclasses int but is never a valid hour count."""
    caplog.set_level(logging.WARNING)
    log = _logger()
    assert validated_cooldown_hours(True, caller="test", symbol="BTC", logger=log) == 6.0
    assert validated_cooldown_hours(False, caller="test", symbol="BTC", logger=log) == 6.0
    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) >= 1


def test_string_rejected_returns_default(caplog):
    caplog.set_level(logging.WARNING)
    assert validated_cooldown_hours("6", caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_dict_rejected_returns_default():
    assert validated_cooldown_hours({"value": 6}, caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_custom_default_used_on_rejection():
    """Passing default=10 → invalid input returns 10, not the spec default 6."""
    assert validated_cooldown_hours(
        "bogus", caller="test", symbol="BTC", logger=_logger(), default=10.0
    ) == 10.0
    assert validated_cooldown_hours(
        -5, caller="test", symbol="BTC", logger=_logger(), default=10.0
    ) == 10.0


# ── Numeric edge cases ─────────────────────────────────────────────────────


def test_nan_rejected_returns_default():
    assert validated_cooldown_hours(float("nan"), caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_inf_rejected_returns_default():
    assert validated_cooldown_hours(float("inf"), caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_neg_inf_rejected_returns_default():
    assert validated_cooldown_hours(float("-inf"), caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_zero_rejected_returns_default():
    assert validated_cooldown_hours(0, caller="test", symbol="BTC", logger=_logger()) == 6.0
    assert validated_cooldown_hours(0.0, caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_negative_rejected_returns_default():
    assert validated_cooldown_hours(-1, caller="test", symbol="BTC", logger=_logger()) == 6.0
    assert validated_cooldown_hours(-6.0, caller="test", symbol="BTC", logger=_logger()) == 6.0


# ── Boundary > 168 ──────────────────────────────────────────────────────────


def test_above_168_rejected_just_above():
    """168.001 is just above the boundary — must reject."""
    assert validated_cooldown_hours(168.001, caller="test", symbol="BTC", logger=_logger()) == 6.0


def test_above_168_rejected_realistic_misconfig():
    """Operator typing 168 hours when meaning days → must reject."""
    assert validated_cooldown_hours(720, caller="test", symbol="BTC", logger=_logger()) == 6.0  # 30 days


def test_above_168_rejected_extreme():
    assert validated_cooldown_hours(1e9, caller="test", symbol="BTC", logger=_logger()) == 6.0


# ── Throttling — same misconfig must warn at most once per (caller, symbol, kind)


def test_throttle_one_warning_per_caller_symbol_kind(caplog):
    """5 calls with same misconfig → 1 warning."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    for _ in range(5):
        validated_cooldown_hours(-5, caller="scan", symbol="BTCUSDT", logger=log)

    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) == 1, f"throttle broken: got {len(matching)} warnings for 5 same-key calls"


def test_throttle_separates_by_caller(caplog):
    caplog.set_level(logging.WARNING)
    log = _logger()

    validated_cooldown_hours(-5, caller="scan", symbol="BTCUSDT", logger=log)
    validated_cooldown_hours(-5, caller="simulate_strategy", symbol="BTCUSDT", logger=log)

    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) == 2


def test_throttle_separates_by_symbol(caplog):
    caplog.set_level(logging.WARNING)
    log = _logger()

    validated_cooldown_hours(-5, caller="scan", symbol="BTCUSDT", logger=log)
    validated_cooldown_hours(-5, caller="scan", symbol="ETHUSDT", logger=log)

    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) == 2


def test_throttle_separates_by_error_kind(caplog):
    """Same (caller, symbol) but different error categories → separate warnings."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    validated_cooldown_hours(-5, caller="scan", symbol="BTCUSDT", logger=log)              # non-positive
    validated_cooldown_hours(float("nan"), caller="scan", symbol="BTCUSDT", logger=log)    # non-finite
    validated_cooldown_hours(200, caller="scan", symbol="BTCUSDT", logger=log)             # above-168
    validated_cooldown_hours("6", caller="scan", symbol="BTCUSDT", logger=log)             # type:str

    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) == 4


def test_throttle_does_not_emit_for_valid_values(caplog):
    """Successful validation must not warn."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    for _ in range(5):
        validated_cooldown_hours(6.0, caller="scan", symbol="BTCUSDT", logger=log)

    matching = [r for r in caplog.records if "cooldown_hours" in r.getMessage()]
    assert len(matching) == 0
