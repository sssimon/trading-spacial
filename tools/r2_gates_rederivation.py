#!/usr/bin/env python3
"""R2 — theoretical re-derivation of per-symbol time_limit_hours + cooldown_hours.

Pre-registered in `docs/superpowers/plans/2026-05-11-r2-gates-rederivation-pre-reg.md`.

Per §2.2 amendment 2026-05-11 (post pre-execution math sanity check),
max_participation_rate is DECOUPLED from R2 (passthrough current values).
PoV re-derivation deferred to issue #325 pending cost model v2 migration.
See audit spec §A.7 for the H7 retroactive reformulation.

Output (in data/retune/2026-05-11-r2-gates/):
  - per_symbol_gates.json   # drop-in for config.defaults.json:symbol_overrides
  - tl_distributions.json   # ATR-based time-to-1-ATR per symbol (forensics)
  - manifest.json           # reproducibility metadata + guard result
  - degenerate_guard_fired.txt (only if §5.1 guard fires)

Exit codes:
  0 — clean derivation, ready for sub-window sweeps
  2 — §5.1 degenerate case guard fired (operator decides re-do or advance)
"""
from __future__ import annotations

import json
import math
import statistics
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

# Pre-registered constants. DO NOT TUNE without spec amendment to
# docs/superpowers/plans/2026-05-11-r2-gates-rederivation-pre-reg.md.
ATR_PERIOD: Final = 14
LOOKAHEAD_MAX_HOURS: Final = 72
TL_CLAMP_LOW: Final = 4
TL_CLAMP_HIGH: Final = 48
COOLDOWN_NW: Final = 4
COOLDOWN_FLOOR: Final = 6
DEGENERATE_GUARD_THRESHOLD: Final = 5  # >=5 of 8 currently-bankrupt tightened -> guard fires
MIN_OBSERVATIONS: Final = 100          # pre-reg §2.1 pathological fallback

