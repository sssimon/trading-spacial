"""Volatility-targeting position sizing for regime-allocation strategy
(epic #338 Phase 1).

Replaces R-multiple sizing (which produced bankruptcy via path-dependency
under the LRC architecture — see audit #323 H4) with portfolio-vol-targeting.

## Mechanics (§4.3 + §8 of epic spec)

For a symbol with realized volatility `σ_30d` and target volatility share
`target_vol_per_symbol`:

    position_size_USD = capital_USD × target_vol_per_symbol / σ_30d

Where:
- `target_vol_per_symbol = portfolio_vol_target / n_active_symbols`
- `portfolio_vol_target` = 30% annualized (locked §8.3)
- `σ_30d` = annualized close-to-close realized vol over 30 daily returns
- `n_active_symbols` = count of symbols with non-zero ensemble direction
  (caller supplies this — module is symbol-agnostic)

Then signed position direction (LONG/SHORT) is applied:

    signed_position_USD = position_size_USD × direction

Hard caps:
- Per-symbol position ≤ `max_position_pct` of capital (default 20%)
- Position size ≥ `min_position_usd` (default $50, Binance min);
  below threshold returns 0 (skip the trade)
- Portfolio leverage: `sum(|positions|) ≤ max_leverage × capital`
  (default 2.0× per §8.6). Excess scales proportionally across all symbols.

## Volatility estimation

Default: **close-to-close daily** annualized via `sqrt(365)` (crypto is 24/7,
no weekend adjustment). Window: 30 daily bars.

Alternative: Yang-Zhang vol (`strategy/vol.py:annualized_vol_yang_zhang`)
is available but NOT used here — close-to-close is the academic standard for
trend-following sizing (Zarattini 2025 uses it implicitly). Switching to
Yang-Zhang would be a pre-Phase 3 decision documented separately.

## NOT in this module

- Donchian ensemble signal (see `strategy/donchian_ensemble.py`)
- Integration into evaluate_signal / scanner (Phase 1B)
- Live execution (deferred to Phase 6 after holdout validation)
- Funding cost accounting (Phase 1C wires to `backtest_costs.py`)
"""
from __future__ import annotations

import math

import pandas as pd

# Crypto trades 365 days/year — no weekend adjustment.
TRADING_DAYS_PER_YEAR = 365

# Defaults locked per §8.3 / §8.6 of epic #338 spec.
DEFAULT_PORTFOLIO_VOL_TARGET = 0.30  # 30% annualized
DEFAULT_MAX_LEVERAGE = 2.0
DEFAULT_MAX_POSITION_PCT = 0.20  # 20% per symbol hard cap
DEFAULT_MIN_POSITION_USD = 50.0  # Binance min order

# Vol estimation window
DEFAULT_VOL_WINDOW_DAYS = 30


