"""Gross PnL, liquidity proxy, v3 recost, paired stats, verdict.

Reuses backtest_costs.compute_trade_costs UNMODIFIED. The active calibration
(costs_calibration.json) is v3 (version 3); we pass model='v3' explicitly and the
calibration's own globals."""
from __future__ import annotations
import numpy as np
from backtest_costs import load_calibration, tier_for_symbol, compute_trade_costs
from .constants import BOOTSTRAP_N, BOOTSTRAP_SEED

_CAL = load_calibration()                    # active = v3 (costs_calibration.json)
assert _CAL.active_model == "v3", f"expected v3 active calibration, got {_CAL.active_model}"


def gross_pnl(*, qty: float, entry: float, exit: float, direction: str) -> float:
    return qty * (exit - entry) if direction == "LONG" else qty * (entry - exit)


def liquidity_series(bars_1h: list[dict]) -> list[tuple[int, float]]:
    """(open_time, rolling-mean USD/min) per 1h bar; matches backtest.py:669-674
    (window 720, min_periods 120). NaN until warmed."""
    times = [b["open_time"] for b in bars_1h]
    upm = [b["close"] * b["volume"] / 60.0 for b in bars_1h]
    out = []
    for i in range(len(upm)):
        lo = max(0, i - 719)
        window = upm[lo:i + 1]
        liq = float(np.mean(window)) if len(window) >= 120 else float("nan")
        out.append((times[i], liq))
    return out


def liquidity_at(series: list[tuple[int, float]], ts_ms: int) -> float:
    """Last rolling-liquidity value at or before ts_ms (backtest.py _liquidity_at)."""
    val = float("nan")
    for t, liq in series:
        if t <= ts_ms:
            val = liq
        else:
            break
    return val


def recost_v3(
    *, symbol: str, entry_notional: float, exit_notional: float,
    entry_liq: float, exit_liq: float, holding_hours: float,
) -> float:
    """Round-trip v3 cost in USD for one trade. Liquidity NaN -> v3 fallback floor."""
    tp = _CAL.tiers[tier_for_symbol(symbol)]
    d = compute_trade_costs(
        entry_notional_usd=entry_notional, exit_notional_usd=exit_notional,
        entry_liquidity_usd_per_min=entry_liq, exit_liquidity_usd_per_min=exit_liq,
        tier_params=tp, holding_hours=holding_hours, model="v3",
        global_params=_CAL.global_,
    )
    return float(d["total_cost_usd"])


def bootstrap_ci(deltas, n: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED):
    """Percentile 95% CI of the paired mean. Returns (lo, mean, hi)."""
    arr = np.asarray(deltas, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n, len(arr)))
    means = arr[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(arr.mean()), float(np.percentile(means, 97.5))


def leave_one_out(deltas, ids):
    """Mean with each trade dropped once. Returns [{dropped_id, mean}], sorted by mean."""
    arr = np.asarray(deltas, dtype=float)
    out = []
    for i, tid in enumerate(ids):
        rest = np.delete(arr, i)
        out.append({"dropped_id": int(tid), "mean": float(rest.mean())})
    return sorted(out, key=lambda d: d["mean"])


def _ci_excludes_zero_positive(deltas) -> bool:
    lo, mean, hi = bootstrap_ci(deltas)
    return lo > 0.0 and mean > 0.0


def verdict(*, pess_deltas, opt_deltas, ids) -> dict:
    """Apply the frozen KILL (spec §6 + §5). Returns full diagnostic dict.

    PASS requires the CI to exclude zero (positive) under BOTH fill conventions AND
    to survive dropping the top influencer (spec §6: both-fills gate is the conservative
    reading — a PASS that holds only under the favorable optimistic fill does not count).
    FAIL requires both fills non-passing with AGREEING sign; if the two fills disagree
    in sign the result is INDETERMINATE (granularity-bound). Any other mix (e.g. one
    fill passes, the other does not, or LOO fails) is INDETERMINATE, never a silent PASS."""
    pess = bootstrap_ci(pess_deltas)
    opt = bootstrap_ci(opt_deltas)
    pess_pass = _ci_excludes_zero_positive(pess_deltas)
    opt_pass = _ci_excludes_zero_positive(opt_deltas)

    loo = leave_one_out(pess_deltas, ids)
    worst_drop_id = loo[0]["dropped_id"]
    i = ids.index(worst_drop_id)
    survives_loo = _ci_excludes_zero_positive(
        [d for j, d in enumerate(pess_deltas) if j != i])

    if pess_pass and opt_pass and survives_loo:
        v = "PASS"
    # both fills fail the positive-CI test: clean FAIL if their means agree in sign,
    # else the verdict flips with the fill convention -> INDETERMINATE (granularity).
    elif not pess_pass and not opt_pass:
        v = "INDETERMINATE" if (pess[1] > 0) != (opt[1] > 0) else "FAIL"
    else:
        v = "INDETERMINATE"
    return {
        "verdict": v,
        "pessimistic_ci": {"lo": pess[0], "mean": pess[1], "hi": pess[2], "excludes_zero": pess_pass},
        "optimistic_ci": {"lo": opt[0], "mean": opt[1], "hi": opt[2], "excludes_zero": opt_pass},
        "loo_survives_top_influencer": survives_loo,
        "loo_top_influencer_id": worst_drop_id,
        "loo": loo,
    }
