"""Orchestrate the tail-aware kill-rule study end-to-end → verdict artifacts.

Run: python -m tools.funding_carry.run_kill   (uses data/funding.db; no network)
Reads funding.db + ohlcv.db (read-only). Writes only under OUTPUT_DIR_KILL. No holdout."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
import numpy as np

from backtest_costs import calibration_identity_hash, load_calibration
from . import simulate, evaluate, kill_rule
from .constants import (OHLCV_DB, FUNDING_DB, OUTPUT_DIR_KILL,
                        WINDOW_START, WINDOW_END, NOTIONAL, KILL_K, K_SENSITIVITY,
                        N_SHOCKS, SHOCK_FUNDING_PER_8H)
from .run import _ms, _covered_symbols

# G2 shock model (spec §6): a SUSTAINED negative episode longer than KILL_K; the kill fires
# at K settlements, capping each shock's bleed at KILL_K * SHOCK_FUNDING_PER_8H. (SHOCK_DAYS
# from the falsification's 5-day B2 is a DIFFERENT shock model and is intentionally unused here.)
REQUIRED_KILL_KEYS = {"symbol", "net_with_kill", "net_no_kill", "n_kills", "max_dd", "churn_cost"}


def _max_dd(equity_curve) -> float:
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) == 0:
        return 0.0
    return float((np.maximum.accumulate(eq) - eq).max())


def _one_symbol(s, w0, w1, k):
    funding = simulate.load_funding(FUNDING_DB, s, w0, w1)
    if len(funding) < 2:
        return None
    times = [t for t, _ in funding]
    marks = simulate.perp_mark_series(FUNDING_DB, s, times)
    spot_e = simulate.spot_price_at(OHLCV_DB, s, times[0])
    perp_e = simulate.perp_price_at(FUNDING_DB, s, times[0])
    if any(np.isnan(x) for x in (spot_e, perp_e)) or any(np.isnan(m) for m in marks):
        return None
    units = NOTIONAL / spot_e
    liq = simulate.spot_liquidity(OHLCV_DB, s, times[0])
    rt = simulate.recost_four_legs(symbol=s, units=units, spot_price=spot_e,
                                   perp_price=perp_e, liq=liq,
                                   holding_hours=(times[-1] - times[0]) / 3_600_000)
    wk = kill_rule.simulate_with_kill(funding, marks=marks, units=units, rt_cost=rt, k=k)
    nk = kill_rule.simulate_no_kill(funding, marks=marks, units=units, rt_cost=rt)
    return {"symbol": s, "net_with_kill": wk["net"] / NOTIONAL,
            "net_no_kill": nk["net"] / NOTIONAL, "n_kills": wk["n_kills"],
            "max_dd": _max_dd(wk["equity_curve"]),                 # $ (funding-equity DD)
            "max_dd_no_kill": _max_dd(nk["equity_curve"]),        # $
            "churn_cost": wk["churn_cost"], "equity_curve": wk["equity_curve"]}


def main():
    os.makedirs(OUTPUT_DIR_KILL, exist_ok=True)
    w0, w1 = _ms(WINDOW_START), _ms(WINDOW_END)
    symbols = _covered_symbols(FUNDING_DB, w0, w1)
    recs = [r for r in (_one_symbol(s, w0, w1, KILL_K) for s in symbols) if r]

    wk_ret = [r["net_with_kill"] for r in recs]
    nk_ret = [r["net_no_kill"] for r in recs]
    kvn = evaluate.kill_vs_nokill(wk_ret, nk_ret)
    wk_pooled = float(np.mean(wk_ret)) if recs else 0.0
    shock_loss = KILL_K * SHOCK_FUNDING_PER_8H
    post = [evaluate.inject_shocks([NOTIONAL * r["net_with_kill"]], n_shocks=N_SHOCKS,
                                   shock_loss=NOTIONAL * shock_loss) / NOTIONAL for r in recs]
    post_pooled = float(np.mean(post)) if recs else 0.0
    gate = evaluate.gate_tail(with_kill_net_pooled=wk_pooled, post_shock_net_pooled=post_pooled)

    ksens = {}
    for k in K_SENSITIVITY:
        rs = [r for r in (_one_symbol(s, w0, w1, k) for s in symbols) if r]
        ksens[k] = {"pooled_net": float(np.mean([x["net_with_kill"] for x in rs])) if rs else 0.0,
                    "mean_kills": float(np.mean([x["n_kills"] for x in rs])) if rs else 0.0}

    cal = load_calibration()
    out = {"verdict": gate, "kill_vs_nokill": kvn, "with_kill_pooled": wk_pooled,
           "no_kill_pooled": float(np.mean(nk_ret)) if recs else 0.0,
           "post_shock_pooled": post_pooled, "k_sensitivity": ksens,
           "manifest": {"experiment": "funding-carry-tail-kill", "spec_commit": "9605758",
                        "kill_k": KILL_K, "n_shocks": N_SHOCKS, "shock_loss_frac": shock_loss,
                        "cost_model": {"active_model": cal.active_model,
                                       "calibration_identity_hash": calibration_identity_hash(cal)},
                        "symbols_used": [r["symbol"] for r in recs],
                        "generated_utc": datetime.now(timezone.utc).isoformat()}}
    dd_wk = float(np.mean([r["max_dd"] for r in recs])) if recs else 0.0
    dd_nk = float(np.mean([r["max_dd_no_kill"] for r in recs])) if recs else 0.0
    out["max_dd_pooled"] = {"with_kill": dd_wk, "no_kill": dd_nk, "kill_lowers_dd": bool(dd_wk < dd_nk)}
    slim = [{kk: r[kk] for kk in REQUIRED_KILL_KEYS} for r in recs]
    with open(os.path.join(OUTPUT_DIR_KILL, "per_symbol.json"), "w") as f:
        json.dump(slim, f, indent=2)
    with open(os.path.join(OUTPUT_DIR_KILL, "verdict.json"), "w") as f:
        json.dump(out, f, indent=2)
    lines = [
        "# Funding-carry tail-aware kill rule: VERDICT", "",
        f"**Verdict: {gate['verdict']}**  (G1 in-sample: {gate['pass_g1']}, G2 out-of-sample: {gate['pass_g2']})", "",
        f"- Symbols used {len(recs)}: {', '.join(r['symbol'] for r in recs)}",
        f"- With-kill pooled net: {wk_pooled:.4f}   No-kill pooled net: {out['no_kill_pooled']:.4f}",
        f"- Kill vs no-kill net: mean delta {kvn['mean_delta']:.4f}, CI95 [{kvn['ci_lo']:.4f}, {kvn['ci_hi']:.4f}], net_adds_value={kvn['kill_adds_value']}",
        f"- Max-DD pooled ($): with-kill {dd_wk:.2f} vs no-kill {dd_nk:.2f}, kill_lowers_dd={dd_wk < dd_nk}",
        f"- Post-{N_SHOCKS}-shock pooled net: {post_pooled:.4f}  (shock_loss/ea {shock_loss:.4f}, kill-capped at K settlements)",
        f"- K-sensitivity (descriptive): " + "; ".join(f"K={k}: net {v['pooled_net']:.4f}, kills {v['mean_kills']:.1f}" for k, v in ksens.items()), "",
        "Interpretation: kill adds value if net_adds_value (CI lo > 0) OR kill_lowers_dd; a PASS where",
        "the kill neither raises net nor lowers DD means the carry is already robust without it. Leverage 2x fixed.",
        "Scope: liquid universe, in-sample 2024-26 + 2 synthetic shocks. NOT production-deployable",
        "(rebalance #2, long-tail #3, live #4 are separate sub-projects).",
    ]
    with open(os.path.join(OUTPUT_DIR_KILL, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"VERDICT: {gate['verdict']}  (G1={gate['pass_g1']} G2={gate['pass_g2']}, "
          f"with-kill {wk_pooled:.4f} vs no-kill {out['no_kill_pooled']:.4f})")


if __name__ == "__main__":
    main()