ALL_CURATED_SYMBOLS: Final = (
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

CURRENTLY_BANKRUPT_SYMBOLS: Final = (
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT",
    "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
)

# Tier mapping (maintained per pre-reg §2.4). External Binance volume
# verification is documented in derivation_audit.md; mismatch would
# halt promotion (not handled in code).
TIER_MAP: Final = {
    "BTCUSDT": "major", "ETHUSDT": "major",
    "ADAUSDT": "mid", "AVAXUSDT": "mid", "DOGEUSDT": "mid",
    "UNIUSDT": "mid", "XLMUSDT": "mid",
    "PENDLEUSDT": "small", "JUPUSDT": "small", "RUNEUSDT": "small",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "retune" / "2026-05-11-r2-gates"
HOLDOUT_START = datetime(2025, 4, 30, tzinfo=timezone.utc)
HOLDOUT_START_MS = int(HOLDOUT_START.timestamp() * 1000)


def load_1h_ohlcv(symbol: str) -> list[tuple]:
    """Load all pre-holdout 1H bars for a symbol.

    Returns list of (open_time_ms, high, low, close, volume).
    """
    db_path = REPO_ROOT / "data" / "ohlcv.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """SELECT open_time, high, low, close, volume
           FROM ohlcv WHERE symbol = ? AND timeframe = '1h' AND open_time < ?
           ORDER BY open_time ASC""",
        (symbol, HOLDOUT_START_MS),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def compute_atr_wilder(highs, lows, closes, period: int = 14):
    """Wilder's ATR. Returns list of same length; first `period` entries are None.

    Identical to the standard Wilder smoothing used in btc_scanner.calc_atr —
    initial mean of first `period` TRs, then smoothed by (prev * (period-1) + tr) / period.
    """
    n = len(closes)
    atr = [None] * n
    if n < period + 1:
        return atr
    tr_sum = 0.0
    for i in range(1, period + 1):
        tr_sum += max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
    atr[period] = tr_sum / period
    for i in range(period + 1, n):
        tr_i = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        atr[i] = (atr[i-1] * (period - 1) + tr_i) / period
    return atr


def time_to_1_atr_observations(closes, atrs, lookahead: int = 72):
    """For each bar i with valid ATR, find next j > i where |close[j] - close[i]| >= ATR[i].

    Returns (observed_hours: list[int], censored_count: int, valid_atr_count: int).
    Bars where no move within `lookahead` hours are right-censored (counted but excluded
    from `observed`).
    """
    n = len(closes)
    observed: list[int] = []
    censored = 0
    valid = 0
    for i in range(n):
        if atrs[i] is None:
            continue
        valid += 1
        ref = closes[i]
        threshold = atrs[i]
        max_j = min(i + lookahead, n)
        hit = False
        for j in range(i + 1, max_j):
            if abs(closes[j] - ref) >= threshold:
                observed.append(j - i)
                hit = True
                break
        if not hit:
            censored += 1
    return observed, censored, valid


def derive_tl_for_symbol(symbol: str) -> dict:
    """Pre-reg §2.1: ATR-based time-to-±1-ATR-move median TL."""
    rows = load_1h_ohlcv(symbol)
    if not rows:
        return {"symbol": symbol, "error": "no_data"}

    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    closes = [r[3] for r in rows]
    n_bars = len(rows)

    atrs = compute_atr_wilder(highs, lows, closes, ATR_PERIOD)
    observed, censored, valid_atr = time_to_1_atr_observations(
        closes, atrs, LOOKAHEAD_MAX_HOURS,
    )

    if len(observed) < MIN_OBSERVATIONS:
        return {
            "symbol": symbol,
            "error": f"insufficient_samples ({len(observed)} < {MIN_OBSERVATIONS})",
            "n_bars": n_bars, "n_valid_atr": valid_atr,
            "n_observations": len(observed), "n_censored": censored,
        }

    median_raw = statistics.median(observed)
    tl_rounded = round(median_raw)
    tl_clamped = max(TL_CLAMP_LOW, min(TL_CLAMP_HIGH, tl_rounded))
    clamped = (tl_rounded != tl_clamped)

    sorted_obs = sorted(observed)
    def _pct(p):
        idx = min(int(p * len(sorted_obs) / 100), len(sorted_obs) - 1)
        return sorted_obs[idx]

    return {
        "symbol": symbol,
        "n_bars": n_bars,
        "n_valid_atr": valid_atr,
        "n_observations": len(observed),
        "n_censored": censored,
        "censored_rate": round(censored / valid_atr, 4) if valid_atr else None,
        "median_hours_raw": round(median_raw, 3),
        "tl_anchor_rounded": tl_rounded,
        "tl_anchor": tl_clamped,
        "clamped": clamped,
        "distribution_percentiles": {
            "p10": _pct(10), "p25": _pct(25), "p50": _pct(50),
            "p75": _pct(75), "p90": _pct(90), "p99": _pct(99),
            "mean": round(statistics.mean(observed), 3),
        },
    }


def derive_cooldown(new_tl: int) -> dict:
    """Pre-reg §2.3: transitive rule cooldown = max(new_TL, NW=4, floor=6)."""
    cd = max(int(new_tl), COOLDOWN_NW, COOLDOWN_FLOOR)
    floor_dominated = (cd == COOLDOWN_FLOOR and new_tl < COOLDOWN_FLOOR)
    return {"value": cd, "floor_dominated": floor_dominated}


def check_degenerate_guard(new_gates: dict, current_gates: dict) -> dict:
    """Pre-reg §5.1 safeguard: >=5 of 8 currently-bankrupt tightened -> guard fires.

    Tightening = new_TL < current_TL for that symbol.
    """
    comparison = []
    tightened_count = 0
    tightened_symbols = []
    for sym in CURRENTLY_BANKRUPT_SYMBOLS:
        new_tl = new_gates.get(sym, {}).get("time_limit_hours")
        cur_tl = current_gates.get(sym, {}).get("time_limit_hours")
        if new_tl is None or cur_tl is None:
            tightened = None
        else:
            tightened = bool(new_tl < cur_tl)
        comparison.append({
            "symbol": sym, "current_TL": cur_tl, "new_TL": new_tl,
            "tightened": tightened,
        })
        if tightened:
            tightened_count += 1
            tightened_symbols.append(sym)
    return {
        "fires": tightened_count >= DEGENERATE_GUARD_THRESHOLD,
        "tightened_count": tightened_count,
        "tightened_symbols": tightened_symbols,
        "threshold": DEGENERATE_GUARD_THRESHOLD,
        "comparison": comparison,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(REPO_ROOT / "config.defaults.json") as f:
        app_config = json.load(f)
    current_gates = app_config.get("symbol_overrides", {})

    print("=== R2 — Per-symbol gates re-derivation (TL only) ===")
    print(f"Pre-holdout end:  {HOLDOUT_START.isoformat()}")
    print(f"PoV mode:         DECOUPLED (passthrough current values per §2.2 amendment)")
    print(f"Issue ref:        #325 (PoV deferred — depends on cost model v2)")
    print()

    # Step 1: TL derivation per symbol
    print("Step 1: TL derivation (ATR-based time-to-±1-ATR-move median)")
    print(f"  {'symbol':<12} {'n_obs':>8} {'cens_rate':>10} {'median_raw':>11} {'tl_anchor':>10}  notes")
    tl_results = {}
    for sym in ALL_CURATED_SYMBOLS:
        res = derive_tl_for_symbol(sym)
        tl_results[sym] = res
        if "error" in res:
            print(f"  {sym:<12} ERROR: {res['error']}")
            continue
        cen_str = f"{res['censored_rate']*100:.2f}%"
        notes = "CLAMPED" if res['clamped'] else "ok"
        print(f"  {sym:<12} {res['n_observations']:>8} {cen_str:>10} "
              f"{res['median_hours_raw']:>11.2f} {res['tl_anchor']:>10}  {notes}")
    print()

    # Step 2: Compose new gates (TL+cooldown changes, PoV passthrough)
    print("Step 2: Compose new gates (PoV passthrough per §2.2)")
    new_gates = {}
    for sym in ALL_CURATED_SYMBOLS:
        tl_res = tl_results[sym]
        cur = dict(current_gates.get(sym, {}))
        if "error" in tl_res:
            new_gates[sym] = {**cur, "_r2_error": tl_res["error"]}
            continue
        new_tl = tl_res["tl_anchor"]
        cd = derive_cooldown(new_tl)
        new_gates[sym] = {
            **cur,
            "time_limit_hours": new_tl,
            "cooldown_hours": cd["value"],
            # max_participation_rate: copied through unchanged (passthrough)
        }

    # Step 3: §5.1 degenerate guard
    print("Step 3: §5.1 degenerate case guard check")
    guard = check_degenerate_guard(new_gates, current_gates)
    print(f"  tightened_count:  {guard['tightened_count']}/{len(CURRENTLY_BANKRUPT_SYMBOLS)} "
          f"(threshold {guard['threshold']})")
    print(f"  tightened:        {guard['tightened_symbols'] or '(none)'}")
    print(f"  guard fires:      {guard['fires']}")
    print()

    # Side-by-side
    print("=== Side-by-side: current vs new ===")
    print(f"  {'symbol':<12} {'cur_TL':>7} {'new_TL':>7} {'ΔTL':>5} "
          f"{'cur_CD':>7} {'new_CD':>7} {'tightened':>10}")
    for sym in ALL_CURATED_SYMBOLS:
        cur = current_gates.get(sym, {})
        new = new_gates[sym]
        if "_r2_error" in new:
            print(f"  {sym:<12} ERROR: {new['_r2_error']}")
            continue
        cur_tl = cur.get("time_limit_hours")
        new_tl = new["time_limit_hours"]
        delta = (new_tl - cur_tl) if isinstance(cur_tl, int) else None
        delta_str = f"{delta:+d}" if isinstance(delta, int) else "?"
        cur_cd = cur.get("cooldown_hours")
        new_cd = new["cooldown_hours"]
        is_8bnkpt = sym in CURRENTLY_BANKRUPT_SYMBOLS
        if not is_8bnkpt:
            tightened_str = "—"
        elif isinstance(delta, int) and delta < 0:
            tightened_str = "YES"
        else:
            tightened_str = "no"
        print(f"  {sym:<12} {cur_tl!s:>7} {new_tl:>7} {delta_str:>5} "
              f"{cur_cd!s:>7} {new_cd:>7} {tightened_str:>10}")
    print()

    # Save artifacts
    with open(OUTPUT_DIR / "per_symbol_gates.json", "w") as f:
        json.dump(new_gates, f, indent=2, sort_keys=True)
    with open(OUTPUT_DIR / "tl_distributions.json", "w") as f:
        json.dump(tl_results, f, indent=2, sort_keys=True)

    manifest = {
        "harness": "tools.r2_gates_rederivation",
        "spec_ref": "docs/superpowers/plans/2026-05-11-r2-gates-rederivation-pre-reg.md",
        "audit_ref": "docs/superpowers/specs/es/2026-05-11-strategy-structural-audit.md (§A.7)",
        "pov_decoupled_issue_ref": "#325",
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "holdout_start_iso": HOLDOUT_START.isoformat(),
        "pre_reg_constants": {
            "atr_period": ATR_PERIOD,
            "lookahead_max_hours": LOOKAHEAD_MAX_HOURS,
            "tl_clamp": [TL_CLAMP_LOW, TL_CLAMP_HIGH],
            "min_observations": MIN_OBSERVATIONS,
            "cooldown_nw": COOLDOWN_NW,
            "cooldown_floor": COOLDOWN_FLOOR,
            "degenerate_guard_threshold": DEGENERATE_GUARD_THRESHOLD,
        },
        "tier_map": TIER_MAP,
        "guard_result": guard,
        "current_max_participation_rate_snapshot": {
            sym: current_gates.get(sym, {}).get("max_participation_rate")
            for sym in ALL_CURATED_SYMBOLS
        },
    }
    with open(OUTPUT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    # §5.1 guard enforcement
    if guard["fires"]:
        print("🔴 §5.1 DEGENERATE CASE GUARD FIRED — R2 ABORTED before sweeps")
        with open(OUTPUT_DIR / "degenerate_guard_fired.txt", "w") as f:
            f.write(f"§5.1 degenerate case guard fired at {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"tightened_count: {guard['tightened_count']}/{len(CURRENTLY_BANKRUPT_SYMBOLS)}\n")
            f.write(f"threshold: {guard['threshold']}\n")
            f.write(f"tightened symbols: {', '.join(guard['tightened_symbols'])}\n\n")
            f.write("Per-symbol comparison:\n")
            for c in guard["comparison"]:
                f.write(f"  {c['symbol']:<12} cur_TL={c['current_TL']} "
                        f"new_TL={c['new_TL']} tightened={c['tightened']}\n")
            f.write("\nOperator decides per §5.1: (a) re-do R2 with new_TL>=current_TL constraint, "
                    "or (b) advance to R1/R3 acknowledging gates unresolved.\n")
        print(f"   See {OUTPUT_DIR / 'degenerate_guard_fired.txt'}")
        return 2

    print(f"✓ §5.1 guard passed ({guard['tightened_count']}/{guard['threshold']} threshold not exceeded)")
    print(f"  → R2 derivation complete. Next: run 3 sub-window sweeps with new gates.")
    print(f"  Outputs in {OUTPUT_DIR}/")
    print(f"    - per_symbol_gates.json")
    print(f"    - tl_distributions.json")
    print(f"    - manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
