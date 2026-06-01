"""Driver: dumped live trades -> diagnosis -> findings.md + per_trade.json.

Read-only. The only prod touch is the prerequisite ssh+sqlite dump (see plan);
this driver consumes that JSON offline.
"""
from __future__ import annotations

import json
import math
import os
from statistics import median

from tools.cost_diagnosis.live_trades import load_live_trades
from tools.cost_diagnosis.liquidity import liquidity_series
from tools.cost_diagnosis.recompute import CORRECTIONS
from tools.cost_diagnosis.assemble import assemble_per_trade
from tools.cost_diagnosis.reconcile import reconcile

def _json_safe(obj):
    """Recursively replace non-finite floats (NaN/inf) with None for strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


OUT_DIR = os.path.join("data", "retune", "2026-06-01-cost-model-diagnosis")
CORRECTIONS_FOR_REPORT = CORRECTIONS


def _over_charge_summary(per_trade: list[dict]) -> dict:
    """Baseline falsification headline: winners whose model cost exceeds the move."""
    obs = [t for t in per_trade if not t["liquidity_unobservable"]]
    winners = [t for t in obs if t["pnl_usd"] > 0]
    exceeded = [
        t for t in winners
        if (t["costs"]["baseline"] / 100.0) > t["observed_move_pct"]
    ]
    ratios = [
        (t["costs"]["baseline"] / 100.0) / t["observed_move_pct"]
        for t in obs if t["observed_move_pct"] > 0
    ]
    return {
        "winners": len(winners), "winners_exceeded": len(exceeded),
        "median_over_charge_ratio": (median(ratios) if ratios else float("nan")),
    }


def write_reports(per_trade: list[dict], out_dir: str, corrections=CORRECTIONS):
    os.makedirs(out_dir, exist_ok=True)
    observable = [t for t in per_trade if not t["liquidity_unobservable"]]
    branch, winning, results = reconcile(observable, corrections)
    summary = _over_charge_summary(per_trade)

    _ocr = summary["median_over_charge_ratio"]
    _ocr_str = f"{_ocr:.2f}" if math.isfinite(_ocr) else "N/A (no observable moves)"
    lines = [
        "# Cost-model diagnosis — findings",
        "",
        f"**Branch verdict: {branch}**",
        f"Winning correction(s): {', '.join(winning) if winning else '(none)'}",
        "",
        "## Baseline falsification (over-charge headline)",
        f"- observable trades: {len(observable)} / {len(per_trade)}",
        f"- winners: {summary['winners']}; winners whose model cost exceeds the "
        f"entire price move: {summary['winners_exceeded']}",
        f"- median over-charge ratio (model cost / observed move): {_ocr_str}",
        "",
        "## Per-correction reconcile",
        "| correction | winners exceeded | tier medians (bps) | reconciles |",
        "|---|---|---|---|",
    ]
    for name, *_ in corrections:
        r = results[name]
        tm = ", ".join(f"{k}:{v:.1f}" for k, v in sorted(r["tier_medians"].items()))
        lines.append(f"| {name} | {r['winners_exceeded']} | {tm} | {r['reconciles']} |")
    lines += [
        "",
        "## Cross-check C — scan-price vs fill slippage (entry, conflated w/ operator delay)",
    ]
    sc = [t["scan_fill_slip_pct"] for t in per_trade if t.get("scan_fill_slip_pct") is not None]
    if sc:
        lines.append(f"- median scan->fill slip: {median(sc):.3f}% over {len(sc)} trades")
    else:
        lines.append("- no scan prices available")
    lines += [
        "",
        "## Next",
        "- RE-ANCHOR -> spec the winning correction; confirm with cross-check B "
        "(re-run pre-holdout under it).",
        "- REBUILD -> spec real-execution data collection + re-derivation (the v3).",
        "",
        "Read-only diagnostic. Thresholds pre-registered in the design spec section 3.",
    ]
    with open(os.path.join(out_dir, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "per_trade.json"), "w", encoding="utf-8") as f:
        json.dump(_json_safe(per_trade), f, indent=2, default=str)
    return branch, winning


def main():
    from backtest import get_cached_data

    trades = load_live_trades(os.path.join(OUT_DIR, "live_trades.json"))
    liq_map: dict = {}
    for sym in sorted({t.symbol for t in trades}):
        df1h = get_cached_data(sym, "1h")
        liq_map[sym] = liquidity_series(df1h) if df1h is not None and len(df1h) else None
    per_trade = assemble_per_trade(trades, liq_map)
    branch, winning = write_reports(per_trade, OUT_DIR)
    print(f"branch={branch} winning={winning}")


if __name__ == "__main__":
    main()
