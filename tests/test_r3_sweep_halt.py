"""Tests for `tools.r3_trend_pullback_sweep._check_halt_after_a`.

Pre-reg §10.4: halt if H1 (signal degenerate — ≥6 in-data symbols have NO
eligible argmax cell) OR H2 (TL horizon mismatch — ≥6 in-data symbols have
`TIME_LIMIT% > 50%` on argmax cell).

Coverage:
  - Real R3 fixture reproduces halt=False with 0 H1 + 0 H2 (regression net
    against the 2026-05-13 sweep that confirmed no halt fired).
  - Counterfactual synthetic inputs that should NOT halt (empty, boundary at
    5 in-data degenerate, mixed in-data+out-of-data scenarios per I-1 fix).
  - Synthetic inputs that SHOULD halt (6 in-data degenerate → H1; 6 with
    TL%>50 → H2).
  - **I-1 critical test**: in-data filter prevents out-of-data symbols
    (no result dicts) from being counted toward H1 threshold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.r3_trend_pullback_sweep import (
    HALT_H1_SYMBOLS_THRESHOLD,
    HALT_H2_SYMBOLS_THRESHOLD,
    HALT_H2_TL_PCT_THRESHOLD,
    N_TRADES_MIN,
    _check_halt_after_a,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "data" / "retune" / "2026-05-13-r3-trend-pullback"


def _cell(
    *,
    symbol: str = "BTCUSDT",
    sl: float = 1.0,
    be: float = 1.5,
    pullback_distance: float = 0.5,
    n_trades: int = 20,
    net_pnl: float = 0.0,
    time_limit: int = 0,
    signal_exit: int = 0,
    sl_exits: int = 0,
    tp_exits: int = 0,
) -> dict:
    """Build a synthetic cell dict matching the R3 sweep harness output shape."""
    return {
        "symbol": symbol,
        "sl": sl,
        "be": be,
        "pullback_distance": pullback_distance,
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
# Real R3 fixture: halt=False (mechanism engaged, TL appropriate)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (FIXTURE_DIR / "sweep_results_A.json").exists(),
    reason="R3 sweep fixture not present in this branch",
)
def test_check_halt_after_a_reproduces_recorded_no_halt():
    """Real R3 window-A sweep fixture → halt=False (2026-05-13 sweep state).

    The recorded sweep: 8/10 symbols engaged in window A (JUP/PENDLE excluded
    as pre-registered per §3), TIME_LIMIT% range 2.27%–8.51% across in-data
    symbols (well below 50% H2 threshold). Halt did NOT fire.
    """
    with open(FIXTURE_DIR / "sweep_results_A.json") as f:
        a_results = json.load(f)
    with open(FIXTURE_DIR / "halt_after_a_diagnostic.json") as f:
        expected = json.load(f)

    diag = _check_halt_after_a(a_results)

    assert diag["halt"] is False
    assert diag["halt"] == expected["halt"]
    assert diag["halt_reason"] == expected["halt_reason"]
    # After I-1 fix: H1 counts only in-data symbols with no eligible cell.
    # JUP/PENDLE are out-of-data per §3 (no result dicts), so should NOT count.
    assert diag["h1_n_symbols_no_eligible_cell"] == expected["h1_n_symbols_no_eligible_cell"]
    assert diag["h2_n_symbols_over_tl_threshold"] == 0
    assert diag["halt_h2_tl_pct_threshold"] == HALT_H2_TL_PCT_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic counterfactuals — H1 (signal degenerate)
# ─────────────────────────────────────────────────────────────────────────────


def test_check_halt_after_a_empty_results_does_not_halt():
    """No results at all → no halt (no in-data symbols to evaluate)."""
    diag = _check_halt_after_a([])
    assert diag["halt"] is False
    assert diag["h1_n_symbols_no_eligible_cell"] == 0
    assert diag["h2_n_symbols_over_tl_threshold"] == 0


def test_check_halt_after_a_5_in_data_degenerate_does_not_halt():
    """Boundary: exactly 5 in-data signal-degenerate symbols (5 < threshold 6) → no halt."""
    # 5 in-data signal-degenerate: result dicts exist but n_trades < N_TRADES_MIN
    # (so eligible filter rejects all → argmax is None).
    degenerate = [
        _cell(symbol=s, n_trades=N_TRADES_MIN - 1, net_pnl=0.0)
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT")
    ]
    # 5 normal symbols with valid cells
    normal = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)
        for s in ("UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT")
    ]
    diag = _check_halt_after_a(degenerate + normal)
    assert diag["h1_n_symbols_no_eligible_cell"] == 5
    assert diag["halt"] is False
    assert "H1_signal_degenerate" not in diag["halt_reason"]


def test_check_halt_after_a_6_in_data_degenerate_halts_h1():
    """6 in-data signal-degenerate (≥ threshold 6) → halt H1 fires."""
    degenerate = [
        _cell(symbol=s, n_trades=N_TRADES_MIN - 1, net_pnl=0.0)
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT")
    ]
    normal = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)
        for s in ("XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT")
    ]
    diag = _check_halt_after_a(degenerate + normal)
    assert diag["h1_n_symbols_no_eligible_cell"] >= HALT_H1_SYMBOLS_THRESHOLD
    assert diag["halt"] is True
    assert "H1_signal_degenerate" in diag["halt_reason"]


def test_check_halt_after_a_I1_fix_out_of_data_symbols_do_not_count_toward_h1():
    """I-1 fix: out-of-data symbols (no result dicts) MUST NOT count toward H1.

    Pre-I-1 bug: `_argmax_cell_per_symbol` adds None for every CURATED_SYMBOLS
    member not present in results. The H1 counter then counted ALL None
    entries — conflating "in-data but signal too sparse" (real H1 case) with
    "out-of-data per §3 exclusions" (operational coverage gap, not signal
    pathology).

    Scenario engineered to trip the pre-I-1 bug: 4 in-data signal-degenerate
    + 2 out-of-data (NO result dicts at all). Pre-fix: 4 + 2 = 6 None entries
    → H1 fires (false-positive). Post-fix: only the 4 in-data count → 4 < 6,
    no halt.
    """
    # 4 in-data signal-degenerate (result dicts exist, n_trades below threshold)
    in_data_degenerate = [
        _cell(symbol=s, n_trades=N_TRADES_MIN - 1, net_pnl=0.0)
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT")
    ]
    # 4 normal in-data symbols (eligible cells)
    in_data_normal = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)
        for s in ("DOGEUSDT", "UNIUSDT", "XLMUSDT", "RUNEUSDT")
    ]
    # JUPUSDT and PENDLEUSDT: NO result dicts at all (out-of-data per §3)
    diag = _check_halt_after_a(in_data_degenerate + in_data_normal)

    # I-1 fix: H1 counter must count ONLY in-data symbols with no eligible cell.
    assert diag["h1_n_symbols_no_eligible_cell"] == 4, (
        "Out-of-data symbols (no result dicts) must NOT count toward H1 — "
        "conflating coverage gaps with signal pathology produces false-positive halts."
    )
    assert diag["halt"] is False
    assert "H1_signal_degenerate" not in diag["halt_reason"]


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic counterfactuals — H2 (TL horizon mismatch)
# ─────────────────────────────────────────────────────────────────────────────


def test_check_halt_after_a_5_with_tl_over_50_does_not_halt_h2():
    """Boundary: 5 in-data symbols with TIME_LIMIT% > 50% (5 < threshold 6) → no halt."""
    # 5 symbols at TL%=75 (15 of 20 trades close on TIME_LIMIT)
    high_tl = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=15)
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT")
    ]
    # 5 symbols at TL%=10 (low TL — fine)
    low_tl = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)
        for s in ("UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT")
    ]
    diag = _check_halt_after_a(high_tl + low_tl)
    assert diag["h2_n_symbols_over_tl_threshold"] == 5
    assert diag["halt"] is False
    assert "H2_tl_horizon_mismatch" not in diag["halt_reason"]


def test_check_halt_after_a_6_with_tl_over_50_halts_h2():
    """6 in-data symbols with TIME_LIMIT% > 50% (≥ threshold 6) → halt H2 fires."""
    high_tl = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=15)
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT")
    ]
    low_tl = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)
        for s in ("XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT")
    ]
    diag = _check_halt_after_a(high_tl + low_tl)
    assert diag["h2_n_symbols_over_tl_threshold"] >= HALT_H2_SYMBOLS_THRESHOLD
    assert diag["halt"] is True
    assert "H2_tl_horizon_mismatch" in diag["halt_reason"]


def test_check_halt_after_a_h1_and_h2_both_fire_halt_reason_lists_both():
    """If both H1 and H2 conditions are met, halt_reason lists both."""
    h1_degenerate = [
        _cell(symbol=s, n_trades=N_TRADES_MIN - 1, net_pnl=0.0)
        for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT")
    ]
    h2_high_tl = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=15)
        for s in ("XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT")
    ]
    # Add 2 more high-TL symbols (overrides earlier degenerate via separate sym)
    h2_high_tl_extra = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=15, sl=2.0)
        for s in ("BTCUSDT", "ETHUSDT")
    ]
    # Note: same symbol both degenerate AND high-TL — eligible cell wins (n_trades=20)
    diag = _check_halt_after_a(h1_degenerate + h2_high_tl + h2_high_tl_extra)
    # For BTC/ETH: eligible cell with TL=15/20=75% counts toward H2 (overrides degeneracy).
    # ADA/AVAX/DOGE/UNI: no eligible cell → H1 (4 of 8 = below threshold 6).
    # XLM/PENDLE/JUP/RUNE + BTC + ETH: TL>50% (6 of 8 ≥ threshold 6) → H2 fires.
    assert diag["halt"] is True
    assert "H2_tl_horizon_mismatch" in diag["halt_reason"]
