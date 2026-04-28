# tests/test_cli_reexport.py
"""cli.scanner_report — re-export breadcrumb.

PR8 cleanup (#225): fmt, save_log, main, get_top_symbols, LOG_FILE,
SCAN_INTERVAL, STABLECOINS all had 0 external callers via btc_scanner and
were removed from btc_scanner.py. Import directly from cli.scanner_report instead.
"""


def test_cli_scanner_report_home_module_accessible():
    """Verify the home module is importable and key names exist there."""
    from cli import scanner_report

    assert callable(scanner_report.fmt)
    assert callable(scanner_report.save_log)
    assert callable(scanner_report.main)
    assert callable(scanner_report.get_top_symbols)
    assert isinstance(scanner_report.LOG_FILE, str)
    assert isinstance(scanner_report.SCAN_INTERVAL, int)
    assert isinstance(scanner_report.STABLECOINS, set)
