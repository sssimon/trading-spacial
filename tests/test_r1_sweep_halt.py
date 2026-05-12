"""Tests for `tools.r1_signal_exit_sweep._check_halt_after_a`.

Pre-reg §10: halt if `TIME_LIMIT% > 35%` on argmax cell for `> 6` symbols.

Coverage:
  - Real R1 fixture reproduces halt=True with 7 symbols breaching (regression net
    against the 2026-05-12 halt event).
  - Counterfactual synthetic inputs that should NOT halt (empty, all-low-TL,
    boundary at exactly 6 breaches).
  - Synthetic input that SHOULD halt (7 breaches).
  - No-data symbols (n_trades < N_TRADES_MIN) contribute None to per_symbol_tl_pct
    and do not count as breaches.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.r1_signal_exit_sweep import (
    HALT_SYMBOLS_THRESHOLD,
    HALT_TL_PCT_THRESHOLD,
    N_TRADES_MIN,
    _check_halt_after_a,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "data" / "retune" / "2026-05-12-r1-dynamic-exit"


def _cell(
    *,
    symbol: str = "BTCUSDT",
    sl: float = 1.0,
    be: float = 1.5,
    lrc_thr: float = 50.0,
    n_trades: int = 20,
    net_pnl: float = 0.0,
    time_limit: int = 0,
    signal_exit: int = 0,
    sl_exits: int = 0,
    tp_exits: int = 0,
) -> dict:
    """Build a synthetic cell dict matching the sweep harness output shape."""
    return {
        "symbol": symbol,
        "sl": sl,
        "be": be,
        "lrc_thr": lrc_thr,
        "n_trades": n_trades,
        "net_pnl": net_pnl,
        "profit_factor": 1.0,
        "exit_reasons": {
            "TIME_LIMIT": time_limit,
            "SIGNAL_EXIT": signal_exit,
            "SL": sl_exits,
            "TP": tp_exits,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Real R1 fixture: halt=True with 7 symbols breaching
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (FIXTURE_DIR / "sweep_results_A.json").exists(),
    reason="R1 sweep fixture not present in this branch",
)
def test_check_halt_after_a_reproduces_recorded_halt_event():
    """Real R1 window-A sweep fixture → halt=True with 7-symbol breach (2026-05-12 event)."""
    with open(FIXTURE_DIR / "sweep_results_A.json") as f:
        a_results = json.load(f)
    with open(FIXTURE_DIR / "halt_after_a_diagnostic.json") as f:
        expected = json.load(f)

    diag = _check_halt_after_a(a_results)

    assert diag["halt"] is True
    assert diag["halt"] == expected["halt"]
    assert diag["n_symbols_over_threshold"] == 7
    assert diag["n_symbols_over_threshold"] == expected["n_symbols_over_threshold"]
    assert diag["symbols_over_threshold"] == expected["symbols_over_threshold"]
    assert diag["halt_tl_pct_threshold"] == HALT_TL_PCT_THRESHOLD
    assert diag["halt_symbols_threshold"] == HALT_SYMBOLS_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic counterfactuals
# ─────────────────────────────────────────────────────────────────────────────


def test_check_halt_after_a_empty_results_does_not_halt():
    diag = _check_halt_after_a([])
    assert diag["halt"] is False
    assert diag["n_symbols_over_threshold"] == 0
    assert diag["symbols_over_threshold"] == []


def test_check_halt_after_a_all_symbols_below_threshold_does_not_halt():
    """Every symbol's argmax has TL%=25 (< 35) → no breach, halt=False."""
    results = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=5)
        for s in (
            "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT",
            "DOGEUSDT", "UNIUSDT", "XLMUSDT", "RUNEUSDT",
        )
    ]
    diag = _check_halt_after_a(results)
    assert diag["halt"] is False
    assert diag["n_symbols_over_threshold"] == 0


def test_check_halt_after_a_exactly_six_breaches_does_not_halt():
    """Halt requires STRICTLY MORE than 6 breaches — exactly 6 stays under the bar."""
    six_breach = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=15)  # TL%=75
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT")
    ]
    rest = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)  # TL%=10
        for s in ("XLMUSDT", "RUNEUSDT")
    ]
    diag = _check_halt_after_a(six_breach + rest)
    assert diag["n_symbols_over_threshold"] == HALT_SYMBOLS_THRESHOLD
    assert diag["halt"] is False


def test_check_halt_after_a_seven_breaches_halts():
    seven_breach = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=15)  # TL%=75
        for s in (
            "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT",
            "DOGEUSDT", "UNIUSDT", "XLMUSDT",
        )
    ]
    diag = _check_halt_after_a(seven_breach)
    assert diag["n_symbols_over_threshold"] == 7
    assert diag["halt"] is True


def test_check_halt_after_a_no_data_symbols_contribute_none_tl_pct():
    """Symbols with no eligible cell (all n_trades < N_TRADES_MIN) → None, not breach."""
    results = [
        # Real breach
        _cell(symbol="BTCUSDT", n_trades=20, net_pnl=-100.0, time_limit=15),
        # No-data: every cell below the trade-count floor
        _cell(symbol="JUPUSDT", n_trades=N_TRADES_MIN - 1, net_pnl=0.0, time_limit=0),
        _cell(symbol="PENDLEUSDT", n_trades=N_TRADES_MIN - 1, net_pnl=0.0, time_limit=0),
    ]
    diag = _check_halt_after_a(results)
    assert diag["per_symbol_tl_pct"]["JUPUSDT"] is None
    assert diag["per_symbol_tl_pct"]["PENDLEUSDT"] is None
    assert diag["per_symbol_tl_pct"]["BTCUSDT"] == 75.0
    assert diag["n_symbols_over_threshold"] == 1
    assert diag["halt"] is False
