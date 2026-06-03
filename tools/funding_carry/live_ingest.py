"""Live FAPI ingest for the funding-carry shadow (spec 2026-06-03 §4).

Fetches recently-settled funding rates, 1h mark klines, and spot prices from
Binance FAPI and appends them idempotently to data/funding.db (same schema as
the historical bulk ingest). Fail-soft per symbol: a down endpoint logs and
yields empty, never poisons the pool or raises into the daily job. Network +
read/append on funding.db only; never touches holdout or positions."""
from __future__ import annotations
import json
import logging
import sqlite3
import urllib.request
from contextlib import closing
from .constants import FAPI_FUNDING, FAPI_MARK_KLINES, FAPI_SPOT, FUNDING_DB

log = logging.getLogger("funding_carry.live_ingest")


def _get_json(url: str, *, timeout: int = 30):
    """GET a URL and parse JSON. Raises on network/HTTP error (callers fail-soft)."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_fapi_funding(payload: list[dict]) -> list[tuple[int, float]]:
    """Map FAPI /fapi/v1/fundingRate JSON to [(fundingTime_ms, rate)], time-ascending."""
    out = [(int(d["fundingTime"]), float(d["fundingRate"])) for d in payload]
    out.sort(key=lambda x: x[0])
    return out


def fetch_recent_funding(symbol: str, *, limit: int) -> list[tuple[int, float]]:
    """Recent settled funding for `symbol`. Fail-soft: [] on any error (logged)."""
    url = f"{FAPI_FUNDING}?symbol={symbol}&limit={int(limit)}"
    try:
        return parse_fapi_funding(_get_json(url))
    except Exception as e:                       # noqa: BLE001 — fail-soft by contract
        log.warning("fetch_recent_funding(%s) failed: %s", symbol, e)
        return []
