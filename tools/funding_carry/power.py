"""One-shot power / calibration layer (spec §3b, §6, §11 REV 5).

Provides two deterministic functions that are run ONCE against the fossil and whose outputs
are hand-frozen into constants.py before any live run:

  fossil_rate_band  — CI bootstrap of gate_a over per-symbol mean(fundingRate) across the
                      full fossil window.  Returns annualized values (×INTERVALS_PER_YEAR).
                      Defines R_FOSSIL_LO / R_FOSSIL_HI.

  cost_floor        — Median per-symbol roundtrip cost / notional / H_ref_years + margin.
                      Returns an annualized-return floor.  Defines T_FLOOR.

Also retains the original min_window_weeks helper (now re-derived on the intra-window rate
sigma from the fossil rather than the old cross-symbol long-run sigma).

Unit convention (REV 5): BOTH anchors are returned in ANNUALIZED units so they can be
compared directly to a live rate reported as mean(rate)×1095/year.  The factor 1095
cancels in rate-vs-rate comparisons but is applied consistently so the thresholds stored
in constants.py are human-readable (percent-per-year scale).

Run ONCE; do NOT import this module in the production shadow path."""
from __future__ import annotations
import json
import math
import statistics

# INTERVALS_PER_YEAR: 3 settlements/day × 365 days/year = 1095 (8-h cadence).
_INTERVALS_PER_YEAR = 1095


def min_window_weeks(*, per_symbol_settlements_per_week: int, n_symbols: int,
                     sigma_annual: float, target_half_band: float) -> int:
    """Smallest integer W (weeks) such that SE(pooled annualized return) <= target_half_band.

    Pooled equal-weight over n_symbols of a per-symbol mean over (W * settlements/week)
    observations: SE ~ sigma_annual / sqrt(n_symbols * W * settlements_per_week)."""
    per_week = max(1, per_symbol_settlements_per_week)
    denom_per_w = n_symbols * per_week
    # Solve sigma / sqrt(denom_per_w * W) <= band  ->  W >= (sigma/band)^2 / denom_per_w
    w_real = (sigma_annual / target_half_band) ** 2 / denom_per_w
    return max(1, math.ceil(w_real))


def fossil_rate_band(funding_db: str, symbols: list[str],
                     start_ms: int, end_ms: int) -> dict:
    """Bootstrap CI of the pooled mean funding rate over the full fossil window.

    For each symbol, computes mean(fundingRate) over [start_ms, end_ms] via
    simulate.load_funding, then calls evaluate.gate_a on the list of per-symbol mean rates.
    Symbols with fewer than 1 settlement are dropped loud (logged to stdout).

    Units: ANNUALIZED (mean per-settlement rate × INTERVALS_PER_YEAR = 1095).
    R_FOSSIL_LO = returned ci_lo, R_FOSSIL_HI = returned ci_hi.

    Returns the full gate_a dict (mean, ci_lo, ci_hi, loo_min_mean, pass_a, n) with all
    values in annualized units.  'pass_a' reflects whether the fossil's own band excludes
    zero — informational only, not used in the decay-kill path."""
    from .simulate import load_funding
    from .evaluate import gate_a

    mean_rates: list[float] = []
    for sym in symbols:
        rows = load_funding(funding_db, sym, start_ms, end_ms)
        if len(rows) < 1:
            print(f"[fossil_rate_band] {sym}: 0 settlements in window — dropped")
            continue
        sym_mean = sum(r for _, r in rows) / len(rows)
        mean_rates.append(sym_mean * _INTERVALS_PER_YEAR)  # annualize

    result = gate_a(mean_rates)
    return result


def cost_floor(per_symbol_json_path: str, *, notional: float,
               h_ref_years: float, margin: float) -> float:
    """Annualized cost floor: median(cost_v3 / notional) / h_ref_years + margin.

    Loads the per_symbol.json list (list of dicts, each with 'cost_v3').  For each symbol
    computes cost_v3 / notional (a dimensionless fraction of notional per round-trip).
    Takes the MEDIAN across symbols — NOT the mean: PENDLE's cost is ~40× others, making
    the mean misleading.  Divides by h_ref_years to annualize (the cost is amortized over
    the declared holding horizon, NOT the observation window — these are orthogonal).
    Adds margin (a conservative buffer; spec §11 sets MARGIN=0.0).

    Units: ANNUALIZED return fraction (same unit as fossil_rate_band outputs), so the
    comparison T_FLOOR vs R_FOSSIL_LO/HI is directly meaningful."""
    with open(per_symbol_json_path, encoding="utf-8") as fh:
        records = json.load(fh)

    per_sym_costs = [rec["cost_v3"] / notional for rec in records]
    med = statistics.median(per_sym_costs)
    return med / h_ref_years + margin
