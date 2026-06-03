"""Gate A (carry net>0) + Gate B (tail) + verdict for funding carry."""
from __future__ import annotations
import numpy as np
from .constants import (BOOTSTRAP_N, BOOTSTRAP_SEED, SHOCK_FUNDING_PER_8H,
                        SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY)


def gate_a(annual_returns: list[float]) -> dict:
    """Pooled equal-weight bootstrap CI of annualized net return + LOO. PASS_A = CI lo > 0."""
    arr = np.asarray(annual_returns, dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(arr), size=(BOOTSTRAP_N, len(arr)))
    means = arr[idx].mean(axis=1)
    ci_lo, ci_hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    loo = [float(np.delete(arr, i).mean()) for i in range(len(arr))]
    return {"mean": float(arr.mean()), "ci_lo": ci_lo, "ci_hi": ci_hi,
            "loo_min_mean": min(loo), "pass_a": bool(ci_lo > 0.0 and min(loo) > 0.0),
            "n": len(arr)}


def gate_b1(interval_pnls: list[float]) -> dict:
    """In-sample tail: max drawdown of the cumulative pooled equity + worst interval."""
    eq = np.cumsum(np.asarray(interval_pnls, dtype=float))
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return {"max_drawdown": float(dd.max()) if len(dd) else 0.0,
            "worst_interval": float(min(interval_pnls)) if interval_pnls else 0.0}


def gate_b2(mean_net_return: float) -> dict:
    """Synthetic short-vol shock (LUNA/FTX-calibrated): a SHOCK_DAYS forced-negative-funding
    episode bleeds SHOCK_DAYS*intervals*F of notional. PASS_B2 = carry survives net>=0."""
    shock_bleed = SHOCK_DAYS * SHOCK_INTERVALS_PER_DAY * SHOCK_FUNDING_PER_8H
    return {"shock_bleed": shock_bleed,
            "post_shock_return": mean_net_return - shock_bleed,
            "pass_b2": bool(mean_net_return - shock_bleed >= 0.0)}


def verdict(a: dict, b2: dict) -> dict:
    """PASS iff Gate A and Gate B2 both pass (spec §7). $-denominated, no mirage."""
    v = "PASS" if (a.get("pass_a") and b2.get("pass_b2")) else "FAIL"
    return {"verdict": v, "pass_a": bool(a.get("pass_a")), "pass_b2": bool(b2.get("pass_b2"))}
