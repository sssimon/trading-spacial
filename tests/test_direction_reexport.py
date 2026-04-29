# tests/test_direction_reexport.py
"""Identity tests: strategy.direction re-exports preserved on btc_scanner.

All 5 names from PR2 are RETAINED in btc_scanner.py (they have callers in
tests/test_scanner.py, tests/test_symbol_overrides_resolution.py, and
backtest.py). This test guards all 5 so that an accidental shadow/rebind is
caught immediately.
"""


def test_direction_reexport_identity():
    import btc_scanner
    from strategy import direction

    assert btc_scanner.resolve_direction_params is direction.resolve_direction_params
    assert btc_scanner.metrics_inc_direction_disabled is direction.metrics_inc_direction_disabled
    assert btc_scanner.ATR_SL_MULT is direction.ATR_SL_MULT
    assert btc_scanner.ATR_TP_MULT is direction.ATR_TP_MULT
    assert btc_scanner.ATR_BE_MULT is direction.ATR_BE_MULT
