"""Orchestrate Brazo A reformulado end-to-end and emit the verdict artifacts.

Run: python -m tools.arm_a_blind_exit.run
Reads papá's DB + data/ohlcv.db (read-only). Writes only under OUTPUT_DIR.
No holdout, no live close path."""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone
import numpy as np

from backtest_costs import calibration_identity_hash, load_calibration
from . import population, exit_rules, evaluate
from .constants import (
    PAPA_DB, OHLCV_DB, OUTPUT_DIR, ATR_PERIOD, ATR_TF, PRICE_TF,
    CHANDELIER_MULT, GIVEBACK_FRAC, MAX_HOLD_H, BOOTSTRAP_SEED, KEEP_SYMBOLS,
)

REQUIRED_PER_TRADE_KEYS = {
    "id", "symbol", "direction", "actual_net_v3",
    "blind_net_v3_pess", "blind_net_v3_opt", "delta_pess", "delta_opt", "hit_cap",
}


def _bars(ohlcv_db, symbol, tf, start_ms, end_ms, with_volume=False):
    con = sqlite3.connect(f"file:{ohlcv_db}?mode=ro", uri=True)
    cols = "open_time, open, high, low, close" + (", volume" if with_volume else "")
    rows = con.execute(
        f"SELECT {cols} FROM ohlcv WHERE symbol=? AND timeframe=? "
        "AND open_time>=? AND open_time<=? ORDER BY open_time",
        (symbol, tf, start_ms, end_ms),
    ).fetchall()
    con.close()
    keys = ["open_time", "open", "high", "low", "close"] + (["volume"] if with_volume else [])
    return [dict(zip(keys, r)) for r in rows]


