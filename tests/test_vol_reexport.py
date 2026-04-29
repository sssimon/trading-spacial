# tests/test_vol_reexport.py
"""Identity tests: strategy.vol re-exports preserved on btc_scanner.

PR8 cleanup (#225): VOL_LOOKBACK_DAYS had 0 external callers and was removed
from btc_scanner.py. Import directly from strategy.vol instead.
"""


def test_vol_reexport_identity():
    import btc_scanner
    from strategy import vol

    # Retained: have callers in tests/test_scanner.py
    assert btc_scanner.annualized_vol_yang_zhang is vol.annualized_vol_yang_zhang
    assert btc_scanner.TARGET_VOL_ANNUAL is vol.TARGET_VOL_ANNUAL
