"""Verify strategy.constants is the single source of truth for indicator
periods, score tiers, and LRC zone thresholds."""
from strategy import constants as K


def test_indicator_periods():
    assert K.LRC_PERIOD == 100
    assert K.LRC_STDEV == 2.0
    assert K.RSI_PERIOD == 14
    assert K.BB_PERIOD == 20
    assert K.BB_STDEV == 2.0
    assert K.VOL_PERIOD == 20
    assert K.ATR_PERIOD == 14


def test_atr_defaults():
    assert K.ATR_SL_MULT_DEFAULT == 1.0
    assert K.ATR_TP_MULT_DEFAULT == 4.0
    assert K.ATR_BE_MULT_DEFAULT == 1.5


def test_lrc_zone_thresholds():
    assert K.LRC_LONG_MAX == 25.0
    assert K.LRC_SHORT_MIN == 75.0


def test_score_tiers():
    assert K.SCORE_MIN_HALF == 0
    assert K.SCORE_STANDARD == 2
    assert K.SCORE_PREMIUM == 4
