# tests/test_direction_reexport.py
"""Identity tests: strategy.direction re-exports preserved on btc_scanner."""


def test_direction_reexport_identity():
    import btc_scanner
    from strategy import direction

    assert btc_scanner.resolve_direction_params is direction.resolve_direction_params
    assert btc_scanner.metrics_inc_direction_disabled is direction.metrics_inc_direction_disabled
    assert btc_scanner.ATR_SL_MULT is direction.ATR_SL_MULT
    assert btc_scanner.ATR_TP_MULT is direction.ATR_TP_MULT
    assert btc_scanner.ATR_BE_MULT is direction.ATR_BE_MULT
