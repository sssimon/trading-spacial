#!/usr/bin/env python3
"""Rule A check — veto-only alert for premature LONG exits.

Origin: data/retune/2026-05-15-manual-exit-market-context/findings.md (PR #359).
Hypothesis (n=16 sample, single bull regime): operator's MANUAL LONG closes on
green bars with close_position > 0.7 are systematically premature (6 of 6
PREMATURE in 4h post-exit hindsight). Rule A vetoes such exits.

## Rule A logic

For a given LONG position:
  GIVEN current 1h bar B (the most recent fully-closed bar for the symbol):

  color = "green" if B.close > B.open else "red"
  close_position = (B.close - B.low) / (B.high - B.low)

  IF color == "green" AND close_position > 0.7:
    RECOMMENDATION = "HOLD" (Rule A veto active)
    Rationale: green-strong bar pattern correlates with premature exit
    historically (6 of 6 LONG winners with this pattern continued favorable
    ≥1% in next 4h).

  ELIF color == "red" OR close_position < 0.5:
    RECOMMENDATION = "EXIT_OK" (Rule A clear)
    Rationale: stall/reversal signal present — operator's historical pattern
    when this fires correctly catches turns.

  ELSE:
    RECOMMENDATION = "AMBIGUOUS" (Rule A neither blocks nor confirms)
    Rationale: bar is green with close_position in [0.5, 0.7] gray zone.

For SHORT positions: Rule A does NOT apply per findings §5 (SHORT timing was
empirically GOOD in n=4 sample). Tool returns "NOT_APPLICABLE" for SHORTs.

## Usage

  python tools/rule_a_check.py --position-id 47 --signals-db PATH
  python tools/rule_a_check.py --symbol BTCUSDT --signals-db PATH --ohlcv-db PATH

## Caveats (do NOT skip)

- Rule A is **hypothesis-only** based on n=16 single-regime sample. NO statistical validation.
- Tool is veto-ONLY: emits recommendation, does NOT execute any trade.
- Requires up-to-date OHLCV cache. Run scanner periodically to refresh.
- Bear/sideways regime may invalidate the green-bar bias. Re-check if regime changes.
- Out-of-sample test pending (Phase 2 — see follow-up issue).

## NOT in this tool

- Telegram/webhook integration (Phase 2)
- Scanner.py integration (Phase 2)
- Auto-close logic (never — Rule A is veto-only by design)
- Live OHLCV provider calls (relies on data/ohlcv.db cache)
- Complementary exit trigger logic (Phase 2)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

# Rule A thresholds (Phase 1, locked by findings PR #359)
CLOSE_POSITION_BLOCK_THRESHOLD: Final[float] = 0.7  # > this on green bar = HOLD
CLOSE_POSITION_EXIT_THRESHOLD: Final[float] = 0.5   # < this = EXIT_OK
LIVE_BAR_TIMEFRAME: Final[str] = "1h"
LIVE_BAR_LOOKBACK_HOURS: Final[int] = 3  # query bars in last 3h to find most-recent closed

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_SIGNALS_DB: Final[Path] = REPO_ROOT / "signals.db"
DEFAULT_OHLCV_DB: Final[Path] = REPO_ROOT / "data" / "ohlcv.db"


def load_position(signals_db: Path, position_id: int | None, symbol: str | None) -> dict | None:
    """Load a single OPEN position. If position_id given, use that. Else load
    most-recent open position for symbol."""
    con = sqlite3.connect(signals_db)
    con.row_factory = sqlite3.Row
    con.text_factory = lambda x: x.decode("utf-8", errors="replace")
    if position_id is not None:
        row = con.execute("""
            SELECT id, symbol, direction, status, entry_price, entry_ts,
                   sl_price, tp_price, size_usd, pnl_usd, pnl_pct, exit_reason
            FROM positions WHERE id = ?
        """, (position_id,)).fetchone()
    elif symbol is not None:
        row = con.execute("""
            SELECT id, symbol, direction, status, entry_price, entry_ts,
                   sl_price, tp_price, size_usd, pnl_usd, pnl_pct, exit_reason
            FROM positions WHERE symbol = ? AND status = 'open'
            ORDER BY entry_ts DESC LIMIT 1
        """, (symbol,)).fetchone()
    else:
        row = None
    con.close()
    return dict(row) if row else None


def get_most_recent_closed_bar(
    ohlcv_db: Path,
    symbol: str,
    timeframe: str = LIVE_BAR_TIMEFRAME,
    as_of: datetime | None = None,
) -> dict | None:
    """Most recent fully-closed bar for symbol/timeframe at/before `as_of`.

    A bar is "fully closed" when current time >= bar.open_time + timeframe_duration.
    If as_of is None, uses datetime.now(UTC).
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    # 1h timeframe — bar is closed when as_of >= bar.open_time + 3600s
    cutoff_ms = int(as_of.timestamp() * 1000) - 3600 * 1000
    con = sqlite3.connect(ohlcv_db)
    row = con.execute("""
        SELECT open_time, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ? AND open_time <= ?
        ORDER BY open_time DESC LIMIT 1
    """, (symbol, timeframe, cutoff_ms)).fetchone()
    con.close()
    if not row:
        return None
    return {
        "open_time": row[0],
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "volume": row[5],
    }


