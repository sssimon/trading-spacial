"""Unit tests for tools/r1_verdict.py — R1 verdict calculator helpers.

Pre-reg: docs/superpowers/plans/2026-05-12-r1-dynamic-exit-pre-reg.md §4

Coverage:
  - `_argmax_cell`: eligibility filter + deterministic tie-break (gap #2).
  - `_avg_pnl_per_trade` / `_time_limit_pct` / `_signal_exit_pct`: None/zero guards.
  - `_analyze_window`: per-symbol argmax + primary/secondary aggregation.
  - `_cross_window_stability`: stable / diverges / single-window flag.
  - `_classify_verdict`: pre-reg §4.2 outcomes + halt+n_windows<3 guard (gap #4).
  - `_load_json`: missing-file → None, present-file → parsed dict.
"""
from __future__ import annotations

import json

import pytest

from tools.r1_verdict import (
    ALL_SYMBOLS,
    N_TRADES_MIN,
    _analyze_window,
    _argmax_cell,
    _avg_pnl_per_trade,
    _classify_verdict,
    _cross_window_stability,
    _load_json,
    _signal_exit_pct,
    _time_limit_pct,
)


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic cell helper
# ─────────────────────────────────────────────────────────────────────────────


def _cell(
    *,
    symbol: str = "BTCUSDT",
    sl: float = 1.0,
    be: float = 1.5,
    lrc_thr: float = 50.0,
    n_trades: int = 20,
    net_pnl: float = 0.0,
    profit_factor: float = 1.0,
    time_limit: int = 0,
    signal_exit: int = 0,
    sl_exits: int = 0,
    tp_exits: int = 0,
) -> dict:
    """Build a synthetic cell dict matching tools.r1_signal_exit_sweep output shape."""
    return {
        "symbol": symbol,
        "sl": sl,
        "be": be,
        "lrc_thr": lrc_thr,
        "n_trades": n_trades,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "exit_reasons": {
            "TIME_LIMIT": time_limit,
            "SIGNAL_EXIT": signal_exit,
            "SL": sl_exits,
            "TP": tp_exits,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# _argmax_cell — eligibility + deterministic tie-break (gap #2)
# ─────────────────────────────────────────────────────────────────────────────


def test_argmax_cell_returns_none_on_empty_list():
    assert _argmax_cell([]) is None


def test_argmax_cell_returns_none_when_no_cell_meets_min_trades():
    cells = [_cell(n_trades=N_TRADES_MIN - 1, net_pnl=100.0)]
    assert _argmax_cell(cells) is None


def test_argmax_cell_picks_max_net_pnl_when_no_ties():
    cells = [
        _cell(sl=1.0, net_pnl=100.0),
        _cell(sl=2.0, net_pnl=200.0),
        _cell(sl=3.0, net_pnl=50.0),
    ]
    winner = _argmax_cell(cells)
    assert winner["net_pnl"] == 200.0
    assert winner["sl"] == 2.0


def test_argmax_cell_excludes_cells_below_min_trades():
    cells = [
        _cell(n_trades=N_TRADES_MIN, net_pnl=50.0),
        _cell(n_trades=N_TRADES_MIN - 1, net_pnl=1000.0),  # excluded
    ]
    winner = _argmax_cell(cells)
    assert winner["net_pnl"] == 50.0


def test_argmax_cell_tiebreak_prefers_lower_sl():
    """Gap #2: when net_pnl ties, tie-break by lower sl (more conservative)."""
    cells = [
        _cell(sl=2.5, be=2.0, lrc_thr=50.0, net_pnl=100.0),
        _cell(sl=1.0, be=2.0, lrc_thr=50.0, net_pnl=100.0),
        _cell(sl=1.5, be=2.0, lrc_thr=50.0, net_pnl=100.0),
    ]
    winner = _argmax_cell(cells)
    assert winner["sl"] == 1.0


def test_argmax_cell_tiebreak_prefers_lower_be_when_sl_ties():
    """Gap #2: net_pnl + sl tied → break by lower be."""
    cells = [
        _cell(sl=1.0, be=2.5, lrc_thr=50.0, net_pnl=100.0),
        _cell(sl=1.0, be=1.5, lrc_thr=50.0, net_pnl=100.0),
        _cell(sl=1.0, be=2.0, lrc_thr=50.0, net_pnl=100.0),
    ]
    winner = _argmax_cell(cells)
    assert winner["be"] == 1.5


def test_argmax_cell_tiebreak_prefers_lower_lrc_thr_when_sl_and_be_tie():
    """Gap #2: net_pnl + sl + be tied → break by lower lrc_thr."""
    cells = [
        _cell(sl=1.0, be=1.5, lrc_thr=55.0, net_pnl=100.0),
        _cell(sl=1.0, be=1.5, lrc_thr=35.0, net_pnl=100.0),
        _cell(sl=1.0, be=1.5, lrc_thr=45.0, net_pnl=100.0),
    ]
    winner = _argmax_cell(cells)
    assert winner["lrc_thr"] == 35.0


def test_argmax_cell_tiebreak_deterministic_across_input_orders():
    """Gap #2: tie-break is independent of insertion order — all 6 permutations agree."""
    a = _cell(sl=1.0, be=1.5, lrc_thr=35.0, net_pnl=100.0)
    b = _cell(sl=1.5, be=1.5, lrc_thr=35.0, net_pnl=100.0)
    c = _cell(sl=2.0, be=1.5, lrc_thr=35.0, net_pnl=100.0)
    for cells in (
        [a, b, c], [a, c, b], [b, a, c], [b, c, a], [c, a, b], [c, b, a],
    ):
        assert _argmax_cell(cells)["sl"] == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# _avg_pnl_per_trade / _time_limit_pct / _signal_exit_pct — None/zero guards
# ─────────────────────────────────────────────────────────────────────────────


def test_avg_pnl_per_trade_none_for_missing_cell():
    assert _avg_pnl_per_trade(None) is None


def test_avg_pnl_per_trade_none_for_zero_trades():
    assert _avg_pnl_per_trade({"n_trades": 0, "net_pnl": 0.0}) is None


def test_avg_pnl_per_trade_divides_net_pnl_by_n_trades():
    assert _avg_pnl_per_trade({"n_trades": 10, "net_pnl": 100.0}) == 10.0


def test_time_limit_pct_none_for_missing_cell():
    assert _time_limit_pct(None) is None


def test_time_limit_pct_none_for_zero_trades():
    assert _time_limit_pct({"n_trades": 0, "exit_reasons": {"TIME_LIMIT": 5}}) is None


def test_time_limit_pct_computes_percentage_on_argmax_cell():
    cell = _cell(n_trades=20, time_limit=10)
    assert _time_limit_pct(cell) == 50.0


def test_signal_exit_pct_none_for_missing_cell():
    assert _signal_exit_pct(None) is None


def test_signal_exit_pct_none_for_zero_trades():
    assert _signal_exit_pct({"n_trades": 0, "exit_reasons": {"SIGNAL_EXIT": 5}}) is None


def test_signal_exit_pct_computes_percentage_on_argmax_cell():
    cell = _cell(n_trades=20, signal_exit=5)
    assert _signal_exit_pct(cell) == 25.0


# ─────────────────────────────────────────────────────────────────────────────
# _analyze_window — primary / secondary criteria
# ─────────────────────────────────────────────────────────────────────────────


def test_analyze_window_empty_results_all_none():
    """No sweep results → every ALL_SYMBOLS entry maps to None."""
    out = _analyze_window("A", [])
    for sym in ALL_SYMBOLS:
        assert out["per_symbol_argmax_cell"][sym] is None
    assert out["primary_criterion"]["pass"] is False
    assert out["secondary_criterion"]["pass"] is False


def test_analyze_window_symbol_with_only_zero_trade_cells_is_none():
    """If every cell for a symbol has n_trades=0, argmax cell is None."""
    results = [_cell(symbol="BTCUSDT", n_trades=0, net_pnl=0.0)]
    out = _analyze_window("A", results)
    assert out["per_symbol_argmax_cell"]["BTCUSDT"] is None


def test_analyze_window_primary_pass_when_all_three_clauses_met():
    """Primary §4: ≥1 sym avg_ppt>0 AND ≥3 sym net_pnl>0 AND avg PF on positive subset > 1.2."""
    results = [
        _cell(symbol="BTCUSDT", n_trades=20, net_pnl=200.0, profit_factor=2.0),
        _cell(symbol="ETHUSDT", n_trades=20, net_pnl=200.0, profit_factor=2.0),
        _cell(symbol="ADAUSDT", n_trades=20, net_pnl=200.0, profit_factor=2.0),
    ]
    out = _analyze_window("A", results)
    assert out["primary_criterion"]["pass"] is True
    assert out["primary_criterion"]["n_syms_net_pnl_positive"] == 3
    assert out["primary_criterion"]["avg_pf_on_positive_subset"] == pytest.approx(2.0)


def test_analyze_window_primary_fail_avg_pf_below_threshold():
    """Three positive symbols but avg PF on positive subset ≤ 1.2 → primary fails."""
    results = [
        _cell(symbol="BTCUSDT", n_trades=20, net_pnl=200.0, profit_factor=1.0),
        _cell(symbol="ETHUSDT", n_trades=20, net_pnl=200.0, profit_factor=1.1),
        _cell(symbol="ADAUSDT", n_trades=20, net_pnl=200.0, profit_factor=1.15),
    ]
    out = _analyze_window("A", results)
    assert out["primary_criterion"]["pass"] is False


def test_analyze_window_primary_fail_below_min_net_pnl_positive_count():
    """Only 2 syms with net_pnl>0 → primary fails (needs ≥3)."""
    results = [
        _cell(symbol="BTCUSDT", n_trades=20, net_pnl=200.0, profit_factor=2.0),
        _cell(symbol="ETHUSDT", n_trades=20, net_pnl=200.0, profit_factor=2.0),
        _cell(symbol="ADAUSDT", n_trades=20, net_pnl=-100.0, profit_factor=0.5),
    ]
    out = _analyze_window("A", results)
    assert out["primary_criterion"]["pass"] is False


def test_analyze_window_secondary_pass_when_six_bankrupt_syms_have_low_tl():
    """6 of 8 currently-bankrupt symbols with TL%<20% on argmax cell → secondary pass."""
    bankrupt_low_tl = ("ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT", "PENDLEUSDT")
    results = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=2)  # TL% = 10%
        for s in bankrupt_low_tl
    ]
    out = _analyze_window("A", results)
    assert out["secondary_criterion"]["pass"] is True
    assert set(out["secondary_criterion"]["bankrupt_pass_tl_under_20pct"]) >= set(bankrupt_low_tl)


