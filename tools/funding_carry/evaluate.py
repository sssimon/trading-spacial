"""Gate A (carry net>0) + Gate B (tail) + verdict for funding carry."""
from __future__ import annotations
import numpy as np
from .constants import (BOOTSTRAP_N, BOOTSTRAP_SEED, SHOCK_FUNDING_PER_8H,
                        SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY, N_SHOCKS)


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


from .constants import N_SHOCKS, SHOCK_FUNDING_PER_8H, SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY


def kill_vs_nokill(with_kill: list[float], no_kill: list[float]) -> dict:
    """Paired pooled delta (with_kill - no_kill) net return + bootstrap CI. Does the kill
    add value net of churn? Positive mean_delta = kill helps."""
    deltas = np.asarray([w - n for w, n in zip(with_kill, no_kill)], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(deltas), size=(BOOTSTRAP_N, len(deltas)))
    means = deltas[idx].mean(axis=1)
    return {"mean_delta": float(deltas.mean()),
            "ci_lo": float(np.percentile(means, 2.5)),
            "ci_hi": float(np.percentile(means, 97.5)),
            "kill_adds_value": bool(np.percentile(means, 2.5) > 0.0)}


def inject_shocks(equity_curve: list[float], *, n_shocks: int, shock_loss: float) -> float:
    """Final equity after subtracting `n_shocks` one-time losses of `shock_loss` each
    (the synthetic out-of-sample tail; the kill caps each shock's bleed). Conservative:
    applies the full loss n_shocks times to the realized final equity."""
    final = equity_curve[-1] if equity_curve else 0.0
    return final - n_shocks * shock_loss


def gate_tail(*, with_kill_net_pooled: float, post_shock_net_pooled: float) -> dict:
    """G1 = with-kill net survives in-sample (>0); G2 = survives N_SHOCKS (>=0). PASS = both."""
    g1 = bool(with_kill_net_pooled > 0.0)
    g2 = bool(post_shock_net_pooled >= 0.0)
    return {"pass_g1": g1, "pass_g2": g2, "verdict": "PASS" if (g1 and g2) else "FAIL"}
