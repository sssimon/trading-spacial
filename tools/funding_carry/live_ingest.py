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
import time
import urllib.error
import urllib.request
from contextlib import closing
from .constants import FAPI_FUNDING, FAPI_MARK_KLINES, FAPI_SPOT, FUNDING_DB, FAPI_PERP_DEPTH, SPOT_DEPTH, DEPTH_LIMIT_PERP, DEPTH_LIMIT_SPOT, KLINE_PAGE_LIMIT, KLINE_MIN_COVERAGE

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


# ---------------------------------------------------------------------------
# Execution-realism v0.2 fetchers (spec 2026-06-03 REV 2.1 §5).
# v0.2 policy is fail-LOUD: FetchFailed propagates and the caller ABORTs the whole
# run — the verdict sample must NEVER be a function of network weather (Halberg).
# v0.1 functions above keep their bare _get_json + fail-soft contract, untouched.
# ---------------------------------------------------------------------------

class FetchFailed(Exception):
    """Network/HTTP failure after bounded retries. v0.2 callers ABORT, never shrink the pool."""


def _default_open(url: str, timeout: int):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        used = resp.headers.get("X-MBX-USED-WEIGHT-1M")
        if used:
            log.info("binance used-weight-1m=%s (%s)", used, url.split("?")[0])
        return json.loads(resp.read().decode("utf-8"))


def _get_json_retry(url: str, *, timeout: int = 30, retries: int = 3,
                    backoff_s: float = 2.0, _open=_default_open, _sleep=time.sleep):
    """GET+parse with bounded retry. Honors Retry-After on HTTP 429/418 (rate-limit),
    generic linear backoff otherwise. Raises FetchFailed after exhausting retries.

    ``retries=N`` means N TOTAL attempts (not N retries after the first).
    ``retries=0`` fails immediately with FetchFailed — no attempt is made.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _open(url, timeout)
        except urllib.error.HTTPError as e:
            last = e
            if attempt < retries - 1:
                ra = e.headers.get("Retry-After") if e.code in (429, 418) and e.headers else None
                if ra is not None:
                    try:
                        delay = float(ra)
                    except ValueError:
                        delay = backoff_s * (attempt + 1)
                else:
                    delay = backoff_s * (attempt + 1)
                _sleep(delay)
        except Exception as e:                   # noqa: BLE001 — converted to FetchFailed below
            last = e
            if attempt < retries - 1:
                _sleep(backoff_s * (attempt + 1))
    raise FetchFailed(f"{url}: {last!r}")


def parse_depth(payload: dict) -> dict:
    """Map a Binance depth payload to {'bids': [(price, qty)...], 'asks': [...]}.
    Order preserved as sent (bids best-first descending, asks best-first ascending)."""
    return {"bids": [(float(p), float(q)) for p, q in payload["bids"]],
            "asks": [(float(p), float(q)) for p, q in payload["asks"]]}


def fetch_perp_depth(symbol: str, *, limit: int = DEPTH_LIMIT_PERP) -> dict:
    """USDT-M perp orderbook snapshot. Raises FetchFailed (v0.2 ABORT policy)."""
    return parse_depth(_get_json_retry(
        f"{FAPI_PERP_DEPTH}?symbol={symbol}&limit={int(limit)}"))


def fetch_spot_depth(symbol: str, *, limit: int = DEPTH_LIMIT_SPOT) -> dict:
    """Spot orderbook snapshot. Raises FetchFailed (v0.2 ABORT policy)."""
    return parse_depth(_get_json_retry(
        f"{SPOT_DEPTH}?symbol={symbol}&limit={int(limit)}"))


def fetch_klines_1m_paginated(symbol: str, *, base_url: str, days: float, end_ms: int,
                              page_limit: int = KLINE_PAGE_LIMIT,
                              min_coverage: float = KLINE_MIN_COVERAGE
                              ) -> list[tuple[int, float]]:
    """1m closes over `days` ending at end_ms, paginated (Binance caps at 1500/request).
    Returns [(open_time_ms, close)] ascending. Raises FetchFailed if total bars
    < min_coverage x expected — NEVER silently truncates (spec §4, Halberg BP-1).
    <=2% missing bars (maintenance, thin perps) is tolerated as benign gaps."""
    start_ms = int(end_ms - days * 86_400_000)
    out: list[tuple[int, float]] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{base_url}?symbol={symbol}&interval=1m"
               f"&startTime={cursor}&endTime={int(end_ms)}&limit={int(page_limit)}")
        page = _get_json_retry(url)
        if not page:
            break
        rows = sorted((int(k[0]), float(k[4])) for k in page)
        out.extend(rows)
        new_cursor = rows[-1][0] + 60_000
        if new_cursor <= cursor:
            raise FetchFailed(
                f"{symbol}: server returned non-advancing page at cursor={cursor}")
        cursor = new_cursor
    expected = days * 1440
    if len({t for t, _ in out}) != len(out):
        raise FetchFailed(f"{symbol}: duplicate bars across pages")
    if len(out) < min_coverage * expected:
        raise FetchFailed(
            f"{symbol}: short 1m series {len(out)} < {min_coverage}x{expected:.0f} expected")
    return out
