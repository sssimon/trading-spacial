"""Unit tests for tools/r3_verdict.py — R3 verdict calculator helpers.

Pre-reg: docs/superpowers/plans/2026-05-13-r3-trend-pullback-pre-reg.md §4

Coverage:
  - `_argmax_cell`: eligibility filter + 5-tuple deterministic tie-break +
    permutation exhaust (mirrors R1 hardened patterns).
  - `_avg_pnl_per_trade` / `_time_limit_pct` / `_signal_exit_pct`: None/zero guards.
  - `_analyze_window`: per-symbol argmax + primary criterion + engagement count.
  - `_cross_window_stability`: stable / diverges / single-window flag.
  - `_classify_verdict`: pre-reg §4.2 outcomes (5 verdicts) + §4.6 asymmetric
    halt-guard.
  - `_extract_halt_from_diagnostic`: isinstance validation (#332 item 2 mirror).
  - `_load_json`: missing-file → None, present-file → parsed dict.
"""
from __future__ import annotations

import json

import pytest

from tools.r3_verdict import (
    ALL_SYMBOLS,
    N_TRADES_MIN,
    PRIMARY_NET_PNL_POSITIVE_SYM_COUNT,
    PRIMARY_PF_THRESHOLD,
    _analyze_window,
    _argmax_cell,
    _avg_pnl_per_trade,
    _classify_verdict,
    _cross_window_stability,
    _extract_halt_from_diagnostic,
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
    pullback_distance: float = 0.5,
    n_trades: int = 20,
    net_pnl: float = 0.0,
    profit_factor: float = 1.0,
    time_limit: int = 0,
    signal_exit: int = 0,
    sl_exits: int = 0,
    tp_exits: int = 0,
) -> dict:
    """Build a synthetic cell dict matching tools.r3_trend_pullback_sweep output shape."""
    return {
        "symbol": symbol,
        "sl": sl,
        "be": be,
        "pullback_distance": pullback_distance,
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
# _argmax_cell — eligibility filter + 5-tuple deterministic tie-break
# ─────────────────────────────────────────────────────────────────────────────


def test_argmax_cell_returns_none_on_empty_list():
    assert _argmax_cell([]) is None


def test_argmax_cell_returns_none_when_no_cell_meets_min_trades():
    cells = [_cell(n_trades=N_TRADES_MIN - 1)]
    assert _argmax_cell(cells) is None


def test_argmax_cell_picks_max_net_pnl_when_no_ties():
    cells = [
        _cell(net_pnl=10.0, sl=1.0, be=1.5, pullback_distance=0.5),
        _cell(net_pnl=50.0, sl=2.0, be=2.0, pullback_distance=0.7),
        _cell(net_pnl=25.0, sl=1.5, be=1.5, pullback_distance=0.4),
    ]
    assert _argmax_cell(cells)["net_pnl"] == 50.0


def test_argmax_cell_excludes_cells_below_min_trades():
    cells = [
        _cell(net_pnl=100.0, n_trades=N_TRADES_MIN - 1),  # below floor, excluded
        _cell(net_pnl=10.0, n_trades=N_TRADES_MIN, sl=2.0),  # eligible
    ]
    out = _argmax_cell(cells)
    assert out["net_pnl"] == 10.0


def test_argmax_cell_tiebreak_prefers_lower_sl():
    cells = [
        _cell(sl=1.0, be=1.5, pullback_distance=0.5, net_pnl=100.0),
        _cell(sl=2.5, be=1.5, pullback_distance=0.5, net_pnl=100.0),
        _cell(sl=0.5, be=1.5, pullback_distance=0.5, net_pnl=100.0),
    ]
    assert _argmax_cell(cells)["sl"] == 0.5


def test_argmax_cell_tiebreak_prefers_lower_be_when_sl_ties():
    cells = [
        _cell(sl=1.0, be=2.5, pullback_distance=0.5, net_pnl=100.0),
        _cell(sl=1.0, be=1.5, pullback_distance=0.5, net_pnl=100.0),
    ]
    assert _argmax_cell(cells)["be"] == 1.5


def test_argmax_cell_tiebreak_prefers_lower_pullback_distance_when_sl_and_be_tie():
    """R3-specific: pullback_distance replaces lrc_thr as 4th tie-break key."""
    cells = [
        _cell(sl=1.0, be=2.0, pullback_distance=0.7, net_pnl=100.0),
        _cell(sl=1.0, be=2.0, pullback_distance=0.3, net_pnl=100.0),
    ]
    assert _argmax_cell(cells)["pullback_distance"] == 0.3


def test_argmax_cell_tiebreak_deterministic_across_input_orders():
    """4-tuple tie-break independent of insertion order — 6 permutations agree."""
    a = _cell(sl=1.0, be=1.5, pullback_distance=0.3, net_pnl=100.0)
    b = _cell(sl=1.5, be=1.5, pullback_distance=0.3, net_pnl=100.0)
    c = _cell(sl=2.0, be=1.5, pullback_distance=0.3, net_pnl=100.0)
    for cells in (
        [a, b, c], [a, c, b], [b, a, c], [b, c, a], [c, a, b], [c, b, a],
    ):
        assert _argmax_cell(cells)["sl"] == 1.0


def test_argmax_cell_5th_tiebreak_symbol_lex_order_on_full_tuple_tie():
    """5th tie-break: lex-greater symbol wins on full 4-tuple tie.

    Defensive contract — per-symbol callers pass identical-symbol cells where
    this is a no-op. Multi-symbol calls (e.g., future caller) get deterministic
    ordering via lex.
    """
    cells = [
        _cell(symbol="BTCUSDT", sl=1.0, be=2.0, pullback_distance=0.5, net_pnl=100.0),
        _cell(symbol="ETHUSDT", sl=1.0, be=2.0, pullback_distance=0.5, net_pnl=100.0),
    ]
    assert _argmax_cell(cells)["symbol"] == "ETHUSDT"


def test_argmax_cell_5th_tiebreak_reverse_input_order_same_winner():
    """Reverse-input deterministic — [ETH, BTC] also yields ETH."""
    cells_reversed = [
        _cell(symbol="ETHUSDT", sl=1.0, be=2.0, pullback_distance=0.5, net_pnl=100.0),
        _cell(symbol="BTCUSDT", sl=1.0, be=2.0, pullback_distance=0.5, net_pnl=100.0),
    ]
    assert _argmax_cell(cells_reversed)["symbol"] == "ETHUSDT"


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
    assert _time_limit_pct({"n_trades": 0, "exit_reasons": {"TIME_LIMIT": 0}}) is None


def test_time_limit_pct_computes_percentage_on_argmax_cell():
    cell = _cell(n_trades=20, time_limit=5)  # 25%
    assert _time_limit_pct(cell) == 25.0


def test_signal_exit_pct_none_for_missing_cell():
    assert _signal_exit_pct(None) is None


def test_signal_exit_pct_none_for_zero_trades():
    assert _signal_exit_pct({"n_trades": 0, "exit_reasons": {"SIGNAL_EXIT": 0}}) is None


def test_signal_exit_pct_computes_percentage_on_argmax_cell():
    cell = _cell(n_trades=20, signal_exit=10)  # 50%
    assert _signal_exit_pct(cell) == 50.0


# ─────────────────────────────────────────────────────────────────────────────
# _analyze_window — per-symbol argmax + primary criterion + engagement
# ─────────────────────────────────────────────────────────────────────────────


def test_analyze_window_empty_results_all_none():
    out = _analyze_window("A", [])
    for sym in ALL_SYMBOLS:
        assert out["per_symbol_argmax_cell"][sym] is None
    assert out["primary_criterion"]["pass"] is False
    assert out["n_symbols_engaged"] == 0


def test_analyze_window_symbol_with_only_zero_trade_cells_is_none():
    """Symbols where every cell has n_trades=0 → None argmax (no fire)."""
    results = [
        _cell(symbol="BTCUSDT", n_trades=0),
        _cell(symbol="BTCUSDT", n_trades=0, sl=2.5),
    ]
    out = _analyze_window("A", results)
    assert out["per_symbol_argmax_cell"]["BTCUSDT"] is None
    assert out["n_symbols_engaged"] == 0


def test_analyze_window_primary_pass_when_3_symbols_positive_with_high_pf():
    """3 symbols net_pnl>0 with PF=1.5 average → primary passes."""
    results = []
    for sym in ("BTCUSDT", "ETHUSDT", "ADAUSDT"):
        results.append(_cell(symbol=sym, net_pnl=100.0, profit_factor=1.5, n_trades=20))
    # 7 more symbols with negative pnl
    for sym in ("AVAXUSDT", "DOGEUSDT", "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT"):
        results.append(_cell(symbol=sym, net_pnl=-100.0, profit_factor=0.5, n_trades=20))
    out = _analyze_window("A", results)
    assert out["primary_criterion"]["pass"] is True
    assert out["primary_criterion"]["n_syms_net_pnl_positive"] == 3


def test_analyze_window_primary_fail_below_min_net_pnl_positive_count():
    """Only 2 symbols net_pnl>0 (below threshold 3) → primary fails."""
    results = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        results.append(_cell(symbol=sym, net_pnl=100.0, profit_factor=1.5, n_trades=20))
    for sym in ("ADAUSDT", "AVAXUSDT", "DOGEUSDT"):
        results.append(_cell(symbol=sym, net_pnl=-100.0, profit_factor=0.5, n_trades=20))
    out = _analyze_window("A", results)
    assert out["primary_criterion"]["pass"] is False
    assert out["primary_criterion"]["n_syms_net_pnl_positive"] == 2


def test_analyze_window_primary_fail_avg_pf_below_threshold():
    """3 symbols net_pnl>0 BUT avg PF = 1.1 (< 1.2) → primary fails."""
    results = []
    for sym in ("BTCUSDT", "ETHUSDT", "ADAUSDT"):
        results.append(_cell(symbol=sym, net_pnl=100.0, profit_factor=1.1, n_trades=20))
    out = _analyze_window("A", results)
    assert out["primary_criterion"]["pass"] is False
    assert out["primary_criterion"]["avg_pf_on_positive_subset"] == pytest.approx(1.1, abs=0.01)


def test_analyze_window_n_symbols_engaged_counts_argmax_with_enough_trades():
    """Engagement count = number of symbols with argmax cell having n_trades ≥ N_TRADES_MIN."""
    results = []
    # 4 engaged symbols (n_trades=15, eligible)
    for sym in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT"):
        results.append(_cell(symbol=sym, n_trades=15, net_pnl=-100.0))
    # 2 with only-zero-trade cells (NOT engaged — no fire)
    for sym in ("DOGEUSDT", "UNIUSDT"):
        results.append(_cell(symbol=sym, n_trades=0))
    # 1 with cell below floor (engaged for purposes of "had any trades" but
    # n_trades < N_TRADES_MIN; argmax → None per eligibility filter)
    results.append(_cell(symbol="XLMUSDT", n_trades=N_TRADES_MIN - 1))
    out = _analyze_window("A", results)
    assert out["n_symbols_engaged"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# _cross_window_stability — same-cell-across-windows / diverges / single
# ─────────────────────────────────────────────────────────────────────────────


def test_cross_window_stability_stable_when_same_cell_3_windows():
    same_cell = {"sl": 1.0, "be": 1.5, "pullback_distance": 0.5}
    per_window_argmax = {
        "A": {"BTCUSDT": same_cell},
        "B": {"BTCUSDT": same_cell},
        "C": {"BTCUSDT": same_cell},
    }
    out = _cross_window_stability(per_window_argmax)
    assert out["BTCUSDT"]["same_cell_across_windows"] is True
    assert out["BTCUSDT"]["n_windows_with_data"] == 3


def test_cross_window_stability_diverges_when_cells_differ():
    per_window_argmax = {
        "A": {"BTCUSDT": {"sl": 1.0, "be": 1.5, "pullback_distance": 0.5}},
        "B": {"BTCUSDT": {"sl": 2.0, "be": 2.0, "pullback_distance": 0.7}},
        "C": {"BTCUSDT": {"sl": 1.0, "be": 1.5, "pullback_distance": 0.5}},
    }
    out = _cross_window_stability(per_window_argmax)
    assert out["BTCUSDT"]["same_cell_across_windows"] is False


def test_cross_window_stability_none_when_single_window():
    per_window_argmax = {
        "A": {"BTCUSDT": None},
        "B": {"BTCUSDT": None},
        "C": {"BTCUSDT": {"sl": 1.0, "be": 1.5, "pullback_distance": 0.5}},
    }
    out = _cross_window_stability(per_window_argmax)
    assert out["BTCUSDT"]["same_cell_across_windows"] is None
    assert out["BTCUSDT"]["n_windows_with_data"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# _classify_verdict — pre-reg §4.2 verdict tree + §4.6 asymmetric halt-guard
# ─────────────────────────────────────────────────────────────────────────────


def _per_window_analysis(
    primary_pass: dict[str, bool],
    n_symbols_engaged: dict[str, int] | None = None,
) -> dict[str, dict]:
    """Build minimal per_window_analysis dict for _classify_verdict tests."""
    if n_symbols_engaged is None:
        n_symbols_engaged = {w: 8 for w in primary_pass}
    return {
        w: {
            "primary_criterion": {"pass": v},
            "n_symbols_engaged": n_symbols_engaged.get(w, 8),
        }
        for w, v in primary_pass.items()
    }


def test_classify_verdict_success_when_primary_pass_3_of_3():
    """R3 SUCCESS = primary ✓ in 3/3 sub-windows."""
    pwa = _per_window_analysis({"A": True, "B": True, "C": True})
    out = _classify_verdict(pwa, halt=False)
    assert out["verdict"] == "R3_SUCCESS"


def test_classify_verdict_success_conditional_when_primary_pass_2_of_3_cells_stable():
    """R3 SUCCESS_CONDITIONAL = primary ✓ in 2/3 + cells stable."""
    pwa = _per_window_analysis({"A": True, "B": True, "C": False})
    # Mock stability with ≥3 stable symbols
    stability = {
        s: {"same_cell_across_windows": True} for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT")
    }
    out = _classify_verdict(pwa, halt=False, stability=stability)
    assert out["verdict"] == "R3_SUCCESS_CONDITIONAL"


def test_classify_verdict_inconclusive_when_primary_pass_2_of_3_cells_diverge():
    """R3 INCONCLUSIVE = primary ✓ in 2/3 + cells diverge wildly."""
    pwa = _per_window_analysis({"A": True, "B": True, "C": False})
    # No stability info OR stability shows cells diverge for all symbols
    stability = {s: {"same_cell_across_windows": False} for s in ALL_SYMBOLS}
    out = _classify_verdict(pwa, halt=False, stability=stability)
    assert out["verdict"] == "R3_INCONCLUSIVE"


def test_classify_verdict_fail_clean_when_primary_fail_3_of_3_engaged():
    """R3 FAIL (clean) = primary ✗ in ≥2/3 + ≥3 in-data engage in ≥2/3 windows."""
    pwa = _per_window_analysis(
        {"A": False, "B": False, "C": False},
        n_symbols_engaged={"A": 8, "B": 5, "C": 9},
    )
    out = _classify_verdict(pwa, halt=False)
    assert out["verdict"] == "R3_FAIL"
    assert out["signal_degenerate_check"]["fires"] is False


def test_classify_verdict_fail_signal_degenerate_when_engagement_le_2_in_2_of_3():
    """R3 FAIL (signal degenerate) = ≥2/3 primary ✗ + ≤2 engage in ≥2/3 windows."""
    pwa = _per_window_analysis(
        {"A": False, "B": False, "C": False},
        n_symbols_engaged={"A": 2, "B": 1, "C": 5},  # 2 windows ≤2 engaged
    )
    out = _classify_verdict(pwa, halt=False)
    assert out["verdict"] == "R3_FAIL_SIGNAL_DEGENERATE"
    assert out["signal_degenerate_check"]["fires"] is True


def test_classify_verdict_fail_clean_when_only_1_window_degenerate():
    """If only 1 window has engagement ≤2, signal-degenerate does NOT fire (need ≥2/3 windows)."""
    pwa = _per_window_analysis(
        {"A": False, "B": False, "C": False},
        n_symbols_engaged={"A": 2, "B": 5, "C": 9},  # 1 window ≤2 engaged
    )
    out = _classify_verdict(pwa, halt=False)
    assert out["verdict"] == "R3_FAIL"
    assert out["signal_degenerate_check"]["fires"] is False


# ─────────────────────────────────────────────────────────────────────────────
# §4.6 asymmetric halt-guard
# ─────────────────────────────────────────────────────────────────────────────


def test_classify_verdict_halt_does_not_override_negative_verdict():
    """§4.6: halt+n_windows<3 preserves R3_FAIL (negative verdict on partial windows)."""
    pwa = _per_window_analysis(
        {"A": False},  # 1 window, primary fail
        n_symbols_engaged={"A": 5},
    )
    out = _classify_verdict(pwa, halt=True)
    # Halt fired + only 1 window + primary failed → R3_FAIL preserved per §4.6
    assert out["verdict"] in ("R3_FAIL", "R3_FAIL_SIGNAL_DEGENERATE")
    assert out["halt"] is True


def test_classify_verdict_halt_overrides_naive_success_to_insufficient_data():
    """§4.6: halt+n_windows<3 + naive R3_SUCCESS → R3_INSUFFICIENT_DATA.

    A favorable verdict on partial windows is spurious — can't declare success
    without seeing all 3 windows. Mirrors R1 §4.6 amendment pattern.
    """
    # Note: with only 1 window primary ✓, naive verdict logic doesn't reach SUCCESS
    # (SUCCESS requires 3/3 windows). Let's test the more realistic SUCCESS_CONDITIONAL
    # override: 2/3 primary ✓ + halt + n_windows<3.
    pwa = _per_window_analysis({"A": True, "B": True})  # 2 of 2 primary pass
    stability = {
        s: {"same_cell_across_windows": True} for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT")
    }
    out = _classify_verdict(pwa, halt=True, stability=stability)
    # Without halt: 2/2 with cells stable would map to R3_SUCCESS_CONDITIONAL.
    # With halt + n_windows<3 + favorable: override to R3_INSUFFICIENT_DATA.
    assert out["verdict"] == "R3_INSUFFICIENT_DATA"


def test_classify_verdict_halt_with_full_3_windows_does_not_override():
    """§4.6: halt with n_windows=3 does NOT override SUCCESS (rare but possible)."""
    pwa = _per_window_analysis({"A": True, "B": True, "C": True})
    out = _classify_verdict(pwa, halt=True)
    # halt=True but n_windows == 3 → no override
    assert out["verdict"] == "R3_SUCCESS"


# ─────────────────────────────────────────────────────────────────────────────
# _extract_halt_from_diagnostic — isinstance validation (#332 item 2 mirror)
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_halt_from_diagnostic_none_returns_false():
    assert _extract_halt_from_diagnostic(None) is False


def test_extract_halt_from_diagnostic_empty_dict_returns_false():
    assert _extract_halt_from_diagnostic({}) is False


def test_extract_halt_from_diagnostic_true_value_returns_true():
    assert _extract_halt_from_diagnostic({"halt": True}) is True


def test_extract_halt_from_diagnostic_false_value_returns_false():
    assert _extract_halt_from_diagnostic({"halt": False}) is False


def test_extract_halt_from_diagnostic_string_value_raises():
    with pytest.raises(ValueError, match=r"halt_diag\['halt'\] must be bool"):
        _extract_halt_from_diagnostic({"halt": "false"})


def test_extract_halt_from_diagnostic_int_zero_raises():
    with pytest.raises(ValueError, match=r"halt_diag\['halt'\] must be bool"):
        _extract_halt_from_diagnostic({"halt": 0})


def test_extract_halt_from_diagnostic_int_one_raises():
    with pytest.raises(ValueError, match=r"halt_diag\['halt'\] must be bool"):
        _extract_halt_from_diagnostic({"halt": 1})


def test_extract_halt_from_diagnostic_non_dict_raises():
    with pytest.raises(ValueError, match=r"halt_diag must be dict or None"):
        _extract_halt_from_diagnostic("not a dict")


def test_extract_halt_from_diagnostic_list_raises():
    with pytest.raises(ValueError, match=r"halt_diag must be dict or None"):
        _extract_halt_from_diagnostic([{"halt": True}])


# ─────────────────────────────────────────────────────────────────────────────
# _load_json — missing vs present
# ─────────────────────────────────────────────────────────────────────────────


def test_load_json_returns_none_for_missing_file(tmp_path, monkeypatch):
    from tools import r3_verdict
    monkeypatch.setattr(r3_verdict, "OUTPUT_DIR", tmp_path)
    assert r3_verdict._load_json("does_not_exist.json") is None


def test_load_json_parses_existing_file(tmp_path, monkeypatch):
    from tools import r3_verdict
    monkeypatch.setattr(r3_verdict, "OUTPUT_DIR", tmp_path)
    payload = {"foo": "bar", "n": 42}
    (tmp_path / "test.json").write_text(json.dumps(payload))
    assert r3_verdict._load_json("test.json") == payload
