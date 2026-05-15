#!/usr/bin/env python3
"""Market context at MANUAL exit moment — feature engineering for rule discovery.

Pre-reg: docs/superpowers/plans/2026-05-15-exit-market-context-pre-reg.md

For each of 16 MANUAL closes on curated 10, extract OHLCV-derived features at
exit_ts. Hypothesis suite H1-H6:
- H1 exit bar pattern (color, close position, range/ATR)
- H2 local extremum (distance from 5-bar high/low, new extremum flag)
- H3 momentum (3-bar momentum, deceleration flag)
- H4 volatility (move from entry in ATR multiples)
- H5 time-favorable interaction (time to +5%, time since peak)
- H6 post-exit hindsight (4h forward price action + exit quality classification)

Subset locked: curated 10, MANUAL, post quality filter (16 positions).

Output (data/retune/2026-05-15-manual-exit-market-context/):
- features.json
- clustering_summary.json
- exit_quality.json
- manifest.json
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Final

import numpy as np

CURATED_10: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

ATR_PERIOD: Final[int] = 14
ROLLING_BARS: Final[int] = 5
MOMENTUM_LOOKBACK: Final[int] = 3
POST_EXIT_HOURS: Final[int] = 4
EXIT_QUALITY_THRESHOLD_PCT: Final[float] = 1.0  # pre-reg §2.1

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_SIGNALS_DB: Final[Path] = Path(r"C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db")
DEFAULT_OHLCV_DB: Final[Path] = REPO_ROOT / "data" / "ohlcv.db"
DEFAULT_OUTPUT_DIR: Final[Path] = REPO_ROOT / "data" / "retune" / "2026-05-15-manual-exit-market-context"


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
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _safe_float(x):
    """Convert NaN/Inf to None for JSON serialization."""
    if x is None:
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────


def load_manual_curated_subset(signals_db: Path) -> list[dict]:
    con = sqlite3.connect(signals_db)
    con.row_factory = sqlite3.Row
    con.text_factory = lambda x: x.decode("utf-8", errors="replace")
    ph = ",".join("?" * len(CURATED_10))
    rows = con.execute(f"""
        SELECT id, symbol, direction, entry_price, entry_ts,
               exit_price, exit_ts, pnl_usd, pnl_pct, atr_entry
        FROM positions
        WHERE status = 'closed'
        AND symbol IN ({ph})
        AND exit_reason = 'MANUAL'
        AND NOT (pnl_usd = 0 AND entry_price = exit_price)
        ORDER BY entry_ts
    """, CURATED_10).fetchall()
    con.close()
    return [dict(r) for r in rows]


def query_ohlcv_window(
    ohlcv_db: Path,
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    tf: str = "1h",
) -> list[dict]:
    con = sqlite3.connect(ohlcv_db)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    rows = con.execute("""
        SELECT open_time, open, high, low, close
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ?
        AND open_time >= ? AND open_time <= ?
        ORDER BY open_time
    """, (symbol, tf, start_ms, end_ms)).fetchall()
    con.close()
    return [
        {"open_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Feature primitives
# ─────────────────────────────────────────────────────────────────────────────


def find_exit_bar_index(bars: list[dict], exit_ts: datetime) -> int | None:
    """Index of bar whose open_time covers exit_ts (i.e., open_time <= exit_ts < open_time + 1h)."""
    target_ms = int(exit_ts.timestamp() * 1000)
    for i, bar in enumerate(bars):
        if bar["open_time"] <= target_ms < bar["open_time"] + 3600 * 1000:
            return i
    # Fallback: bar with open_time closest at-or-before exit_ts
    eligible = [i for i, b in enumerate(bars) if b["open_time"] <= target_ms]
    if eligible:
        return eligible[-1]
    return None


def compute_atr(bars: list[dict], end_idx: int, period: int = ATR_PERIOD) -> float | None:
    """ATR over `period` bars ending at end_idx (exclusive of end_idx)."""
    if end_idx < period:
        return None
    trs = []
    for i in range(end_idx - period, end_idx):
        if i <= 0:
            continue
        h = bars[i]["high"]
        l = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    if not trs:
        return None
    return mean(trs)


# ─────────────────────────────────────────────────────────────────────────────
# H1 — Exit bar pattern
# ─────────────────────────────────────────────────────────────────────────────


def compute_h1_bar_pattern(exit_bar: dict, atr: float | None, direction: str) -> dict:
    o = exit_bar["open"]
    h = exit_bar["high"]
    l = exit_bar["low"]
    c = exit_bar["close"]
    bar_range = h - l
    if bar_range > 0:
        close_position = (c - l) / bar_range
    else:
        close_position = None
    bar_color = "green" if c > o else ("red" if c < o else "doji")
    # Direction-relative interpretation
    if direction == "LONG":
        color_relative = "favorable" if bar_color == "green" else ("adverse" if bar_color == "red" else "neutral")
    else:  # SHORT
        color_relative = "favorable" if bar_color == "red" else ("adverse" if bar_color == "green" else "neutral")
    range_atr_ratio = bar_range / atr if (atr is not None and atr > 0) else None
    return {
        "exit_bar_color": bar_color,
        "color_relative_to_direction": color_relative,
        "exit_bar_close_position": _safe_float(close_position),
        "exit_bar_range_atr_ratio": _safe_float(range_atr_ratio),
    }


# ─────────────────────────────────────────────────────────────────────────────
# H2 — Local extremum
# ─────────────────────────────────────────────────────────────────────────────


def compute_h2_local_extremum(
    bars: list[dict],
    exit_idx: int,
    exit_price: float,
    direction: str,
) -> dict:
    if exit_idx < ROLLING_BARS:
        return {"dist_from_local_extremum_pct": None, "is_new_local_extremum": None}
    prior_bars = bars[exit_idx - ROLLING_BARS:exit_idx]
    if direction == "LONG":
        rolling_high = max(b["high"] for b in prior_bars)
        dist_pct = (rolling_high - exit_price) / exit_price * 100
        is_new_extremum = bars[exit_idx]["high"] > rolling_high
    else:  # SHORT
        rolling_low = min(b["low"] for b in prior_bars)
        dist_pct = (exit_price - rolling_low) / exit_price * 100
        is_new_extremum = bars[exit_idx]["low"] < rolling_low
    return {
        "dist_from_local_extremum_pct": _safe_float(dist_pct),
        "is_new_local_extremum": bool(is_new_extremum),
    }


# ─────────────────────────────────────────────────────────────────────────────
# H3 — Momentum at exit
# ─────────────────────────────────────────────────────────────────────────────


def compute_h3_momentum(bars: list[dict], exit_idx: int, direction: str) -> dict:
    if exit_idx < MOMENTUM_LOOKBACK:
        return {"last_3bar_momentum_pct": None, "momentum_deceleration_flag": None}
    close_now = bars[exit_idx]["close"]
    close_prev_3 = bars[exit_idx - MOMENTUM_LOOKBACK]["close"]
    raw_momentum = (close_now - close_prev_3) / close_prev_3 * 100
    if direction == "LONG":
        momentum_pct = raw_momentum  # positive = favorable
    else:
        momentum_pct = -raw_momentum  # short profits from price drop, so flip sign

    # Deceleration: did last bar's favorable move shrink vs prior bar?
    if exit_idx < 2:
        decel = None
    else:
        bar_last = bars[exit_idx]
        bar_prev = bars[exit_idx - 1]
        bar_prev_2 = bars[exit_idx - 2]
        if direction == "LONG":
            move_last = bar_last["close"] - bar_prev["close"]
            move_prev = bar_prev["close"] - bar_prev_2["close"]
        else:
            move_last = bar_prev["close"] - bar_last["close"]
            move_prev = bar_prev_2["close"] - bar_prev["close"]
        # Deceleration only meaningful if both moves were favorable
        if move_prev > 0:
            decel = move_last < move_prev
        else:
            decel = None  # prior wasn't favorable, deceleration concept N/A
    return {
        "last_3bar_momentum_pct": _safe_float(momentum_pct),
        "momentum_deceleration_flag": decel,
    }


# ─────────────────────────────────────────────────────────────────────────────
# H4 — Volatility / ATR-normalized move
# ─────────────────────────────────────────────────────────────────────────────


def compute_h4_volatility(
    entry_price: float,
    exit_price: float,
    atr_entry: float | None,
    direction: str,
) -> dict:
    if atr_entry is None or atr_entry <= 0:
        return {"move_from_entry_atr_normalized": None}
    raw_move = exit_price - entry_price
    if direction == "LONG":
        move = raw_move
    else:
        move = -raw_move
    atr_normalized = move / atr_entry
    return {"move_from_entry_atr_normalized": _safe_float(atr_normalized)}


# ─────────────────────────────────────────────────────────────────────────────
# H5 — Time-favorable interaction
# ─────────────────────────────────────────────────────────────────────────────


def compute_h5_time_favorable(
    bars: list[dict],
    entry_dt: datetime,
    exit_dt: datetime,
    entry_price: float,
    direction: str,
) -> dict:
    """Find time to first +5% favorable and time since max favorable peak."""
    entry_ms = int(entry_dt.timestamp() * 1000)
    exit_ms = int(exit_dt.timestamp() * 1000)
    in_window = [b for b in bars if entry_ms <= b["open_time"] <= exit_ms]
    if not in_window:
        return {"hours_to_first_favorable_5pct": None, "time_since_max_favorable_hours": None}

    target_5pct = 5.0
    first_favorable_5pct_dt = None
    max_favorable_pct = -float("inf")
    max_favorable_time = None
    for b in in_window:
        # Per-bar max favorable
        if direction == "LONG":
            bar_max_pct = (b["high"] - entry_price) / entry_price * 100
        else:
            bar_max_pct = (entry_price - b["low"]) / entry_price * 100
        if bar_max_pct > max_favorable_pct:
            max_favorable_pct = bar_max_pct
            max_favorable_time = datetime.fromtimestamp(b["open_time"] / 1000, tz=timezone.utc)
        if first_favorable_5pct_dt is None and bar_max_pct >= target_5pct:
            first_favorable_5pct_dt = datetime.fromtimestamp(b["open_time"] / 1000, tz=timezone.utc)

    if first_favorable_5pct_dt is not None:
        hours_to_5pct = (first_favorable_5pct_dt - entry_dt).total_seconds() / 3600
    else:
        hours_to_5pct = None

    if max_favorable_time is not None and max_favorable_pct > 0:
        time_since_peak_hours = (exit_dt - max_favorable_time).total_seconds() / 3600
    else:
        time_since_peak_hours = None

    return {
        "hours_to_first_favorable_5pct": _safe_float(hours_to_5pct),
        "time_since_max_favorable_hours": _safe_float(time_since_peak_hours),
    }


# ─────────────────────────────────────────────────────────────────────────────
# H6 — Post-exit hindsight (4h)
# ─────────────────────────────────────────────────────────────────────────────


def compute_h6_post_exit(
    bars: list[dict],
    exit_dt: datetime,
    exit_price: float,
    direction: str,
    forward_hours: int = POST_EXIT_HOURS,
) -> dict:
    exit_ms = int(exit_dt.timestamp() * 1000)
    forward_end_ms = exit_ms + forward_hours * 3600 * 1000
    forward_bars = [b for b in bars if exit_ms <= b["open_time"] <= forward_end_ms]
    if not forward_bars:
        return {
            "post_exit_4h_favorable_pct": None,
            "post_exit_4h_adverse_pct": None,
            "exit_quality": None,
            "n_forward_bars": 0,
        }
    if direction == "LONG":
        max_high = max(b["high"] for b in forward_bars)
        min_low = min(b["low"] for b in forward_bars)
        favorable_pct = (max_high - exit_price) / exit_price * 100
        adverse_pct = (exit_price - min_low) / exit_price * 100
    else:  # SHORT
        max_high = max(b["high"] for b in forward_bars)
        min_low = min(b["low"] for b in forward_bars)
        favorable_pct = (exit_price - min_low) / exit_price * 100
        adverse_pct = (max_high - exit_price) / exit_price * 100

    # Classification
    if favorable_pct < EXIT_QUALITY_THRESHOLD_PCT:
        if adverse_pct >= EXIT_QUALITY_THRESHOLD_PCT:
            quality = "REVERSAL_CAUGHT"
        else:
            quality = "GOOD"
    else:
        quality = "PREMATURE"

    return {
        "post_exit_4h_favorable_pct": _safe_float(favorable_pct),
        "post_exit_4h_adverse_pct": _safe_float(adverse_pct),
        "exit_quality": quality,
        "n_forward_bars": len(forward_bars),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-position feature record
# ─────────────────────────────────────────────────────────────────────────────


def compute_features_for_position(
    position: dict,
    ohlcv_db: Path,
) -> dict:
    entry_dt = _parse_position_ts(position["entry_ts"])
    exit_dt = _parse_position_ts(position["exit_ts"])
    symbol = position["symbol"]
    direction = position["direction"]
    entry_price = float(position["entry_price"])
    exit_price = float(position["exit_price"])
    atr_entry = position.get("atr_entry")

    # Query OHLCV: 14h trailing for ATR + position window + 4h post-exit
    query_start = entry_dt - timedelta(hours=ATR_PERIOD + 5)
    query_end = exit_dt + timedelta(hours=POST_EXIT_HOURS + 1)
    bars = query_ohlcv_window(ohlcv_db, symbol, query_start, query_end)
    exit_idx = find_exit_bar_index(bars, exit_dt)
    if exit_idx is None or not bars:
        return {
            "id": int(position["id"]),
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": float(position["pnl_pct"]),
            "is_winner": float(position["pnl_usd"]) > 0,
            "error": "no_ohlcv_bars",
        }
    exit_bar = bars[exit_idx]
    atr_at_exit = compute_atr(bars, exit_idx, ATR_PERIOD)

    h1 = compute_h1_bar_pattern(exit_bar, atr_at_exit, direction)
    h2 = compute_h2_local_extremum(bars, exit_idx, exit_price, direction)
    h3 = compute_h3_momentum(bars, exit_idx, direction)
    h4 = compute_h4_volatility(entry_price, exit_price, atr_entry, direction)
    h5 = compute_h5_time_favorable(bars, entry_dt, exit_dt, entry_price, direction)
    h6 = compute_h6_post_exit(bars, exit_dt, exit_price, direction)

    return {
        "id": int(position["id"]),
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": float(position["pnl_pct"]),
        "is_winner": float(position["pnl_usd"]) > 0,
        "atr_entry": _safe_float(atr_entry),
        "atr_at_exit": _safe_float(atr_at_exit),
        "n_bars_in_query": len(bars),
        **h1, **h2, **h3, **h4, **h5, **h6,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Clustering summary
# ─────────────────────────────────────────────────────────────────────────────


def _stats_continuous(values: list[float]) -> dict:
    arr = np.array([v for v in values if v is not None and math.isfinite(v)])
    if len(arr) == 0:
        return {"n": 0, "median": None, "mean": None, "std": None, "p25": None, "p75": None, "min": None, "max": None}
    return {
        "n": int(len(arr)),
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def _frequency_table(values: list) -> dict:
    """Count frequency of each value (for categorical/binary features)."""
    from collections import Counter
    cnt = Counter([v for v in values if v is not None])
    total = sum(cnt.values())
    return {
        "counts": dict(cnt),
        "total": total,
        "max_frequency_pct": (max(cnt.values()) / total * 100) if total else 0,
    }


def build_clustering_summary(features: list[dict]) -> dict:
    """Per-feature distribution stats."""
    long_subset = [f for f in features if f.get("direction") == "LONG"]
    short_subset = [f for f in features if f.get("direction") == "SHORT"]
    winner_subset = [f for f in features if f.get("is_winner")]
    loser_subset = [f for f in features if not f.get("is_winner")]

    continuous_features = [
        "exit_bar_close_position", "exit_bar_range_atr_ratio",
        "dist_from_local_extremum_pct", "last_3bar_momentum_pct",
        "move_from_entry_atr_normalized",
        "hours_to_first_favorable_5pct", "time_since_max_favorable_hours",
        "post_exit_4h_favorable_pct", "post_exit_4h_adverse_pct",
    ]
    binary_categorical_features = [
        "color_relative_to_direction", "is_new_local_extremum",
        "momentum_deceleration_flag", "exit_quality",
    ]

    out = {"continuous": {}, "categorical": {}}
    for feat in continuous_features:
        out["continuous"][feat] = {
            "all": _stats_continuous([f.get(feat) for f in features]),
            "winners": _stats_continuous([f.get(feat) for f in winner_subset]),
            "losers": _stats_continuous([f.get(feat) for f in loser_subset]),
            "long": _stats_continuous([f.get(feat) for f in long_subset]),
            "short": _stats_continuous([f.get(feat) for f in short_subset]),
        }
    for feat in binary_categorical_features:
        out["categorical"][feat] = {
            "all": _frequency_table([f.get(feat) for f in features]),
            "winners": _frequency_table([f.get(feat) for f in winner_subset]),
            "losers": _frequency_table([f.get(feat) for f in loser_subset]),
            "long": _frequency_table([f.get(feat) for f in long_subset]),
            "short": _frequency_table([f.get(feat) for f in short_subset]),
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args():
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
    print(f"[ctx] subset n={len(positions)}")

    print("[ctx] computing features per position (H1-H6, ~12 features)...")
    features = [compute_features_for_position(p, ohlcv_db) for p in positions]
    _save_json(output_dir / "features.json", features)

    print("[ctx] building clustering summary...")
    summary = build_clustering_summary(features)
    _save_json(output_dir / "clustering_summary.json", summary)

    # Exit quality classification
    eq = {
        "by_quality": {},
        "per_position": [
            {
                "id": f["id"], "symbol": f["symbol"], "direction": f["direction"],
                "is_winner": f["is_winner"],
                "post_exit_4h_favorable_pct": f.get("post_exit_4h_favorable_pct"),
                "post_exit_4h_adverse_pct": f.get("post_exit_4h_adverse_pct"),
                "exit_quality": f.get("exit_quality"),
            }
            for f in features
        ],
    }
    for f in features:
        q = f.get("exit_quality")
        if q:
            eq["by_quality"].setdefault(q, []).append(f["id"])
    _save_json(output_dir / "exit_quality.json", eq)

    print("[ctx] summary by exit_quality:")
    for q, ids in eq["by_quality"].items():
        print(f"  {q}: n={len(ids)} (ids={ids})")

    _save_json(output_dir / "manifest.json", {
        "schema_version": 1,
        "spec_ref": "docs/superpowers/plans/2026-05-15-exit-market-context-pre-reg.md",
        "code_commit": _git_commit(),
        "signals_db_path": str(signals_db),
        "ohlcv_db_path": str(ohlcv_db),
        "n_positions": len(features),
        "locks": {
            "subset": "curated_10_manual_quality_filtered",
            "atr_period": ATR_PERIOD,
            "rolling_bars": ROLLING_BARS,
            "momentum_lookback": MOMENTUM_LOOKBACK,
            "post_exit_hours": POST_EXIT_HOURS,
            "exit_quality_threshold_pct": EXIT_QUALITY_THRESHOLD_PCT,
            "ohlcv_timeframe": "1h",
        },
    })
    print(f"[ctx] artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
