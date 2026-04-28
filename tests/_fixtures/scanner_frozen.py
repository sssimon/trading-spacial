"""Frozen fixture for scan() snapshot tests.

Monkeypatches:
- datetime.now() → fixed UTC timestamp
- data.market_data.get_klines() → CSVs from tests/_fixtures/btcusdt_*.csv
- data.market_data.prefetch() → no-op
- requests.get() → fixed JSON for F&G, funding rate, exchange info
- _REGIME_CACHE_FILE / _REGIME_CACHE_PATH / _regime_cache → tmp_path isolation
- observability.record_decision → no-op
- strategy.kill_switch_v2_shadow.emit_shadow_decision → no-op

PR6 updated: regime cache vars now patched on `strategy.regime.*` (home module)
rather than `btc_scanner.*` (re-export). Production code reads from home module.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import requests

from data import market_data as md

_FIXTURE_DIR = Path(__file__).resolve().parent
_RESPONSES_PATH = _FIXTURE_DIR / "scanner_frozen_responses.json"


def _frozen_get_klines(symbol, interval, limit=None, **kw):
    csv_path = _FIXTURE_DIR / f"{symbol.lower()}_{interval}.csv"
    if not csv_path.exists():
        return pd.DataFrame()
    return pd.read_csv(csv_path)


def _frozen_requests_get(url, **kw):
    payloads = json.loads(_RESPONSES_PATH.read_text())

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.ok = True

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    if "fng" in url:
        return _Resp(payloads["fng"])
    if "fundingRate" in url:
        return _Resp(payloads["funding"])
    if "exchangeInfo" in url:
        return _Resp(payloads["exchangeInfo"])
    raise RuntimeError(f"unexpected URL in frozen test: {url}")


_FIXED_NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """datetime subclass with frozen now()/utcnow()."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FIXED_NOW.replace(tzinfo=None)
        return _FIXED_NOW.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return _FIXED_NOW.replace(tzinfo=None)


def _normalize(obj):
    """Convert any non-JSON-native types (e.g. numpy) to native Python."""
    import numpy as np

    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(x) for x in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


@pytest.fixture
def frozen_scan(monkeypatch, tmp_path):
    """Apply all monkeypatches needed to get deterministic scan() output."""
    monkeypatch.setattr("btc_scanner.datetime", _FrozenDatetime)
    monkeypatch.setattr(md, "get_klines", _frozen_get_klines)
    monkeypatch.setattr(md, "prefetch", lambda *a, **kw: None)
    # PR6: regime moved to strategy.regime — patch the home module so the
    # production read of _REGIME_CACHE_FILE inside _save_regime_cache picks
    # up the tmp_path. Patching btc_scanner.* would only rebind the
    # re-export name, not the home-module name that production code reads.
    monkeypatch.setattr(
        "strategy.regime._REGIME_CACHE_FILE", str(tmp_path / "regime.json"))
    monkeypatch.setattr(
        "strategy.regime._REGIME_CACHE_PATH", str(tmp_path / "regime.json"))
    monkeypatch.setattr("strategy.regime._regime_cache", {})
    monkeypatch.setattr(requests, "get", _frozen_requests_get)
    monkeypatch.setattr("observability.record_decision", lambda **kw: None)
    monkeypatch.setattr(
        "strategy.kill_switch_v2_shadow.emit_shadow_decision", lambda **kw: None)
    yield
