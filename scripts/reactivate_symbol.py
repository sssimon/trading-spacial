"""Manually reactivate a PAUSED symbol.

Usage:
    python scripts/reactivate_symbol.py BTCUSDT --reason "backtest validated"

Resets symbol_health.state to NORMAL and sets manual_override=1 so future
evaluations respect the reactivation until a severe rule (e.g. 3mo neg
again) triggers another transition. Records an event in
symbol_health_events with trigger_reason='manual_override'.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbol", help="Symbol to reactivate (e.g. BTCUSDT)")
    ap.add_argument("--reason", default="manual",
                     help="Context recorded in symbol_health_events (default: 'manual')")
    args = ap.parse_args()

    symbol = args.symbol.upper()

    from health import get_symbol_state, reactivate_symbol
    import btc_api

    btc_api.init_db()  # idempotent — ensures the tables exist on first run

    before = get_symbol_state(symbol)
    print(f"State before: {before}")

    if before == "NORMAL":
        print(f"Note: {symbol} is already NORMAL. Proceeding anyway to set manual_override=1 + log event.")

    reactivate_symbol(symbol, reason=args.reason)

    after = get_symbol_state(symbol)
    print(f"State after:  {after}")
    print(f"Reason logged: {args.reason!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
