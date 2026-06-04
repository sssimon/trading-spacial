"""Funding-carry execution-realism v0.2 — Unidad 2 (spec 2026-06-03 REV 2.1 §4).

DESCRIPTIVE ONLY — NO VERDICT, by design: sigma_T x NOTIONAL is a 2nd-moment
quantity, and a risk bound is only comparable against a risk budget (2nd moment),
which does not exist yet. Renaming a drag to a bound does not co-locate the types
(Axiom-0 / Richter). This module measures and tabulates; interpretation waits.

One-shot, paper-only. Approximations declared in spec §6: sqrt(T) sub-minute
extrapolation (§6.2), T is an ASSUMED window (§6.3), mark-basis != executable
basis (§6.4). Fail-LOUD: a short klines series raises FetchFailed (never sigma
over 25h labeled 30d)."""
from __future__ import annotations
import json
import math
import os
import statistics
from datetime import datetime, timezone
from .constants import (SHADOW_SYMBOLS, NOTIONAL, LEG_LAG_DAYS, LEG_LAG_T_SWEEP,
                        SPOT_KLINES_1M, FAPI_MARK_KLINES,
                        EXEC_REALISM_OUTPUT_DIR, EXEC_REALISM_VERSION)


def basis_sigma_1m(spot_closes: list[tuple[int, float]],
                   perp_closes: list[tuple[int, float]]) -> float:
    """std of per-minute changes of the relative basis (perp-spot)/spot, computed
    over timestamps present in BOTH series (inner join). Returns 0.0 when fewer
    than 2 deltas exist (degenerate, not an error — the table will show it)."""
    spot = dict(spot_closes)
    perp = dict(perp_closes)
    ts = sorted(set(spot) & set(perp))
    basis = [(perp[t] - spot[t]) / spot[t] for t in ts]
    deltas = [b2 - b1 for b1, b2 in zip(basis, basis[1:])]
    if len(deltas) < 2:
        return 0.0
    return statistics.stdev(deltas)


def scale_to_window(sigma_1m: float, t_seconds: float) -> float:
    """Random-walk scaling: sigma_T = sigma_1m * sqrt(T/60). Declared approximation
    (spec §6.2) — a description, not a measurement."""
    return sigma_1m * math.sqrt(t_seconds / 60.0)
