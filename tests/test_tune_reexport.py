# tests/test_tune_reexport.py
"""strategy.tune — re-export breadcrumb.

PR8 cleanup (#225): _classify_tune_result had 0 external callers and was removed
from btc_scanner.py. Import directly from strategy.tune instead.
"""


def test_tune_home_module_accessible():
    """Verify the home module is importable and _classify_tune_result exists there."""
    from strategy import tune
    assert callable(tune._classify_tune_result)
