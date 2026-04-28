# tests/test_cli_reexport.py
def test_cli_reexport_identity():
    import btc_scanner
    from cli import scanner_report

    assert btc_scanner.fmt is scanner_report.fmt
    assert btc_scanner.save_log is scanner_report.save_log
    assert btc_scanner.main is scanner_report.main
    assert btc_scanner.get_top_symbols is scanner_report.get_top_symbols
    assert btc_scanner.LOG_FILE is scanner_report.LOG_FILE
    assert btc_scanner.SCAN_INTERVAL is scanner_report.SCAN_INTERVAL
    assert btc_scanner.STABLECOINS is scanner_report.STABLECOINS
