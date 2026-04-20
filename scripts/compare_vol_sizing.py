"""Compare baseline (vol_mult=1.0) vs. vol-normalized sizing on the same data.

Baseline is obtained by monkey-patching `annualized_vol_yang_zhang` to
return TARGET_VOL_ANNUAL, which makes vol_mult fold to 1.0 everywhere.
This keeps both runs on the same simulate_strategy code path so the
only variable is the risk-scaling term.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, ".")

import backtest
from backtest import (
    get_cached_data,
    simulate_strategy,
    calculate_metrics,
    TARGET_VOL_ANNUAL,
)


def run_one(label: str, df1h, df4h, df5m, df1d, df_fng, df_funding,
            symbol: str, start: datetime, end: datetime, patch_vol: bool):
    print(f"\n=== {label} ===", flush=True)
    kwargs = dict(
        symbol=symbol,
        df1d=df1d,
        sim_start=start,
        sim_end=end,
        df_fng=df_fng,
        df_funding=df_funding,
    )
    if patch_vol:
        with patch.object(backtest, "annualized_vol_yang_zhang", return_value=TARGET_VOL_ANNUAL):
            trades, equity = simulate_strategy(df1h, df4h, df5m, **kwargs)
    else:
        trades, equity = simulate_strategy(df1h, df4h, df5m, **kwargs)

    metrics = calculate_metrics(trades, equity)
    print(f"  Trades:        {metrics['total_trades']}")
    print(f"  Win Rate:      {metrics['win_rate']}%")
    print(f"  Profit Factor: {metrics['profit_factor']}")
    print(f"  Net P&L:       ${metrics['net_pnl']:+,.2f}")
    print(f"  Return:        {metrics['total_return_pct']:+.2f}%")
    print(f"  Max Drawdown:  {metrics['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe:        {metrics['sharpe_ratio']}")
    print(f"  Final Equity:  ${metrics['final_equity']:,.2f}")
    return metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--json-out", default=None, help="Optional path to dump both metrics")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
           if args.end else datetime.now(timezone.utc))
    data_start = datetime(start.year - 1, 1, 1, tzinfo=timezone.utc)

    print(f"=== Comparative backtest: {symbol} | {args.start} → {args.end or 'now'} ===")
    print("  Loading OHLCV via data.market_data (shared cache for both runs)...")

    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)
    df_fng = backtest.get_historical_fear_greed()
    df_funding = backtest.get_historical_funding_rate()

    baseline = run_one("BASELINE (vol_mult = 1.0)", df1h, df4h, df5m, df1d, df_fng, df_funding,
                       symbol, start, end, patch_vol=True)
    vol_sized = run_one("VOL-NORMALIZED", df1h, df4h, df5m, df1d, df_fng, df_funding,
                        symbol, start, end, patch_vol=False)

    d_pnl = vol_sized["net_pnl"] - baseline["net_pnl"]
    d_ret = vol_sized["total_return_pct"] - baseline["total_return_pct"]
    # max_drawdown_pct is stored as a NEGATIVE number (loss). Positive delta
    # means vol-sized is less negative → smaller drawdown → BETTER.
    d_dd = vol_sized["max_drawdown_pct"] - baseline["max_drawdown_pct"]
    d_sr = vol_sized["sharpe_ratio"] - baseline["sharpe_ratio"]
    print("\n=== DELTA ===")
    print(f"  Net P&L delta:     ${d_pnl:+,.2f}  ({'better' if d_pnl > 0 else 'worse'})")
    print(f"  Return delta:      {d_ret:+.2f} pp")
    print(f"  Max DD delta:      {d_dd:+.2f} pp  ({'better (smaller DD)' if d_dd > 0 else 'worse (deeper DD)'})")
    print(f"  Sharpe delta:      {d_sr:+.2f}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"baseline": baseline, "vol_sized": vol_sized,
                       "delta": {"pnl": d_pnl, "return_pp": d_ret, "dd_pp": d_dd, "sharpe": d_sr}},
                      f, indent=2, default=str)
        print(f"\n  Wrote {args.json_out}")


if __name__ == "__main__":
    main()
