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


def parse_mark_klines(payload: list[list]) -> list[tuple[int, float]]:
    """Map FAPI markPriceKlines to [(open_time_ms, close)], same fields as the bulk
    ingest keeps (ingest.py:117: open_time index 0, close index 4)."""
    out = [(int(k[0]), float(k[4])) for k in payload]
    out.sort(key=lambda x: x[0])
    return out


def fetch_mark_klines(symbol: str, *, interval: str = "1h", limit: int = 1000
                      ) -> list[tuple[int, float]]:
    """Recent perp mark klines at the SAME grain as the fossil (1h). Fail-soft: []."""
    url = f"{FAPI_MARK_KLINES}?symbol={symbol}&interval={interval}&limit={int(limit)}"
    try:
        return parse_mark_klines(_get_json(url))
    except Exception as e:                       # noqa: BLE001 — fail-soft
        log.warning("fetch_mark_klines(%s) failed: %s", symbol, e)
        return []


def append_perp_klines(db_path: str, symbol: str, rows: list[tuple[int, float]]) -> int:
    """Idempotent append to perp_klines (PK (symbol, open_time)). Returns rows attempted."""
    with closing(sqlite3.connect(db_path)) as con:
        con.executemany("INSERT OR IGNORE INTO perp_klines VALUES(?,?,?)",
                        [(symbol, t, c) for t, c in rows])
        con.commit()
    return len(rows)


def append_funding(db_path: str, symbol: str, rows: list[tuple[int, float]]) -> int:
    """Idempotent append to funding (PK (symbol, funding_time_ms)). Returns rows attempted."""
    with closing(sqlite3.connect(db_path)) as con:
        con.executemany("INSERT OR IGNORE INTO funding VALUES(?,?,?)",
                        [(symbol, t, r) for t, r in rows])
        con.commit()
    return len(rows)


def fetch_spot(symbol: str) -> float:
    """Current spot price via Binance spot ticker. Fail-soft: NaN on error."""
    try:
        return float(_get_json(f"{FAPI_SPOT}?symbol={symbol}")["price"])
    except Exception as e:                       # noqa: BLE001 — fail-soft
        log.warning("fetch_spot(%s) failed: %s", symbol, e)
        return float("nan")


def ingest_live(symbols: list[str], *, db_path: str = FUNDING_DB,
                limit: int) -> dict:
    """Fetch + append funding and 1h mark klines for each symbol. Fail-soft per
    symbol (a down symbol contributes 0 rows, never raises). Returns per-symbol counts."""
    summary = {}
    for s in symbols:
        funding = fetch_recent_funding(s, limit=limit)
        klines = fetch_mark_klines(s, interval="1h", limit=limit)
        nf = append_funding(db_path, s, funding) if funding else 0
        nk = append_perp_klines(db_path, s, klines) if klines else 0
        summary[s] = {"funding": nf, "klines": nk}
    return summary
