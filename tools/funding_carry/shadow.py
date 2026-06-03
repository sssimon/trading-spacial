"""Funding-carry shadow-deploy v0.1 (spec 2026-06-03).

Recomputes the FOSSIL'S OWN statistic (simulate.carry_for_symbol -> net_return_annual,
span-annualized, entry-mark funding, $/notional) over a trailing W-week window, pools
it equal-weight via evaluate.gate_a (identical bootstrap CI), and fires a pre-registered
decay-kill when the live CI-hi falls below the backtest CI-lo (0.0502) for N consecutive
non-overlapping windows. Paper-only: no positions, no orders, no holdout. The statistic
reuses carry_for_symbol verbatim (audit N1) — no new annualization formula."""
from __future__ import annotations
from . import simulate, evaluate
from .constants import DECAY_CI_LO, DECAY_KILL_N


def symbol_window_return(symbol: str, *, funding_db: str, ohlcv_db: str,
                         start_ms: int, end_ms: int) -> float:
    """net_return_annual for `symbol` over [start_ms, end_ms], computed by the fossil's
    carry_for_symbol (span-annualized). Raises ValueError on missing prices (drop upstream)."""
    funding = simulate.load_funding(funding_db, symbol, start_ms, end_ms)
    if len(funding) < 2:
        raise ValueError(f"{symbol}: <2 settlements in window")
    entry_ms, exit_ms = funding[0][0], funding[-1][0]
    rec = simulate.carry_for_symbol(
        symbol=symbol, funding=funding,
        spot_entry=simulate.spot_price_at(ohlcv_db, symbol, entry_ms),
        spot_exit=simulate.spot_price_at(ohlcv_db, symbol, exit_ms),
        perp_entry=simulate.perp_price_at(funding_db, symbol, entry_ms),
        perp_exit=simulate.perp_price_at(funding_db, symbol, exit_ms),
        liq=simulate.spot_liquidity(ohlcv_db, symbol, entry_ms))
    return rec["net_return_annual"]


def pooled_decay(symbols: list[str], *, funding_db: str, ohlcv_db: str,
                 start_ms: int, end_ms: int) -> dict:
    """Equal-weight pooled CI of net_return_annual over the window — identical to gate_a.
    Symbols with <2 settlements / missing prices are dropped loud (not poisoned)."""
    annual, dropped = [], []
    for s in symbols:
        try:
            annual.append(symbol_window_return(
                s, funding_db=funding_db, ohlcv_db=ohlcv_db,
                start_ms=start_ms, end_ms=end_ms))
        except ValueError:
            dropped.append(s)
    out = evaluate.gate_a(annual) if annual else {
        "mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "loo_min_mean": 0.0, "pass_a": False, "n": 0}
    out["dropped"] = dropped
    return out


_HEADLINE = 0.0633        # backtest gate_a mean — top of the thin band


def decay_state(*, ci_lo: float, ci_hi: float, weeks_below: int) -> dict:
    """State machine over the live CI vs the in-sample threshold (spec §6).

    REFUTED   : ci_hi < DECAY_CI_LO for DECAY_KILL_N consecutive non-overlapping windows.
    THIN      : CI overlaps [DECAY_CI_LO, headline] — compressing, not dead.
    ALIVE     : CI sits at/above the band.

    `weeks_below` is the prior consecutive count; this call updates it. A window whose
    ci_hi recovers to/above the threshold RESETS the counter (consecutive, not cumulative)."""
    below = ci_hi < DECAY_CI_LO
    new_count = (weeks_below + 1) if below else 0
    if new_count >= DECAY_KILL_N:
        state = "REFUTED"
    elif ci_hi >= _HEADLINE:
        state = "ALIVE"
    elif ci_hi >= DECAY_CI_LO:
        state = "THIN"            # CI overlaps [DECAY_CI_LO, headline] — compressing, not dead
    else:
        state = "THIN"            # below once but not yet N — still compressing, not dead
    return {"decay_state": state, "weeks_below": new_count}
