"""Read-only loader for the one-time mode=ro dump of closed live positions.

The dump is produced by the prerequisite ssh+sqlite3 command (see plan). This
module only loads + validates that JSON, keeping the rest of the diagnostic
offline and testable. No prod access here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

_REQUIRED = (
    "symbol", "direction", "size_usd", "entry_price", "entry_ts",
    "exit_price", "exit_ts", "pnl_usd",
)


@dataclass(frozen=True)
class LiveTrade:
    symbol: str
    direction: str
    size_usd: float
    entry_price: float
    entry_ts: str
    exit_price: float
    exit_ts: str
    pnl_usd: float
    scan_price: float | None
    scan_ts: str | None


def load_live_trades(path: str) -> list[LiveTrade]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out: list[LiveTrade] = []
    for r in raw:
        for k in _REQUIRED:
            if r.get(k) is None:
                raise ValueError(f"live trade {r.get('id')} missing required field {k!r}")
        sp = r.get("scan_price")
        out.append(LiveTrade(
            symbol=str(r["symbol"]), direction=str(r["direction"]),
            size_usd=float(r["size_usd"]), entry_price=float(r["entry_price"]),
            entry_ts=str(r["entry_ts"]), exit_price=float(r["exit_price"]),
            exit_ts=str(r["exit_ts"]), pnl_usd=float(r["pnl_usd"]),
            scan_price=(float(sp) if sp is not None else None),
            scan_ts=(str(r["scan_ts"]) if r.get("scan_ts") is not None else None),
        ))
    return out
