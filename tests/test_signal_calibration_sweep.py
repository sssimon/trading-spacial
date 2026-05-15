"""Tests for tools/signal_calibration_{verdict,diagnostic,sweep}.py (epic C Phase 1).

Pre-reg `docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md` §6
locks this as the single test file covering 3 production modules. Tests are
grouped by module:
  - TestPhase2VerdictLogic / TestPhase3VerdictLogic / TestPhase4VerdictLogic /
    TestAsymmetricHaltGuard   — module 2 (verdict.py, §4.1/§4.2/§4.3/§4.6)
  - TestDiagnosticRunner      — module 3 (diagnostic.py)
  - TestSweepRunner / TestSmoke — module 4 (sweep.py + --smoke flag)

Thresholds reference (pre-reg §8 summary table + Q-PR locks):
  T_COUNTS_FIRING       = 5     (Q-PR1)
  P50_MAGNITUDE_MAX     = 2.0   (Q-PR2, strict `<`)
  WIN_RATE_FLOOR        = 0.30  (Q-PR3)
  WIN_RATE_DEGRADATION  = 0.50  (Q-PR4, intervention < 50% of baseline)
  A1_SUBSET             = (5, 10, 20)  (Q-PR6)
  AGGREGATE_MATCH_FRAC  = 0.75  (≥75% símbolos satisfy criterion; heredar #338)
"""
from __future__ import annotations

import pytest

