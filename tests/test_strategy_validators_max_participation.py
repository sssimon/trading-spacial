"""validated_max_participation_rate — boundary, throttle, type rejection.

Mirror of the validated_time_limit_hours pattern with a > 1.0 upper-bound
rejection that's unique to participation rates (values >100% of bar volume
are nonsense for retail).
"""
from __future__ import annotations

import logging

import pytest

from strategy._validators import validated_max_participation_rate


@pytest.fixture(autouse=True)
def _reset_validator_throttle(monkeypatch):
    """Fresh shared throttle state per test — avoids cross-test bleed."""
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())


def _logger():
    return logging.getLogger("test_validator")


# ── Pass-through for valid values ───────────────────────────────────────────


def test_none_passthrough_silent(caplog):
    caplog.set_level(logging.WARNING)
    assert validated_max_participation_rate(None, "BTCUSDT", "test", _logger()) is None
    assert len(caplog.records) == 0, "None passthrough must not log"


def test_valid_floats_passthrough():
    log = _logger()
    assert validated_max_participation_rate(0.0015, "XLMUSDT", "test", log) == 0.0015
    assert validated_max_participation_rate(0.010, "BTCUSDT", "test", log) == 0.010
    assert validated_max_participation_rate(0.5, "FOO", "test", log) == 0.5


def test_valid_int_passthrough():
    """int values get coerced to float; valid ints in (0, 1] pass."""
    assert validated_max_participation_rate(1, "BTC", "test", _logger()) == 1.0


def test_boundary_1_0_inclusive():
    """1.0 is the inclusive upper bound — exactly 1.0 passes."""
    assert validated_max_participation_rate(1.0, "FOO", "test", _logger()) == 1.0


def test_just_below_one_passes():
    assert validated_max_participation_rate(0.9999, "BTC", "test", _logger()) == 0.9999


# ── Type rejections ─────────────────────────────────────────────────────────


def test_bool_rejected(caplog):
    """bool is a subclass of int in Python but never valid as a rate."""
    caplog.set_level(logging.WARNING)
    assert validated_max_participation_rate(True, "BTC", "test", _logger()) is None
    assert validated_max_participation_rate(False, "BTC", "test", _logger()) is None


def test_string_rejected(caplog):
    caplog.set_level(logging.WARNING)
    assert validated_max_participation_rate("0.005", "BTC", "test", _logger()) is None


def test_dict_rejected(caplog):
    caplog.set_level(logging.WARNING)
    assert validated_max_participation_rate({"value": 0.005}, "BTC", "test", _logger()) is None


# ── Numeric edge cases ─────────────────────────────────────────────────────


def test_nan_rejected():
    assert validated_max_participation_rate(float("nan"), "BTC", "test", _logger()) is None


def test_inf_rejected():
    assert validated_max_participation_rate(float("inf"), "BTC", "test", _logger()) is None


def test_neg_inf_rejected():
    assert validated_max_participation_rate(float("-inf"), "BTC", "test", _logger()) is None


def test_zero_rejected():
    assert validated_max_participation_rate(0, "BTC", "test", _logger()) is None
    assert validated_max_participation_rate(0.0, "BTC", "test", _logger()) is None


def test_negative_rejected():
    assert validated_max_participation_rate(-0.005, "BTC", "test", _logger()) is None
    assert validated_max_participation_rate(-1.0, "BTC", "test", _logger()) is None


# ── Boundary > 1.0 — UNIQUE to max_pov vs time_limit_hours ─────────────────


def test_above_one_rejected_just_above():
    """1.0001 is just above the boundary — must reject."""
    assert validated_max_participation_rate(1.0001, "BTC", "test", _logger()) is None


def test_above_one_rejected_realistic_misconfig():
    """Operator typing 5 instead of 0.005 → must reject loudly, not skip."""
    assert validated_max_participation_rate(5.0, "BTC", "test", _logger()) is None


def test_above_one_rejected_extreme():
    assert validated_max_participation_rate(100.0, "BTC", "test", _logger()) is None


# ── Throttling — same misconfig must warn at most once per (caller, symbol, kind)


def test_throttle_one_warning_per_caller_symbol_kind(caplog):
    """5 calls with same misconfig → 1 warning. Critical for long-running scanner."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    for _ in range(5):
        validated_max_participation_rate(-0.005, "BTCUSDT", "scan", log)

    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) == 1, f"throttle broken: got {len(matching)} warnings for 5 same-key calls"


def test_throttle_separates_by_caller(caplog):
    """Different callers must each emit their own warning."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    validated_max_participation_rate(-0.005, "BTCUSDT", "scan", log)
    validated_max_participation_rate(-0.005, "BTCUSDT", "simulate_strategy", log)

    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) == 2


def test_throttle_separates_by_symbol(caplog):
    """Different symbols must each emit their own warning even with same caller."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    validated_max_participation_rate(-0.005, "BTCUSDT", "scan", log)
    validated_max_participation_rate(-0.005, "ETHUSDT", "scan", log)

    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) == 2


def test_throttle_separates_by_error_kind(caplog):
    """Same (caller, symbol) but different error categories → separate warnings.

    Catches a regression where the throttle key would collapse error_kind.
    """
    caplog.set_level(logging.WARNING)
    log = _logger()

    validated_max_participation_rate(-0.005, "BTCUSDT", "scan", log)        # non-positive
    validated_max_participation_rate(float("nan"), "BTCUSDT", "scan", log)  # non-finite
    validated_max_participation_rate(2.0, "BTCUSDT", "scan", log)           # above-one
    validated_max_participation_rate("0.005", "BTCUSDT", "scan", log)       # type:str

    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) == 4


def test_throttle_does_not_emit_for_valid_values(caplog):
    """Successful validation must not warn."""
    caplog.set_level(logging.WARNING)
    log = _logger()

    for _ in range(5):
        validated_max_participation_rate(0.005, "BTCUSDT", "scan", log)

    matching = [r for r in caplog.records if "max_participation_rate" in r.getMessage()]
    assert len(matching) == 0
