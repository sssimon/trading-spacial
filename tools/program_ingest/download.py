"""Bulk download de klines spot 1h → data/program_ohlcv.db.

Clona el patrón de red de tools/funding_carry/ingest.py (paquete IRREVOCABLE,
no se importa de él — spec §Negative space). Diferencias del mundo spot:
- CSV: open_time,open,high,low,close,volume,close_time,quote_vol,count,... ;
  header presente o ausente según la era (detección por celda alfabética).
- Timestamps: Binance Vision cambió open_time de ms a MICROSEGUNDOS en los
  archivos spot desde 2025-01. Se normaliza todo a ms (regla: >1e14 → //1000).
"""
from __future__ import annotations
import csv
import io
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import closing

from .constants import PROGRAM_DB, TIMEFRAME, VISION_DOWNLOAD, SPOT_KLINES_PREFIX

_MS_CUTOFF = 100_000_000_000_000  # 1e14: any open_time above this is microseconds


def normalize_open_time(raw: float) -> int:
    """Normalize a Vision kline open_time to milliseconds."""
    t = int(raw)
    if t > _MS_CUTOFF:
        return t // 1000
    return t


def parse_kline_rows(rows: list[list[str]]) -> list[tuple[int, float, float, float, float, float]]:
    """Map kline CSV rows → [(open_time_ms, open, high, low, close, volume)]."""
    out = []
    for r in rows:
        out.append((
            normalize_open_time(float(r[0])),
            float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
        ))
    return out


def _fetch_zip_csv(url: str, *, retries: int = 4) -> tuple[list[str], list[list[str]]] | None:
    """Download a Binance Vision .zip → (header, rows). None on 404 / dead retry.

    Same contract as funding_carry's twin: the CDN 404s unpublished months and
    occasionally 200s an HTML error page (BadZipFile → None).
    """
    blob = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                blob = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if attempt == retries - 1:
                print(f"  skip after {retries} retries: {url} ({e})", file=sys.stderr)
                return None
        time.sleep(1.5 * (attempt + 1))
    if blob is None:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = z.namelist()
            if not names:
                return None
            text = z.read(names[0]).decode("utf-8")
    except zipfile.BadZipFile:
        return None
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    first = reader[0][0].strip().lower()
    if any(c.isalpha() for c in first):
        return reader[0], reader[1:]
    return [], reader


def init_db(db_path: str = PROGRAM_DB) -> None:
    with closing(sqlite3.connect(db_path)) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS spot_klines("
            "symbol TEXT NOT NULL, open_time INTEGER NOT NULL,"
            "open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
            "close REAL NOT NULL, volume REAL NOT NULL,"
            "PRIMARY KEY(symbol, open_time)) WITHOUT ROWID"
        )
        con.commit()


def download_symbol(symbol: str, months: list[str], db_path: str = PROGRAM_DB) -> dict:
    """Download the given months for one symbol. Returns {month: rows_ingested}."""
    per_month: dict[str, int] = {}
    with closing(sqlite3.connect(db_path)) as con:
        for mo in months:
            url = (f"{VISION_DOWNLOAD}{SPOT_KLINES_PREFIX}{symbol}/{TIMEFRAME}/"
                   f"{symbol}-{TIMEFRAME}-{mo}.zip")
            res = _fetch_zip_csv(url)
            n = 0
            if res and res[1]:
                for row in parse_kline_rows(res[1]):
                    con.execute(
                        "INSERT OR IGNORE INTO spot_klines VALUES(?,?,?,?,?,?,?)",
                        (symbol, *row),
                    )
                    n += 1
            per_month[mo] = n
        con.commit()  # per-symbol commit: a mid-run failure keeps prior work
    return per_month
