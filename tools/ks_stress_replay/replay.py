"""Chronological event-loop replay: base stream + overlay -> portfolio result.

Decisions are made at ENTRY (using only state from trades CLOSED so far);
realized scaled PnL and DD updates happen at CLOSE. Events at the same
timestamp process CLOSE before ENTRY so a fresh decision sees freed capital.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _to_iso(t) -> str:
    if isinstance(t, str):
        dt = datetime.fromisoformat(t)
    else:
        dt = t
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def replay(base_stream: dict, overlay, capital_base: float = 1000.0) -> dict:
    """Replay every symbol's trades chronologically through `overlay`.

    base_stream: dict[symbol -> list[trade]], trade has entry_time, exit_time,
    pnl_usd, exit_reason. Returns dict with max_dd (negative fraction),
    total_pnl, final_equity, taken, skipped, engagements.
    """
    # ord: CLOSE=0, ENTRY=1 so closes settle before same-ts entries.
    events = []
    for symbol, trades in base_stream.items():
        for idx, tr in enumerate(trades):
            key = (symbol, idx)
            events.append((_to_iso(tr["entry_time"]), 1, "ENTRY", symbol, key, tr))
            events.append((_to_iso(tr["exit_time"]), 0, "CLOSE", symbol, key, tr))
    events.sort(key=lambda e: (e[0], e[1]))

    decisions: dict = {}
    equity = peak = float(capital_base)
    max_dd = 0.0
    total_pnl = 0.0
    taken = skipped = engagements = 0

    for ts, _ord, kind, symbol, key, tr in events:
        if kind == "ENTRY":
            skip, size_factor = overlay.decide(symbol, ts)
            decisions[key] = (skip, float(size_factor))
            if skip:
                skipped += 1
            else:
                taken += 1
            if skip or float(size_factor) < 1.0:
                engagements += 1
        else:  # CLOSE
            skip, size_factor = decisions.get(key, (False, 1.0))
            scaled = 0.0 if skip else float(tr["pnl_usd"]) * size_factor
            overlay.record_close(
                symbol, ts, scaled, tr.get("exit_reason", "") or "",
            )
            equity += scaled
            peak = max(peak, equity)
            dd = (equity - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
            total_pnl += scaled

    return {
        "max_dd": max_dd,
        "total_pnl": total_pnl,
        "final_equity": equity,
        "taken": taken,
        "skipped": skipped,
        "engagements": engagements,
    }
