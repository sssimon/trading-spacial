# tests/test_http_reexport.py
"""Identity + minimal behavior tests for infra.http (PR5)."""
import time


def test_http_reexport_identity():
    import btc_scanner
    from infra import http
    assert btc_scanner._load_proxy is http._load_proxy
    assert btc_scanner._rate_limit is http._rate_limit
    assert btc_scanner._API_MIN_INTERVAL is http._API_MIN_INTERVAL
    assert btc_scanner._api_lock is http._api_lock


def test_rate_limit_enforces_min_interval():
    """Two consecutive calls must be at least _API_MIN_INTERVAL seconds apart."""
    from infra.http import _rate_limit, _API_MIN_INTERVAL

    _rate_limit()
    t0 = time.time()
    _rate_limit()
    elapsed = time.time() - t0

    # Allow some slack — must be at least the min interval (with epsilon for clock jitter).
    assert elapsed >= _API_MIN_INTERVAL * 0.95


def test_load_proxy_from_env(monkeypatch):
    """HTTPS_PROXY env var takes precedence."""
    from infra import http

    monkeypatch.setenv("HTTPS_PROXY", "socks5://test:1080")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)

    proxy = http._load_proxy()
    assert proxy == {"http": "socks5://test:1080", "https": "socks5://test:1080"}
