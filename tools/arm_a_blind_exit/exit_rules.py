"""Wilder ATR + blind exit simulators with explicit intra-bar fill conventions.

All rules trail from the RUNNING peak/trough (causal — no look-ahead, Halberg).
Pessimistic fill: within a 5m bar the adverse extreme is assumed touched before
the favorable one. Optimistic: the reverse (sensitivity arm, spec §5)."""
from __future__ import annotations
from .constants import CHANDELIER_MULT, GIVEBACK_FRAC, MAX_HOLD_H


def wilder_atr(bars_1h: list[dict], period: int = 22) -> float:
    """Wilder's ATR over the LAST `period` true ranges ending at the final bar.

    bars_1h: chronological dicts with high/low/close. Needs >= period+1 bars."""
    if len(bars_1h) < period + 1:
        raise ValueError(f"need >= {period + 1} 1h bars, got {len(bars_1h)}")
    trs = []
    for prev, cur in zip(bars_1h[-period - 1:-1], bars_1h[-period:]):
        tr = max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        )
        trs.append(tr)
    atr = sum(trs) / period          # seed = simple mean of first window
    return atr


def _cap_ms(path: list[dict]) -> int:
    return path[0]["open_time"] + int(MAX_HOLD_H * 3600 * 1000)


def simulate_chandelier(
    path: list[dict], direction: str, entry_price: float, atr: float,
    *, mult: float = CHANDELIER_MULT, fill: str = "pessimistic",
) -> tuple[float, int, bool]:
    """Trailing chandelier on a 5m path. Returns (exit_price, exit_open_time, hit_cap).

    LONG:  stop = running_peak  − mult*ATR, exit when bar low  <= stop.
    SHORT: stop = running_trough + mult*ATR, exit when bar high >= stop.
    Stop is monotone (LONG: non-decreasing). `fill` decides intra-bar order when
    both the favorable extreme (updating the stop) and the adverse extreme (crossing
    it) live in the same bar."""
    cap_ms = _cap_ms(path)
    long = direction == "LONG"
    peak = entry_price                       # running favorable extreme
    stop = entry_price - mult * atr if long else entry_price + mult * atr
    for bar in path:
        if bar["open_time"] > cap_ms:
            break
        hi, lo = bar["high"], bar["low"]
        fav = hi if long else lo             # favorable extreme this bar
        adv = lo if long else hi             # adverse extreme this bar
        new_peak = max(peak, fav) if long else min(peak, fav)
        new_stop = (new_peak - mult * atr) if long else (new_peak + mult * atr)
        if fill == "optimistic":
            # favorable first: stop ratchets, THEN test adverse against new stop
            peak, stop = new_peak, (max(stop, new_stop) if long else min(stop, new_stop))
            crossed = adv <= stop if long else adv >= stop
            if crossed:
                return stop, bar["open_time"], False
        else:
            # pessimistic: adverse first, tested against the PRIOR bar's stop
            crossed = adv <= stop if long else adv >= stop
            if crossed:
                return stop, bar["open_time"], False
            peak, stop = new_peak, (max(stop, new_stop) if long else min(stop, new_stop))
    # never stopped within data/cap -> exit at last available bar within cap
    last = max((b for b in path if b["open_time"] <= cap_ms), key=lambda b: b["open_time"])
    return last["close"], last["open_time"], True
