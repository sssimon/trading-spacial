# tests/test_patterns_reexport.py
"""Identity tests: btc_scanner re-exports must point to the same objects as
their canonical home in strategy.patterns. Prevents silent drift if a
re-export is accidentally rebound or shadowed inside btc_scanner.py.

All 6 names from PR1 are RETAINED in btc_scanner.py (they are either called
internally by scan() or have external callers in tests/test_scanner.py and
backtest.py). This test guards all 6 so that an accidental shadow/rebind is
caught immediately.
"""


def test_patterns_reexport_identity():
    import btc_scanner
    from strategy import patterns

    assert btc_scanner.detect_bull_engulfing is patterns.detect_bull_engulfing
    assert btc_scanner.detect_bear_engulfing is patterns.detect_bear_engulfing
    assert btc_scanner.detect_rsi_divergence is patterns.detect_rsi_divergence
    assert btc_scanner.check_trigger_5m is patterns.check_trigger_5m
    assert btc_scanner.check_trigger_5m_short is patterns.check_trigger_5m_short
    assert btc_scanner.score_label is patterns.score_label
