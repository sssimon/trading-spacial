# tests/test_patterns_reexport.py
"""Identity tests: btc_scanner re-exports must point to the same objects as
their new home in strategy.patterns. Prevents silent drift if a re-export
is accidentally rebound or shadowed.

PR8 cleanup (#225): detect_bull_engulfing, detect_rsi_divergence, score_label,
check_trigger_5m had 0 external callers and were removed from btc_scanner.py.
The canonical imports are now in strategy.patterns directly.
"""


def test_patterns_reexport_identity():
    import btc_scanner
    from strategy import patterns

    # Retained: have callers in tests/test_scanner.py
    assert btc_scanner.detect_bear_engulfing is patterns.detect_bear_engulfing
    assert btc_scanner.check_trigger_5m_short is patterns.check_trigger_5m_short
