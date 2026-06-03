"""Funding-negative KILL rule simulator (spec §3-§4). IN/OUT state machine over the
per-settlement funding stream, charging one v3 round-trip per IN-tramo (churn is real).
Pure functions — `rt_cost` (round-trip transaction cost) is passed in, computed by the
caller via simulate.recost_four_legs."""
from __future__ import annotations


def _accrue(funding, marks, units, lo, hi):
    """Sum funding over settlements [lo, hi) (a single IN-tramo)."""
    return sum(funding[i][1] * marks[i] * units for i in range(lo, hi))


def simulate_no_kill(funding, *, marks, units, rt_cost) -> dict:
    """Continuous hold: one tramo over all settlements, one round-trip cost."""
    n = len(funding)
    assert len(marks) == n, f"marks/funding length mismatch: {len(marks)} vs {n}"
    eq, run = [], 0.0
    for i in range(n):
        run += funding[i][1] * marks[i] * units
        eq.append(run)
    churn = rt_cost if n else 0.0          # no cost on an empty series (symmetry w/ with_kill)
    return {"net": run - churn, "n_tramos": 1 if n else 0, "n_kills": 0,
            "churn_cost": churn, "equity_curve": eq}


def simulate_with_kill(funding, *, marks, units, rt_cost, k) -> dict:
    """IN/OUT machine. Exit after `k` consecutive rate<0 settlements; re-enter on the
    first rate>=0. Each IN-tramo charges `rt_cost` (one round trip). Equity curve is the
    cumulative time-ordered P&L (funding only; basis handled by the caller per tramo).

    Settlement convention (matches spec §4 pseudocode): funding is collected only for
    settlements where the position is IN *at the charge*. The kill tick (the Kth negative)
    IS collected then we exit; the re-entry tick (the first rate>=0 while OUT) is NOT
    collected — we re-enter after observing the positive and collect from the next
    settlement on. Tiny, symmetric, and intentional."""
    n = len(funding)
    assert len(marks) == n, f"marks/funding length mismatch: {len(marks)} vs {n}"
    eq, run = [], 0.0
    state, neg = "IN", 0
    n_tramos = 1 if n else 0          # start IN
    n_kills = 0
    for i in range(n):
        rate = funding[i][1]
        if state == "IN":
            run += rate * marks[i] * units
            neg = neg + 1 if rate < 0 else 0
            if neg >= k:
                state, neg = "OUT", 0
                n_kills += 1
        elif rate >= 0:               # OUT and funding back positive -> re-enter (not collected)
            state = "IN"
            n_tramos += 1
        eq.append(run)
    churn = rt_cost * n_tramos
    return {"net": run - churn, "n_tramos": n_tramos, "n_kills": n_kills,
            "churn_cost": churn, "equity_curve": eq}
