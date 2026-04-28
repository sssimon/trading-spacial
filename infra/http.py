"""HTTP infra — proxy loader + rate limiter (extracted from btc_scanner.py per #225).

Used by:
- strategy.regime (PR6) — _rate_limit before F&G + funding-rate calls
- cli.scanner_report (PR7) — _load_proxy in get_top_symbols (CoinGecko)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_proxy() -> dict:
    """Lee proxy de config.json o de variables de entorno."""
    cfg_path = REPO_ROOT / "config.json"
    proxy_str = ""
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            proxy_str = cfg.get("proxy", "").strip()
        except Exception:
            pass
    proxy_str = os.environ.get(
        "HTTPS_PROXY", os.environ.get("HTTP_PROXY", proxy_str)).strip()
    if proxy_str:
        return {"http": proxy_str, "https": proxy_str}
    return {}


_last_api_call: float = 0.0
_API_MIN_INTERVAL = 0.1   # 100ms between API calls
_api_lock = threading.Lock()


def _rate_limit() -> None:
    """Enforce minimum interval between API calls to avoid rate-limit bans."""
    global _last_api_call
    with _api_lock:
        now = time.time()
        elapsed = now - _last_api_call
        if elapsed < _API_MIN_INTERVAL:
            time.sleep(_API_MIN_INTERVAL - elapsed)
        _last_api_call = time.time()
