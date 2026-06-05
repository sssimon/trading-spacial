"""Regla del universo (spec 2026-06-05, pre-registrada): enumerar el listing S3
de Binance Vision, filtrar por forma/exclusión declarada, partir el resto en
panel (archivo 2021-01 presente) vs listed_later. Delistados se quedan.

Solo este módulo y download.py tocan red; la regla pura es testeable offline.
"""
from __future__ import annotations
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .constants import (
    COVERAGE_OK_THRESHOLD,
    EXCLUDED_BASES,
    LEVERAGED_SUFFIXES,
    SPOT_KLINES_PREFIX,
    TIMEFRAME,
    VISION_LISTING,
    WINDOW_END,
    WINDOW_START,
)

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_MONTH_RE = re.compile(r"-1h-(\d{4}-\d{2})\.zip$")


def parse_listing_page(xml_text: str) -> tuple[list[str], list[str], str | None]:
    """Parse one S3 listing page → (common_prefixes, keys, next_marker|None).

    Only <CommonPrefixes><Prefix> children are taken (the top-level <Prefix>
    is the query echo, not a result).
    """
    root = ET.fromstring(xml_text)
    prefixes = [cp.findtext(f"{_S3_NS}Prefix") or ""
                for cp in root.iter(f"{_S3_NS}CommonPrefixes")]
    prefixes = [p for p in prefixes if p]
    keys = [el.text for el in root.iter(f"{_S3_NS}Key") if el.text]
    truncated = (root.findtext(f"{_S3_NS}IsTruncated") or "false").lower() == "true"
    marker = root.findtext(f"{_S3_NS}NextMarker") if truncated else None
    if truncated and marker is None:
        # Without delimiter S3 omits NextMarker; with delimiter it is present.
        # Fall back to the last key/prefix seen.
        marker = (keys or prefixes)[-1] if (keys or prefixes) else None
    return prefixes, keys, marker


def _fetch_listing(prefix: str, marker: str | None = None, *, retries: int = 4) -> str:
    # quote(): the listing contains at least one directory with non-ASCII
    # characters in its name; an unencoded prefix breaks http.client (ascii).
    url = f"{VISION_LISTING}{urllib.parse.quote(prefix)}"
    if marker:
        url += f"&marker={urllib.parse.quote(marker)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def list_symbols() -> list[str]:
    """Enumerate every symbol directory under spot/monthly/klines/ (paginated)."""
    symbols, marker = [], None
    while True:
        page = _fetch_listing(SPOT_KLINES_PREFIX, marker)
        prefixes, _, marker = parse_listing_page(page)
        for p in prefixes:
            sym = p[len(SPOT_KLINES_PREFIX):].strip("/")
            if sym:
                symbols.append(sym)
        if marker is None:
            break
    return symbols


def list_months(symbol: str) -> list[str]:
    """List the YYYY-MM months with a published 1h monthly zip for `symbol`."""
    prefix = f"{SPOT_KLINES_PREFIX}{symbol}/{TIMEFRAME}/"
    months, marker = [], None
    while True:
        page = _fetch_listing(prefix, marker)
        _, keys, marker = parse_listing_page(page)
        for k in keys:
            m = _MONTH_RE.search(k)
            if m:
                months.append(m.group(1))
        if marker is None:
            break
    return sorted(set(months))


def classify_symbol(symbol: str) -> str | None:
    """Apply the form/exclusion filters (spec §Regla 2-3).

    Returns the exclusion reason, or None if the symbol survives the filters.
    """
    if not symbol.endswith("USDT"):
        return "not_usdt"
    if any(symbol.endswith(suf) for suf in LEVERAGED_SUFFIXES):
        return "leveraged"
    base = symbol[: -len("USDT")]
    if base in EXCLUDED_BASES:
        return "excluded_base"
    return None


def months_in_window(months: list[str]) -> list[str]:
    return [m for m in months if WINDOW_START <= m <= WINDOW_END]


def expected_months(first: str, last: str) -> int:
    """Count of calendar months from `first` to `last`, inclusive."""
    fy, fm = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    return (ly - fy) * 12 + (lm - fm) + 1


def build_universe(symbol_months: dict[str, list[str]]) -> dict:
    """Apply the pre-registered rule to {symbol: [available months]}.

    Pure function — the whole rule is testeable offline. Coverage is REPORTED,
    never used to exclude (spec §Regla 6).
    """
    panel, listed_later, excluded = {}, [], {}
    for sym in sorted(symbol_months):
        reason = classify_symbol(sym)
        if reason is not None:
            excluded.setdefault(reason, []).append(sym)
            continue
        months = months_in_window(symbol_months[sym])
        if not months:
            excluded.setdefault("no_data_in_window", []).append(sym)
            continue
        if WINDOW_START not in months:
            listed_later.append(sym)
            continue
        first, last = months[0], months[-1]
        n_expected = expected_months(first, last)
        coverage = len(months) / n_expected if n_expected else 0.0
        panel[sym] = {
            "first_month": first,
            "last_month": last,
            "months_present": len(months),
            "months_expected": n_expected,
            "coverage_ok": coverage >= COVERAGE_OK_THRESHOLD,
            "delisted_in_window": last < WINDOW_END,
        }
    return {
        "rule": "spec 2026-06-05-programa-t0-ingest-universo.md (pre-registrada)",
        "window": [WINDOW_START, WINDOW_END],
        "panel": panel,
        "listed_later": listed_later,
        "excluded": excluded,
        "counts": {
            "enumerated": len(symbol_months),
            "panel": len(panel),
            "listed_later": len(listed_later),
            "excluded": sum(len(v) for v in excluded.values()),
        },
    }


def enumerate_universe(progress: bool = True) -> dict:
    """Network entrypoint: listing → per-symbol months → rule. Returns universe dict."""
    symbols = list_symbols()
    symbol_months: dict[str, list[str]] = {}
    for i, sym in enumerate(symbols):
        # Skip per-symbol month listings for symbols the form filter kills:
        # one request saved per excluded symbol, and the reason is recorded
        # by build_universe from an empty month list only for survivors.
        if classify_symbol(sym) is not None:
            symbol_months[sym] = []
            continue
        symbol_months[sym] = list_months(sym)
        if progress and (i + 1) % 50 == 0:
            print(f"  listed months {i + 1}/{len(symbols)}", file=sys.stderr)
    return build_universe(symbol_months)