def compute_rule_a(bar: dict, direction: str) -> dict:
    """Apply Rule A check to a bar + position direction.

    Returns dict with:
      - recommendation: HOLD / EXIT_OK / AMBIGUOUS / NOT_APPLICABLE
      - reasoning: human-readable explanation
      - bar_color: green / red / doji
      - close_position: float in [0, 1] (NaN-safe; None if bar.range == 0)
      - rule_a_applicable: bool
    """
    if direction not in ("LONG", "SHORT"):
        return {
            "recommendation": "NOT_APPLICABLE",
            "reasoning": f"Unknown direction {direction!r}. Rule A applies to LONG positions only.",
            "bar_color": None,
            "close_position": None,
            "rule_a_applicable": False,
        }
    if direction == "SHORT":
        return {
            "recommendation": "NOT_APPLICABLE",
            "reasoning": (
                "Rule A does NOT apply to SHORT positions (findings §5: SHORT exit "
                "timing empirically GOOD in n=4 sample, no green-bar bias)."
            ),
            "bar_color": None,
            "close_position": None,
            "rule_a_applicable": False,
        }

    o = bar["open"]
    h = bar["high"]
    l = bar["low"]
    c = bar["close"]
    bar_range = h - l
    if bar_range <= 0:
        # Degenerate bar (high == low): doji-like, no close_position info
        return {
            "recommendation": "AMBIGUOUS",
            "reasoning": "Bar has zero range (high == low). Cannot compute close_position.",
            "bar_color": "doji",
            "close_position": None,
            "rule_a_applicable": True,
        }
    close_position = (c - l) / bar_range
    bar_color = "green" if c > o else ("red" if c < o else "doji")

    # Rule A logic
    if bar_color == "green" and close_position > CLOSE_POSITION_BLOCK_THRESHOLD:
        return {
            "recommendation": "HOLD",
            "reasoning": (
                f"Rule A veto active. Current bar is green ({c:.4f} > {o:.4f}) AND "
                f"close_position {close_position:.2f} > {CLOSE_POSITION_BLOCK_THRESHOLD}. "
                f"Historical pattern: 6/6 LONG winners with this signature exited prematurely "
                f"(median +2.74% additional upside continued in next 4h). "
                f"Recommend HOLD."
            ),
            "bar_color": bar_color,
            "close_position": round(close_position, 4),
            "rule_a_applicable": True,
        }
    if bar_color == "red" or close_position < CLOSE_POSITION_EXIT_THRESHOLD:
        return {
            "recommendation": "EXIT_OK",
            "reasoning": (
                f"Rule A clear. Bar color {bar_color}, close_position {close_position:.2f}. "
                f"Historical pattern: 2/2 LONG REVERSAL_CAUGHT exits had this signature "
                f"(red bar OR close near bottom of range). Exit timing supported."
            ),
            "bar_color": bar_color,
            "close_position": round(close_position, 4),
            "rule_a_applicable": True,
        }
    # Gray zone: green AND close_position in [0.5, 0.7]
    return {
        "recommendation": "AMBIGUOUS",
        "reasoning": (
            f"Bar is {bar_color}, close_position {close_position:.2f} in gray zone "
            f"[{CLOSE_POSITION_EXIT_THRESHOLD}, {CLOSE_POSITION_BLOCK_THRESHOLD}]. "
            f"Rule A neither vetoes nor confirms exit. Operator discretion."
        ),
        "bar_color": bar_color,
        "close_position": round(close_position, 4),
        "rule_a_applicable": True,
    }


