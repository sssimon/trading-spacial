"""One-shot script to generate frozen klines CSVs and frozen HTTP response JSON.

Run: python -m tests._fixtures.capture_klines

Output is committed to the repo and regenerated only on intentional behavior change.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pandas as pd
import requests

# Ensure repo root on path (for `data` imports)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data import market_data as md  # noqa: E402

_OUT = Path(__file__).parent


def main() -> None:
    for tf in ("5m", "1h", "4h", "1d"):
        df = md.get_klines("BTCUSDT", tf, limit=210)
        if df.empty:
            raise RuntimeError(f"empty klines for BTCUSDT {tf}")
        out_path = _OUT / f"btcusdt_{tf}.csv"
        df.to_csv(out_path, index=False)
        print(f"saved {out_path.name} ({len(df)} rows)")

    fng = requests.get(
        "https://api.alternative.me/fng/?limit=1", timeout=10).json()
    funding = requests.get(
        "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
        timeout=10,
    ).json()
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "ADAUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "AVAXUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "DOGEUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "UNIUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "XLMUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "PENDLEUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "JUPUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "RUNEUSDT", "status": "TRADING", "quoteAsset": "USDT"},
        ],
    }

    payloads = {
        "fng": fng,
        "funding": funding,
        "exchangeInfo": exchange_info,
    }
    out = _OUT / "scanner_frozen_responses.json"
    out.write_text(json.dumps(payloads, indent=2))
    print(f"saved {out.name}")


if __name__ == "__main__":
    main()
