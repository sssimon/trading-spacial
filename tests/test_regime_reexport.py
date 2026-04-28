# tests/test_regime_reexport.py
"""Identity tests: strategy.regime re-exports preserved on btc_scanner.

PR8 cleanup (#225): detect_regime, _save_regime_cache, _REGIME_CACHE_FILE,
_REGIME_CACHE_PATH, _REGIME_TTL_SEC, _regime_cache had 0 external callers
and were removed from btc_scanner.py. Import directly from strategy.regime instead.

Retained re-exports (10 names) all have callers in tests/ or production code;
see the comment block in btc_scanner.py for the full caller table.
"""


def test_regime_reexport_identity():
    import btc_scanner
    from strategy import regime

    # Retained: have callers (see btc_scanner.py re-export comment block)
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
