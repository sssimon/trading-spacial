# tests/test_direction_reexport.py
"""Identity tests: strategy.direction re-exports preserved on btc_scanner.

PR8 cleanup (#225): resolve_direction_params, metrics_inc_direction_disabled,
ATR_TP_MULT, ATR_BE_MULT had 0 external callers and were removed from btc_scanner.py.
The canonical imports are now in strategy.direction directly.
"""


def test_direction_reexport_identity():
    import btc_scanner
    from strategy import direction

    # Retained: ATR_SL_MULT has a caller in tests/test_scanner.py
    assert btc_scanner.ATR_SL_MULT is direction.ATR_SL_MULT
