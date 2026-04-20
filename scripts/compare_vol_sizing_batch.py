"""Batch comparative backtest across all #125 symbols. Aggregates the
per-symbol results into a single table and a total P&L swing.

Reuses the same patch-vol technique so both runs share the data layer
cache (faster second pass) and the simulate_strategy code path (only
variable is vol_mult)."""
import argparse
import json
import sys
import time
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


SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
]


def run_pair(symbol: str, start: datetime, end: datetime, df_fng, df_funding):
    data_start = datetime(start.year - 1, 1, 1, tzinfo=timezone.utc)
    t0 = time.time()
    df1h = get_cached_data(symbol, "1h", start_date=data_start)
    df4h = get_cached_data(symbol, "4h", start_date=data_start)
    df5m = get_cached_data(symbol, "5m", start_date=data_start)
    df1d = get_cached_data(symbol, "1d", start_date=data_start)
    load_s = time.time() - t0

    if df1h.empty or df4h.empty or df5m.empty:
        print(f"  ! {symbol}: no data returned — skipping")
        return None

    kwargs = dict(symbol=symbol, df1d=df1d, sim_start=start, sim_end=end,
                  df_fng=df_fng, df_funding=df_funding)

    with patch.object(backtest, "annualized_vol_yang_zhang", return_value=TARGET_VOL_ANNUAL):
        trades_b, eq_b = simulate_strategy(df1h, df4h, df5m, **kwargs)
    m_b = calculate_metrics(trades_b, eq_b)

    trades_v, eq_v = simulate_strategy(df1h, df4h, df5m, **kwargs)
    m_v = calculate_metrics(trades_v, eq_v)

    print(f"  {symbol:10s}  trades={m_b['total_trades']:3d}  "
          f"baseline=${m_b['net_pnl']:+9,.0f}  vol=${m_v['net_pnl']:+9,.0f}  "
          f"delta=${m_v['net_pnl'] - m_b['net_pnl']:+9,.0f}  "
          f"(load {load_s:.1f}s)", flush=True)

    return {
        "symbol": symbol,
        "trades": m_b["total_trades"],
        "baseline": m_b,
        "vol_sized": m_v,
        "pnl_delta": m_v["net_pnl"] - m_b["net_pnl"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--symbols", default=None, help="Comma-separated override")
    ap.add_argument("--json-out", default="/tmp/vol_compare_batch.json")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
           if args.end else datetime.now(timezone.utc))
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else SYMBOLS

    print(f"=== Batch comparative: {args.start} → {args.end or 'now'} | {len(symbols)} symbols ===")
    df_fng = backtest.get_historical_fear_greed()
    df_funding = backtest.get_historical_funding_rate()

    results = []
    for sym in symbols:
        try:
            r = run_pair(sym, start, end, df_fng, df_funding)
            if r is not None:
                results.append(r)
        except Exception as e:
            print(f"  ! {sym}: {type(e).__name__}: {e}", flush=True)

    total_baseline = sum(r["baseline"]["net_pnl"] for r in results)
    total_vol = sum(r["vol_sized"]["net_pnl"] for r in results)
    swing = total_vol - total_baseline

    print("\n=== AGGREGATE ===")
    print(f"  Symbols run:           {len(results)}")
    print(f"  Total baseline P&L:    ${total_baseline:+,.2f}")
    print(f"  Total vol-sized P&L:   ${total_vol:+,.2f}")
    print(f"  Total swing:           ${swing:+,.2f}")

    with open(args.json_out, "w") as f:
        json.dump({
            "start": args.start, "end": args.end or datetime.now(timezone.utc).date().isoformat(),
            "symbols": [r["symbol"] for r in results],
            "per_symbol": results,
            "total_baseline_pnl": total_baseline,
            "total_vol_pnl": total_vol,
            "swing": swing,
        }, f, indent=2, default=str)
    print(f"  Wrote {args.json_out}")


if __name__ == "__main__":
    main()
