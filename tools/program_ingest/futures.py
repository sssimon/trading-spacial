"""T0-bis: ingest perp USDT-M (klines 1h de trade + funding) para el sub-universo
panel ∩ perps de Vision. Habilita la celda 4 (stat-arb en perps, funding neto).

Misma naturaleza que T0: ingest TOTAL, no mira resultados ni estrategias. La
selección de pares es del estudio (pre-registrada en su spec). El listing de
Vision retiene perps delistados — point-in-time, no snapshot de fapi (que
además está geo-bloqueado 451 desde esta máquina).

Funding CSV: mismo esquema bi-era que funding_carry (calc_time/last_funding_rate
vs fundingTime/fundingRate) — mapeo por inspección de header.
Usage: python -m tools.program_ingest.futures
"""
from __future__ import annotations
import json
import pathlib
import sqlite3
import sys
import time
from contextlib import closing

from .constants import OUTPUT_DIR, PROGRAM_DB, WINDOW_END, WINDOW_START
from .download import _fetch_zip_csv, parse_kline_rows
from .universe import _fetch_listing, parse_listing_page

FUTURES_KLINES_PREFIX = "data/futures/um/monthly/klines/"
FUTURES_FUNDING_PREFIX = "data/futures/um/monthly/fundingRate/"
VISION_DOWNLOAD = "https://data.binance.vision/"

_TIME_KEYS = ("funding_time_ms", "fundingtime", "calc_time", "calctime")
_RATE_KEYS = ("funding_rate", "fundingrate", "last_funding_rate", "lastfundingrate")


def parse_funding_rows(header: list[str], rows: list[list[str]]) -> list[tuple[int, float]]:
    """Map a funding CSV (any known schema) to [(funding_time_ms, funding_rate)]."""
    norm = [h.strip().lower() for h in header]
    try:
        ti = next(i for i, h in enumerate(norm) if h in _TIME_KEYS)
    except StopIteration:
        raise ValueError(f"no time column in funding header: {header}") from None
    try:
        ri = next(i for i, h in enumerate(norm) if h in _RATE_KEYS)
    except StopIteration:
        raise ValueError(f"no rate column in funding header: {header}") from None
    return [(int(float(r[ti])), float(r[ri])) for r in rows]


def list_perp_symbols() -> set[str]:
    """All USDT-M perp symbol dirs in Vision (delisted retained)."""
    perps, marker = set(), None
    while True:
        page = _fetch_listing(FUTURES_KLINES_PREFIX, marker)
        prefixes, _, marker = parse_listing_page(page)
        for p in prefixes:
            sym = p[len(FUTURES_KLINES_PREFIX):].strip("/")
            if sym:
                perps.add(sym)
        if marker is None:
            break
    return perps


def init_futures_tables(db_path: str = PROGRAM_DB) -> None:
    with closing(sqlite3.connect(db_path)) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS perp_klines("
            "symbol TEXT NOT NULL, open_time INTEGER NOT NULL,"
            "open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
            "close REAL NOT NULL, volume REAL NOT NULL,"
            "PRIMARY KEY(symbol, open_time)) WITHOUT ROWID"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS perp_funding("
            "symbol TEXT NOT NULL, funding_time_ms INTEGER NOT NULL,"
            "funding_rate REAL NOT NULL,"
            "PRIMARY KEY(symbol, funding_time_ms)) WITHOUT ROWID"
        )
        # Resume ledger compartido con kind para no chocar con el de spot.
        con.execute(
            "CREATE TABLE IF NOT EXISTS futures_ingest_log("
            "kind TEXT NOT NULL, symbol TEXT NOT NULL, month TEXT NOT NULL,"
            "rows INTEGER NOT NULL, PRIMARY KEY(kind, symbol, month)) WITHOUT ROWID"
        )
        con.commit()


def _months_in_window() -> list[str]:
    fy, fm = int(WINDOW_START[:4]), int(WINDOW_START[5:7])
    ly, lm = int(WINDOW_END[:4]), int(WINDOW_END[5:7])
    res, y, m = [], fy, fm
    while (y, m) <= (ly, lm):
        res.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return res


def download_perp_symbol(symbol: str, months: list[str], db_path: str = PROGRAM_DB) -> dict:
    """Klines 1h + funding for one perp symbol. Resumable via futures_ingest_log."""
    counts = {"klines": 0, "funding": 0, "gap_months": []}
    with closing(sqlite3.connect(db_path)) as con:
        done = {(k, mo) for k, mo in con.execute(
            "SELECT kind, month FROM futures_ingest_log WHERE symbol = ?", (symbol,)
        ).fetchall()}
        for mo in months:
            if ("klines", mo) not in done:
                res = _fetch_zip_csv(
                    f"{VISION_DOWNLOAD}{FUTURES_KLINES_PREFIX}{symbol}/1h/"
                    f"{symbol}-1h-{mo}.zip")
                n = 0
                if res and res[1]:
                    for row in parse_kline_rows(res[1]):
                        con.execute(
                            "INSERT OR IGNORE INTO perp_klines VALUES(?,?,?,?,?,?,?)",
                            (symbol, *row))
                        n += 1
                con.execute("INSERT OR REPLACE INTO futures_ingest_log VALUES(?,?,?,?)",
                            ("klines", symbol, mo, n))
                if n == 0:
                    counts["gap_months"].append(mo)
                counts["klines"] += n
            if ("funding", mo) not in done:
                res = _fetch_zip_csv(
                    f"{VISION_DOWNLOAD}{FUTURES_FUNDING_PREFIX}{symbol}/"
                    f"{symbol}-fundingRate-{mo}.zip")
                n = 0
                if res and res[0]:
                    for t, rate in parse_funding_rows(res[0], res[1]):
                        con.execute(
                            "INSERT OR IGNORE INTO perp_funding VALUES(?,?,?)",
                            (symbol, t, rate))
                        n += 1
                con.execute("INSERT OR REPLACE INTO futures_ingest_log VALUES(?,?,?,?)",
                            ("funding", symbol, mo, n))
                counts["funding"] += n
        con.commit()
    return counts


def main() -> int:
    out = pathlib.Path(OUTPUT_DIR)
    universe = json.loads((out / "universe.json").read_text(encoding="utf-8"))
    panel = set(universe["panel"])
    print("listando perps de Vision...")
    perps = list_perp_symbols()
    target = sorted(panel & perps)
    print(f"panel x perps: {len(target)} simbolos")  # ASCII: consola Windows cp1252

    init_futures_tables(PROGRAM_DB)
    months = _months_in_window()
    coverage: dict[str, dict] = {}
    t0 = time.time()
    for i, sym in enumerate(target):
        coverage[sym] = download_perp_symbol(sym, months)
        print(f"[{i + 1}/{len(target)}] {sym}: {coverage[sym]['klines']}k/"
              f"{coverage[sym]['funding']}f ({len(coverage[sym]['gap_months'])} gaps)"
              f" — {time.time() - t0:.0f}s", file=sys.stderr)
    (out / "futures_coverage.json").write_text(
        json.dumps({
            "window": [WINDOW_START, WINDOW_END],
            "db": PROGRAM_DB,
            "symbols": coverage,
            "totals": {
                "symbols": len(coverage),
                "kline_rows": sum(c["klines"] for c in coverage.values()),
                "funding_rows": sum(c["funding"] for c in coverage.values()),
            },
        }, indent=2, sort_keys=True), encoding="utf-8")
    print(f"futures_coverage.json escrito: {len(coverage)} símbolos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
