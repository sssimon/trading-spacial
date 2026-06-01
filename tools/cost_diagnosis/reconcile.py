"""Pre-registered reconcile thresholds + branch verdict (spec §3).

A correction reconciles iff:
  1. no winning trade (pnl_usd > 0) has cost_pct > observed_move_pct, AND
  2. per-tier median round-trip cost <= band (major/mid 30 bps, small 50 bps).
Branch RE-ANCHOR if any non-baseline correction reconciles, else REBUILD.
Thresholds are fixed here and do not move after seeing results.
"""
from __future__ import annotations

from statistics import median

TIER_BAND_BPS = {"major": 30.0, "mid": 30.0, "small": 50.0}


def reconcile(per_trade: list[dict], corrections: list) -> tuple[str, list[str], dict]:
    if not per_trade:
        raise ValueError("reconcile: no trades to evaluate (empty per_trade)")
    results: dict = {}
    for name, *_ in corrections:
        winners_exceeded = [
            t for t in per_trade
            if t["pnl_usd"] > 0 and (t["costs"][name] / 100.0) > t["observed_move_pct"]
        ]
        tier_medians: dict = {}
        ok_band = True
        # Band is checked only for tiers actually present in the data; a tier
        # with no trades cannot fail its band.
        for tier, band in TIER_BAND_BPS.items():
            vals = [t["costs"][name] for t in per_trade if t["tier"] == tier]
            if vals:
                m = median(vals)
                tier_medians[tier] = m
                if m > band:
                    ok_band = False
        reconciles = (len(winners_exceeded) == 0) and ok_band
        results[name] = {
            "winners_exceeded": len(winners_exceeded),
            "tier_medians": tier_medians,
            "reconciles": reconciles,
        }
    winning = [
        name for name, *_ in corrections
        if name != "baseline" and results[name]["reconciles"]
    ]
    branch = "RE-ANCHOR" if winning else "REBUILD"
    return branch, winning, results