def test_analyze_window_secondary_fail_when_bankrupt_syms_have_high_tl():
    """All 8 bankrupt syms have TL%>=20% → secondary fails."""
    bankrupt_all = (
        "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT",
        "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
    )
    results = [
        _cell(symbol=s, n_trades=20, net_pnl=-100.0, time_limit=10)  # TL% = 50%
        for s in bankrupt_all
    ]
    out = _analyze_window("A", results)
    assert out["secondary_criterion"]["pass"] is False


# ─────────────────────────────────────────────────────────────────────────────
# _cross_window_stability
# ─────────────────────────────────────────────────────────────────────────────


def test_cross_window_stability_same_cell_across_three_windows():
    """Identical (sl, be, lrc_thr) in all three windows → stable=True."""
    cell = {"sl": 1.0, "be": 1.5, "lrc_thr": 35.0}
    per_window = {"A": {"BTCUSDT": cell}, "B": {"BTCUSDT": cell}, "C": {"BTCUSDT": cell}}
    out = _cross_window_stability(per_window)
    assert out["BTCUSDT"]["same_cell_across_windows"] is True
    assert out["BTCUSDT"]["n_windows_with_data"] == 3


def test_cross_window_stability_diverges_when_cells_differ():
    per_window = {
        "A": {"BTCUSDT": {"sl": 1.0, "be": 1.5, "lrc_thr": 35.0}},
        "B": {"BTCUSDT": {"sl": 2.0, "be": 1.5, "lrc_thr": 35.0}},
        "C": {"BTCUSDT": None},
    }
    out = _cross_window_stability(per_window)
    assert out["BTCUSDT"]["same_cell_across_windows"] is False
    assert out["BTCUSDT"]["n_windows_with_data"] == 2


