"""Hunger games gate: run baseline + 3 contenders, emit winner.

Spec: docs/superpowers/specs/es/2026-04-20-per-symbol-regime-design.md §9
Plan: docs/superpowers/plans/2026-04-20-per-symbol-regime.md Task 6
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


SANITY_THRESHOLD_USD = 10.0
TIEBREAK_THRESHOLD_PCT = 5.0


def check_sanity(baseline: dict, global_contender: dict) -> tuple[bool, str]:
    """Return (ok, message). OK if |baseline − global| ≤ $10."""
    delta = abs(baseline["total_pnl"] - global_contender["total_pnl"])
    if delta <= SANITY_THRESHOLD_USD:
        return True, f"|ΔP&L| = ${delta:.2f} OK"
    return False, f"|ΔP&L| = ${delta:.2f} exceeds ${SANITY_THRESHOLD_USD} drift threshold"


def evaluate_regime_gate(baseline: dict, contenders: dict) -> dict:
    """Apply 4-criterion gate to each contender vs. baseline (inherited from matiz #2).

    Returns {mode: {verdict: PASS|FAIL, reasons: [...]}}.
    """
    verdicts = {}
    for mode, tuned in contenders.items():
        reasons = []
        fail = False

        # 1. Aggregate P&L
        bl_pnl = baseline["total_pnl"]
        tn_pnl = tuned["total_pnl"]
        if bl_pnl > 0:
            req = bl_pnl * 1.10
            ok = tn_pnl >= req
            pct = (tn_pnl - bl_pnl) / bl_pnl * 100
            reasons.append(f"[1] agg P&L: ${bl_pnl:+,.0f} -> ${tn_pnl:+,.0f} "
                           f"({pct:+.1f}%, req +10%) {'OK' if ok else 'FAIL'}")
        else:
            ok = tn_pnl >= bl_pnl + 1000
            reasons.append(f"[1] agg P&L baseline <= 0; req >= baseline + $1000. "
                           f"Tuned ${tn_pnl:+,.0f} {'OK' if ok else 'FAIL'}")
        fail = fail or not ok

        # 2. Max DD
        dd_delta = tuned["max_dd_pct"] - baseline["max_dd_pct"]
        ok2 = dd_delta >= -2.0
        reasons.append(f"[2] Max DD: {baseline['max_dd_pct']:.1f}% -> "
                       f"{tuned['max_dd_pct']:.1f}% ({dd_delta:+.1f}pp, tol -2pp) "
                       f"{'OK' if ok2 else 'FAIL'}")
        fail = fail or not ok2

        # 3. Per-symbol
        fails_sym = []
        for sym, bl in baseline["per_symbol"].items():
            tn = tuned["per_symbol"].get(sym, {"pnl": 0})
            if bl["pnl"] > 0:
                pct = (tn["pnl"] - bl["pnl"]) / bl["pnl"] * 100
                if pct < -10.0:
                    fails_sym.append(f"{sym} ({pct:+.1f}%)")
            elif bl["pnl"] < 0:
                if tn["pnl"] < bl["pnl"] - 1000:
                    fails_sym.append(f"{sym} (deepened loss)")
        ok3 = len(fails_sym) == 0
        reasons.append(f"[3] per-symbol: {'OK' if ok3 else 'FAIL — ' + '; '.join(fails_sym)}")
        fail = fail or not ok3

        # 4. DOGE PF ≥ 4.0
        doge_pf = tuned["per_symbol"].get("DOGEUSDT", {}).get("pf", 0)
        ok4 = doge_pf >= 4.0
        reasons.append(f"[4] DOGE PF: {doge_pf:.2f} (req >= 4.0) {'OK' if ok4 else 'FAIL'}")
        fail = fail or not ok4

        verdicts[mode] = {"verdict": "FAIL" if fail else "PASS", "reasons": reasons}
    return verdicts


def rank_winners(passing_contenders: dict) -> str | None:
    """Rank by P&L desc; tiebreak (within 5%) by lower per-symbol P&L variance."""
    if not passing_contenders:
        return None
    modes = list(passing_contenders.keys())
    modes.sort(key=lambda m: -passing_contenders[m]["total_pnl"])
    top = modes[0]
    top_pnl = passing_contenders[top]["total_pnl"]

    def var(mode):
        pnls = [s["pnl"] for s in passing_contenders[mode]["per_symbol"].values()]
        return statistics.pstdev(pnls) if len(pnls) > 1 else 0

    for m in modes[1:]:
        m_pnl = passing_contenders[m]["total_pnl"]
        if abs(top_pnl - m_pnl) / max(abs(top_pnl), 1) * 100 <= TIEBREAK_THRESHOLD_PCT:
            if var(m) < var(top):
                top = m
                top_pnl = m_pnl
    return top


def run_portfolio(config_path: str, start, end, symbols, regime_mode: str, df1d_btc):
    """Run portfolio backtest with regime_mode. Returns aggregate dict."""
    import backtest
    from backtest import get_cached_data, simulate_strategy, calculate_metrics

    data_start = datetime(start.year - 1, 1, 1, tzinfo=timezone.utc)
    cfg = json.loads(Path(config_path).read_text()) if Path(config_path).exists() else {}
    overrides = cfg.get("symbol_overrides", {})

    df_fng = backtest.get_historical_fear_greed()
    df_funding = backtest.get_historical_funding_rate()

    per_sym = {}
    total_pnl = 0.0
    max_dd = 0.0
    for sym in symbols:
        try:
            df1h = get_cached_data(sym, "1h", start_date=data_start)
            df4h = get_cached_data(sym, "4h", start_date=data_start)
            df5m = get_cached_data(sym, "5m", start_date=data_start)
            df1d = get_cached_data(sym, "1d", start_date=data_start)
        except Exception as e:
            per_sym[sym] = {"pnl": 0, "pf": 0, "max_dd_pct": 0, "error": str(e)}
            continue
        if df1h.empty:
            per_sym[sym] = {"pnl": 0, "pf": 0, "max_dd_pct": 0, "error": "no data"}
            continue
        trades, equity = simulate_strategy(
            df1h, df4h, df5m, sym, df1d=df1d,
            sim_start=start, sim_end=end,
            df_fng=df_fng, df_funding=df_funding,
            symbol_overrides=overrides,
            regime_mode=regime_mode,
            df1d_btc=df1d_btc,
        )
        m = calculate_metrics(trades, equity)
        per_sym[sym] = {"pnl": m["net_pnl"], "pf": m["profit_factor"],
                       "max_dd_pct": m["max_drawdown_pct"]}
        total_pnl += m["net_pnl"]
        max_dd = min(max_dd, m["max_drawdown_pct"])
    return {"total_pnl": total_pnl, "max_dd_pct": max_dd, "per_symbol": per_sym}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-start", required=True)
    ap.add_argument("--test-end", required=True)
    ap.add_argument("--full-start", required=True)
    ap.add_argument("--full-end", required=True)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--output", default="/tmp/gate_regime_report.json")
    args = ap.parse_args()

    from btc_scanner import DEFAULT_SYMBOLS
    from backtest import get_cached_data
    symbols = list(DEFAULT_SYMBOLS)

    test_start = datetime.strptime(args.test_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    test_end = datetime.strptime(args.test_end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    full_start = datetime.strptime(args.full_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    full_end = datetime.strptime(args.full_end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    data_start = datetime(full_start.year - 1, 1, 1, tzinfo=timezone.utc)
    df1d_btc = get_cached_data("BTCUSDT", "1d", start_date=data_start)

    print(f"=== Hunger Games: 4 contenders × test + full windows ===", flush=True)

    baseline = run_portfolio(args.config, test_start, test_end, symbols, "global", df1d_btc)
    contenders = {}
    for mode in ["global", "hybrid", "hybrid_momentum"]:
        print(f"  running {mode} (test window)...", flush=True)
        contenders[mode] = run_portfolio(args.config, test_start, test_end, symbols, mode, df1d_btc)

    ok, msg = check_sanity(baseline, contenders["global"])
    if not ok:
        print(f"SANITY CHECK FAILED: {msg}")
        sys.exit(2)

    competing = {m: c for m, c in contenders.items() if m != "global"}
    verdicts = evaluate_regime_gate(baseline, competing)

    passing = {m: contenders[m] for m, v in verdicts.items() if v["verdict"] == "PASS"}
    winner = rank_winners(passing)

    full_results = {}
    for mode in ["global", "hybrid", "hybrid_momentum"]:
        full_results[mode] = run_portfolio(args.config, full_start, full_end, symbols, mode, df1d_btc)

    print("\n" + "=" * 60)
    print(f"  GATE: Hunger Games — per-symbol regime")
    print(f"  Test window: {args.test_start} → {args.test_end}")
    print("=" * 60)
    print(f"Sanity check: {msg}")
    print(f"Baseline (global):  ${baseline['total_pnl']:+,.0f}, DD {baseline['max_dd_pct']:.1f}%")
    for mode, c in contenders.items():
        if mode == "global":
            continue
        v = verdicts[mode]
        doge_pf = c['per_symbol'].get('DOGEUSDT', {}).get('pf', 0)
        print(f"{mode:20s} ${c['total_pnl']:+,.0f}, DD {c['max_dd_pct']:.1f}%, "
              f"DOGE PF {doge_pf:.2f} → {v['verdict']}")
        for r in v["reasons"]:
            print(f"  {r}")
    print("")
    if winner:
        print(f"WINNER: {winner} (${passing[winner]['total_pnl']:+,.0f})")
    else:
        print("NO WINNER — no contender passed the gate")
    print("")
    print("Full-window context (NOT for verdict):")
    for mode, c in full_results.items():
        print(f"  {mode}: ${c['total_pnl']:+,.0f}")
    print("=" * 60)

    Path(args.output).write_text(json.dumps({
        "winner": winner,
        "sanity_check": {"ok": ok, "message": msg},
        "baseline": baseline,
        "contenders": contenders,
        "verdicts": verdicts,
        "full_window": full_results,
    }, indent=2, default=str))
    print(f"Wrote {args.output}")

    sys.exit(0 if winner else 1)


if __name__ == "__main__":
    main()
