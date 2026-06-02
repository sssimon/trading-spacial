"""Realistic transaction cost model for backtests (A.0.2 #277; v2 epic #338 Phase 0 #340).

Provides tier-based slippage + bid-ask spread + fee + funding-rate components.
Designed so backtest.py can compute per-trade cost_bps deterministically without
depending on per-symbol orderbook history.

## Model versions

**v1 — linear in participation rate** (epic #277, legacy):

    slippage_bps = base_bps + size_factor * (notional / liquidity_per_min)

v1 explodes super-proportionally for high-participation trades. Documented
failure mode: DOGE -$30,489 single-trade case (R3 forensic, audit H8 #323).

**v2 — sqrt-participation Almgren-Chriss** (epic #338 Phase 0 #340, default):

    slippage_bps = base_bps + size_factor * sqrt(notional / liquidity_per_min)
    slippage_bps = min(slippage_bps, EXTREME_PARTICIPATION_CAP_BPS)

v2 is the empirically-validated baseline for crypto market impact (Donier-Bonart
2015; Tóth et al 2011). The square-root law holds across asset classes and is
the standard quantitative finance anchor. At the calibration anchor (0.1%
participation), v2 produces the same total slippage as v1 — they differ only at
non-anchor participation rates:
- At < anchor: v2 charges more (correctly — small orders aren't free)
- At > anchor: v2 charges less than v1's super-linear explosion (correctly —
  even market makers refuse to widen spreads beyond a hard practical cap)

Additionally, v2 adds:
- **Funding-rate accounting** for perp positions (epic #338 §8.5 — SHORT
  bidirectional enables perp dependency). Conservative per-tier estimate
  charged per 8h interval the position is held.
- **Extreme participation cap** (`EXTREME_PARTICIPATION_CAP_BPS = 500.0`):
  hard cap on per-fill slippage. Real execution would refuse a fill at >5%
  adverse price; cap protects backtests from residual single-trade
  catastrophes even under sqrt.

## Migration notes (v1 → v2)

- `compute_slippage_bps` extended with `model: Literal['v1', 'v2']` arg, default
  `'v2'`. Callers wanting linear behavior must pass `model='v1'` explicitly.
- `compute_trade_costs` extended with same `model` arg + new `holding_hours`
  and `enable_funding` args. Funding accounted when `enable_funding=True`
  (default) and `holding_hours > 0`.
- `TierParams` gains `funding_rate_bps_per_8h` field with default 0.0 for
  backward compat with v1 callers.
- `costs_calibration.json` v2: size_factors re-calibrated so anchor parity
  holds (size_factor_v2 = size_factor_v1 × sqrt(anchor_participation) /
  anchor_participation = size_factor_v1 / sqrt(anchor_participation)). At
  anchor=0.001, conversion factor = 1/sqrt(0.001) ≈ 31.623.

Calibration lives in `costs_calibration.json` (committed alongside this module).
Each parameter cites its source — invented numbers are not allowed (#277, #340).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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

# v2 extreme-participation cap. Slippage above this is non-physical: real
# execution would refuse a fill at >5% adverse price. Protects backtests from
# residual single-trade catastrophes even under the sqrt model.
EXTREME_PARTICIPATION_CAP_BPS = 500.0

# ── v3 two-body upper bound ─────────────────────────────────────────────────
# Exchange-published taker fee, used as the model-INDEPENDENT mandatory lower
# bound in the falsification harness (NOT read from calibration).
PUBLISHED_TAKER_FEE_BPS = 5.0

DEFAULT_Y_IMPACT = 1.5                   # top of the empirical O(1) band (type-coherent bound)
DEFAULT_TOTAL_COST_CAP_BPS = 1000.0      # total round-trip cap (re-spec; v2's 500 was per-leg)
DEFAULT_LIQUIDITY_FALLBACK_FLOOR_BPS = 100.0
DEFAULT_V_DAILY_MINUTES_PER_DAY = 1440.0


@dataclass(frozen=True)
class GlobalParams:
    """v3 calibration globals (the `global` block of costs_calibration.json)."""
    Y_impact_constant: float = DEFAULT_Y_IMPACT
    total_cost_cap_bps: float = DEFAULT_TOTAL_COST_CAP_BPS
    liquidity_fallback_floor_bps: float = DEFAULT_LIQUIDITY_FALLBACK_FLOOR_BPS
    v_daily_minutes_per_day: float = DEFAULT_V_DAILY_MINUTES_PER_DAY


_DEFAULT_GLOBAL = GlobalParams()


def compute_slippage_bps(
    *,
    order_usd: float,
    liquidity_usd_per_min: float,
    base_bps: float,
    size_factor: float,
    fallback_bps: float = _DEFAULT_LIQUIDITY_FALLBACK_BPS,
    model: Literal["v1", "v2"] = "v2",
) -> float:
    """Compute per-fill slippage in bps.

    Returns total slippage for a single fill of `order_usd` against a liquidity
    proxy of `liquidity_usd_per_min`. The proxy is meant to be a rolling
    average of (volume × price) per minute over the last ~30 days on the same
    timeframe the strategy trades on.

    Args:
        order_usd: USD notional of the fill.
        liquidity_usd_per_min: USD volume per minute (rolling 30d proxy).
        base_bps: Per-tier base slippage (always-on minimum).
        size_factor: Per-tier slope. Units depend on model — see formula.
        fallback_bps: Returned when liquidity is non-positive/non-finite.
        model: 'v1' (linear in participation) or 'v2' (sqrt Almgren-Chriss).
            Default 'v2'.

    Formula:
        - v1: bps = base + size_factor * (order/liquidity). size_factor is
          unitless slope per unit participation.
        - v2: bps = base + size_factor * sqrt(order/liquidity). size_factor
          here is approximately √31.6× larger than v1's to hit the same
          anchor at 0.1% participation. v2 result is capped at
          EXTREME_PARTICIPATION_CAP_BPS.

    Edge cases (both models):
      - liquidity_usd_per_min ≤ 0, NaN, or non-finite → fallback_bps.
        Rationale: a zero-volume bar is exceptional; entering then is closer
        to "we have no idea what fill we'd get" than "we'd get a tight fill".
        Default fallback is punitive (100 bps) so the strategy is penalized
        for picking such a bar.
    """
    if (
        liquidity_usd_per_min is None
        or not math.isfinite(liquidity_usd_per_min)
        or liquidity_usd_per_min <= 0.0
    ):
        return fallback_bps

    participation = order_usd / liquidity_usd_per_min

    if model == "v1":
        return base_bps + size_factor * participation
    elif model == "v2":
        # sqrt-participation impact. Negative participation (degenerate input)
        # treated as zero — square root of negative is meaningless here.
        if participation < 0.0:
            participation = 0.0
        slippage = base_bps + size_factor * math.sqrt(participation)
        return min(slippage, EXTREME_PARTICIPATION_CAP_BPS)  # NaN MUST stay first arg: min(nan,x)=nan, min(x,nan)=x — preserves the v3 poison signal
    else:
        raise ValueError(f"Unknown cost model {model!r}; expected 'v1' or 'v2'")


def compute_tail_bps(
    *,
    order_usd: float,
    liquidity_usd_per_min: float,
    sigma_daily_bps: float,
    Y: float,
    v_daily_minutes_per_day: float,
) -> float:
    """v3 impact tail (per fill), in bps. sqrt on the DAILY participation basis.

    tail = Y * sigma_daily_bps * sqrt(order_usd / (liquidity_usd_per_min * v_daily_minutes_per_day))

    Returns NaN when liquidity is non-positive/non-finite — the caller
    (`compute_trade_costs` v3 branch) detects NaN and applies the floor-anchored
    leg fallback. Negative participation (degenerate input) is clamped to 0.
    """
    if (
        liquidity_usd_per_min is None
        or not math.isfinite(liquidity_usd_per_min)
        or liquidity_usd_per_min <= 0.0
    ):
        return float("nan")
    if not math.isfinite(v_daily_minutes_per_day) or v_daily_minutes_per_day <= 0.0:
        return float("nan")
    v_daily = liquidity_usd_per_min * v_daily_minutes_per_day
    participation = order_usd / v_daily
    if participation < 0.0:
        participation = 0.0
    return Y * sigma_daily_bps * math.sqrt(participation)


def compute_funding_cost_bps(
    *,
    holding_hours: float,
    funding_rate_bps_per_8h: float,
    conservative: bool = True,
) -> float:
    """Compute cumulative funding cost (in bps) for a perp position held
    `holding_hours`.

    Args:
        holding_hours: How long the position is held. Funding intervals are
            8h on Binance USDT-M perps.
        funding_rate_bps_per_8h: Per-tier conservative absolute estimate of
            the funding rate per 8h interval.
        conservative: If True (default), always charge the absolute funding
            rate regardless of position direction (worst-case assumption for
            backtest validation). If False, caller must provide signed rate
            and direction — currently NOT implemented (raise NotImplementedError).

    Returns:
        Total funding cost in bps for the holding period. Zero if
        holding_hours <= 0.

    Notes:
        - Uses floor: a position held 7h pays 0 funding intervals; 8h pays 1;
          24h pays 3. Mirrors Binance's discrete funding settlement.
        - Conservative mode is appropriate for v2 baseline validation.
          Direction-aware mode is deferred to Phase 1+ when per-bar funding
          rate data is integrated.
    """
    if not conservative:
        raise NotImplementedError(
            "Direction-aware funding (non-conservative) is deferred to Phase "
            "1+; v2 baseline uses conservative absolute-rate accounting only."
        )
    if holding_hours <= 0.0:
        return 0.0
    if not math.isfinite(holding_hours):
        return 0.0
    n_intervals = math.floor(holding_hours / 8.0)
    return n_intervals * abs(funding_rate_bps_per_8h)


@dataclass(frozen=True)
class TierParams:
    """Per-tier cost parameters. Dual-shaped to carry BOTH v2 and v3 fields.

    v2 fields: base_bps, size_factor (NaN when loaded from a v3 calibration).
    v3 fields: stress_mult, sigma_daily_bps (NaN when loaded from v2).
    Shared:    half_spread_bps, fee_bps_per_side, funding_rate_bps_per_8h.

    Cross-version fields default to NaN ("poison"): a v3-loaded TierParams fed to
    the v2 slippage path yields NaN cost (loud, detectable) rather than 0.0
    (silent split-brain). Construct via from_v2_flat / from_v3_tier; the 5-arg
    positional form is preserved for legacy v2 tests.

    The funding_rate_bps_per_8h field is new in v2 (#340); defaults to 0.0 for
    backward compat with v1 callers that constructed TierParams manually.
    """
    base_bps: float
    size_factor: float
    half_spread_bps: float
    fee_bps_per_side: float
    funding_rate_bps_per_8h: float = 0.0
    stress_mult: float = float("nan")
    sigma_daily_bps: float = float("nan")

    @classmethod
    def from_v2_flat(cls, *, base_bps, size_factor, half_spread_bps,
                     fee_bps_per_side, funding_rate_bps_per_8h=0.0):
        return cls(
            base_bps=float(base_bps), size_factor=float(size_factor),
            half_spread_bps=float(half_spread_bps),
            fee_bps_per_side=float(fee_bps_per_side),
            funding_rate_bps_per_8h=float(funding_rate_bps_per_8h),
        )

    @classmethod
    def from_v3_tier(cls, *, floor: dict, impact_tail: dict):
        return cls(
            base_bps=float("nan"), size_factor=float("nan"),
            half_spread_bps=float(floor["half_spread_bps"]),
            fee_bps_per_side=float(floor["fee_bps_per_side"]),
            funding_rate_bps_per_8h=float(floor["funding_rate_bps_per_8h"]),
            stress_mult=float(floor["stress_mult"]),
            sigma_daily_bps=float(impact_tail["sigma_daily_bps"]),
        )


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
            funding_rate_bps_per_8h=float(t.get("funding_rate_bps_per_8h", 0.0)),
        )
        for name, t in raw["tiers"].items()
    }
    return Calibration(
        version=int(raw["version"]),
        model=raw["model"],
        v2_planned=raw.get("v2_planned", raw.get("v3_planned", "")),
        tiers=tiers,
        sources=dict(raw["sources"]),
        sensitivity_note=raw["sensitivity_note"],
    )


def _v3_leg_cost(order_usd, liquidity_usd_per_min, tp, g):
    """Return (leg_bps, slip_component_bps, is_fallback) for one v3 fill.

    Normal leg:   floor_leg(spread+fee, stress-scaled) + tail.
    Fallback leg: max(floor_leg, fallback_floor) when liquidity is unusable.
    slip_component_bps is the portion reported as *_slippage_bps (the tail, or
    the fallback excess above the floor leg) so the output dict still sums.
    """
    floor_leg = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side)
    tail = compute_tail_bps(
        order_usd=order_usd, liquidity_usd_per_min=liquidity_usd_per_min,
        sigma_daily_bps=tp.sigma_daily_bps, Y=g.Y_impact_constant,
        v_daily_minutes_per_day=g.v_daily_minutes_per_day,
    )
    if math.isnan(tail):
        leg = max(floor_leg, g.liquidity_fallback_floor_bps)
        return leg, max(0.0, leg - floor_leg), True
    return floor_leg + tail, tail, False


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
    enable_funding: bool = True,
    holding_hours: float = 0.0,
    model: Literal["v1", "v2", "v3"] = "v2",
    global_params: GlobalParams | None = None,
) -> dict:
    """Compute per-component cost dict for a single round-trip trade.

    Returns keys (all models): entry_slippage_bps, exit_slippage_bps,
    entry_spread_bps, exit_spread_bps, fee_bps (round-trip),
    funding_cost_bps, total_cost_bps, total_cost_usd.

    v3 ADDS: floor_bps, tail_bps, cap_hit (bool), fallback_hit (bool).
    v1/v2 do NOT have these keys.

    Notional is the position USD value at fill time. Liquidity is a 30-day
    rolling proxy of (volume × price) per minute on the bar's timeframe.

    Args:
        model: 'v1' (legacy linear slippage), 'v2' (sqrt + cap, default),
            or 'v3' (two-body floor+tail+cap). v3 uses stress-scaled spread
            + fee as a floor, an Almgren-Chriss tail on the daily participation
            basis, and a hard total round-trip cap (GlobalParams.total_cost_cap_bps).
        global_params: v3 calibration globals. Uses _DEFAULT_GLOBAL when None.
            Ignored by v1/v2.
        enable_funding: Include funding-rate cost for perp positions held
            across funding intervals. Default True (matches epic #338 §8.5
            which locked SHORT bidirectional → perp dependency).
        holding_hours: How long the position was held end-to-end. Funding
            costs accrue per 8h interval (floor). Zero by default for
            backward-compat with v1 callers.

    Backward compat: if `holding_hours=0` (default), funding_cost_bps=0
    regardless of enable_funding — preserves v1 test expectations for callers
    that don't yet supply holding_hours.
    """
    if model not in ("v1", "v2", "v3"):
        raise ValueError(f"Unknown cost model {model!r}; expected 'v1', 'v2', or 'v3'")

    if model == "v3":
        g = global_params if global_params is not None else _DEFAULT_GLOBAL
        if not (math.isfinite(tier_params.stress_mult)
                and math.isfinite(tier_params.sigma_daily_bps)):
            raise ValueError(
                "v3 cost requires finite stress_mult and sigma_daily_bps; got a "
                "v2/poisoned TierParams (construct via TierParams.from_v3_tier)")
        _, entry_slip, entry_fb = _v3_leg_cost(
            entry_notional_usd, entry_liquidity_usd_per_min, tier_params, g)
        _, exit_slip, exit_fb = _v3_leg_cost(
            exit_notional_usd, exit_liquidity_usd_per_min, tier_params, g)
        sm = tier_params.stress_mult
        entry_spread = sm * tier_params.half_spread_bps if enable_spread else 0.0
        exit_spread = sm * tier_params.half_spread_bps if enable_spread else 0.0
        fee_bps = sm * 2.0 * tier_params.fee_bps_per_side if enable_fees else 0.0
        funding_bps = (
            compute_funding_cost_bps(
                holding_hours=holding_hours,
                funding_rate_bps_per_8h=tier_params.funding_rate_bps_per_8h,
            )
            if (enable_funding and holding_hours > 0.0) else 0.0
        )
        # The liquidity fallback excess rides on the slippage component (consistent
        # with v2), so enable_slippage=False omits both tail and fallback excess —
        # the bound guarantee applies to the default all-costs-on config.
        entry_tail = entry_slip if enable_slippage else 0.0
        exit_tail = exit_slip if enable_slippage else 0.0
        fallback_hit = entry_fb or exit_fb
        floor_bps = entry_spread + exit_spread + fee_bps + funding_bps
        tail_bps = entry_tail + exit_tail
        uncapped = floor_bps + tail_bps
        cap_hit = uncapped >= g.total_cost_cap_bps
        total_cost_bps = min(uncapped, g.total_cost_cap_bps)
        avg_notional = 0.5 * (entry_notional_usd + exit_notional_usd)
        return {
            "entry_slippage_bps": entry_tail, "exit_slippage_bps": exit_tail,
            "entry_spread_bps": entry_spread, "exit_spread_bps": exit_spread,
            "fee_bps": fee_bps, "funding_cost_bps": funding_bps,
            "floor_bps": floor_bps, "tail_bps": tail_bps,
            "cap_hit": cap_hit, "fallback_hit": fallback_hit,
            "total_cost_bps": total_cost_bps,
            "total_cost_usd": total_cost_bps * avg_notional / 10_000.0,
        }

    if enable_slippage:
        entry_slip = compute_slippage_bps(
            order_usd=entry_notional_usd,
            liquidity_usd_per_min=entry_liquidity_usd_per_min,
            base_bps=tier_params.base_bps,
            size_factor=tier_params.size_factor,
            model=model,
        )
        exit_slip = compute_slippage_bps(
            order_usd=exit_notional_usd,
            liquidity_usd_per_min=exit_liquidity_usd_per_min,
            base_bps=tier_params.base_bps,
            size_factor=tier_params.size_factor,
            model=model,
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

    if enable_funding and holding_hours > 0.0:
        funding_bps = compute_funding_cost_bps(
            holding_hours=holding_hours,
            funding_rate_bps_per_8h=tier_params.funding_rate_bps_per_8h,
            conservative=True,
        )
    else:
        funding_bps = 0.0

    total_cost_bps = (
        entry_slip + exit_slip + entry_spread + exit_spread + fee_bps + funding_bps
    )
    avg_notional = 0.5 * (entry_notional_usd + exit_notional_usd)
    total_cost_usd = total_cost_bps * avg_notional / 10_000.0

    return {
        "entry_slippage_bps": entry_slip,
        "exit_slippage_bps": exit_slip,
        "entry_spread_bps": entry_spread,
        "exit_spread_bps": exit_spread,
        "fee_bps": fee_bps,
        "funding_cost_bps": funding_bps,
        "total_cost_bps": total_cost_bps,
        "total_cost_usd": total_cost_usd,
    }
