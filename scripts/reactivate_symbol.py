"""Manually reactivate a PAUSED symbol — transitions PAUSED → PROBATION (B5 #199).

Usage:
    python scripts/reactivate_symbol.py BTCUSDT --reason "backtest validated"

Transitions symbol_health.state from PAUSED to PROBATION. The symbol enters
PROBATION with `probation_trades_remaining` computed from days_paused. Reason
'manual' sets manual_override=1; any other reason sets it to 0 (e.g.
'backtest_validated' implies operator-driven but auto-style).

Records an event in symbol_health_events with trigger_reason='reactivated_<reason>'.
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

    if before != "PAUSED":
        print(f"Note: {symbol} is not in PAUSED (current={before}). reactivate_symbol will no-op + warn.")

    cfg = btc_api.load_config()
    reactivate_symbol(symbol, reason=args.reason, cfg=cfg)

    after = get_symbol_state(symbol)
    print(f"State after:  {after}")
    print(f"Reason logged: {args.reason!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
