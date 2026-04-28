# tests/test_vol_reexport.py
def test_vol_reexport_identity():
    import btc_scanner
    from strategy import vol
    assert btc_scanner.annualized_vol_yang_zhang is vol.annualized_vol_yang_zhang
    assert btc_scanner.TARGET_VOL_ANNUAL is vol.TARGET_VOL_ANNUAL
    assert btc_scanner.VOL_LOOKBACK_DAYS is vol.VOL_LOOKBACK_DAYS