def test_cross_window_stability_single_window_flag_is_none():
    """Only one window has data → can't determine stability."""
    per_window = {
        "A": {"BTCUSDT": {"sl": 1.0, "be": 1.5, "lrc_thr": 35.0}},
        "B": {"BTCUSDT": None},
        "C": {"BTCUSDT": None},
    }
    out = _cross_window_stability(per_window)
    assert out["BTCUSDT"]["same_cell_across_windows"] is None
    assert out["BTCUSDT"]["n_windows_with_data"] == 1


def test_cross_window_stability_no_data_flag_is_none_count_zero():
    per_window = {"A": {"BTCUSDT": None}, "B": {"BTCUSDT": None}, "C": {"BTCUSDT": None}}
    out = _cross_window_stability(per_window)
    assert out["BTCUSDT"]["same_cell_across_windows"] is None
    assert out["BTCUSDT"]["n_windows_with_data"] == 0


def test_cross_window_stability_covers_all_symbols_when_per_window_dicts_partial():
    """Symbols absent from a per_window dict still appear in output (with None)."""
    out = _cross_window_stability({"A": {}, "B": {}, "C": {}})
    for sym in ALL_SYMBOLS:
        assert sym in out
        assert out[sym]["n_windows_with_data"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# _classify_verdict — pre-reg §4.2 outcomes + gap #4 halt guard
# ─────────────────────────────────────────────────────────────────────────────


def _per_window_analysis(p_per_w: dict, s_per_w: dict) -> dict:
    """Build the minimal _classify_verdict input from primary/secondary pass flags."""
    return {
        w: {
            "primary_criterion": {"pass": p_per_w[w]},
            "secondary_criterion": {"pass": s_per_w[w]},
        }
        for w in p_per_w
    }


def test_classify_verdict_success_all_windows_pass_both():
    pwa = _per_window_analysis(
        {"A": True, "B": True, "C": True},
        {"A": True, "B": True, "C": True},
    )
    assert _classify_verdict(pwa)["verdict"] == "R1_SUCCESS"


def test_classify_verdict_fail_all_windows_fail_both():
    pwa = _per_window_analysis(
        {"A": False, "B": False, "C": False},
        {"A": False, "B": False, "C": False},
    )
    assert _classify_verdict(pwa)["verdict"] == "R1_FAIL"


def test_classify_verdict_success_conditional_one_primary_fail_secondary_all_pass():
    """Exactly one window fails primary while secondary passes everywhere → SUCCESS_CONDITIONAL."""
    pwa = _per_window_analysis(
        {"A": True, "B": True, "C": False},
        {"A": True, "B": True, "C": True},
    )
    assert _classify_verdict(pwa)["verdict"] == "R1_SUCCESS_CONDITIONAL"


def test_classify_verdict_inconclusive_two_primary_fail_secondary_high():
    """Two windows fail primary but secondary still meets the (n_windows-1) bar → INCONCLUSIVE."""
    pwa = _per_window_analysis(
        {"A": True, "B": False, "C": False},
        {"A": True, "B": True, "C": True},
    )
    assert _classify_verdict(pwa)["verdict"] == "R1_INCONCLUSIVE"


def test_classify_verdict_default_halt_false_preserves_existing_behavior():
    """Gap #4: default halt=False — caller without the flag gets pre-fix behavior."""
    pwa = _per_window_analysis(
        {"A": False, "B": False, "C": False},
        {"A": False, "B": False, "C": False},
    )
    assert _classify_verdict(pwa)["verdict"] == "R1_FAIL"


def test_classify_verdict_halt_with_one_window_returns_insufficient_data():
    """Gap #4: halt fired + single window — even if A passes, we cannot declare SUCCESS."""
    pwa = _per_window_analysis({"A": True}, {"A": True})
    out = _classify_verdict(pwa, halt=True)
    assert out["verdict"] == "R1_INSUFFICIENT_DATA"


def test_classify_verdict_halt_with_two_windows_returns_insufficient_data():
    """Gap #4: halt fired + only A+B run — still <3 windows ⇒ INSUFFICIENT_DATA."""
    pwa = _per_window_analysis(
        {"A": True, "B": True},
        {"A": True, "B": True},
    )
    out = _classify_verdict(pwa, halt=True)
    assert out["verdict"] == "R1_INSUFFICIENT_DATA"


def test_classify_verdict_halt_with_three_windows_runs_normal_logic():
    """Gap #4: halt fired but operator overrode and ran all 3 → normal classification."""
    pwa = _per_window_analysis(
        {"A": True, "B": True, "C": True},
        {"A": True, "B": True, "C": True},
    )
    assert _classify_verdict(pwa, halt=True)["verdict"] == "R1_SUCCESS"


def test_classify_verdict_no_halt_one_window_normal_logic():
    """Gap #4: halt=False + 1 window → existing logic, no INSUFFICIENT_DATA guard."""
    pwa = _per_window_analysis({"A": False}, {"A": False})
    assert _classify_verdict(pwa, halt=False)["verdict"] == "R1_FAIL"


def test_classify_verdict_halt_with_one_window_primary_fails_stays_fail():
    """Gap #4 scope: halt + 1 window + clear FAIL is NOT spurious — verdict stays FAIL.

    The halt guard targets favorable verdicts on partial evidence; negative
    evidence (primary/secondary failing in the windows that ran) is honest
    and not invalidated by the early halt. Locks the recorded R1 outcome
    (halt=True, n_windows=1, primary+secondary both failed → R1_FAIL).
    """
    pwa = _per_window_analysis({"A": False}, {"A": False})
    assert _classify_verdict(pwa, halt=True)["verdict"] == "R1_FAIL"


def test_classify_verdict_halt_with_two_windows_inconclusive_stays_inconclusive():
    """Gap #4 scope: halt + naive INCONCLUSIVE stays INCONCLUSIVE.

    Halt overrides only favorable verdicts (SUCCESS / SUCCESS_CONDITIONAL).
    INCONCLUSIVE is an honest "we can't tell from these windows" — halt
    doesn't invalidate that, and replacing it with INSUFFICIENT_DATA would
    erase informative content.
    """
    pwa = _per_window_analysis(
        {"A": True, "B": False},
        {"A": True, "B": False},
    )
    assert _classify_verdict(pwa, halt=True)["verdict"] == "R1_INCONCLUSIVE"


def test_classify_verdict_halt_with_two_windows_success_conditional_overridden():
    """Gap #4 scope: halt + naive SUCCESS_CONDITIONAL on 2 windows → INSUFFICIENT_DATA.

    SUCCESS_CONDITIONAL is still favorable evidence on partial windows, so
    halt should block it the same way it blocks SUCCESS.
    """
    pwa = _per_window_analysis(
        {"A": True, "B": False},
        {"A": True, "B": True},
    )
    assert _classify_verdict(pwa, halt=True)["verdict"] == "R1_INSUFFICIENT_DATA"


# ─────────────────────────────────────────────────────────────────────────────
# _load_json — missing vs present
# ─────────────────────────────────────────────────────────────────────────────


def test_load_json_returns_none_for_missing_file(tmp_path, monkeypatch):
    from tools import r1_verdict
    monkeypatch.setattr(r1_verdict, "OUTPUT_DIR", tmp_path)
    assert r1_verdict._load_json("does_not_exist.json") is None


def test_load_json_parses_existing_file(tmp_path, monkeypatch):
    from tools import r1_verdict
    monkeypatch.setattr(r1_verdict, "OUTPUT_DIR", tmp_path)
    payload = {"foo": "bar", "n": 42}
    (tmp_path / "test.json").write_text(json.dumps(payload))
    assert r1_verdict._load_json("test.json") == payload
