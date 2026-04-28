# tests/test_http_reexport.py
"""Behavior tests for infra.http (PR5).

PR8 cleanup (#225): _load_proxy, _rate_limit, _API_MIN_INTERVAL, _api_lock had
0 external callers via btc_scanner and were removed from btc_scanner.py.
Import directly from infra.http instead. Behavior tests retained below.
"""
import time


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
