# tests/test_regime_reexport.py
"""Identity tests: strategy.regime re-exports preserved on btc_scanner.

Covers all 16 names: 12 functions + 4 constants/globals. The `is` check on
`_regime_cache` ensures both names reference the same dict object so mutations
from either name propagate correctly.
"""


def test_regime_reexport_identity():
    import btc_scanner
    from strategy import regime

    # Functions (12)
    assert btc_scanner.detect_regime is regime.detect_regime
    assert btc_scanner.get_cached_regime is regime.get_cached_regime
    assert btc_scanner.detect_regime_for_symbol is regime.detect_regime_for_symbol
    assert btc_scanner._compute_price_score is regime._compute_price_score
    assert btc_scanner._compute_fng_score is regime._compute_fng_score
    assert btc_scanner._compute_funding_score is regime._compute_funding_score
    assert btc_scanner._compute_rsi_score is regime._compute_rsi_score
    assert btc_scanner._compute_adx_score is regime._compute_adx_score
    assert btc_scanner._regime_cache_key is regime._regime_cache_key
    assert btc_scanner._compute_local_regime is regime._compute_local_regime
    assert btc_scanner._load_regime_cache is regime._load_regime_cache
    assert btc_scanner._save_regime_cache is regime._save_regime_cache

    # Constants (3)
    assert btc_scanner._REGIME_CACHE_FILE is regime._REGIME_CACHE_FILE
    assert btc_scanner._REGIME_CACHE_PATH is regime._REGIME_CACHE_PATH
    assert btc_scanner._REGIME_TTL_SEC is regime._REGIME_TTL_SEC

    # Module-global dict — same object so mutations from either name propagate
    assert btc_scanner._regime_cache is regime._regime_cache
