"""Load the frozen 27 from papá's DB; drop the un-reconstructable 16.

Reconstructibility gate (spec §2): a closed position is kept iff its symbol is in
KEEP_SYMBOLS (full 5m coverage) AND it has ≥ATR_PERIOD 1h bars before entry_ts.
The keep-set is MANUAL+SL_HIT only (the 2 TP_HIT fall on dropped symbols)."""
from __future__ import annotations
import sqlite3
from contextlib import closing
from datetime import datetime
from .constants import KEEP_SYMBOLS, ATR_PERIOD, ATR_TF


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _has_pre_entry_1h(ohlcv_db: str, symbol: str, entry_ts: datetime) -> bool:
    end_ms = int(entry_ts.timestamp() * 1000)
    with closing(sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)) as con:
        n = con.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time < ?",
            (symbol, ATR_TF, end_ms),
        ).fetchone()[0]
    return n >= ATR_PERIOD


def load_population(papa_db: str, ohlcv_db: str) -> tuple[list[dict], list[dict]]:
    """Return (kept_positions, dropped_summary).

    kept_positions: list of dicts with id, symbol, direction, entry_price, entry_ts
    (datetime), exit_price, exit_ts (datetime), qty, exit_reason, pnl_usd.
    dropped_summary: [{"symbol":..., "n":...}] counting every dropped closed position,
    whether dropped for being off KEEP_SYMBOLS or for having <ATR_PERIOD pre-entry 1h bars.
    """
    with closing(sqlite3.connect(f"file:{papa_db}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, symbol, direction, entry_price, entry_ts, exit_price, exit_ts, "
            "qty, exit_reason, pnl_usd FROM positions WHERE status='closed'"
        ).fetchall()

    kept, dropped_counts = [], {}
    for r in rows:
        sym = r["symbol"]
        if sym not in KEEP_SYMBOLS:
            dropped_counts[sym] = dropped_counts.get(sym, 0) + 1
            continue
        entry_ts = _parse_ts(r["entry_ts"])
        if not _has_pre_entry_1h(ohlcv_db, sym, entry_ts):
            dropped_counts[sym] = dropped_counts.get(sym, 0) + 1
            continue
        kept.append({
            "id": int(r["id"]), "symbol": sym, "direction": r["direction"],
            "entry_price": float(r["entry_price"]), "entry_ts": entry_ts,
            "exit_price": float(r["exit_price"]), "exit_ts": _parse_ts(r["exit_ts"]),
            "qty": float(r["qty"]), "exit_reason": r["exit_reason"],
            "pnl_usd": float(r["pnl_usd"]),
        })
    dropped = [{"symbol": s, "n": n} for s, n in sorted(dropped_counts.items())]
    return kept, dropped
