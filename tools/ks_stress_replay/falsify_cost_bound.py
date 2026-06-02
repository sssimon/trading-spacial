"""Falsify the v3 cost bound against live realized P&L (read-only).

R1: live data is a sanity CEILING, never a fit target. The bound is falsified
ONLY when it underestimates an indisputable cost, or when it INVERTS a per-symbol
price-winner into a backtest loser. NN#3: reads prod signals.db (mode=ro) + 2026
OHLCV only; NEVER pre-2025-04-29 frames; NEVER imports holdout access.
"""
from __future__ import annotations

from backtest_costs import (
    load_calibration, tier_for_symbol, compute_trade_costs, PUBLISHED_TAKER_FEE_BPS,
)

EXPECTED_MIN = 20
NOISE_BAND_USD = 5.0
MANDATORY_LOWER_BOUND_BPS = 2 * PUBLISHED_TAKER_FEE_BPS   # 10.0 RT bps, exchange-published


class InsufficientDataError(RuntimeError):
    pass


def _v3_cost_usd(symbol: str, size_usd: float, liq: float, *, force_cost_bps=None) -> float:
    """Return round-trip cost in USD for one position.

    ``force_cost_bps`` is an escape hatch for unit tests that want to inject
    an artificially high cost without touching the calibration file.
    """
    if force_cost_bps is not None:
        return force_cost_bps * size_usd / 10_000.0
    cal = load_calibration()
    tp = cal.tiers[tier_for_symbol(symbol)]
    c = compute_trade_costs(
        entry_notional_usd=size_usd, exit_notional_usd=size_usd,
        entry_liquidity_usd_per_min=liq, exit_liquidity_usd_per_min=liq,
        tier_params=tp, model=cal.active_model, global_params=cal.global_,
    )
    return c["total_cost_usd"]


def score_positions(rows: list[dict], *, force_cost_bps=None) -> list[dict]:
    """Attach v3 cost to each closed-short row. Returns list of scored dicts."""
    scored = []
    for r in rows:
        cost_usd = _v3_cost_usd(
            r["symbol"], r["size_usd"], r["liquidity_per_min"],
            force_cost_bps=force_cost_bps,
        )
        cost_bps = cost_usd / r["size_usd"] * 10_000.0
        scored.append({**r, "v3_cost_usd": cost_usd, "v3_cost_bps": cost_bps})
    return scored


def assert_no_sign_inversion(scored: list[dict]) -> None:
    """Raise if the v3 cost model inverts any per-symbol winner or violates the fee floor.

    Two checks are performed in order:

    1. **Precondition guard** — aborts with ``InsufficientDataError`` when the
       sample is too small to be meaningful (< ``EXPECTED_MIN`` rows).

    2. **Mandatory fee-floor tripwire** — asserts that every scored position has
       a v3 cost >= ``MANDATORY_LOWER_BOUND_BPS`` (= 2 × published taker fee,
       10.0 RT bps).  This is an *external* reference, independent of the model's
       own internal floor, so it cannot be silently lowered by a calibration edit.

    3. **No per-symbol sign inversion** — for each symbol whose aggregate gross
       P&L exceeds ``NOISE_BAND_USD`` (to filter statistical noise), asserts that
       the sign of net P&L (gross minus v3 cost) matches the sign of gross P&L.
    """
    n = len(scored)
    if n < EXPECTED_MIN:
        raise InsufficientDataError(
            f"falsification needs >={EXPECTED_MIN} closed shorts, got {n}"
        )

    # Secondary tripwire: model never sits below the external mandatory fee floor.
    for s in scored:
        assert s["v3_cost_bps"] >= MANDATORY_LOWER_BOUND_BPS, (
            f"{s['symbol']}: v3 cost {s['v3_cost_bps']:.2f}bps below mandatory "
            f"{MANDATORY_LOWER_BOUND_BPS}bps (fee mis-config)"
        )

    # Primary test: no per-symbol sign inversion.
    by_symbol: dict[str, list] = {}
    for s in scored:
        by_symbol.setdefault(s["symbol"], []).append(s)

    for symbol, ss in by_symbol.items():
        gross = sum(s["pnl_usd"] for s in ss)
        if abs(gross) <= NOISE_BAND_USD:
            continue  # too noisy to distinguish real edge from rounding
        net = sum(s["pnl_usd"] - s["v3_cost_usd"] for s in ss)
        assert (gross > 0) == (net > 0), (
            f"{symbol}: v3 cost causes sign inversion "
            f"(gross {gross:.2f} -> net {net:.2f})"
        )
