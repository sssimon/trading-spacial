# tests/test_patterns_reexport.py
"""Identity tests: btc_scanner re-exports must point to the same objects as
their new home in strategy.patterns. Prevents silent drift if a re-export
is accidentally rebound or shadowed.
"""


def test_patterns_reexport_identity():
    import btc_scanner
    from strategy import patterns

    assert btc_scanner.detect_bull_engulfing is patterns.detect_bull_engulfing
    assert btc_scanner.detect_bear_engulfing is patterns.detect_bear_engulfing
    assert btc_scanner.detect_rsi_divergence is patterns.detect_rsi_divergence
    assert btc_scanner.score_label is patterns.score_label
    assert btc_scanner.check_trigger_5m is patterns.check_trigger_5m
    assert btc_scanner.check_trigger_5m_short is patterns.check_trigger_5m_short
