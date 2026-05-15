#!/usr/bin/env python3
"""Manual-exit pattern EDA — descriptive characterization of operator's MANUAL closes.

Pre-reg: docs/superpowers/plans/2026-05-15-manual-exit-eda-pre-reg.md

Studies the 16 MANUAL exits on curated 10 from papá's prod data backup. Descriptive
EDA only — no verdict tree, no threshold locks.

Four pre-registered dimensions:
- D1: Hold time distribution (winners vs losers)
- D2: Exit price vs planned SL/TP distance traveled
- D3: Max favorable/adverse excursion (intra-position reconstruction from 1h OHLCV)
- D4: Per-symbol patterns (D1+D2+D3 grouped by symbol)

Subset locked: status=closed AND symbol IN curated 10 AND NOT(pnl_usd=0 AND
entry_price=exit_price) AND exit_reason=MANUAL.

Output (data/retune/2026-05-15-manual-exit-eda/):
- d1_hold_time.json
- d2_sl_tp_distance.json
- d3_excursion.json
- d4_per_symbol.json
- eda_manifest.json

Usage: python tools/manual_exit_eda.py [--signals-db PATH] [--ohlcv-db PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Final

import numpy as np

CURATED_10: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_SIGNALS_DB: Final[Path] = Path(r"C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db")
DEFAULT_OHLCV_DB: Final[Path] = REPO_ROOT / "data" / "ohlcv.db"
DEFAULT_OUTPUT_DIR: Final[Path] = REPO_ROOT / "data" / "retune" / "2026-05-15-manual-exit-eda"


def _save_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str, allow_nan=False)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _parse_position_ts(ts: str) -> datetime:
    """positions table uses ISO 8601 with +00:00 or Z suffix."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _stats(values: list[float]) -> dict:
    """Median + percentile + min/max summary."""
    if not values:
        return {"n": 0, "median": None, "mean": None, "p25": None, "p75": None, "min": None, "max": None}
    arr = np.array(values)
    return {
        "n": int(len(arr)),
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Subset loading
# ─────────────────────────────────────────────────────────────────────────────


def load_manual_curated_subset(signals_db: Path) -> list[dict]:
    """16 MANUAL closes on curated 10 post-quality-filter."""
    con = sqlite3.connect(signals_db)
    con.row_factory = sqlite3.Row
    con.text_factory = lambda x: x.decode("utf-8", errors="replace")
    placeholders = ",".join("?" * len(CURATED_10))
    rows = con.execute(f"""
        SELECT id, symbol, direction, status, entry_price, entry_ts,
               sl_price, tp_price, size_usd, exit_price, exit_ts,
               exit_reason, pnl_usd, pnl_pct, atr_entry
        FROM positions
        WHERE status = 'closed'
        AND symbol IN ({placeholders})
        AND exit_reason = 'MANUAL'
        AND NOT (pnl_usd = 0 AND entry_price = exit_price)
        ORDER BY entry_ts
    """, CURATED_10).fetchall()
    con.close()
    return [dict(r) for r in rows]


def query_ohlcv_range(
    ohlcv_db: Path,
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    timeframe: str = "1h",
) -> list[dict]:
    """All OHLCV bars in [start_dt, end_dt] for symbol/timeframe."""
    con = sqlite3.connect(ohlcv_db)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    rows = con.execute("""
        SELECT open_time, open, high, low, close
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ?
        AND open_time >= ? AND open_time <= ?
        ORDER BY open_time
    """, (symbol, timeframe, start_ms, end_ms)).fetchall()
    con.close()
    return [
        {"open_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# D1 — Hold time
# ─────────────────────────────────────────────────────────────────────────────


def compute_d1_hold_time(positions: list[dict]) -> dict:
    """Hold time distribution per position, stratified winners vs losers."""
    per_position = []
    for p in positions:
        entry = _parse_position_ts(p["entry_ts"])
        exit_t = _parse_position_ts(p["exit_ts"])
        hours = (exit_t - entry).total_seconds() / 3600.0
        per_position.append({
            "id": int(p["id"]),
            "symbol": p["symbol"],
            "direction": p["direction"],
            "hold_hours": round(hours, 2),
            "pnl_usd": float(p["pnl_usd"]),
            "pnl_pct": float(p["pnl_pct"]),
            "is_winner": float(p["pnl_usd"]) > 0,
        })
    winners = [x["hold_hours"] for x in per_position if x["is_winner"]]
    losers = [x["hold_hours"] for x in per_position if not x["is_winner"]]
    return {
        "per_position": per_position,
        "all": _stats([x["hold_hours"] for x in per_position]),
        "winners": _stats(winners),
        "losers": _stats(losers),
    }


# ─────────────────────────────────────────────────────────────────────────────
# D2 — Exit vs SL/TP distance traveled
# ─────────────────────────────────────────────────────────────────────────────


def compute_d2_sl_tp_distance(positions: list[dict]) -> dict:
    """% of planned TP distance captured (winners) and % of SL distance traveled (losers)."""
    per_position = []
    pct_of_tp_winners = []
    pct_of_sl_losers = []
    excluded_null_tp = 0
    excluded_null_sl = 0
    excluded_zero_distance = 0

    for p in positions:
        entry = float(p["entry_price"])
        exit_p = float(p["exit_price"])
        pnl_usd = float(p["pnl_usd"])
        direction = 1 if p["direction"] == "LONG" else -1
        tp = p["tp_price"]
        sl = p["sl_price"]

        entry_record = {
            "id": int(p["id"]),
            "symbol": p["symbol"],
            "direction": p["direction"],
            "entry_price": entry,
            "exit_price": exit_p,
            "tp_price": tp,
            "sl_price": sl,
            "pnl_pct": float(p["pnl_pct"]),
            "is_winner": pnl_usd > 0,
            "pct_of_tp_captured": None,
            "pct_of_sl_traveled": None,
        }

        if pnl_usd > 0:
            if tp is None:
                excluded_null_tp += 1
            else:
                tp_distance = abs(float(tp) - entry)
                realized_dist = abs(exit_p - entry)
                if tp_distance <= 0:
                    excluded_zero_distance += 1
                else:
                    pct = realized_dist / tp_distance * 100.0
                    entry_record["pct_of_tp_captured"] = round(pct, 2)
                    pct_of_tp_winners.append(pct)
        else:
            if sl is None:
                excluded_null_sl += 1
            else:
                sl_distance = abs(entry - float(sl))
                realized_loss_dist = abs(entry - exit_p)
                if sl_distance <= 0:
                    excluded_zero_distance += 1
                else:
                    pct = realized_loss_dist / sl_distance * 100.0
                    entry_record["pct_of_sl_traveled"] = round(pct, 2)
                    pct_of_sl_losers.append(pct)
        per_position.append(entry_record)

    return {
        "per_position": per_position,
        "pct_of_tp_captured_winners": _stats(pct_of_tp_winners),
        "pct_of_sl_traveled_losers": _stats(pct_of_sl_losers),
        "excluded": {
            "null_tp_winners": excluded_null_tp,
            "null_sl_losers": excluded_null_sl,
            "zero_distance": excluded_zero_distance,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# D3 — Max favorable/adverse excursion + capture rate
# ─────────────────────────────────────────────────────────────────────────────


def compute_position_excursion(
    position: dict,
    bars: list[dict],
) -> dict:
    """Max favorable + max adverse intra-position excursion + capture rate.

    LONG: favorable = max(high) above entry; adverse = min(low) below entry.
    SHORT: favorable = min(low) below entry; adverse = max(high) above entry.

    capture_rate_pct = realized_pct / max_favorable_pct × 100. Values > 100%
    impossible by construction; < 100% means premature exit; < 0 means exited
    at loss while favorable excursion was positive.
    """
    if not bars:
        return {
            "n_bars": 0,
            "max_favorable_pct": None,
            "max_adverse_pct": None,
            "capture_rate_pct": None,
            "note": "no_ohlcv_bars_in_window",
        }
    entry = float(position["entry_price"])
    realized_pct = float(position["pnl_pct"])
    direction = 1 if position["direction"] == "LONG" else -1

    if direction == 1:  # LONG
        max_high = max(b["high"] for b in bars)
        min_low = min(b["low"] for b in bars)
        max_favorable_pct = (max_high - entry) / entry * 100.0
        max_adverse_pct = (entry - min_low) / entry * 100.0  # positive if went below entry
    else:  # SHORT
        max_high = max(b["high"] for b in bars)
        min_low = min(b["low"] for b in bars)
        max_favorable_pct = (entry - min_low) / entry * 100.0  # short profits if price drops
        max_adverse_pct = (max_high - entry) / entry * 100.0

    if max_favorable_pct > 0:
        capture_rate_pct = realized_pct / max_favorable_pct * 100.0
    else:
        capture_rate_pct = None  # never went favorable

    return {
        "n_bars": len(bars),
        "max_favorable_pct": round(max_favorable_pct, 4),
        "max_adverse_pct": round(max_adverse_pct, 4),
        "capture_rate_pct": round(capture_rate_pct, 2) if capture_rate_pct is not None else None,
    }


def compute_d3_excursion(positions: list[dict], ohlcv_db: Path) -> dict:
    """Per-position max excursion + capture rate aggregates."""
    per_position = []
    capture_rates = []
    max_favorable_pcts = []
    max_adverse_pcts = []
    no_ohlcv_count = 0
    capture_negative_count = 0  # exited at loss while favorable excursion was positive

    for p in positions:
        entry_dt = _parse_position_ts(p["entry_ts"])
        exit_dt = _parse_position_ts(p["exit_ts"])
        bars = query_ohlcv_range(ohlcv_db, p["symbol"], entry_dt, exit_dt)
        exc = compute_position_excursion(p, bars)

        record = {
            "id": int(p["id"]),
            "symbol": p["symbol"],
            "direction": p["direction"],
            "hold_hours": round((exit_dt - entry_dt).total_seconds() / 3600.0, 2),
            "realized_pnl_pct": float(p["pnl_pct"]),
            **exc,
        }
        per_position.append(record)

        if exc["n_bars"] == 0:
            no_ohlcv_count += 1
            continue
        max_favorable_pcts.append(exc["max_favorable_pct"])
        max_adverse_pcts.append(exc["max_adverse_pct"])
        if exc["capture_rate_pct"] is not None:
            capture_rates.append(exc["capture_rate_pct"])
            if exc["capture_rate_pct"] < 0:
                capture_negative_count += 1

    return {
        "per_position": per_position,
        "max_favorable_pct": _stats(max_favorable_pcts),
        "max_adverse_pct": _stats(max_adverse_pcts),
        "capture_rate_pct": _stats(capture_rates),
        "n_no_ohlcv": no_ohlcv_count,
        "n_capture_rate_negative": capture_negative_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# D4 — Per-symbol aggregation
# ─────────────────────────────────────────────────────────────────────────────


def compute_d4_per_symbol(
    d1: dict,
    d2: dict,
    d3: dict,
) -> dict:
    """Group D1/D2/D3 metrics by symbol."""
    by_symbol: dict[str, dict] = {}
    # Index D2 / D3 by id for lookup
    d2_by_id = {r["id"]: r for r in d2["per_position"]}
    d3_by_id = {r["id"]: r for r in d3["per_position"]}

    for d1_rec in d1["per_position"]:
        sym = d1_rec["symbol"]
        by_symbol.setdefault(sym, {
            "n_trades": 0,
            "hold_hours": [],
            "realized_pnl_pct": [],
            "pct_of_tp_captured": [],
            "pct_of_sl_traveled": [],
            "max_favorable_pct": [],
            "capture_rate_pct": [],
            "n_winners": 0,
            "n_losers": 0,
        })
        b = by_symbol[sym]
        b["n_trades"] += 1
        b["hold_hours"].append(d1_rec["hold_hours"])
        b["realized_pnl_pct"].append(d1_rec["pnl_pct"])
        if d1_rec["is_winner"]:
            b["n_winners"] += 1
            tp_capt = d2_by_id.get(d1_rec["id"], {}).get("pct_of_tp_captured")
            if tp_capt is not None:
                b["pct_of_tp_captured"].append(tp_capt)
        else:
            b["n_losers"] += 1
            sl_trav = d2_by_id.get(d1_rec["id"], {}).get("pct_of_sl_traveled")
            if sl_trav is not None:
                b["pct_of_sl_traveled"].append(sl_trav)
        d3_rec = d3_by_id.get(d1_rec["id"], {})
        if d3_rec.get("max_favorable_pct") is not None:
            b["max_favorable_pct"].append(d3_rec["max_favorable_pct"])
        if d3_rec.get("capture_rate_pct") is not None:
            b["capture_rate_pct"].append(d3_rec["capture_rate_pct"])

    # Reduce raw lists to summary stats
    out = {}
    for sym, b in by_symbol.items():
        out[sym] = {
            "n_trades": b["n_trades"],
            "n_winners": b["n_winners"],
            "n_losers": b["n_losers"],
            "hold_hours": _stats(b["hold_hours"]),
            "realized_pnl_pct": _stats(b["realized_pnl_pct"]),
            "pct_of_tp_captured": _stats(b["pct_of_tp_captured"]),
            "pct_of_sl_traveled": _stats(b["pct_of_sl_traveled"]),
            "max_favorable_pct": _stats(b["max_favorable_pct"]),
            "capture_rate_pct": _stats(b["capture_rate_pct"]),
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signals-db", default=str(DEFAULT_SIGNALS_DB))
    p.add_argument("--ohlcv-db", default=str(DEFAULT_OHLCV_DB))
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    signals_db = Path(args.signals_db)
    ohlcv_db = Path(args.ohlcv_db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not signals_db.exists():
        sys.stderr.write(f"ERROR: signals.db not found at {signals_db}\n")
        return 1
    if not ohlcv_db.exists():
        sys.stderr.write(f"ERROR: ohlcv.db not found at {ohlcv_db}\n")
        return 1

    positions = load_manual_curated_subset(signals_db)
    print(f"[eda] MANUAL curated 10 quality-filtered subset: n={len(positions)}")

    print("[eda] D1 hold time...")
    d1 = compute_d1_hold_time(positions)
    _save_json(output_dir / "d1_hold_time.json", d1)
    print(f"[eda] D1: all median={d1['all']['median']:.1f}h "
          f"winners median={d1['winners']['median']:.1f}h (n={d1['winners']['n']}) "
          f"losers median={d1['losers']['median']:.1f}h (n={d1['losers']['n']})")

    print("[eda] D2 SL/TP distance...")
    d2 = compute_d2_sl_tp_distance(positions)
    _save_json(output_dir / "d2_sl_tp_distance.json", d2)
    tp_capt = d2["pct_of_tp_captured_winners"]
    sl_trav = d2["pct_of_sl_traveled_losers"]
    print(f"[eda] D2: pct_of_TP_captured (winners, n={tp_capt['n']}): "
          f"median={tp_capt['median']:.1f}%" if tp_capt['n'] else "[eda] D2: no winners with TP")
    print(f"[eda]     pct_of_SL_traveled (losers, n={sl_trav['n']}): "
          f"median={sl_trav['median']:.1f}%" if sl_trav['n'] else "[eda] D2: no losers with SL")

    print("[eda] D3 max excursion (intra-bar reconstruction)...")
    d3 = compute_d3_excursion(positions, ohlcv_db)
    _save_json(output_dir / "d3_excursion.json", d3)
    cap = d3["capture_rate_pct"]
    mfp = d3["max_favorable_pct"]
    print(f"[eda] D3: max_favorable_pct median={mfp['median']:.2f}% (n={mfp['n']})")
    print(f"[eda]     capture_rate_pct median={cap['median']:.1f}% (n={cap['n']}, "
          f"n_negative={d3['n_capture_rate_negative']})")

    print("[eda] D4 per-symbol...")
    d4 = compute_d4_per_symbol(d1, d2, d3)
    _save_json(output_dir / "d4_per_symbol.json", d4)
    print(f"[eda] D4: {len(d4)} symbols with MANUAL closes")

    _save_json(output_dir / "eda_manifest.json", {
        "schema_version": 1,
        "spec_ref": "docs/superpowers/plans/2026-05-15-manual-exit-eda-pre-reg.md",
        "code_commit": _git_commit(),
        "signals_db_path": str(signals_db),
        "ohlcv_db_path": str(ohlcv_db),
        "subset": {
            "filter": "status=closed AND symbol IN curated_10 AND exit_reason=MANUAL AND NOT(zero_pnl_anomaly)",
            "curated_symbols": list(CURATED_10),
            "n_positions": len(positions),
        },
        "dimensions": ["D1_hold_time", "D2_sl_tp_distance", "D3_excursion", "D4_per_symbol"],
        "intra_bar_timeframe": "1h",
    })
    print(f"[eda] artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