from tools.signal_calibration_verdict import (
    AGGREGATE_MATCH_FRACTION,
    P50_MAGNITUDE_MAX,
    T_COUNTS_FIRING,
    WIN_RATE_DEGRADATION,
    WIN_RATE_FLOOR,
    apply_asymmetric_halt_guard,
    classify_phase2_verdict,
    classify_phase3_verdict,
    classify_phase4_verdict,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic Phase 2 / Phase 3 / Phase 4 cell shapes
# ---------------------------------------------------------------------------


def _phase2_cell(symbol: str, *, firings: dict[int, int], p50_mag: float) -> dict:
    """Construct a synthetic Phase 2 observability cell for verdict tests.

    Args:
        symbol: ticker label
        firings: {N: firing_count} for each short-lookback in {5, 10, 20}
        p50_mag: aggregate p50(|sum|) value over the window
    """
    return {
        "symbol": symbol,
        "per_lookback": {
            n: {"firing_count": firings.get(n, 0)} for n in (5, 10, 20)
        },
        "sum_distribution": {"p50": p50_mag},
    }


def _phase3_cell(
    symbol: str,
    *,
    n_trades: int,
    bankruptcy_count: int,
    win_rate: float,
    vol_target: float = 0.30,
) -> dict:
    return {
        "symbol": symbol,
        "vol_target": vol_target,
        "n_trades": n_trades,
        "bankruptcy_count": bankruptcy_count,
        "win_rate": win_rate,
    }


def _phase4_window_pass_summary(window_id: str, n_pass: int, n_in_coverage: int) -> dict:
    """Summary of a single Phase 4 walk-forward window."""
    return {
        "window_id": window_id,
        "n_pass": n_pass,
        "n_in_coverage": n_in_coverage,
    }


# ---------------------------------------------------------------------------
# Phase 2 verdict (§4.1 decision tree)
# ---------------------------------------------------------------------------


class TestPhase2VerdictLogic:
    def test_a_detected_8_of_8_show_both_evidence_types(self):
        """Counts ≥5 per N∈{5,10,20} ∧ p50(|sum|) < 2 in all 8 in-coverage symbols."""
        cells = [
            _phase2_cell(s, firings={5: 10, 10: 8, 20: 6}, p50_mag=1.5)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")
        ]
        result = classify_phase2_verdict(cells, in_coverage_count=8, halt=False)
        assert result["verdict"] == "A_DETECTED"
        assert result["n_symbols_a_evidence"] == 8

    def test_b_detected_8_of_8_show_neither_evidence(self):
        """Counts <5 in at least one short-lookback ∧ p50(|sum|) ≥ 2 in all 8."""
        cells = [
            _phase2_cell(s, firings={5: 2, 10: 1, 20: 0}, p50_mag=3.0)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")
        ]
        result = classify_phase2_verdict(cells, in_coverage_count=8, halt=False)
        assert result["verdict"] == "B_DETECTED"

    def test_ambiguous_4_of_8_split(self):
        """Pre-reg §4.1 line 290 worked example: 4 show A-evidence, 4 don't → AMBIGUOUS.

        Derives from "else" clause: doesn't satisfy ≥6/8 for either A_DETECTED
        or B_DETECTED.
        """
        a_cells = [
            _phase2_cell(s, firings={5: 10, 10: 8, 20: 6}, p50_mag=1.5)
            for s in ("BTC", "ETH", "ADA", "AVAX")
        ]
        b_cells = [
            _phase2_cell(s, firings={5: 1, 10: 0, 20: 0}, p50_mag=4.0)
            for s in ("DOGE", "UNI", "XLM", "RUNE")
        ]
        result = classify_phase2_verdict(a_cells + b_cells, in_coverage_count=8, halt=False)
        assert result["verdict"] == "AMBIGUOUS"

    def test_phase2_insufficient_data_when_halt_fired(self):
        """If halt fires in Phase 2 (e.g., universal bankruptcy), verdict =
        PHASE_2_INSUFFICIENT_DATA regardless of naive evidence pattern."""
        cells = [
            _phase2_cell(s, firings={5: 10, 10: 8, 20: 6}, p50_mag=1.5)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")
        ]
        result = classify_phase2_verdict(cells, in_coverage_count=8, halt=True)
        assert result["verdict"] == "PHASE_2_INSUFFICIENT_DATA"

    def test_a_detected_at_exactly_6_of_8_boundary(self):
        """≥75% threshold = ≥6 of 8 in-coverage symbols. Exactly 6 → A_DETECTED."""
        a_cells = [
            _phase2_cell(s, firings={5: 10, 10: 8, 20: 6}, p50_mag=1.5)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI")
        ]
        # 2 symbols with mixed/missing evidence
        other_cells = [
            _phase2_cell("XLM", firings={5: 1, 10: 0, 20: 0}, p50_mag=4.0),
            _phase2_cell("RUNE", firings={5: 10, 10: 8, 20: 6}, p50_mag=4.0),  # counts but no mag
        ]
        result = classify_phase2_verdict(a_cells + other_cells, in_coverage_count=8, halt=False)
        assert result["verdict"] == "A_DETECTED"
        assert result["n_symbols_a_evidence"] == 6


# ---------------------------------------------------------------------------
# Phase 3 verdict (§4.2 decision tree + Q-PR4 H-C4 halt)
# ---------------------------------------------------------------------------


class TestPhase3VerdictLogic:
    def test_phase3_a_pass_strong_6_of_8_with_3_of_4_sensitivity(self):
        """≥6/8 primary PASS ∧ ≥3/4 sensitivity PASS → PHASE_3_A_PASS."""
        primary = [
            _phase3_cell(s, n_trades=10, bankruptcy_count=0, win_rate=0.40)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")
        ]
        sensitivity = []
        # 3/4 vol_target PASS, 1/4 FAIL
        for vt, win_rate in zip((0.25, 0.30, 0.35, 0.40), (0.40, 0.40, 0.40, 0.20)):
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE"):
                sensitivity.append(
                    _phase3_cell(s, n_trades=10, bankruptcy_count=0, win_rate=win_rate, vol_target=vt)
                )
        baseline_win_rates = {s: 0.40 for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")}
        result = classify_phase3_verdict(
            primary_cells=primary,
            sensitivity_cells=sensitivity,
            baseline_win_rates=baseline_win_rates,
            in_coverage_count=8,
            halt=False,
        )
        assert result["verdict"] == "PHASE_3_A_PASS"

    def test_a_intervention_insufficient_when_5_of_8_below_threshold(self):
        """5/8 < 6/8 ≥75% threshold → A_INTERVENTION_INSUFFICIENT."""
        primary = [
            _phase3_cell(s, n_trades=10, bankruptcy_count=0, win_rate=0.40)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE")
        ] + [
            _phase3_cell(s, n_trades=2, bankruptcy_count=0, win_rate=0.40)  # FAIL: n_trades < 5
            for s in ("UNI", "XLM", "RUNE")
        ]
        result = classify_phase3_verdict(
            primary_cells=primary,
            sensitivity_cells=[],
            baseline_win_rates={s: 0.40 for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")},
            in_coverage_count=8,
            halt=False,
        )
        assert result["verdict"] == "A_INTERVENTION_INSUFFICIENT"

    def test_a_intervention_harmful_h_c3_when_bankruptcy_in_1_symbol(self):
        """H-C3: ≥1 bankruptcy → A_INTERVENTION_HARMFUL (zero-tolerance, §10.4)."""
        primary = [
            _phase3_cell(s, n_trades=10, bankruptcy_count=0, win_rate=0.40)
            for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM")
        ] + [
            _phase3_cell("RUNE", n_trades=10, bankruptcy_count=1, win_rate=0.40),  # bankruptcy
        ]
        result = classify_phase3_verdict(
            primary_cells=primary,
            sensitivity_cells=[],
            baseline_win_rates={s: 0.40 for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")},
            in_coverage_count=8,
            halt=False,
        )
        assert result["verdict"] == "A_INTERVENTION_HARMFUL"
        assert "h_c3" in result["halt_trigger"].lower()

    def test_a_intervention_harmful_h_c4_when_win_rate_degraded_in_4_of_8(self):
        """H-C4 (Q-PR4): A1 win_rate < 50% of baseline in ≥4/8 símbolos."""
        # Baseline win_rate = 0.40 for all; A1 < 0.20 for 4 symbols (degradation)
        primary = [
            _phase3_cell(s, n_trades=10, bankruptcy_count=0, win_rate=0.40)
            for s in ("BTC", "ETH", "ADA", "AVAX")
        ] + [
            _phase3_cell(s, n_trades=10, bankruptcy_count=0, win_rate=0.15)  # < 50% of 0.40
            for s in ("DOGE", "UNI", "XLM", "RUNE")
        ]
        result = classify_phase3_verdict(
            primary_cells=primary,
            sensitivity_cells=[],
            baseline_win_rates={s: 0.40 for s in ("BTC", "ETH", "ADA", "AVAX", "DOGE", "UNI", "XLM", "RUNE")},
            in_coverage_count=8,
            halt=False,
        )
        assert result["verdict"] == "A_INTERVENTION_HARMFUL"
        assert "h_c4" in result["halt_trigger"].lower()


# ---------------------------------------------------------------------------
# Phase 4 verdict (§4.3 conjunctive over Windows B + C)
# ---------------------------------------------------------------------------


class TestPhase4VerdictLogic:
    def test_phase4_pass_strong_both_b_and_c_with_strong_sensitivity(self):
        """B PASS ∧ C PASS ∧ sensitivity 3-4/4 in both → PHASE_4_A_PASS_STRONG."""
        window_summaries = {
            "B": _phase4_window_pass_summary("B", n_pass=7, n_in_coverage=8),  # 7/8 ≥75%
            "C": _phase4_window_pass_summary("C", n_pass=8, n_in_coverage=9),  # 8/9 ≥75%
        }
        sensitivity_per_window = {
            "B": {"n_pass_out_of_4": 4, "n_available_out_of_4": 4},
            "C": {"n_pass_out_of_4": 3, "n_available_out_of_4": 4},
        }
        result = classify_phase4_verdict(
            window_summaries=window_summaries,
            sensitivity_per_window=sensitivity_per_window,
            halt=False,
        )
        assert result["verdict"] == "PHASE_4_A_PASS_STRONG"

    def test_phase4_fail_clean_neither_window_passes(self):
        """B FAIL ∧ C FAIL → PHASE_4_A_FAIL_CLEAN."""
        window_summaries = {
            "B": _phase4_window_pass_summary("B", n_pass=3, n_in_coverage=8),
            "C": _phase4_window_pass_summary("C", n_pass=4, n_in_coverage=9),
        }
        sensitivity_per_window = {
            "B": {"n_pass_out_of_4": 0, "n_available_out_of_4": 4},
            "C": {"n_pass_out_of_4": 0, "n_available_out_of_4": 4},
        }
        result = classify_phase4_verdict(
            window_summaries=window_summaries,
            sensitivity_per_window=sensitivity_per_window,
            halt=False,
        )
        assert result["verdict"] == "PHASE_4_A_FAIL_CLEAN"


# ---------------------------------------------------------------------------
# Asymmetric halt-guard (§4.6 mirror)
# ---------------------------------------------------------------------------


class TestAsymmetricHaltGuard:
    def test_favorable_verdict_overridden_when_halt_partial(self):
        """Favorable naive verdict + halt + partial windows → *_INSUFFICIENT_DATA."""
        # Phase 3 favorable (PHASE_3_A_PASS) overridden under halt
        guarded = apply_asymmetric_halt_guard(
            naive_verdict="PHASE_3_A_PASS",
            halt=True,
            n_windows_available=1,
            n_windows_expected=1,  # Phase 3 = 1 window (A)
            phase=3,
        )
        # For Phase 3, halt is what triggers override even with 1/1 window
        assert guarded == "PHASE_3_INSUFFICIENT_DATA"

    def test_negative_verdict_preserved_under_halt(self):
        """Negative naive verdicts (FAIL_*) preserved under halt — §4.6 + R3."""
        # FAIL_CLEAN preserved
        guarded = apply_asymmetric_halt_guard(
            naive_verdict="PHASE_4_A_FAIL_CLEAN",
            halt=True,
            n_windows_available=1,
            n_windows_expected=2,
            phase=4,
        )
        assert guarded == "PHASE_4_A_FAIL_CLEAN"

        # A_INTERVENTION_INSUFFICIENT preserved
        guarded2 = apply_asymmetric_halt_guard(
            naive_verdict="A_INTERVENTION_INSUFFICIENT",
            halt=True,
            n_windows_available=1,
            n_windows_expected=1,
            phase=3,
        )
        assert guarded2 == "A_INTERVENTION_INSUFFICIENT"


# ---------------------------------------------------------------------------
# Threshold constants exported (sanity)
# ---------------------------------------------------------------------------


class TestThresholdConstants:
    def test_thresholds_match_q_pr_locks(self):
        """Constants in module must equal Q-PR locked values."""
        assert T_COUNTS_FIRING == 5  # Q-PR1
        assert P50_MAGNITUDE_MAX == 2.0  # Q-PR2
        assert WIN_RATE_FLOOR == 0.30  # Q-PR3
        assert WIN_RATE_DEGRADATION == 0.50  # Q-PR4
        assert AGGREGATE_MATCH_FRACTION == 0.75  # heredar #338 ≥75%


# ---------------------------------------------------------------------------
# Module 3: signal_calibration_diagnostic.py — Phase 2 runner unit tests
# ---------------------------------------------------------------------------


class TestDiagnosticRunner:
    def test_phase2_jobs_window_a_only_8_cells(self):
        """build_phase2_jobs produces exactly 8 cells, all Window A, vol_target=30%."""
        from tools.signal_calibration_diagnostic import build_phase2_jobs

        jobs = build_phase2_jobs(app_config_path="fake/path.json")
        assert len(jobs) == 8
        assert all(j["sub_window"] == "A" for j in jobs)
        assert all(j["vol_target"] == 0.30 for j in jobs)

    def test_phase2_jobs_exclude_pendle_jup(self):
        """Pre-reg §5.1 — PENDLE + JUP NOT in Window A coverage (warmup-fail)."""
        from tools.signal_calibration_diagnostic import build_phase2_jobs

        jobs = build_phase2_jobs(app_config_path="fake/path.json")
        symbols = {j["symbol"] for j in jobs}
        assert "PENDLEUSDT" not in symbols
        assert "JUPUSDT" not in symbols
        # Sanity: BTC + ETH ARE in coverage
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols

    def test_phase2_halt_fires_at_6_of_8_bankrupt(self):
        """Pre-reg §10.4 — ≥6/8 bankrupt → halt=True."""
        from tools.signal_calibration_diagnostic import check_phase2_halt

        results = [
            {"symbol": s, "sub_window": "A", "bankruptcy_count": 1, "n_trades": 0, "net_pnl_usd": -10000}
            for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT")
        ] + [
            {"symbol": s, "sub_window": "A", "bankruptcy_count": 0, "n_trades": 5, "net_pnl_usd": 100}
            for s in ("XLMUSDT", "RUNEUSDT")
        ]
        halt_diag = check_phase2_halt(results)
        assert halt_diag["halt"] is True
        assert halt_diag["n_symbols_bankrupt"] == 6

    def test_phase2_halt_does_not_fire_at_5_of_8(self):
        """5 < 6 threshold → halt=False."""
        from tools.signal_calibration_diagnostic import check_phase2_halt

        results = [
            {"symbol": s, "sub_window": "A", "bankruptcy_count": 1, "n_trades": 0, "net_pnl_usd": -10000}
            for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT")
        ] + [
            {"symbol": s, "sub_window": "A", "bankruptcy_count": 0, "n_trades": 5, "net_pnl_usd": 100}
            for s in ("UNIUSDT", "XLMUSDT", "RUNEUSDT")
        ]
        halt_diag = check_phase2_halt(results)
        assert halt_diag["halt"] is False
        assert halt_diag["n_symbols_bankrupt"] == 5

    def test_aggregate_observability_cells_passes_through(self):
        """aggregate_observability_cells extracts per_lookback + sum_distribution.

        Used to feed classify_phase2_verdict downstream.
        """
        from tools.signal_calibration_diagnostic import aggregate_observability_cells

        results = [
            {
                "symbol": "BTCUSDT",
                "observability": {
                    "per_lookback": {5: {"firing_count": 10}, 10: {"firing_count": 8}},
                    "sum_distribution": {"p50": 1.5},
                },
            },
            {
                "symbol": "ETHUSDT",
                "observability": {
                    "per_lookback": {5: {"firing_count": 12}, 10: {"firing_count": 9}},
                    "sum_distribution": {"p50": 1.8},
                },
            },
            # Cell with no observability (e.g., error path) — should be skipped
            {"symbol": "ADAUSDT", "observability": {}, "error": "fetch failed"},
        ]
        cells = aggregate_observability_cells(results)
        assert len(cells) == 2  # ADA skipped
        assert cells[0]["symbol"] == "BTCUSDT"
        assert cells[0]["sum_distribution"]["p50"] == 1.5
        assert cells[1]["sum_distribution"]["p50"] == 1.8


# ---------------------------------------------------------------------------
# Module 4: signal_calibration_sweep.py — Phase 3 + Phase 4 runner unit tests
# ---------------------------------------------------------------------------


class TestSweepRunner:
    def test_phase3_primary_jobs_8_cells_window_a_a1_vol30(self):
        """Pre-reg §2.5 Phase 3 primary: 8 × Window A × A1 × vol=30%."""
        from tools.signal_calibration_sweep import (
            A1_SUBSET, build_phase3_primary_jobs,
        )

        jobs = build_phase3_primary_jobs(app_config_path="fake/path.json")
        assert len(jobs) == 8
        assert all(j["sub_window"] == "A" for j in jobs)
        assert all(j["vol_target"] == 0.30 for j in jobs)
        assert all(tuple(j["lookbacks_subset"]) == A1_SUBSET for j in jobs)
        # A1 subset is exactly (5, 10, 20) per Q-PR6 lock
        assert A1_SUBSET == (5, 10, 20)

    def test_phase3_sensitivity_jobs_32_cells_4_vol_targets(self):
        """Pre-reg §2.5 Phase 3 sensitivity: 8 × 4 vol_target = 32 cells."""
        from tools.signal_calibration_sweep import (
            SENSITIVITY_VOL_TARGETS, build_phase3_sensitivity_jobs,
        )

        jobs = build_phase3_sensitivity_jobs(app_config_path="fake/path.json")
        assert len(jobs) == 32
        # 4 distinct vol_targets
        assert {j["vol_target"] for j in jobs} == set(SENSITIVITY_VOL_TARGETS)

    def test_phase4_primary_jobs_17_cells_b_plus_c(self):
        """Pre-reg §2.5 Phase 4 primary: 8 in B + 9 in C × A1 × vol=30% = 17 cells."""
        from tools.signal_calibration_sweep import build_phase4_primary_jobs

        jobs = build_phase4_primary_jobs(app_config_path="fake/path.json")
        assert len(jobs) == 17
        b_jobs = [j for j in jobs if j["sub_window"] == "B"]
        c_jobs = [j for j in jobs if j["sub_window"] == "C"]
        assert len(b_jobs) == 8
        assert len(c_jobs) == 9
        # Pre-reg §3 — PENDLE NOT in B (first bar AFTER B end), IS in C
        b_symbols = {j["symbol"] for j in b_jobs}
        c_symbols = {j["symbol"] for j in c_jobs}
        assert "PENDLEUSDT" not in b_symbols
        assert "PENDLEUSDT" in c_symbols

    def test_aggregate_window_summary_counts_primary_passing_cells(self):
        """aggregate_window_summary counts cells satisfying §4.2 PRIMARY conditions."""
        from tools.signal_calibration_sweep import aggregate_window_summary

        cells = [
            # 6 cells PASS in Window B (n_trades=10, no bankruptcy, win_rate=0.40)
            {"symbol": s, "sub_window": "B", "vol_target": 0.30,
             "n_trades": 10, "bankruptcy_count": 0, "win_rate": 0.40}
            for s in ("BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "UNIUSDT")
        ] + [
            # 2 cells FAIL (n_trades < 5)
            {"symbol": s, "sub_window": "B", "vol_target": 0.30,
             "n_trades": 2, "bankruptcy_count": 0, "win_rate": 0.40}
            for s in ("XLMUSDT", "RUNEUSDT")
        ]
        summary = aggregate_window_summary(cells, "B")
        assert summary["n_pass"] == 6
        assert summary["n_in_coverage"] == 8
        # 6/8 = 0.75 → just at threshold

    def test_smoke_job_btc_window_a_a1(self):
        """--smoke produces a single BTC × Window A × A1 × vol=30% job."""
        from tools.signal_calibration_sweep import A1_SUBSET, build_smoke_job

        job = build_smoke_job(app_config_path="fake/path.json")
        assert job["symbol"] == "BTCUSDT"
        assert job["sub_window"] == "A"
        assert job["vol_target"] == 0.30
        assert tuple(job["lookbacks_subset"]) == A1_SUBSET

    def test_normalize_win_rate_percent_to_fraction(self):
        """backtest returns 50.0 for 50%; normalize to 0.50 fraction for verdict layer."""
        from tools.signal_calibration_sweep import _normalize_win_rate_to_fraction

        assert _normalize_win_rate_to_fraction(50.0) == 0.50
        assert _normalize_win_rate_to_fraction(100.0) == 1.0
        assert _normalize_win_rate_to_fraction(0.0) == 0.0
        # Already-fraction values are passed through (defensive, won't surface
        # with current backtest but protects against future contract change)
        assert _normalize_win_rate_to_fraction(0.40) == 0.40
        assert _normalize_win_rate_to_fraction(0.30) == 0.30
        # Boundary case docstring-locked: 1.0 exact is ambiguous (could be 1% or 100%).
        # Strict `>` operator treats 1.0 as fraction (preserved as-is). Locking this
        # behavior prevents future refactors from silently flipping the semantic.
        assert _normalize_win_rate_to_fraction(1.0) == 1.0
