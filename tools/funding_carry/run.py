"""Orchestrate the funding-carry falsification end-to-end → verdict artifacts.

Run: python -m tools.funding_carry.run   (requires data/funding.db from ingest first)
Reads funding.db + ohlcv.db (read-only). Writes only under OUTPUT_DIR. No holdout."""
from __future__ import annotations
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
import numpy as np

from backtest_costs import calibration_identity_hash, load_calibration
from . import simulate, evaluate
from .constants import (OHLCV_DB, FUNDING_DB, OUTPUT_DIR, CANDIDATE_SYMBOLS,
                        WINDOW_START, WINDOW_END, NOTIONAL, BOOTSTRAP_SEED,
                        SHOCK_FUNDING_PER_8H, SHOCK_DAYS)

REQUIRED_SYMBOL_KEYS = {"symbol", "net_return_annual", "net", "funding_pnl", "basis_pnl", "cost_v3"}


def _ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp() * 1000)


def _covered_symbols(funding_db: str, w0: int, w1: int) -> list[str]:
    """Candidate symbols that have funding AND perp coverage spanning the window."""
    out = []
    with closing(sqlite3.connect(f"file:{funding_db}?mode=ro", uri=True)) as con:
        for s in CANDIDATE_SYMBOLS:
            f = con.execute("SELECT MIN(funding_time_ms), MAX(funding_time_ms), COUNT(*) "
                            "FROM funding WHERE symbol=?", (s,)).fetchone()
            k = con.execute("SELECT COUNT(*) FROM perp_klines WHERE symbol=?", (s,)).fetchone()
            if f and f[2] and f[2] > 100 and k and k[0] > 100:
                out.append(s)
    return out


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    w0, w1 = _ms(WINDOW_START), _ms(WINDOW_END)
    symbols = _covered_symbols(FUNDING_DB, w0, w1)
    dropped = [s for s in CANDIDATE_SYMBOLS if s not in symbols]

    records = []
    for s in symbols:
        funding = simulate.load_funding(FUNDING_DB, s, w0, w1)
        if len(funding) < 2:
            dropped.append(s); continue
        entry_ms, exit_ms = funding[0][0], funding[-1][0]
        try:
            rec = simulate.carry_for_symbol(
                symbol=s, funding=funding,
                spot_entry=simulate.spot_price_at(OHLCV_DB, s, entry_ms),
                spot_exit=simulate.spot_price_at(OHLCV_DB, s, exit_ms),
                perp_entry=simulate.perp_price_at(FUNDING_DB, s, entry_ms),
                perp_exit=simulate.perp_price_at(FUNDING_DB, s, exit_ms),
                liq=simulate.spot_liquidity(OHLCV_DB, s, entry_ms))
        except ValueError:        # missing spot/perp price -> drop loud, don't poison the pool
            dropped.append(s); continue
        records.append(rec)

    annual = [r["net_return_annual"] for r in records]
    a = evaluate.gate_a(annual)
    b1 = evaluate.gate_b1([r["net"] for r in records])
    b2 = evaluate.gate_b2(float(np.mean([r["net_return"] for r in records])) if records else 0.0)
    v = evaluate.verdict(a, b2)

    cal = load_calibration()
    out = {"verdict": v, "gate_a": a, "gate_b1": b1, "gate_b2": b2,
           "manifest": {"experiment": "funding-carry-falsification", "spec_commit": "2f10134",
                        "window": [WINDOW_START, WINDOW_END], "notional": NOTIONAL,
                        "bootstrap_seed": BOOTSTRAP_SEED,
                        "shock": {"funding_per_8h": SHOCK_FUNDING_PER_8H, "days": SHOCK_DAYS},
                        "cost_model": {"active_model": cal.active_model,
                                       "calibration_identity_hash": calibration_identity_hash(cal)},
                        "symbols_kept": symbols, "symbols_dropped": sorted(set(dropped)),
                        "generated_utc": datetime.now(timezone.utc).isoformat()}}
    with open(os.path.join(OUTPUT_DIR, "per_symbol.json"), "w") as f:
        json.dump(records, f, indent=2)
    with open(os.path.join(OUTPUT_DIR, "verdict.json"), "w") as f:
        json.dump(out, f, indent=2)
    lines = [
        "# Funding-carry falsification: VERDICT", "",
        f"**Verdict: {v['verdict']}**  (Gate A: {v['pass_a']}, Gate B2: {v['pass_b2']})", "",
        f"- Symbols kept {len(symbols)}: {', '.join(symbols)}",
        f"- Pooled annualized net return: mean {a['mean']:.4f}, CI95 [{a['ci_lo']:.4f}, {a['ci_hi']:.4f}]",
        f"- LOO min mean: {a['loo_min_mean']:.4f}",
        f"- Gate B1 max drawdown (pooled net): {b1['max_drawdown']:.2f}; worst symbol net {b1['worst_interval']:.2f}",
        f"- Gate B2 synthetic shock bleed {b2['shock_bleed']:.4f}; post-shock mean return {b2['post_shock_return']:.4f}", "",
        "Scope: LIQUID universe only. A FAIL = liquid carry arbed/short-vol, NOT 'no carry anywhere'.",
        "PASS -> strategy-design fork (sizing/rebalance/long-tail). FAIL -> portfolio decision.",
    ]
    with open(os.path.join(OUTPUT_DIR, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"VERDICT: {v['verdict']}  (A={v['pass_a']} B2={v['pass_b2']}, pooled mean {a['mean']:.4f})")


if __name__ == "__main__":
    main()
