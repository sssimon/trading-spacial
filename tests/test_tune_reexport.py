# tests/test_tune_reexport.py
def test_tune_reexport_identity():
    import btc_scanner
    from strategy import tune
    assert btc_scanner._classify_tune_result is tune._classify_tune_result
