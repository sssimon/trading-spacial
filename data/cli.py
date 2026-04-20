"""Convenience CLI: python -m data.cli {backfill, repair, stats, init}"""
import argparse
import json
import sys
from datetime import datetime, timezone

from data import market_data as md
from data import _storage


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "+" not in s and "Z" not in s \
        else datetime.fromisoformat(s.replace("Z", "+00:00"))


def cmd_backfill(args):
    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else None
    n = md.backfill(args.symbol, args.timeframe, start, end)
    print(f"Backfilled {n} bars for {args.symbol} {args.timeframe}")


def cmd_repair(args):
    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else None
    n = md.repair(args.symbol, args.timeframe, start, end)
    print(f"Repaired {n} bars for {args.symbol} {args.timeframe}")


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def cmd_stats(args):
    stats = md.get_stats()
    print(json.dumps(_jsonable(stats), indent=2, default=str))


def cmd_init(args):
    _storage.init_schema()
    print(f"Schema initialized at {_storage.DB_PATH}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m data.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_b = sub.add_parser("backfill", help="Bulk historical fetch")
    p_b.add_argument("symbol"); p_b.add_argument("timeframe")
    p_b.add_argument("start"); p_b.add_argument("end", nargs="?")
    p_b.set_defaults(func=cmd_backfill)

    p_r = sub.add_parser("repair", help="Force re-fetch overwriting a range")
    p_r.add_argument("symbol"); p_r.add_argument("timeframe")
    p_r.add_argument("start"); p_r.add_argument("end", nargs="?")
    p_r.set_defaults(func=cmd_repair)

    p_s = sub.add_parser("stats", help="Print metrics snapshot")
    p_s.set_defaults(func=cmd_stats)

    p_i = sub.add_parser("init", help="Create ohlcv.db with schema (usually auto)")
    p_i.set_defaults(func=cmd_init)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
