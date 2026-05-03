"""generate_report — disabled-symbol guard for cooldown resolution."""
from __future__ import annotations


def _minimal_metrics() -> dict:
    """Smallest dict shape that satisfies generate_report's f-string interpolation."""
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0,
        "profit_factor": 0.0, "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
        "avg_duration_hours": 0.0, "avg_win_duration_hours": 0.0,
        "avg_loss_duration_hours": 0.0,
        "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        "trades_per_month": 0.0,
        "best_trade_pct": 0.0, "worst_trade_pct": 0.0, "median_trade_pct": 0.0,
        "final_equity": 10000.0, "score_tiers": {},
    }


def _minimal_regimes() -> dict:
    """Shape required by generate_report's regime table — dict-of-dicts."""
    empty = {"trades": 0, "win_rate": 0.0, "avg_pnl_pct": 0.0, "total_pnl_usd": 0.0}
    return {"bull": dict(empty), "bear": dict(empty), "sideways": dict(empty)}


def test_generate_report_handles_disabled_symbol():
    """generate_report must not crash when symbol_overrides[sym] = False.

    Regression net for C-R2-1: the cooldown-resolution helper was added in PR3
    R1 without a `False`-value guard, leaving an unmasked crash if any caller
    invokes generate_report with a disabled symbol entry. Today the upstream
    `if not trades: return` short-circuits this path, but any refactor that
    drops the early return would unmask the bug.
    """
    from backtest import generate_report

    so = {"BTCUSDT": False}
    rep = generate_report(
        "BTCUSDT",
        _minimal_metrics(),
        _minimal_regimes(),
        trades=[],
        symbol_overrides=so,
    )
    # Template resolved (no AttributeError, no f-string placeholders left).
    assert isinstance(rep, str) and len(rep) > 0
    assert "{COOLDOWN_H}h" not in rep
    assert "{_eff_cd" not in rep


def test_generate_report_handles_dict_override():
    """Sanity: with a normal dict override the resolution still works."""
    from backtest import generate_report

    so = {"BTCUSDT": {"cooldown_hours": 14}}
    rep = generate_report(
        "BTCUSDT",
        _minimal_metrics(),
        _minimal_regimes(),
        trades=[],
        symbol_overrides=so,
    )
    # Effective cooldown for BTC should be 14 (the override).
    assert "14h cooldown" in rep


def test_generate_report_handles_no_overrides():
    """`symbol_overrides=None` falls back to COOLDOWN_H global."""
    from backtest import generate_report, COOLDOWN_H

    rep = generate_report(
        "BTCUSDT",
        _minimal_metrics(),
        _minimal_regimes(),
        trades=[],
        symbol_overrides=None,
    )
    assert f"{COOLDOWN_H}h cooldown" in rep
