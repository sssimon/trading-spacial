"""Donchian channel ensemble for regime-allocation strategy (epic #338 Phase 1).

Implements multi-timeframe Donchian breakout signals aggregated by equal-weight
vote, per the parameters locked in §8 of the epic spec (Zarattini, Pagani &
Barbon 2025 "Catching Crypto Trends" replication).

## Parameters (LOCKED §8 of epic spec)

- Lookbacks: 5, 10, 20, 30, 60, 90, 150, 250, 360 days (9 total)
- Aggregation: equal-weight vote
- Update frequency: daily (caller-orchestrated; module is timeframe-agnostic)

## Mechanics

Each Donchian channel at lookback N produces a sticky direction signal:

- **LONG** when latest close > N-day prior upper bound (close broke above
  the prior N-day high range, excluding today)
- **SHORT** when latest close < N-day prior lower bound
- Otherwise: **maintain previous direction** (classic Donchian sticky-breakout
  rule; once you're long, you stay long until you break the lower bound)

The ensemble votes equally across all lookbacks:
- `vote = sum(directions)` ∈ [-N_lookbacks, +N_lookbacks]
- `position_direction = sign(vote)` ∈ {-1, 0, +1}
- `confidence = abs(vote) / n_lookbacks` ∈ [0, 1]

## NOT in this module

- Position sizing (see `strategy/vol_targeting.py`)
- Integration into evaluate_signal / scanner (Phase 1B)
- Live execution (deferred to Phase 6 after holdout validation)
- Cost-aware execution (Phase 1C wires to `backtest_costs.py`)
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

# Locked per §8.4 of epic #338 spec — Zarattini exact 9.
ZARATTINI_LOOKBACKS: tuple[int, ...] = (5, 10, 20, 30, 60, 90, 150, 250, 360)


def compute_donchian_channel(
    *,
    highs: pd.Series,
    lows: pd.Series,
    lookback_days: int,
) -> dict[str, float]:
    """Compute the current Donchian channel from the latest bar.

    The channel for lookback N spans the highest high and lowest low of the
    last N bars INCLUDING the latest. The "prior" channel (excluding today)
    is what direction signals use — see `compute_donchian_direction_history`.

    Args:
        highs: daily high prices, indexed by date.
        lows: daily low prices, indexed by date.
        lookback_days: N. Must be ≥ 2.

    Returns:
        dict with keys: upper, lower, mid. NaN if insufficient history.

    Raises:
        ValueError: lookback_days < 2.
    """
    if lookback_days < 2:
        raise ValueError(f"lookback_days must be ≥ 2; got {lookback_days}")

    if len(highs) < lookback_days or len(lows) < lookback_days:
        return {"upper": float("nan"), "lower": float("nan"), "mid": float("nan")}

    window_high = highs.iloc[-lookback_days:]
    window_low = lows.iloc[-lookback_days:]
    upper = float(window_high.max())
    lower = float(window_low.min())
    mid = 0.5 * (upper + lower)
    return {"upper": upper, "lower": lower, "mid": mid}


def compute_donchian_direction_history(
    *,
    closes: pd.Series,
    highs: pd.Series,
    lows: pd.Series,
    lookback_days: int,
) -> pd.Series:
    """Compute sticky breakout direction at each bar over the full history.

    For each bar t (after warmup), the direction is determined by:
    - If close[t] > max(highs[t-lookback_days : t])  (PRIOR N-day high) → LONG (+1)
    - Elif close[t] < min(lows[t-lookback_days : t])  (PRIOR N-day low)  → SHORT (-1)
    - Else: maintain previous direction (sticky)

    The first lookback_days bars are warmup (direction = 0, flat).

    Args:
        closes: daily close prices.
        highs: daily high prices.
        lows: daily low prices.
        lookback_days: N (must be ≥ 2).

    Returns:
        pd.Series of int in {-1, 0, +1} with the same index as `closes`.

    Raises:
        ValueError: lookback_days < 2.
        ValueError: input series have mismatched indexes.
    """
    if lookback_days < 2:
        raise ValueError(f"lookback_days must be ≥ 2; got {lookback_days}")
    if not (closes.index.equals(highs.index) and closes.index.equals(lows.index)):
        raise ValueError(
            "closes/highs/lows must share the same index — provide daily-aligned series"
        )

    n = len(closes)
    direction = np.zeros(n, dtype=np.int8)
    if n <= lookback_days:
        # Insufficient history for any breakout signal → all flat
        return pd.Series(direction, index=closes.index, dtype=np.int8)

    # Rolling prior N-day extremes (shifted by 1 to exclude today's bar)
    # Use min_periods=lookback_days so we get NaN before warmup completes
    prior_high = highs.rolling(window=lookback_days, min_periods=lookback_days).max().shift(1)
    prior_low = lows.rolling(window=lookback_days, min_periods=lookback_days).min().shift(1)

    closes_arr = closes.to_numpy()
    prior_high_arr = prior_high.to_numpy()
    prior_low_arr = prior_low.to_numpy()

    last_dir = 0
    for i in range(n):
        if np.isnan(prior_high_arr[i]) or np.isnan(prior_low_arr[i]):
            direction[i] = 0  # warmup
            continue
        if closes_arr[i] > prior_high_arr[i]:
            last_dir = 1
        elif closes_arr[i] < prior_low_arr[i]:
            last_dir = -1
        # else: sticky, keep last_dir
        direction[i] = last_dir

    return pd.Series(direction, index=closes.index, dtype=np.int8)


def aggregate_ensemble(
    directions: dict[int, int],
    *,
    method: Literal["equal_weight_vote"] = "equal_weight_vote",
) -> dict[str, float | int]:
    """Aggregate per-lookback directions into a single ensemble decision.

    Locked aggregation method per §8.1 of epic spec: equal-weight vote.

    Args:
        directions: mapping {lookback_days → direction ∈ {-1, 0, +1}}.
            Empty dict raises ValueError.
        method: must be 'equal_weight_vote'. Other methods raise
            NotImplementedError (reserved for future migrations).

    Returns:
        dict with keys:
            - direction: int in {-1, 0, +1} (sign of vote)
            - vote: int in [-n_lookbacks, +n_lookbacks]
            - confidence: float in [0, 1] = abs(vote) / n_lookbacks
            - n_long: int, count of LONG signals
            - n_short: int, count of SHORT signals
            - n_flat: int, count of flat signals
            - n_lookbacks: int

    Raises:
        ValueError: directions is empty.
        ValueError: any direction not in {-1, 0, +1}.
        NotImplementedError: method != 'equal_weight_vote'.
    """
    if method != "equal_weight_vote":
        raise NotImplementedError(
            f"Aggregation method {method!r} not implemented. Only "
            f"'equal_weight_vote' is supported per §8.1 of epic #338 spec."
        )
    if not directions:
        raise ValueError("directions dict is empty; need at least one lookback")
    for lookback, d in directions.items():
        if d not in (-1, 0, 1):
            raise ValueError(
                f"Direction for lookback {lookback} must be in {{-1, 0, 1}}; got {d!r}"
            )

    vote = sum(directions.values())
    n_lookbacks = len(directions)

    if vote > 0:
        direction = 1
    elif vote < 0:
        direction = -1
    else:
        direction = 0

    n_long = sum(1 for d in directions.values() if d > 0)
    n_short = sum(1 for d in directions.values() if d < 0)
    n_flat = n_lookbacks - n_long - n_short

    return {
        "direction": direction,
        "vote": vote,
        "confidence": abs(vote) / n_lookbacks,
        "n_long": n_long,
        "n_short": n_short,
        "n_flat": n_flat,
        "n_lookbacks": n_lookbacks,
    }


def compute_ensemble_history(
    *,
    closes: pd.Series,
    highs: pd.Series,
    lows: pd.Series,
    lookbacks: tuple[int, ...] = ZARATTINI_LOOKBACKS,
) -> pd.DataFrame:
    """Run the full Donchian ensemble over a price history.

    For each bar t, computes per-lookback direction and the aggregated
    ensemble decision. Convenience wrapper that combines
    `compute_donchian_direction_history` over all lookbacks +
    `aggregate_ensemble` row-wise.

    Args:
        closes: daily close prices.
        highs: daily high prices.
        lows: daily low prices.
        lookbacks: tuple of lookback periods. Defaults to ZARATTINI_LOOKBACKS.

    Returns:
        DataFrame indexed by date with columns:
            - dir_{N} for each N in lookbacks (per-lookback direction)
            - vote, direction, confidence, n_long, n_short, n_flat
    """
    if not lookbacks:
        raise ValueError("lookbacks tuple is empty")

    per_lookback = {}
    for n in lookbacks:
        per_lookback[n] = compute_donchian_direction_history(
            closes=closes, highs=highs, lows=lows, lookback_days=n
        )

    # Build directions matrix
    df = pd.DataFrame(
        {f"dir_{n}": series for n, series in per_lookback.items()},
        index=closes.index,
    )

    # Vote column (sum of per-lookback dirs)
    df["vote"] = df[[f"dir_{n}" for n in lookbacks]].sum(axis=1)
    n_lookbacks = len(lookbacks)
    df["direction"] = df["vote"].apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0)).astype(np.int8)
    df["confidence"] = df["vote"].abs() / n_lookbacks
    df["n_long"] = df[[f"dir_{n}" for n in lookbacks]].apply(lambda r: (r > 0).sum(), axis=1)
    df["n_short"] = df[[f"dir_{n}" for n in lookbacks]].apply(lambda r: (r < 0).sum(), axis=1)
    df["n_flat"] = n_lookbacks - df["n_long"] - df["n_short"]

    return df
