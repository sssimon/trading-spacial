"""Gate A (carry net>0) + Gate B (tail) + verdict for funding carry."""
from __future__ import annotations
import numpy as np
from .constants import (BOOTSTRAP_N, BOOTSTRAP_SEED, SHOCK_FUNDING_PER_8H,
                        SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY, NOTIONAL)


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