def _one_trade(p, ohlcv_db):
    sym, direction, qty = p["symbol"], p["direction"], p["qty"]
    entry_ms = int(p["entry_ts"].timestamp() * 1000)
    cap_ms = entry_ms + int(MAX_HOLD_H * 3600 * 1000)

    # 1h bars: [entry - 60d, cap] for ATR + liquidity proxy
    h1 = _bars(ohlcv_db, sym, ATR_TF, entry_ms - 60 * 86400 * 1000, cap_ms, with_volume=True)
    pre = [b for b in h1 if b["open_time"] < entry_ms]
    atr = exit_rules.wilder_atr(pre, period=ATR_PERIOD)
    liq = evaluate.liquidity_series(h1)

    # 5m path from entry to cap
    path = _bars(ohlcv_db, sym, PRICE_TF, entry_ms, cap_ms)

    def net(exit_price, exit_ms):
        hold_h = (exit_ms - entry_ms) / 3_600_000
        entry_notional = abs(qty) * p["entry_price"]
        exit_notional = abs(qty) * exit_price
        cost = evaluate.recost_v3(
            symbol=sym, entry_notional=entry_notional, exit_notional=exit_notional,
            entry_liq=evaluate.liquidity_at(liq, entry_ms),
            exit_liq=evaluate.liquidity_at(liq, exit_ms), holding_hours=hold_h)
        return evaluate.gross_pnl(qty=qty, entry=p["entry_price"], exit=exit_price,
                                  direction=direction) - cost

    # baseline: the operator's REAL exit, recosted to v3
    actual_net = net(p["exit_price"], int(p["exit_ts"].timestamp() * 1000))

    rec = {"id": p["id"], "symbol": sym, "direction": direction, "actual_net_v3": actual_net}
    for fill in ("pess", "opt"):
        conv = "pessimistic" if fill == "pess" else "optimistic"
        px, ts, cap = exit_rules.simulate_chandelier(path, direction, p["entry_price"], atr, fill=conv)
        rec[f"blind_net_v3_{fill}"] = net(px, ts)
        rec[f"delta_{fill}"] = rec[f"blind_net_v3_{fill}"] - actual_net
        if fill == "pess":
            rec["hit_cap"] = cap
        # confirmatory (descriptive)
        gpx, gts, _ = exit_rules.simulate_giveback(path, direction, p["entry_price"], fill=conv)
        rec[f"giveback_net_v3_{fill}"] = net(gpx, gts)
    return rec


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    kept, dropped = population.load_population(PAPA_DB, OHLCV_DB)
    records = [_one_trade(p, OHLCV_DB) for p in kept]
    ids = [r["id"] for r in records]
    pess = [r["delta_pess"] for r in records]
    opt = [r["delta_opt"] for r in records]

    v = evaluate.verdict(pess_deltas=pess, opt_deltas=opt, ids=ids)
    cal = load_calibration()
    manifest = {
        "experiment": "arm-a-reformulado-blind-exit",
        "spec_commit": "370f1d5",
        "frozen_params": {"atr_period": ATR_PERIOD, "atr_tf": ATR_TF, "price_tf": PRICE_TF,
                          "chandelier_mult": CHANDELIER_MULT, "giveback_frac": GIVEBACK_FRAC,
                          "max_hold_h": MAX_HOLD_H, "bootstrap_seed": BOOTSTRAP_SEED},
        "cost_model": {"active_model": cal.active_model,
                       "calibration_identity_hash": calibration_identity_hash(cal)},
        "population": {"kept": len(kept), "dropped": dropped, "keep_symbols": list(KEEP_SYMBOLS)},
        "generated_utc": None,   # stamp post-run; Date.now() unavailable in some envs
    }
    confirmatory = {
        "pess_mean_delta": float(np.mean([r["giveback_net_v3_pess"] - r["actual_net_v3"] for r in records])),
        "opt_mean_delta": float(np.mean([r["giveback_net_v3_opt"] - r["actual_net_v3"] for r in records])),
        "note": "DESCRIPTIVE ONLY — barred from any edge-existence claim (spec §6, Adrian F-4)",
    }

    with open(os.path.join(OUTPUT_DIR, "per_trade.json"), "w") as f:
        json.dump(records, f, indent=2)
    with open(os.path.join(OUTPUT_DIR, "verdict.json"), "w") as f:
        json.dump({"primary": v, "confirmatory_descriptive": confirmatory, "manifest": manifest}, f, indent=2)

    n_cap = sum(1 for r in records if r["hit_cap"])
    n_blind_worse = sum(1 for r in records if r["delta_pess"] < 0)
    lines = [
        "# Brazo A reformulado — blind exit policy: VERDICT", "",
        f"**Verdict (primary, chandelier 3xATR): {v['verdict']}**", "",
        f"- N = {len(records)} (27 frozen; dropped {sum(d['n'] for d in dropped)})",
        f"- Pessimistic CI95: [{v['pessimistic_ci']['lo']:.4f}, {v['pessimistic_ci']['hi']:.4f}], "
        f"mean {v['pessimistic_ci']['mean']:.4f}, excludes_zero={v['pessimistic_ci']['excludes_zero']}",
        f"- Optimistic CI95:  [{v['optimistic_ci']['lo']:.4f}, {v['optimistic_ci']['hi']:.4f}], "
        f"mean {v['optimistic_ci']['mean']:.4f}, excludes_zero={v['optimistic_ci']['excludes_zero']}",
        f"- LOO survives top influencer (id={v['loo_top_influencer_id']}): {v['loo_survives_top_influencer']}",
        f"- Blind worse than operator on {n_blind_worse}/{len(records)} trades; cap hits {n_cap}/{len(records)}",
        f"- Confirmatory 38%-giveback (DESCRIPTIVE): pess mean delta {confirmatory['pess_mean_delta']:.4f}", "",
        "Ceiling: n=27, single bull regime, 8 liquid symbols. PASS = in-regime only, not deployable.",
        "FAIL -> Lyra Sage (double-FAIL with Brazo B).",
    ]
    with open(os.path.join(OUTPUT_DIR, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"VERDICT: {v['verdict']}  (pess mean delta {v['pessimistic_ci']['mean']:.4f})")


if __name__ == "__main__":
    main()
