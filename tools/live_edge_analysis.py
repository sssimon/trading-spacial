#!/usr/bin/env python3
"""Live-edge analysis (Direction A Phase D2 execution).

Pre-reg: docs/superpowers/plans/2026-05-15-live-edge-analysis-pre-reg.md

Executes 3 sub-questions over papá's production data backup:
- Q1: Strategy P&L vs equal-weighted basket B&H (curated 10 per operator amendment)
- Q2: MANUAL vs SL_HIT exit P&L bootstrap CI
- Q3: APPROVED vs REJECTED signal counterfactual (24h primary + 1h/4h/72h sensitivity)

## Locked thresholds (Q-LE1..Q-LE5 via AskUserQuestion 2026-05-15)

- Q-LE1: Equal-weighted basket of 16 traded symbols — **amended 2026-05-15** to
  curated 10 (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE) after
  finding 6 of 14 closed-position symbols (LINK, SOL, TON, TRX, XAUT, ZEC) lack
  OHLCV cache. Operator rationale: "antes teníamos 20 y curamos a 10, trabajemos
  con las 10 que tenemos actualmente". Documented as runtime amendment.
- Q-LE2: Q1 threshold ≥ 1.0 percentage point.
- Q-LE3: ± 1 hour matching window for Q3 position↔scan link.
- Q-LE4: Tiered verdict (3/3 STRONG, 2/3 PARTIAL [3 sub-combos], 1/3 WEAK, 0/3 NO_EDGE).
- Q-LE5: Q3 threshold ≥ 0.5 percentage point.

## Output (data/retune/2026-05-15-live-edge-analysis/)

- q1_overall_edge.json
- q2_filtering_edge.json
- q3_counterfactual.json
- data_quality.json
- analysis_verdict.json
- analysis_manifest.json

## Usage

  python tools/live_edge_analysis.py [--signals-db PATH] [--ohlcv-db PATH]

Default `--signals-db`: `C:/Users/simon/Desktop/Papa/trading_backup_extracted/signals.db`
Default `--ohlcv-db`: `data/ohlcv.db` (repo-relative).

Papá's signals.db lives in sandbox path OUTSIDE the repo and MUST NOT be committed.
Analysis is read-only on the backup.
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
from typing import Final

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# Constants — pre-reg locks
# ─────────────────────────────────────────────────────────────────────────────

# Q-LE1 amended 2026-05-15: curated 10 (CLAUDE.md DEFAULT_SYMBOLS) instead of
# 16 traded symbols. 6 off-curated traded symbols (LINK, SOL, TON, TRX, XAUT,
# ZEC) lack OHLCV cache; restricting scope preserves methodology consistency.
CURATED_10: Final[tuple[str, ...]] = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

# Q-LE2 + Q-LE3 + Q-LE5
Q1_THRESHOLD_PP: Final[float] = 1.0
Q3_MATCH_HOURS: Final[float] = 1.0
Q3_THRESHOLD_PP: Final[float] = 0.5

# Q3 forward windows (per pre-reg §2.5 + §2.6)
PRIMARY_FORWARD_WINDOW_H: Final[int] = 24
SENSITIVITY_FORWARD_WINDOWS_H: Final[tuple[int, ...]] = (1, 4, 72)

# Window dates (pre-reg §2.3)
WINDOW_START_ISO: Final[str] = "2026-03-30T00:00:00+00:00"
WINDOW_END_ISO: Final[str] = "2026-05-07T23:59:59+00:00"

# Pre-reg §4.4
BOOTSTRAP_N: Final[int] = 10_000
BOOTSTRAP_SEED: Final[int] = 20260515  # reproducibility lock

# Paths
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_SIGNALS_DB: Final[Path] = Path(r"C:\Users\simon\Desktop\Papa\trading_backup_extracted\signals.db")
DEFAULT_OHLCV_DB: Final[Path] = REPO_ROOT / "data" / "ohlcv.db"
DEFAULT_OUTPUT_DIR: Final[Path] = REPO_ROOT / "data" / "retune" / "2026-05-15-live-edge-analysis"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


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


def _iso_to_ms(iso: str) -> int:
    """ISO 8601 -> unix milliseconds (UTC)."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_position_ts(ts: str) -> datetime:
    """positions table uses ISO 8601 timestamps with +00:00 or Z suffix."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _parse_scan_ts(ts: str) -> datetime:
    """scans table uses '2026-03-24 15:34:15 UTC' format (no T separator)."""
    # Strip trailing ' UTC' if present
    clean = ts.replace(" UTC", "").strip()
    # Try ISO parse first
    try:
        dt = datetime.fromisoformat(clean.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        # Fallback: explicit format
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _bootstrap_diff_ci(
    sample_a: np.ndarray,
    sample_b: np.ndarray,
    *,
    n_iterations: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Bootstrap 95% CI on (mean(A) - mean(B)). Returns dict with mean diff + CI bounds."""
    if len(sample_a) == 0 or len(sample_b) == 0:
        return {
            "mean_a": float(np.mean(sample_a)) if len(sample_a) else 0.0,
            "mean_b": float(np.mean(sample_b)) if len(sample_b) else 0.0,
            "diff": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "n_a": int(len(sample_a)),
            "n_b": int(len(sample_b)),
            "ci_excludes_zero": False,
        }
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_iterations)
    for i in range(n_iterations):
        resample_a = rng.choice(sample_a, size=len(sample_a), replace=True)
        resample_b = rng.choice(sample_b, size=len(sample_b), replace=True)
        diffs[i] = resample_a.mean() - resample_b.mean()
    ci_low, ci_high = np.percentile(diffs, [2.5, 97.5])
    mean_diff = float(np.mean(sample_a) - np.mean(sample_b))
    return {
        "mean_a": float(np.mean(sample_a)),
        "mean_b": float(np.mean(sample_b)),
        "diff": mean_diff,
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "n_a": int(len(sample_a)),
        "n_b": int(len(sample_b)),
        "ci_excludes_zero": bool(ci_low > 0 or ci_high < 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data loading + quality filter (pre-reg §2.1)
# ─────────────────────────────────────────────────────────────────────────────


def load_real_positions(signals_db: Path) -> list[dict]:
    """Load positions, apply data quality filter, restrict to curated 10.

    Returns real-trade subset: closed positions excluding ($0 P&L AND entry=exit
    anomalies) restricted to curated 10 per operator amendment 2026-05-15.
    """
    con = sqlite3.connect(signals_db)
    con.row_factory = sqlite3.Row
    con.text_factory = lambda x: x.decode("utf-8", errors="replace")
    placeholders = ",".join("?" * len(CURATED_10))
    rows = con.execute(f"""
        SELECT id, symbol, direction, status, entry_price, entry_ts,
               sl_price, tp_price, size_usd, exit_price, exit_ts, exit_reason,
               pnl_usd, pnl_pct, scan_id
        FROM positions
        WHERE status = 'closed'
        AND symbol IN ({placeholders})
        AND NOT (pnl_usd = 0 AND entry_price = exit_price)
    """, CURATED_10).fetchall()
    con.close()
    return [dict(r) for r in rows]


def load_excluded_positions(signals_db: Path) -> dict:
    """Load counts of excluded positions for transparency."""
    con = sqlite3.connect(signals_db)
    con.text_factory = lambda x: x.decode("utf-8", errors="replace")
    placeholders = ",".join("?" * len(CURATED_10))
    out = {
        "total_positions": con.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
        "total_closed": con.execute("SELECT COUNT(*) FROM positions WHERE status='closed'").fetchone()[0],
        "off_curated_closed": con.execute(
            f"SELECT COUNT(*) FROM positions WHERE status='closed' AND symbol NOT IN ({placeholders})",
            CURATED_10,
        ).fetchone()[0],
        "anomalies_zero_pnl_entry_eq_exit": con.execute(f"""
            SELECT COUNT(*) FROM positions
            WHERE status='closed' AND symbol IN ({placeholders})
            AND pnl_usd = 0 AND entry_price = exit_price
        """, CURATED_10).fetchone()[0],
        "off_curated_symbols": [
            r[0] for r in con.execute(
                f"SELECT DISTINCT symbol FROM positions WHERE status='closed' AND symbol NOT IN ({placeholders}) ORDER BY symbol",
                CURATED_10,
            ).fetchall()
        ],
    }
    con.close()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# OHLCV lookups (data/ohlcv.db)
# ─────────────────────────────────────────────────────────────────────────────


def get_close_at_or_after(
    ohlcv_db: Path,
    symbol: str,
    target_dt: datetime,
    timeframe: str = "1h",
) -> float | None:
    """Closest OHLCV close at-or-after target_dt for symbol/timeframe."""
    con = sqlite3.connect(ohlcv_db)
    target_ms = int(target_dt.timestamp() * 1000)
    row = con.execute("""
        SELECT close FROM ohlcv
        WHERE symbol=? AND timeframe=? AND open_time >= ?
        ORDER BY open_time ASC LIMIT 1
    """, (symbol, timeframe, target_ms)).fetchone()
    con.close()
    return float(row[0]) if row else None


def get_close_at_or_before(
    ohlcv_db: Path,
    symbol: str,
    target_dt: datetime,
    timeframe: str = "1h",
) -> float | None:
    """Closest OHLCV close at-or-before target_dt for symbol/timeframe."""
    con = sqlite3.connect(ohlcv_db)
    target_ms = int(target_dt.timestamp() * 1000)
    row = con.execute("""
        SELECT close FROM ohlcv
        WHERE symbol=? AND timeframe=? AND open_time <= ?
        ORDER BY open_time DESC LIMIT 1
    """, (symbol, timeframe, target_ms)).fetchone()
    con.close()
    return float(row[0]) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Q1 — Overall edge vs equal-weighted basket B&H (pre-reg §2.3)
# ─────────────────────────────────────────────────────────────────────────────


def compute_q1(positions: list[dict], ohlcv_db: Path) -> dict:
    """Q1: strategy total return vs equal-weighted basket B&H (curated 10)."""
    window_start = datetime.fromisoformat(WINDOW_START_ISO)
    window_end = datetime.fromisoformat(WINDOW_END_ISO)

    # Strategy aggregates
    sized = [p for p in positions if p.get("size_usd") is not None]
    strategy_pnl_usd = float(sum(float(p["pnl_usd"]) for p in positions))
    capital_basis_usd = float(sum(float(p["size_usd"]) for p in sized))
    strategy_return_pct = (
        strategy_pnl_usd / capital_basis_usd * 100.0 if capital_basis_usd > 0 else 0.0
    )

    # Basket B&H per symbol
    per_symbol = {}
    bh_returns = []
    for sym in CURATED_10:
        p_start = get_close_at_or_after(ohlcv_db, sym, window_start)
        p_end = get_close_at_or_before(ohlcv_db, sym, window_end)
        if p_start is None or p_end is None or p_start <= 0:
            per_symbol[sym] = {"price_start": p_start, "price_end": p_end, "return_pct": None}
            continue
        ret_pct = (p_end - p_start) / p_start * 100.0
        per_symbol[sym] = {
            "price_start": p_start, "price_end": p_end, "return_pct": ret_pct,
        }
        bh_returns.append(ret_pct)

    basket_bh_return_pct = float(np.mean(bh_returns)) if bh_returns else 0.0
    gap_pct = strategy_return_pct - basket_bh_return_pct

    q1_pass = gap_pct >= Q1_THRESHOLD_PP

    return {
        "strategy_pnl_usd": round(strategy_pnl_usd, 4),
        "strategy_capital_basis_usd": round(capital_basis_usd, 4),
        "strategy_return_pct": round(strategy_return_pct, 4),
        "basket_bh_return_pct": round(basket_bh_return_pct, 4),
        "gap_pct": round(gap_pct, 4),
        "threshold_pp": Q1_THRESHOLD_PP,
        "q1_pass": q1_pass,
        "n_positions_in_strategy": len(positions),
        "n_positions_sized": len(sized),
        "n_symbols_in_basket": len(CURATED_10),
        "n_symbols_with_data": len(bh_returns),
        "window_start_iso": WINDOW_START_ISO,
        "window_end_iso": WINDOW_END_ISO,
        "per_symbol_basket": per_symbol,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Q2 — Operator filtering edge (pre-reg §2.4)
# ─────────────────────────────────────────────────────────────────────────────


def compute_q2(positions: list[dict]) -> dict:
    """Q2: bootstrap CI on (avg MANUAL P&L − avg SL_HIT P&L)."""
    manual = np.array([float(p["pnl_usd"]) for p in positions if p["exit_reason"] == "MANUAL"])
    sl_hit = np.array([float(p["pnl_usd"]) for p in positions if p["exit_reason"] == "SL_HIT"])
    tp_hit = np.array([float(p["pnl_usd"]) for p in positions if p["exit_reason"] == "TP_HIT"])

    ci = _bootstrap_diff_ci(manual, sl_hit)
    q2_pass = (ci["mean_a"] > ci["mean_b"]) and (ci["ci_low"] > 0)

    return {
        "manual": {
            "n": int(len(manual)),
            "mean_pnl_usd": float(manual.mean()) if len(manual) else 0.0,
            "sum_pnl_usd": float(manual.sum()) if len(manual) else 0.0,
            "std_pnl_usd": float(manual.std(ddof=1)) if len(manual) > 1 else 0.0,
        },
        "sl_hit": {
            "n": int(len(sl_hit)),
            "mean_pnl_usd": float(sl_hit.mean()) if len(sl_hit) else 0.0,
            "sum_pnl_usd": float(sl_hit.sum()) if len(sl_hit) else 0.0,
            "std_pnl_usd": float(sl_hit.std(ddof=1)) if len(sl_hit) > 1 else 0.0,
        },
        "tp_hit": {
            "n": int(len(tp_hit)),
            "mean_pnl_usd": float(tp_hit.mean()) if len(tp_hit) else 0.0,
            "sum_pnl_usd": float(tp_hit.sum()) if len(tp_hit) else 0.0,
            "std_pnl_usd": float(tp_hit.std(ddof=1)) if len(tp_hit) > 1 else 0.0,
        },
        "bootstrap_diff_manual_minus_sl_hit": ci,
        "q2_pass": q2_pass,
        "bootstrap_n": BOOTSTRAP_N,
        "bootstrap_seed": BOOTSTRAP_SEED,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Q3 — APPROVED vs REJECTED counterfactual (pre-reg §2.5)
# ─────────────────────────────────────────────────────────────────────────────


def _infer_direction(estado: str) -> int:
    """Infer LONG (+1) / SHORT (-1) / ambiguous (0) from estado string."""
    if estado is None:
        return 0
    up = estado.upper()
    if "SHORT" in up:
        return -1
    if "LONG" in up:
        return 1
    return 0


def _compute_hypothetical_return(
    ohlcv_db: Path,
    symbol: str,
    signal_ts: datetime,
    signal_price: float,
    direction: int,
    forward_hours: int,
) -> float | None:
    """Hypothetical forward-window return for a signal (direction-adjusted).

    Returns return_pct adjusted by direction (long unchanged, short flipped).
    None if OHLCV unavailable.
    """
    if direction == 0 or signal_price <= 0:
        return None
    target_dt = signal_ts + timedelta(hours=forward_hours)
    target_price = get_close_at_or_after(ohlcv_db, symbol, target_dt)
    if target_price is None:
        return None
    raw_ret_pct = (target_price - signal_price) / signal_price * 100.0
    return raw_ret_pct * direction  # short flips sign


def compute_q3(
    signals_db: Path,
    ohlcv_db: Path,
    positions: list[dict],
) -> dict:
    """Q3: APPROVED vs REJECTED counterfactual 24h return + sensitivity views."""
    con = sqlite3.connect(signals_db)
    con.text_factory = lambda x: x.decode("utf-8", errors="replace")

    # Curated 10 + window-aligned scans with señal=1
    placeholders = ",".join("?" * len(CURATED_10))
    rows = con.execute(f"""
        SELECT id, ts, symbol, estado, price, score, macro_ok
        FROM scans
        WHERE "señal" = 1
        AND symbol IN ({placeholders})
        AND ts >= ? AND ts <= ?
    """, (*CURATED_10, WINDOW_START_ISO.replace("T", " ").replace("+00:00", " UTC"),
          WINDOW_END_ISO.replace("T", " ").replace("+00:00", " UTC"))).fetchall()
    con.close()

    # Pre-reg §2.5: filter scans where date matches window
    signal_scans = []
    for row in rows:
        scan_id, ts_raw, symbol, estado, price, score, macro_ok = row
        try:
            ts = _parse_scan_ts(ts_raw)
        except ValueError:
            continue
        if not (datetime.fromisoformat(WINDOW_START_ISO) <= ts <= datetime.fromisoformat(WINDOW_END_ISO)):
            continue
        if price is None or float(price) <= 0:
            continue
        direction = _infer_direction(estado)
        if direction == 0:
            # estado without LONG/SHORT -> setup, no direction -> skip from counterfactual
            continue
        signal_scans.append({
            "scan_id": int(scan_id),
            "ts": ts,
            "symbol": symbol,
            "estado": estado,
            "price": float(price),
            "score": int(score) if score is not None else None,
            "direction": direction,
        })

    # Position ↔ scan matching (±Q3_MATCH_HOURS, symbol-matched)
    match_window = timedelta(hours=Q3_MATCH_HOURS)
    approved_scan_ids: set[int] = set()
    no_scan_match_positions: list[int] = []
    for p in positions:
        entry_ts = _parse_position_ts(p["entry_ts"])
        candidates = [
            s for s in signal_scans
            if s["symbol"] == p["symbol"]
            and abs(s["ts"] - entry_ts) <= match_window
        ]
        if not candidates:
            no_scan_match_positions.append(int(p["id"]))
            continue
        candidates.sort(key=lambda s: abs(s["ts"] - entry_ts))
        approved_scan_ids.add(candidates[0]["scan_id"])

    # Compute hypothetical returns per forward window
    results_by_window = {}
    for fw_h in (PRIMARY_FORWARD_WINDOW_H,) + SENSITIVITY_FORWARD_WINDOWS_H:
        approved_returns = []
        rejected_returns = []
        ohlcv_unavailable_count = 0
        for scan in signal_scans:
            ret = _compute_hypothetical_return(
                ohlcv_db, scan["symbol"], scan["ts"], scan["price"],
                scan["direction"], fw_h,
            )
            if ret is None:
                ohlcv_unavailable_count += 1
                continue
            if scan["scan_id"] in approved_scan_ids:
                approved_returns.append(ret)
            else:
                rejected_returns.append(ret)

        approved_arr = np.array(approved_returns)
        rejected_arr = np.array(rejected_returns)

        if len(approved_arr) > 0 and len(rejected_arr) > 0:
            ci = _bootstrap_diff_ci(approved_arr, rejected_arr)
        else:
            ci = {
                "mean_a": float(approved_arr.mean()) if len(approved_arr) else 0.0,
                "mean_b": float(rejected_arr.mean()) if len(rejected_arr) else 0.0,
                "diff": float("nan"),
                "ci_low": float("nan"),
                "ci_high": float("nan"),
                "n_a": int(len(approved_arr)),
                "n_b": int(len(rejected_arr)),
                "ci_excludes_zero": False,
            }
        results_by_window[fw_h] = {
            "n_approved": int(len(approved_arr)),
            "n_rejected": int(len(rejected_arr)),
            "n_ohlcv_unavailable": ohlcv_unavailable_count,
            "mean_approved_pct": float(approved_arr.mean()) if len(approved_arr) else None,
            "mean_rejected_pct": float(rejected_arr.mean()) if len(rejected_arr) else None,
            "bootstrap_diff_approved_minus_rejected": ci,
        }

    primary = results_by_window[PRIMARY_FORWARD_WINDOW_H]
    primary_ci = primary["bootstrap_diff_approved_minus_rejected"]
    q3_pass = (
        primary_ci.get("diff") is not None
        and not math.isnan(primary_ci["diff"])
        and primary_ci["diff"] >= Q3_THRESHOLD_PP
        and primary_ci["ci_low"] > 0
    )

    return {
        "primary_window_hours": PRIMARY_FORWARD_WINDOW_H,
        "sensitivity_windows_hours": list(SENSITIVITY_FORWARD_WINDOWS_H),
        "threshold_pp": Q3_THRESHOLD_PP,
        "q3_pass": q3_pass,
        "n_signal_scans_in_window": len(signal_scans),
        "n_approved": len(approved_scan_ids),
        "n_no_scan_match_positions": len(no_scan_match_positions),
        "no_scan_match_position_ids": no_scan_match_positions,
        "match_window_hours": Q3_MATCH_HOURS,
        "by_forward_window": {str(k): v for k, v in results_by_window.items()},
        "bootstrap_n": BOOTSTRAP_N,
        "bootstrap_seed": BOOTSTRAP_SEED,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Verdict tree (pre-reg §3, tiered per Q-LE4)
# ─────────────────────────────────────────────────────────────────────────────


def compute_verdict(q1: dict, q2: dict, q3: dict) -> dict:
    """Tiered verdict from 3 sub-questions per pre-reg §3 (post-amendment)."""
    q1_pass = bool(q1["q1_pass"])
    q2_pass = bool(q2["q2_pass"])
    q3_pass = bool(q3["q3_pass"])
    n_pass = int(q1_pass) + int(q2_pass) + int(q3_pass)

    if n_pass == 3:
        verdict = "EDGE_STRONG"
    elif n_pass == 2:
        # 3 sub-combos per amended pre-reg §3
        if q1_pass and q2_pass and not q3_pass:
            verdict = "EDGE_PARTIAL_RETURN_FILTER"
        elif q1_pass and q3_pass and not q2_pass:
            verdict = "EDGE_PARTIAL_RETURN_SELECTION"
        elif q2_pass and q3_pass and not q1_pass:
            verdict = "EDGE_PARTIAL_FILTER_SELECTION"
        else:
            verdict = "EDGE_PARTIAL_UNKNOWN"  # defensive; should not reach
    elif n_pass == 1:
        verdict = "EDGE_WEAK"
    else:
        verdict = "NO_EDGE"

    return {
        "verdict": verdict,
        "q1_pass": q1_pass,
        "q2_pass": q2_pass,
        "q3_pass": q3_pass,
        "n_pass": n_pass,
        "auto_advance_to_phase_d3": verdict == "EDGE_STRONG",
        "operator_decision_required": verdict != "EDGE_STRONG",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signals-db", default=str(DEFAULT_SIGNALS_DB),
                   help="Path to papa's signals.db backup (default: sandbox path)")
    p.add_argument("--ohlcv-db", default=str(DEFAULT_OHLCV_DB),
                   help="Path to data/ohlcv.db (default: repo)")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                   help="Output dir for JSON artifacts")
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

    print(f"[live-edge] signals.db: {signals_db}")
    print(f"[live-edge] ohlcv.db:   {ohlcv_db}")
    print(f"[live-edge] output:     {output_dir}")

    # Load data
    positions = load_real_positions(signals_db)
    exclusions = load_excluded_positions(signals_db)
    print(f"[live-edge] real positions (curated 10, quality-filtered): {len(positions)}")

    # Q1
    print("[live-edge] computing Q1 (overall edge vs basket B&H)...")
    q1 = compute_q1(positions, ohlcv_db)
    _save_json(output_dir / "q1_overall_edge.json", q1)
    print(f"[live-edge] Q1: strategy_return={q1['strategy_return_pct']:.2f}% "
          f"basket_bh={q1['basket_bh_return_pct']:.2f}% gap={q1['gap_pct']:.2f}pp "
          f"-> {'PASS' if q1['q1_pass'] else 'FAIL'}")

    # Q2
    print("[live-edge] computing Q2 (MANUAL vs SL_HIT bootstrap)...")
    q2 = compute_q2(positions)
    _save_json(output_dir / "q2_filtering_edge.json", q2)
    ci = q2["bootstrap_diff_manual_minus_sl_hit"]
    print(f"[live-edge] Q2: MANUAL avg=${q2['manual']['mean_pnl_usd']:.2f} (n={q2['manual']['n']}) "
          f"SL_HIT avg=${q2['sl_hit']['mean_pnl_usd']:.2f} (n={q2['sl_hit']['n']}) "
          f"diff_CI95=[{ci['ci_low']:.2f}, {ci['ci_high']:.2f}] -> "
          f"{'PASS' if q2['q2_pass'] else 'FAIL'}")

    # Q3
    print("[live-edge] computing Q3 (APPROVED vs REJECTED counterfactual)...")
    q3 = compute_q3(signals_db, ohlcv_db, positions)
    _save_json(output_dir / "q3_counterfactual.json", q3)
    primary = q3["by_forward_window"][str(PRIMARY_FORWARD_WINDOW_H)]
    primary_ci = primary["bootstrap_diff_approved_minus_rejected"]
    print(f"[live-edge] Q3 (primary 24h): APPROVED avg={primary['mean_approved_pct']:.2f}% (n={primary['n_approved']}) "
          f"REJECTED avg={primary['mean_rejected_pct']:.2f}% (n={primary['n_rejected']}) "
          f"diff_CI95=[{primary_ci['ci_low']:.4f}, {primary_ci['ci_high']:.4f}] -> "
          f"{'PASS' if q3['q3_pass'] else 'FAIL'}")

    # Verdict
    verdict = compute_verdict(q1, q2, q3)
    _save_json(output_dir / "analysis_verdict.json", verdict)
    print(f"[live-edge] FINAL VERDICT: {verdict['verdict']}")

    # Data quality + manifest
    _save_json(output_dir / "data_quality.json", {
        "real_subset_count": len(positions),
        "exclusions_log": exclusions,
        "q3_no_scan_match_count": q3["n_no_scan_match_positions"],
        "amendment_2026_05_15": (
            "Q-LE1 amended: basket restricted from 16 traded symbols to "
            "curated 10 (CLAUDE.md DEFAULT_SYMBOLS) after finding 6 off-curated "
            "traded symbols (LINK, SOL, TON, TRX, XAUT, ZEC) lack OHLCV cache. "
            "Operator rationale: 'antes teníamos 20 y curamos a 10, trabajemos "
            "con las 10 que tenemos actualmente'."
        ),
    })

    _save_json(output_dir / "analysis_manifest.json", {
        "schema_version": 1,
        "spec_ref": "docs/superpowers/plans/2026-05-15-live-edge-analysis-pre-reg.md",
        "code_commit": _git_commit(),
        "signals_db_path": str(signals_db),
        "ohlcv_db_path": str(ohlcv_db),
        "window_start_iso": WINDOW_START_ISO,
        "window_end_iso": WINDOW_END_ISO,
        "locks": {
            "Q_LE1_basket": list(CURATED_10),
            "Q_LE1_amendment_note": "Restricted from 16 traded -> curated 10 (operator decision 2026-05-15)",
            "Q_LE2_q1_threshold_pp": Q1_THRESHOLD_PP,
            "Q_LE3_match_window_hours": Q3_MATCH_HOURS,
            "Q_LE4_verdict_tree": "tiered",
            "Q_LE5_q3_threshold_pp": Q3_THRESHOLD_PP,
        },
        "bootstrap_n": BOOTSTRAP_N,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "primary_forward_window_h": PRIMARY_FORWARD_WINDOW_H,
        "sensitivity_forward_windows_h": list(SENSITIVITY_FORWARD_WINDOWS_H),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