def format_report(position: dict, bar: dict | None, ruling: dict) -> str:
    """Human-readable report for CLI stdout."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"Rule A check — position id={position['id']} {position['symbol']} {position['direction']}")
    lines.append("=" * 70)
    lines.append(f"Status: {position['status']}")
    lines.append(f"Entry: ${position['entry_price']} @ {position['entry_ts']}")
    if position.get("size_usd"):
        lines.append(f"Size: ${position['size_usd']:.2f}")
    if bar:
        bar_dt = datetime.fromtimestamp(bar["open_time"] / 1000, tz=timezone.utc)
        lines.append(f"\nCurrent 1h bar @ {bar_dt.isoformat()}:")
        lines.append(f"  open={bar['open']:.4f}  high={bar['high']:.4f}  low={bar['low']:.4f}  close={bar['close']:.4f}")
        lines.append(f"  color={ruling['bar_color']}  close_position={ruling['close_position']}")
    else:
        lines.append("\n(no OHLCV bar available for symbol — cannot evaluate)")
    lines.append(f"\n=== RECOMMENDATION: {ruling['recommendation']} ===")
    lines.append(f"\n{ruling['reasoning']}")
    if ruling["recommendation"] == "HOLD":
        lines.append("\n(Operator may still override — Rule A is veto-only, not auto-action)")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--position-id", type=int, help="Specific position id to check")
    grp.add_argument("--symbol", help="Symbol — checks most-recent OPEN position for this symbol")
    p.add_argument("--signals-db", default=str(DEFAULT_SIGNALS_DB))
    p.add_argument("--ohlcv-db", default=str(DEFAULT_OHLCV_DB))
    p.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    p.add_argument("--as-of", help="ISO timestamp (UTC) to evaluate as-of. Default: now.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    signals_db = Path(args.signals_db)
    ohlcv_db = Path(args.ohlcv_db)
    if not signals_db.exists():
        sys.stderr.write(f"ERROR: signals.db not found at {signals_db}\n")
        return 1
    if not ohlcv_db.exists():
        sys.stderr.write(f"ERROR: ohlcv.db not found at {ohlcv_db}\n")
        return 1
    as_of = None
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)

    position = load_position(signals_db, args.position_id, args.symbol)
    if position is None:
        sys.stderr.write("ERROR: no matching position found\n")
        return 1

    bar = get_most_recent_closed_bar(ohlcv_db, position["symbol"], as_of=as_of)
    if bar is None:
        ruling = {
            "recommendation": "NOT_APPLICABLE",
            "reasoning": "No OHLCV bar available for symbol (cache may be stale). Refresh OHLCV before re-checking.",
            "bar_color": None,
            "close_position": None,
            "rule_a_applicable": False,
        }
    else:
        ruling = compute_rule_a(bar, position["direction"])

    if args.json:
        out = {
            "position": {
                "id": position["id"],
                "symbol": position["symbol"],
                "direction": position["direction"],
                "status": position["status"],
                "entry_price": position["entry_price"],
                "entry_ts": position["entry_ts"],
            },
            "current_bar": bar,
            "ruling": ruling,
            "thresholds": {
                "close_position_block": CLOSE_POSITION_BLOCK_THRESHOLD,
                "close_position_exit": CLOSE_POSITION_EXIT_THRESHOLD,
            },
            "tool_version": 1,
            "source_findings": "data/retune/2026-05-15-manual-exit-market-context/findings.md (PR #359)",
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        print(format_report(position, bar, ruling))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
