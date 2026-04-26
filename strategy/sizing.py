"""Pure sizing logic — composes score tier x kill-switch health tier (#186 A4)."""
from __future__ import annotations

from typing import Any


RISK_PER_TRADE = 0.01
SCORE_PREMIUM = 4  # threshold for 1.5x
SCORE_STANDARD = 2  # threshold for 1.0x (else 0.5x)


def _score_multiplier(score: int) -> float:
    if score >= SCORE_PREMIUM:
        return 1.5
    if score >= SCORE_STANDARD:
        return 1.0
    return 0.5


def _health_multiplier(health_tier: str, cfg: dict[str, Any]) -> float:
    """Returns multiplier based on kill switch tier.

    PAUSED -> 0 (no trade). REDUCED -> reduce_size_factor.
    PROBATION -> v2.probation.size_factor (falls back to reduce_size_factor).
    NORMAL/ALERT -> 1.0.
    """
    if health_tier == "PAUSED":
        return 0.0
    if health_tier == "REDUCED":
        ks_cfg = cfg.get("kill_switch", {})
        return float(ks_cfg.get("reduce_size_factor", 0.5))
    if health_tier == "PROBATION":
        # Mirrors apply_reduce_factor's lookup order in health.py.
        ks_cfg = cfg.get("kill_switch", {})
        v2_cfg = (ks_cfg.get("v2") or {})
        prob_cfg = (v2_cfg.get("probation") or {})
        return float(prob_cfg.get(
            "size_factor",
            ks_cfg.get("reduce_size_factor", 0.5),
        ))
    return 1.0


def compute_size(
    score: int,
    health_tier: str,
    capital: float,
    cfg: dict[str, Any],
) -> float:
    """Return risk-adjusted size for a trade.

    Composition: capital x RISK_PER_TRADE x score_mult x health_mult.
    """
    return capital * RISK_PER_TRADE * _score_multiplier(score) * _health_multiplier(health_tier, cfg)