def compute_realized_vol_annualized(
    daily_returns: pd.Series,
    *,
    window: int = DEFAULT_VOL_WINDOW_DAYS,
) -> float:
    """Compute annualized close-to-close realized volatility from daily returns.

    Args:
        daily_returns: pd.Series of simple or log daily returns (the difference
            between simple/log returns is negligible at daily horizon for
            crypto-typical magnitudes). Index ignored — only last `window`
            values are used.
        window: number of daily returns to use. Default 30.

    Returns:
        Annualized volatility (e.g., 0.50 = 50% annualized). NaN if fewer than
        `window` returns are available or std is undefined.
    """
    if window < 2:
        raise ValueError(f"window must be ≥ 2; got {window}")
    if len(daily_returns) < window:
        return float("nan")
    sample = daily_returns.iloc[-window:].dropna()
    if len(sample) < 2:
        return float("nan")
    std_daily = sample.std(ddof=1)
    if not math.isfinite(std_daily) or std_daily <= 0.0:
        return float("nan")
    return float(std_daily * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_position_size(
    *,
    capital_usd: float,
    direction: int,
    target_vol_per_symbol: float,
    realized_vol_annualized: float,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    min_position_usd: float = DEFAULT_MIN_POSITION_USD,
) -> float:
    """Compute the signed USD position size for a single symbol.

    Args:
        capital_usd: total portfolio capital in USD.
        direction: -1 (SHORT), 0 (flat), or +1 (LONG). 0 returns 0.
        target_vol_per_symbol: portfolio vol target divided by number of
            active symbols (caller-supplied). E.g., 0.30 / 6 = 0.05 for a
            6-symbol active book.
        realized_vol_annualized: σ_30d annualized for the symbol. NaN or
            non-positive → returns 0.
        max_position_pct: hard cap on per-symbol notional as fraction of
            capital. Default 20%.
        min_position_usd: minimum notional below which we skip the trade.
            Default $50 (Binance min). Returns 0 if computed size falls below.

    Returns:
        Signed position size in USD. Positive = LONG, negative = SHORT,
        zero = flat or below caps. Sign matches `direction`.
    """
    if direction not in (-1, 0, 1):
        raise ValueError(f"direction must be in {{-1, 0, 1}}; got {direction!r}")
    if direction == 0:
        return 0.0
    if capital_usd <= 0.0 or not math.isfinite(capital_usd):
        return 0.0
    if target_vol_per_symbol <= 0.0 or not math.isfinite(target_vol_per_symbol):
        return 0.0
    if (
        realized_vol_annualized is None
        or not math.isfinite(realized_vol_annualized)
        or realized_vol_annualized <= 0.0
    ):
        return 0.0

    # Vol-targeted raw notional
    raw_notional = capital_usd * target_vol_per_symbol / realized_vol_annualized

    # Hard cap: per-symbol fraction of capital
    if max_position_pct > 0:
        cap_usd = capital_usd * max_position_pct
        raw_notional = min(raw_notional, cap_usd)

    # Min order threshold
    if raw_notional < min_position_usd:
        return 0.0

    return raw_notional * direction


def apply_leverage_cap(
    positions: dict[str, float],
    *,
    capital_usd: float,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
) -> dict[str, float]:
    """Apply portfolio leverage cap by proportional scaling.

    If `sum(|positions|) > max_leverage × capital_usd`, scale every position
    by the same factor so the constraint binds exactly. Otherwise unchanged.

    Args:
        positions: mapping {symbol → signed position USD}.
        capital_usd: total capital in USD. Must be positive.
        max_leverage: ceiling on sum(|positions|) / capital_usd. Default 2.0
            per §8.6 of epic spec.

    Returns:
        Same shape mapping with positions possibly scaled down. Signs
        preserved.

    Raises:
        ValueError: capital_usd ≤ 0 or non-finite.
        ValueError: max_leverage ≤ 0.
    """
    if capital_usd <= 0.0 or not math.isfinite(capital_usd):
        raise ValueError(f"capital_usd must be positive finite; got {capital_usd!r}")
    if max_leverage <= 0.0 or not math.isfinite(max_leverage):
        raise ValueError(f"max_leverage must be positive finite; got {max_leverage!r}")

    if not positions:
        return {}

    total_notional = sum(abs(p) for p in positions.values())
    cap_usd = max_leverage * capital_usd

    if total_notional <= cap_usd or total_notional == 0.0:
        return dict(positions)  # already within cap (or all zero)

    scale = cap_usd / total_notional
    return {sym: p * scale for sym, p in positions.items()}


def compute_portfolio_positions(
    *,
    capital_usd: float,
    directions: dict[str, int],
    realized_vols: dict[str, float],
    portfolio_vol_target: float = DEFAULT_PORTFOLIO_VOL_TARGET,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    min_position_usd: float = DEFAULT_MIN_POSITION_USD,
) -> dict[str, float]:
    """End-to-end portfolio sizing: per-symbol vol-targeted + leverage cap.

    Convenience orchestrator that:
    1. Counts active symbols (direction != 0)
    2. Computes target_vol_per_symbol = portfolio_vol_target / n_active
    3. Calls compute_position_size per symbol
    4. Applies leverage cap on the resulting book

    Args:
        capital_usd: total portfolio capital.
        directions: mapping {symbol → -1|0|+1} from ensemble.
        realized_vols: mapping {symbol → σ_30d annualized}.
        portfolio_vol_target: total target vol (default 30%).
        max_leverage: leverage cap (default 2x).
        max_position_pct: per-symbol hard cap fraction (default 20%).
        min_position_usd: minimum notional (default $50).

    Returns:
        dict {symbol → signed USD position}, leverage-capped. Symbols with
        direction=0 are excluded from the result (not in dict).
    """
    active_symbols = [sym for sym, d in directions.items() if d != 0]
    if not active_symbols:
        return {}

    target_per_symbol = portfolio_vol_target / len(active_symbols)

    positions: dict[str, float] = {}
    for sym in active_symbols:
        size = compute_position_size(
            capital_usd=capital_usd,
            direction=directions[sym],
            target_vol_per_symbol=target_per_symbol,
            realized_vol_annualized=realized_vols.get(sym, float("nan")),
            max_position_pct=max_position_pct,
            min_position_usd=min_position_usd,
        )
        if size != 0.0:
            positions[sym] = size

    return apply_leverage_cap(
        positions, capital_usd=capital_usd, max_leverage=max_leverage
    )
