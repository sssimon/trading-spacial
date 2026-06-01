"""Read-only edge measurement: does the BASE strategy (no kill switch) have
positive edge over the pre-holdout window, BEFORE we invest in validating any
kill switch?

Rationale (2026-06-01): a de-risking breaker cannot manufacture edge. On a
negative-edge base strategy the optimal kill switch degenerates to "turn off",
so a v1-vs-v2 comparison is meaningless. Establishing the SIGN of the base
edge is therefore a precondition for the whole stress-replay program. This
reuses the tested, holdout-safe Pass 1 (generate_base_stream) and reconstructs
each symbol's standalone compounding equity from its pnl_usd sequence.

Read-only on OHLCV. Touches no DB, no production state. Holdout cutoff enforced
inside generate_base_stream (NON-NEGOTIABLE #3).
"""
from __future__ import annotations

from tools.ks_stress_replay.base_stream import generate_base_stream

INITIAL_CAPITAL = 10000.0  # matches backtest.INITIAL_CAPITAL


def _equity_curve(trades: list[dict]) -> list[float]:
    """Reconstruct standalone compounding equity from the pnl_usd sequence.

    Each trade's pnl_usd was already computed against the running capital at
    that point inside simulate_strategy, so a running sum from INITIAL_CAPITAL
    faithfully reproduces the standalone equity curve.
    """
    eq = [INITIAL_CAPITAL]
    for tr in trades:
        eq.append(eq[-1] + float(tr.get("pnl_usd", 0.0)))
    return eq


def _max_drawdown(curve: list[float]) -> float:
    """Max drawdown as a negative fraction of the running peak (e.g. -0.90)."""
    peak = curve[0]
    worst = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < worst:
                worst = dd
    return worst


def _win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for tr in trades if float(tr.get("pnl_usd", 0.0)) > 0)
    return wins / len(trades)


def measure() -> dict:
    stream = generate_base_stream()
    rows = []
    for sym, trades in stream.items():
        curve = _equity_curve(trades)
        final = curve[-1]
        bankrupt = any(tr.get("exit_reason") == "BANKRUPT" for tr in trades)
        rows.append({
            "symbol": sym,
            "n_trades": len(trades),
            "final_equity": final,
            "return_pct": (final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0,
            "max_dd_pct": _max_drawdown(curve) * 100.0,
            "win_rate_pct": _win_rate(trades) * 100.0,
            "total_pnl_usd": final - INITIAL_CAPITAL,
            "bankrupt": bankrupt,
        })
    return {"rows": rows}


def _print_table(result: dict) -> None:
    rows = result["rows"]
    hdr = (
        f"{'symbol':<10} {'trades':>7} {'final$':>12} {'return%':>10} "
        f"{'maxDD%':>9} {'win%':>7} {'bankrupt':>9}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['symbol']:<10} {r['n_trades']:>7d} {r['final_equity']:>12.2f} "
            f"{r['return_pct']:>10.1f} {r['max_dd_pct']:>9.1f} "
            f"{r['win_rate_pct']:>7.1f} {str(r['bankrupt']):>9}"
        )
    print("-" * len(hdr))

    # Naive equal-weight portfolio read (NOT the shared-account model — that is
    # Approach B's job). This is only a directional aggregate of the SIGN.
    n = len(rows)
    if n:
        avg_ret = sum(r["return_pct"] for r in rows) / n
        n_winners = sum(1 for r in rows if r["return_pct"] > 0)
        n_bankrupt = sum(1 for r in rows if r["bankrupt"])
        print(
            f"avg return per symbol: {avg_ret:.1f}%  |  "
            f"winners: {n_winners}/{n}  |  bankrupt: {n_bankrupt}/{n}"
        )
        print(
            "NOTE: pre-#223/#224 absolutes are inflated (Non-Negotiable #5). "
            "Read the SIGN/direction, not the magnitudes."
        )


if __name__ == "__main__":
    _print_table(measure())
