"""Poll GET /status and watch market_data counters for the Epic 140 soak window.

Run this for ~1 week after deploy. Thresholds (per plan Task 24 Step 4):
  fetches_total              : should grow at ~2.2 req/min (≈130/hour at 20 syms × 3 TFs × 12/h / some dedup)
  fallback_fetches_total     : should stay near zero (Bybit fallback rarely triggered)
  invalid_bars_dropped_total : MUST stay at 0 (any non-zero = data integrity issue)
  provider_errors_total      : occasional 5xx is normal; persistent = provider problem

Usage:
  python scripts/watch_market_data_status.py                       # 60s interval, local API
  python scripts/watch_market_data_status.py --url https://... --interval 300
  python scripts/watch_market_data_status.py --api-key $KEY        # if /status is protected
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)


def fetch(url: str, api_key: str | None) -> dict:
    headers = {"X-API-Key": api_key} if api_key else {}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def _counter(md: dict, name: str) -> int:
    """Sum all label combinations for a counter."""
    raw = md.get("counters", {}).get(name, {})
    if isinstance(raw, dict):
        total = 0
        for v in raw.values():
            if isinstance(v, (int, float)):
                total += int(v)
            elif isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, (int, float)):
                        total += int(vv)
        return total
    if isinstance(raw, (int, float)):
        return int(raw)
    return 0


def summarize(md: dict) -> dict:
    return {
        "fetches_total": _counter(md, "fetches_total"),
        "fallback_fetches_total": _counter(md, "fallback_fetches_total"),
        "invalid_bars_dropped_total": _counter(md, "invalid_bars_dropped_total"),
        "provider_errors_total": _counter(md, "provider_errors_total"),
        "provider_switches_total": _counter(md, "provider_switches_total"),
        "cache_hits_total": _counter(md, "cache_hits_total"),
        "double_checked_hits_total": _counter(md, "double_checked_hits_total"),
    }


def verdict(prev: dict | None, cur: dict, elapsed_s: float) -> list[str]:
    """Flags (emoji-free) for the thresholds."""
    flags = []
    if cur["invalid_bars_dropped_total"] > 0:
        flags.append(f"[CRITICAL] invalid_bars_dropped_total = {cur['invalid_bars_dropped_total']} — data integrity issue")
    if cur["fallback_fetches_total"] > 0 and prev is not None:
        delta = cur["fallback_fetches_total"] - prev["fallback_fetches_total"]
        if delta > 0:
            flags.append(f"[WARN]  fallback_fetches_total +{delta} in last {elapsed_s:.0f}s — check primary provider health")
    if prev is not None:
        fetch_delta = cur["fetches_total"] - prev["fetches_total"]
        rate_per_min = fetch_delta / max(elapsed_s, 1) * 60
        flags.append(f"[OK]    fetch rate in last window: {rate_per_min:.1f} req/min (target ~2.2)")
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("BTC_API_URL", "http://localhost:8000/status"))
    ap.add_argument("--api-key", default=os.environ.get("BTC_API_KEY"))
    ap.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    ap.add_argument("--once", action="store_true", help="Print once and exit")
    args = ap.parse_args()

    prev = None
    prev_t = time.time()
    while True:
        try:
            body = fetch(args.url, args.api_key)
        except Exception as e:
            print(f"{datetime.now(timezone.utc).isoformat()}  [ERROR] fetch failed: {e}")
            if args.once:
                sys.exit(1)
            time.sleep(args.interval)
            continue

        md = body.get("market_data", {})
        cur = summarize(md)
        now = time.time()
        elapsed = now - prev_t

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"{ts}  fetches={cur['fetches_total']}  "
              f"fallback={cur['fallback_fetches_total']}  "
              f"invalid_bars={cur['invalid_bars_dropped_total']}  "
              f"errors={cur['provider_errors_total']}  "
              f"switches={cur['provider_switches_total']}  "
              f"cache_hits={cur['cache_hits_total']}")

        for flag in verdict(prev, cur, elapsed):
            print(f"    {flag}")

        if args.once:
            return

        prev, prev_t = cur, now
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
