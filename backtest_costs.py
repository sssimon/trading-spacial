"""Realistic transaction cost model for backtests (A.0.2, #277).

Provides tier-based slippage + bid-ask spread + fee components. Designed so
backtest.py can compute per-trade cost_bps deterministically without depending
on per-symbol orderbook history.

v1 model is **linear in participation rate**:

    slippage_bps = base_bps + size_factor * (order_usd / liquidity_usd_per_min)

This deliberately overpenalizes small orders and underpenalizes large ones
relative to the empirically-better Almgren-Chriss `sqrt(participation)` baseline.
v2 should migrate to sqrt; this is documented here so it does not get forgotten.

Calibration lives in `costs_calibration.json` (committed alongside this module).
Each parameter cites its source — invented numbers are not allowed (#277).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# Curated symbols are organized into three liquidity tiers. The split is the
# same as the spec's recommended grouping (#277 §2): majors trade tightest,
# mid-cap symbols see moderate slippage/spread, small-cap symbols see the
# widest. Membership is closed over the 10 curated symbols
# (DEFAULT_SYMBOLS in btc_scanner.py); any other symbol must explicitly extend
# this mapping before being usable in a cost-aware backtest.
_TIER_BY_SYMBOL: dict[str, str] = {
    # Majors
    "BTCUSDT": "major",
    "ETHUSDT": "major",
    # Mid-cap
    "ADAUSDT": "mid",
    "AVAXUSDT": "mid",
    "DOGEUSDT": "mid",
    "UNIUSDT": "mid",
    "XLMUSDT": "mid",
    # Small-cap
    "PENDLEUSDT": "small",
    "JUPUSDT": "small",
    "RUNEUSDT": "small",
}


class UnknownSymbolError(ValueError):
    """Raised when a symbol is not in the curated tier mapping."""


def tier_for_symbol(symbol: str) -> str:
    try:
        return _TIER_BY_SYMBOL[symbol]
    except KeyError as e:
        raise UnknownSymbolError(
            f"{symbol!r} is not in the curated tier mapping. Extend "
            f"_TIER_BY_SYMBOL in backtest_costs.py with a tier + justify the "
            f"calibration source before using cost-aware backtests for it."
        ) from e


# Punitive default — entering a position when liquidity is unobservable should
# not be a free lunch in the backtest. 1% (100 bps) leans toward "do not trust
# this trade" without forcing a hard skip; callers can lower it if they have a
# better fallback (e.g. tier-default participation × tier-default base_bps).
_DEFAULT_LIQUIDITY_FALLBACK_BPS = 100.0


def compute_slippage_bps(
    *,
    order_usd: float,
    liquidity_usd_per_min: float,
    base_bps: float,
    size_factor: float,
    fallback_bps: float = _DEFAULT_LIQUIDITY_FALLBACK_BPS,
) -> float:
    """v1 linear slippage model.

    Returns total slippage in bps for a single fill of `order_usd` against a
    liquidity proxy of `liquidity_usd_per_min`. The proxy is meant to be a
    rolling average of (volume × price) per minute over the last ~30 days
    on the same timeframe the strategy trades on.

    Edge cases:
      - liquidity_usd_per_min ≤ 0, NaN, or non-finite → fallback_bps.
        Rationale: a zero-volume bar is exceptional; entering then is closer
        to "we have no idea what fill we'd get" than "we'd get a tight fill".
        Default fallback is punitive (100 bps) so the strategy is penalized
        for picking such a bar.

    NOT modeled in v1 (deferred to v2):
      - sqrt-participation impact (Almgren-Chriss) — overpenalizes small,
        underpenalizes large relative to linear.
      - Permanent vs temporary impact decomposition.
      - Order book depth heterogeneity within a single bar.
    """
    if (
        liquidity_usd_per_min is None
        or not math.isfinite(liquidity_usd_per_min)
        or liquidity_usd_per_min <= 0.0
    ):
        return fallback_bps
    return base_bps + size_factor * (order_usd / liquidity_usd_per_min)


@dataclass(frozen=True)
class TierParams:
    """Per-tier cost parameters loaded from costs_calibration.json."""
    base_bps: float
    size_factor: float
    half_spread_bps: float
    fee_bps_per_side: float


@dataclass(frozen=True)
class Calibration:
    """Top-level calibration object."""
    version: int
    model: str
    v2_planned: str
    tiers: dict[str, TierParams]
    sources: dict[str, str]
    sensitivity_note: str


_CALIBRATION_PATH = Path(__file__).resolve().parent / "costs_calibration.json"


def load_calibration(path: str | Path | None = None) -> Calibration:
    """Load and validate costs_calibration.json. Raises FileNotFoundError if
    missing — refuses to silently fall back to hardcoded defaults."""
    p = Path(path) if path is not None else _CALIBRATION_PATH
    with p.open() as f:
        raw = json.load(f)

    tiers = {
        name: TierParams(
            base_bps=float(t["base_bps"]),
            size_factor=float(t["size_factor"]),
            half_spread_bps=float(t["half_spread_bps"]),
            fee_bps_per_side=float(t["fee_bps_per_side"]),
        )
        for name, t in raw["tiers"].items()
    }
    return Calibration(
        version=int(raw["version"]),
        model=raw["model"],
        v2_planned=raw["v2_planned"],
        tiers=tiers,
        sources=dict(raw["sources"]),
        sensitivity_note=raw["sensitivity_note"],
    )


def compute_trade_costs(
    *,
    entry_notional_usd: float,
    exit_notional_usd: float,
    entry_liquidity_usd_per_min: float,
    exit_liquidity_usd_per_min: float,
    tier_params: TierParams,
    enable_slippage: bool = True,
    enable_spread: bool = True,
    enable_fees: bool = True,
) -> dict:
    """Compute per-component cost dict for a single round-trip trade.

    Returns keys: entry_slippage_bps, exit_slippage_bps, entry_spread_bps,
    exit_spread_bps, fee_bps (round-trip), total_cost_bps, total_cost_usd.

    Notional is the position USD value at fill time. Liquidity is a 30-day
    rolling proxy of (volume × price) per minute on the bar's timeframe.
    """
    if enable_slippage:
        entry_slip = compute_slippage_bps(
            order_usd=entry_notional_usd,
            liquidity_usd_per_min=entry_liquidity_usd_per_min,
            base_bps=tier_params.base_bps,
            size_factor=tier_params.size_factor,
        )
        exit_slip = compute_slippage_bps(
            order_usd=exit_notional_usd,
            liquidity_usd_per_min=exit_liquidity_usd_per_min,
            base_bps=tier_params.base_bps,
            size_factor=tier_params.size_factor,
        )
    else:
        entry_slip = 0.0
        exit_slip = 0.0

    if enable_spread:
        entry_spread = tier_params.half_spread_bps
        exit_spread = tier_params.half_spread_bps
    else:
        entry_spread = 0.0
        exit_spread = 0.0

    if enable_fees:
        fee_bps = 2.0 * tier_params.fee_bps_per_side
    else:
        fee_bps = 0.0

    total_cost_bps = entry_slip + exit_slip + entry_spread + exit_spread + fee_bps
    avg_notional = 0.5 * (entry_notional_usd + exit_notional_usd)
    total_cost_usd = total_cost_bps * avg_notional / 10_000.0

    return {
        "entry_slippage_bps": entry_slip,
        "exit_slippage_bps": exit_slip,
        "entry_spread_bps": entry_spread,
        "exit_spread_bps": exit_spread,
        "fee_bps": fee_bps,
        "total_cost_bps": total_cost_bps,
        "total_cost_usd": total_cost_usd,
    }
