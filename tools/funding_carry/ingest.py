"""Download Binance Vision bulk funding + perp mark klines into data/funding.db.

The bulk fundingRate CSV schema is mapped by header inspection (column names vary
by era: calc_time/last_funding_rate vs fundingTime/fundingRate). markPriceKlines is
standard kline-shaped. Read-only on ohlcv.db. Network is used here only."""
from __future__ import annotations
import csv
import io
import sqlite3
import urllib.request
import zipfile
from contextlib import closing
from .constants import BULK_BASE, FUNDING_DB, CANDIDATE_SYMBOLS, WINDOW_START, WINDOW_END

_TIME_KEYS = ("funding_time_ms", "fundingtime", "calc_time", "calctime")
_RATE_KEYS = ("funding_rate", "fundingrate", "last_funding_rate", "lastfundingrate")


def parse_funding_rows(header: list[str], rows: list[list[str]]) -> list[tuple[int, float]]:
    """Map a funding CSV (any known schema) to [(funding_time_ms, funding_rate)]."""
    norm = [h.strip().lower() for h in header]
    ti = next(i for i, h in enumerate(norm) if h in _TIME_KEYS)
    ri = next(i for i, h in enumerate(norm) if h in _RATE_KEYS)
    out = []
    for r in rows:
        out.append((int(float(r[ti])), float(r[ri])))
    return out


def _months(start: str, end: str) -> list[str]:
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    res, y, m = [], sy, sm
    while (y, m) <= (ey, em):
        res.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return res


def _fetch_zip_csv(url: str) -> tuple[list[str], list[list[str]]] | None:
    """Download a Binance Vision .zip, return (header, rows) of its single CSV.
    Returns None on 404 (month not published)."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8")
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    # Some files have a header row; some are headerless. Detect: if first cell is non-numeric.
    first = reader[0][0].strip().lower()
    if any(c.isalpha() for c in first):
        return reader[0], reader[1:]
    # headerless fundingRate (older): calc_time,funding_interval_hours,last_funding_rate
    return ["calc_time", "funding_interval_hours", "last_funding_rate"], reader


def ingest_all(db_path: str = FUNDING_DB) -> dict:
    """Populate funding.db for all candidate symbols over the window. Returns coverage summary."""
    months = _months(WINDOW_START, WINDOW_END)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("CREATE TABLE IF NOT EXISTS funding("
                    "symbol TEXT, funding_time_ms INTEGER, funding_rate REAL,"
                    "PRIMARY KEY(symbol, funding_time_ms))")
        con.execute("CREATE TABLE IF NOT EXISTS perp_klines("
                    "symbol TEXT, open_time INTEGER, close REAL,"
                    "PRIMARY KEY(symbol, open_time))")
        summary = {}
        for sym in CANDIDATE_SYMBOLS:
            nf, nk = 0, 0
            for mo in months:
                fu = _fetch_zip_csv(f"{BULK_BASE}/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip")
                if fu:
                    hdr, rows = fu
                    for t, rate in parse_funding_rows(hdr, rows):
                        con.execute("INSERT OR IGNORE INTO funding VALUES(?,?,?)", (sym, t, rate))
                        nf += 1
                kl = _fetch_zip_csv(f"{BULK_BASE}/markPriceKlines/{sym}/1h/{sym}-1h-{mo}.zip")
                if kl:
                    _, rows = kl
                    for r in rows:
                        con.execute("INSERT OR IGNORE INTO perp_klines VALUES(?,?,?)",
                                    (sym, int(float(r[0])), float(r[4])))  # open_time, close
                        nk += 1
            summary[sym] = {"funding_rows": nf, "perp_klines": nk}
        con.commit()
    return summary


if __name__ == "__main__":
    print(ingest_all())
