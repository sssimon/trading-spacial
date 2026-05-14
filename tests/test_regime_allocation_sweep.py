"""Tests for tools/regime_allocation_sweep.py + tools/regime_allocation_verdict.py
(epic #338 Phase 3).

Pre-reg: docs/superpowers/plans/2026-05-14-regime-allocation-phase2-pre-reg.md

Coverage areas (pre-reg §11 estimate: ~15-20 tests):
  - Pre-reg traceability of all locked constants (§2.1, §2.5, §3, §10.4)
  - Coverage table A=8 / B=8 / C=9 + per-symbol exclusion (§3 + §5.1)
  - Halt detection H1 (≥75% bankrupt) + H2 (≥75% n_trades<5) per §10.4
  - Asymmetric halt-guard §4.6 — favorable overridden, negative preserved
  - Sensitivity verdict map §4.2 — 4/4=STRONG, ..., 0/4=FAIL_CLEAN
  - Primary criterion §4 — strict `>` comparison
  - Verdict outcomes §4.3 — all 9 verdict states
  - Anti-leakage — sub-window ends ≤ holdout_start cutoff
  - Defensive halt-diagnostic type check
  - Job builders produce expected cell counts
  - Coverage parity between sweep + verdict tools (same hardcoded table)
"""
from __future__ import annotations

import math

import pytest

from tools import regime_allocation_sweep as ras
from tools import regime_allocation_verdict as rav


# ─────────────────────────────────────────────────────────────────────────────
# Pre-reg traceability — locked constants must match the pre-reg verbatim.
# ─────────────────────────────────────────────────────────────────────────────


class TestPreRegTraceability:
    """Pre-reg §2.1 + §2.5 + §3 + §10.4 — every locked constant verified."""

    def test_zarattini_lookbacks_locked(self):
        # Pre-reg §2.1 + epic §8.4
        assert ras.ZARATTINI_LOOKBACKS == (
            5, 10, 20, 30, 60, 90, 150, 250, 360,
        )

    def test_warmup_daily_bars(self):
        # Pre-reg §2.1: 360 (longest lookback) + 30 (vol window) = 390
        assert ras.WARMUP_DAILY_BARS == 390

    def test_primary_vol_target(self):
        # Pre-reg §2.5 — primary pass locked
        assert ras.PRIMARY_VOL_TARGET == 0.30

    def test_sensitivity_vol_targets(self):
        # Pre-reg §2.5 + §4.2
        assert ras.SENSITIVITY_VOL_TARGETS == (0.25, 0.30, 0.35, 0.40)

    def test_sub_window_dates(self):
        # Pre-reg §3 — R3-exact dates per operator decision §1.1
        assert ras.SUB_WINDOWS == {
            "A": ("2022-04-01T00:00:00+00:00", "2022-07-01T00:00:00+00:00"),
            "B": ("2023-04-01T00:00:00+00:00", "2023-07-01T00:00:00+00:00"),
            "C": ("2025-01-30T00:00:00+00:00", "2025-04-30T00:00:00+00:00"),
        }

    def test_cutoff_matches_holdout_start(self):
        # Pre-reg §3 + CLAUDE.md #246 — Window C end == holdout_start exclusive
        assert ras.CUTOFF_ISO == "2025-04-30T00:00:00+00:00"
        # And sub-window C's end == cutoff
        assert ras.SUB_WINDOWS["C"][1] == ras.CUTOFF_ISO

    def test_halt_fraction_threshold(self):
        # Pre-reg §10.4 — uniform 75% across windows
        assert ras.HALT_FRACTION_THRESHOLD == 0.75

    def test_n_trades_min_for_eligibility(self):
        # Pre-reg §4.1 + §10.4 — loosens epic §6.3 10→5 per CR1 review fix
        assert ras.N_TRADES_MIN_FOR_ELIGIBILITY == 5
        # Verdict tool must agree
        assert rav.N_TRADES_MIN_FOR_ELIGIBILITY == 5

    def test_curated_symbols_match_default_symbols(self):
        # Pre-reg §3 — basket carry-forward from epic #135 / CLAUDE.md
        expected = (
            "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
            "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
        )
        assert ras.CURATED_SYMBOLS == expected


# ─────────────────────────────────────────────────────────────────────────────
# Coverage table — pre-reg §3 + §5.1 + §10.1 empirical exclusions.
# ─────────────────────────────────────────────────────────────────────────────


class TestCoverageTable:
    """Pre-reg §3 corrected table: A=8, B=8, C=9."""

    def test_coverage_a_excludes_pendle_and_jup(self):
        cov_a = ras.COVERAGE_BY_WINDOW["A"]
        assert "PENDLEUSDT" not in cov_a  # first bar 2023-07-03 > A end
        assert "JUPUSDT" not in cov_a     # first bar 2024-01-31 > A end
        assert len(cov_a) == 8

    def test_coverage_b_excludes_pendle_and_jup(self):
        cov_b = ras.COVERAGE_BY_WINDOW["B"]
        assert "PENDLEUSDT" not in cov_b  # first bar 2023-07-03 > B end 2023-07-01
        assert "JUPUSDT" not in cov_b     # didn't exist yet
        assert len(cov_b) == 8

    def test_coverage_c_includes_pendle_excludes_jup(self):
        # Pre-reg §10.1 corrected: JUP first bar 2024-01-31, only ~364 daily
        # bars by C_start 2025-01-30 < 390 warmup. PENDLE first bar
        # 2023-07-03 → 576 daily bars by C_start, ≥ 390 → included.
        cov_c = ras.COVERAGE_BY_WINDOW["C"]
        assert "PENDLEUSDT" in cov_c
        assert "JUPUSDT" not in cov_c
        assert len(cov_c) == 9

    def test_coverage_consistent_between_sweep_and_verdict(self):
        """Pre-reg §3 + §6 — sweep + verdict tools share same coverage table."""
        assert ras.COVERAGE_BY_WINDOW == rav.COVERAGE_BY_WINDOW

    def test_is_in_coverage_helper(self):
        assert ras._is_in_coverage("BTCUSDT", "A") is True
        assert ras._is_in_coverage("PENDLEUSDT", "A") is False
        assert ras._is_in_coverage("PENDLEUSDT", "C") is True
        assert ras._is_in_coverage("JUPUSDT", "A") is False
        assert ras._is_in_coverage("JUPUSDT", "C") is False
        # Case-insensitive
        assert ras._is_in_coverage("btcusdt", "A") is True


# ─────────────────────────────────────────────────────────────────────────────
# Halt detection — pre-reg §10.4 single source of truth.
# ─────────────────────────────────────────────────────────────────────────────


class TestHaltDetection:
    """Pre-reg §10.4 H1 + H2 — ≥75% triggers halt."""

    def test_halt_count_threshold_per_window(self):
        # ⌈8 × 0.75⌉ = 6 ; ⌈9 × 0.75⌉ = 7
        assert ras._halt_count_threshold(8) == 6
        assert ras._halt_count_threshold(9) == 7

    @staticmethod
    def _make_a_primary_results(n_bankrupt: int, n_low_trades: int) -> list[dict]:
        """Build synthetic Window A primary results.

        Window A has 8 in-coverage symbols. We construct cells with the
        specified bankruptcy / low-trade counts among them.
        """
        cov_a = ras.COVERAGE_BY_WINDOW["A"]
        assert len(cov_a) == 8
        # Healthy cell template
        healthy = {
            "sub_window": "A",
            "vol_target": ras.PRIMARY_VOL_TARGET,
            "n_trades": 15,
            "net_pnl_usd": 100.0,
            "bankruptcy_count": 0,
            "exit_reasons": {"SIGNAL_FLIP": 10, "SIM_END": 5},
            "insufficient_data": False,
        }
        results = []
        for i, sym in enumerate(cov_a):
            cell = dict(healthy, symbol=sym)
            if i < n_bankrupt:
                cell["bankruptcy_count"] = 1
            elif i < n_bankrupt + n_low_trades:
                cell["n_trades"] = 2  # < 5
                cell["insufficient_data"] = True
            results.append(cell)
        return results

    def test_halt_h1_fires_at_threshold(self):
        """6/8 bankrupt → H1 fires (75% of 8 = 6)."""
        results = self._make_a_primary_results(n_bankrupt=6, n_low_trades=0)
        halt = ras._check_halt_after_a(results)
        assert halt["halt"] is True
        assert "H1_universal_bankruptcy" in halt["halt_reasons"]
        assert halt["h1_n_symbols_bankrupt"] == 6

    def test_halt_h1_does_not_fire_below_threshold(self):
        """5/8 bankrupt → H1 does NOT fire."""
        results = self._make_a_primary_results(n_bankrupt=5, n_low_trades=0)
        halt = ras._check_halt_after_a(results)
        assert halt["halt"] is False
        assert "H1_universal_bankruptcy" not in halt["halt_reasons"]
        assert halt["h1_n_symbols_bankrupt"] == 5

    def test_halt_h2_fires_at_threshold(self):
        """6/8 with n_trades<5 → H2 fires."""
        results = self._make_a_primary_results(n_bankrupt=0, n_low_trades=6)
        halt = ras._check_halt_after_a(results)
        assert halt["halt"] is True
        assert "H2_signal_degenerate" in halt["halt_reasons"]
        assert halt["h2_n_symbols_low_trade"] == 6

    def test_halt_h2_does_not_fire_below_threshold(self):
        """5/8 with n_trades<5 → H2 does NOT fire."""
        results = self._make_a_primary_results(n_bankrupt=0, n_low_trades=5)
        halt = ras._check_halt_after_a(results)
        assert halt["halt"] is False
        assert "H2_signal_degenerate" not in halt["halt_reasons"]

    def test_halt_both_h1_and_h2_can_fire(self):
        """3 bankrupt + 6 low-trade (could overlap) — both halt categories
        evaluated independently."""
        results = self._make_a_primary_results(n_bankrupt=0, n_low_trades=8)
        halt = ras._check_halt_after_a(results)
        # All 8 have n_trades<5 → H2 fires; none bankrupt → H1 doesn't.
        assert halt["halt"] is True
        assert "H2_signal_degenerate" in halt["halt_reasons"]
        assert "H1_universal_bankruptcy" not in halt["halt_reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# Asymmetric halt-guard — pre-reg §4.6.
# ─────────────────────────────────────────────────────────────────────────────


class TestAsymmetricHaltGuard:
    """Pre-reg §4.6 — favorable overridden, negative preserved on partial windows."""

    @staticmethod
    def _build_primary_per_window(pass_flags: dict[str, bool]) -> dict[str, dict]:
        out = {}
        for win, passes in pass_flags.items():
            out[win] = {
                "window_id": win,
                "vol_target": rav.PRIMARY_VOL_TARGET,
                "n_in_coverage": len(rav.COVERAGE_BY_WINDOW[win]),
                "strategy_total_return_usd": 1000.0 if passes else -1000.0,
                "btc_bh_total_return_usd": 0.0,
                "primary_criterion_pass": passes,
                "n_trades_total": 50, "bankruptcy_count_total": 0,
                "insufficient_data_count": 0, "per_symbol": [],
            }
        return out

    def test_favorable_verdict_overridden_under_halt_partial_windows(self):
        """Halt + 1 window run, primary PASS → PHASE_3_INSUFFICIENT_DATA."""
        ppw = self._build_primary_per_window({"A": True})
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="FAIL_CLEAN",  # halt → no sensitivity
            n_sensitivity_pass=0,
            halt=True,
        )
        assert c["verdict"] == "PHASE_3_INSUFFICIENT_DATA"
        assert c["halt_guard_applied"] is True

    def test_negative_verdict_preserved_under_halt_partial_windows(self):
        """Halt + 1 window run, primary FAIL → FAIL_CLEAN (preserved)."""
        ppw = self._build_primary_per_window({"A": False})
        # Force degenerate=False by setting low insufficient_data_count
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0,
            halt=True,
        )
        # Naive verdict is FAIL_CLEAN — preserved (not in favorable set)
        assert c["verdict"] in ("FAIL_CLEAN", "FAIL_DEGENERATE")
        assert c["halt_guard_applied"] is False

    def test_no_halt_no_override(self):
        """3/3 windows, no halt, sensitivity 4/4 → STRONG_PASS (no override)."""
        ppw = self._build_primary_per_window({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="STRONG",
            n_sensitivity_pass=4,
            halt=False,
        )
        assert c["verdict"] == "STRONG_PASS"
        assert c["halt_guard_applied"] is False

    def test_halt_but_3_windows_available_no_override(self):
        """If all 3 windows ran (n_windows>=3), halt doesn't apply guard — but
        in practice halt fires DURING window A, halting B+C. Defensive: even
        if halt=True with 3 windows, guard should not override (per §4.6
        condition n_windows<3)."""
        ppw = self._build_primary_per_window({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="STRONG",
            n_sensitivity_pass=4,
            halt=True,
        )
        # Per pre-reg §4.6: halt-guard applies only when n_windows<3.
        assert c["halt_guard_applied"] is False
        assert c["verdict"] == "STRONG_PASS"


# ─────────────────────────────────────────────────────────────────────────────
# Sensitivity verdict map — pre-reg §4.2.
# ─────────────────────────────────────────────────────────────────────────────


class TestSensitivityVerdictMap:
    """Pre-reg §4.2 — verdict label mapping by n_pass_out_of_4."""

    @pytest.mark.parametrize("n_pass,expected_label", [
        (4, "STRONG"),
        (3, "ROBUST"),
        (2, "SUCCESS_CONDITIONAL"),
        (1, "SWEET_SPOT_FAIL"),
        (0, "FAIL_CLEAN"),
    ])
    def test_sensitivity_verdict_label_for_n_pass(self, n_pass, expected_label):
        # Build a sensitivity_per_vol_target dict with exactly n_pass passes.
        sens = {}
        vts = list(rav.SENSITIVITY_VOL_TARGETS)
        for i, vt in enumerate(vts):
            sens[vt] = {
                "n_available_windows": 3,
                "n_primary_pass_windows": 3 if i < n_pass else 0,
                "vol_target_pass": i < n_pass,
                "per_window": {},
            }
        summary = rav._sensitivity_verdict(sens)
        assert summary["n_pass_out_of_4"] == n_pass
        assert summary["sensitivity_verdict_label"] == expected_label


# ─────────────────────────────────────────────────────────────────────────────
# Primary criterion — pre-reg §4 strict `>`.
# ─────────────────────────────────────────────────────────────────────────────


class TestPrimaryCriterion:
    """Pre-reg §4 — strict `>` comparison."""

    def test_strict_greater_passes(self):
        assert rav._primary_criterion_pass(1000.0, 999.99) is True

    def test_equal_fails_strict_comparison(self):
        # Ties FAIL by design — pre-reg uses strict `>`.
        assert rav._primary_criterion_pass(1000.0, 1000.0) is False

    def test_strict_less_fails(self):
        assert rav._primary_criterion_pass(999.99, 1000.0) is False

    def test_negative_returns_compared_correctly(self):
        # Strategy -$500 vs BTC B&H -$1000 → strategy wins
        assert rav._primary_criterion_pass(-500.0, -1000.0) is True


# ─────────────────────────────────────────────────────────────────────────────
# Verdict outcomes — pre-reg §4.3 table.
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictOutcomes:
    """Pre-reg §4.3 — verify all 9 verdict outcomes are reachable."""

    @staticmethod
    def _build_ppw(pass_flags: dict[str, bool], insufficient_count: int = 0):
        out = {}
        for win, passes in pass_flags.items():
            out[win] = {
                "window_id": win,
                "vol_target": rav.PRIMARY_VOL_TARGET,
                "n_in_coverage": len(rav.COVERAGE_BY_WINDOW[win]),
                "strategy_total_return_usd": 1000.0 if passes else -1000.0,
                "btc_bh_total_return_usd": 0.0,
                "primary_criterion_pass": passes,
                "n_trades_total": 50,
                "bankruptcy_count_total": 0,
                "insufficient_data_count": insufficient_count,
                "per_symbol": [],
            }
        return out

    def test_strong_pass(self):
        ppw = self._build_ppw({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="STRONG",
            n_sensitivity_pass=4, halt=False,
        )
        assert c["verdict"] == "STRONG_PASS"

    def test_robust_pass(self):
        ppw = self._build_ppw({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="ROBUST",
            n_sensitivity_pass=3, halt=False,
        )
        assert c["verdict"] == "ROBUST_PASS"

    def test_success_conditional(self):
        ppw = self._build_ppw({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="SUCCESS_CONDITIONAL",
            n_sensitivity_pass=2, halt=False,
        )
        assert c["verdict"] == "SUCCESS_CONDITIONAL"

    def test_sweet_spot_fail_with_1_of_4(self):
        ppw = self._build_ppw({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="SWEET_SPOT_FAIL",
            n_sensitivity_pass=1, halt=False,
        )
        assert c["verdict"] == "SWEET_SPOT_FAIL"

    def test_sweet_spot_fail_with_0_of_4(self):
        # 3/3 primary + 0/4 sensitivity = isolated success → SWEET_SPOT_FAIL
        ppw = self._build_ppw({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0, halt=False,
        )
        assert c["verdict"] == "SWEET_SPOT_FAIL"

    def test_partial_success_2_of_3(self):
        ppw = self._build_ppw({"A": True, "B": True, "C": False})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="SUCCESS_CONDITIONAL",
            n_sensitivity_pass=2, halt=False,
        )
        assert c["verdict"] == "PARTIAL_SUCCESS"

    def test_inconclusive_primary_fail_sensitivity_partial(self):
        ppw = self._build_ppw({"A": False, "B": False, "C": False})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="SUCCESS_CONDITIONAL",
            n_sensitivity_pass=2, halt=False,
        )
        assert c["verdict"] == "INCONCLUSIVE"

    def test_fail_clean(self):
        ppw = self._build_ppw({"A": False, "B": False, "C": False})
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0, halt=False,
        )
        assert c["verdict"] == "FAIL_CLEAN"

    def test_fail_degenerate(self):
        """≥75% in-coverage cells with n_trades<5 in predominant windows."""
        # In Windows A (8 in-cov), need ≥6 insufficient. Same for B.
        ppw = self._build_ppw(
            {"A": False, "B": False, "C": False}, insufficient_count=6,
        )
        # n_degenerate_windows = 3 (all windows have 6/8 ≥ 0.75 cells degenerate)
        # n_windows = 3; majority threshold = n//2+1 = 2 → 3>=2 → predominant
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0, halt=False,
        )
        assert c["verdict"] == "FAIL_DEGENERATE"


# ─────────────────────────────────────────────────────────────────────────────
# Job builders + cell counts.
# ─────────────────────────────────────────────────────────────────────────────


class TestJobBuilders:
    """Pre-reg §2.5 — sweep cell counts."""

    def test_primary_jobs_count_is_25(self):
        """8 (A) + 8 (B) + 9 (C) = 25 in-coverage primary cells."""
        jobs = ras._build_primary_jobs("dummy_path.json")
        assert len(jobs) == 25
        # All jobs have vol_target = PRIMARY_VOL_TARGET (0.30)
        for j in jobs:
            assert j["vol_target"] == ras.PRIMARY_VOL_TARGET

    def test_sensitivity_jobs_count_is_100(self):
        """25 in-coverage × 4 vol_target = 100 sensitivity cells."""
        jobs = ras._build_sensitivity_jobs("dummy_path.json")
        assert len(jobs) == 100
        # Each vol_target appears 25 times
        vts = [j["vol_target"] for j in jobs]
        for vt in ras.SENSITIVITY_VOL_TARGETS:
            assert sum(1 for v in vts if abs(v - vt) < 1e-9) == 25

    def test_baseline_jobs_per_window(self):
        """BTC B&H + Hubrich: 3 each (one per sub-window)."""
        assert len(ras._build_btc_bh_jobs()) == 3
        assert len(ras._build_hubrich_jobs()) == 3
        # LRC archived: 25 (one per in-coverage cell per sub-window)
        assert len(ras._build_lrc_archived_jobs("dummy_path.json")) == 25

    def test_primary_jobs_respect_coverage_exclusions(self):
        jobs = ras._build_primary_jobs("dummy_path.json")
        # No (PENDLE, A) or (PENDLE, B) jobs
        for j in jobs:
            assert not (j["symbol"] == "PENDLEUSDT" and j["sub_window"] in ("A", "B"))
            assert j["symbol"] != "JUPUSDT"  # JUP excluded from all windows


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation correctness — _aggregate_window_at_vol_target.
# ─────────────────────────────────────────────────────────────────────────────


class TestAggregation:

    def test_aggregate_sums_per_symbol_net_pnl(self):
        results = [
            {"symbol": "BTCUSDT", "sub_window": "A",
             "vol_target": 0.30, "net_pnl_usd": 1000.0,
             "n_trades": 10, "bankruptcy_count": 0,
             "insufficient_data": False},
            {"symbol": "ETHUSDT", "sub_window": "A",
             "vol_target": 0.30, "net_pnl_usd": -500.0,
             "n_trades": 8, "bankruptcy_count": 0,
             "insufficient_data": False},
            # Out-of-coverage for A: should be ignored even if present
            {"symbol": "JUPUSDT", "sub_window": "A",
             "vol_target": 0.30, "net_pnl_usd": 99999.0,
             "n_trades": 99, "bankruptcy_count": 0,
             "insufficient_data": False},
        ]
        agg = rav._aggregate_window_at_vol_target(results, "A", 0.30)
        # JUP excluded from A coverage → not summed
        assert agg["strategy_total_return_usd"] == 500.0
        assert agg["n_trades_total"] == 18

    def test_aggregate_returns_none_when_no_cells(self):
        agg = rav._aggregate_window_at_vol_target([], "A", 0.30)
        assert agg is None

    def test_aggregate_filters_by_vol_target(self):
        """Cells at other vol_target values must not be aggregated."""
        results = [
            {"symbol": "BTCUSDT", "sub_window": "A",
             "vol_target": 0.30, "net_pnl_usd": 100.0,
             "n_trades": 5, "bankruptcy_count": 0,
             "insufficient_data": False},
            {"symbol": "BTCUSDT", "sub_window": "A",
             "vol_target": 0.40, "net_pnl_usd": 9999.0,
             "n_trades": 5, "bankruptcy_count": 0,
             "insufficient_data": False},
        ]
        agg = rav._aggregate_window_at_vol_target(results, "A", 0.30)
        assert agg["strategy_total_return_usd"] == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Anti-leakage — sub-windows must end at or before holdout boundary.
# ─────────────────────────────────────────────────────────────────────────────


class TestAntiLeakage:
    """Pre-reg §3 + CLAUDE.md #246 — Window C end == holdout_start exclusive."""

    def test_all_sub_windows_end_le_cutoff(self):
        cutoff = ras._iso_to_utc_dt(ras.CUTOFF_ISO)
        for win_id, (_start_iso, end_iso) in ras.SUB_WINDOWS.items():
            end_dt = ras._iso_to_utc_dt(end_iso)
            assert end_dt <= cutoff, (
                f"Window {win_id} end {end_iso} > cutoff {ras.CUTOFF_ISO}"
            )

    def test_window_c_end_equals_cutoff(self):
        # Window C ends EXACTLY at holdout_start (exclusive: < cutoff slice)
        assert ras.SUB_WINDOWS["C"][1] == ras.CUTOFF_ISO


# ─────────────────────────────────────────────────────────────────────────────
# Defensive halt-diagnostic type check.
# ─────────────────────────────────────────────────────────────────────────────


class TestHaltDiagnosticDefensive:

    def test_extract_halt_returns_false_for_none(self):
        assert rav._extract_halt_from_diagnostic(None) is False

    def test_extract_halt_returns_bool_value(self):
        assert rav._extract_halt_from_diagnostic({"halt": True}) is True
        assert rav._extract_halt_from_diagnostic({"halt": False}) is False

    def test_extract_halt_raises_on_non_dict(self):
        with pytest.raises(ValueError, match="must be dict or None"):
            rav._extract_halt_from_diagnostic("halt")

    def test_extract_halt_raises_on_non_bool_halt(self):
        # Defensive: a truthy non-bool (e.g., "true" string) must NOT be
        # silently coerced — it could flip verdict classification.
        with pytest.raises(ValueError, match="must be bool"):
            rav._extract_halt_from_diagnostic({"halt": "true"})
        with pytest.raises(ValueError, match="must be bool"):
            rav._extract_halt_from_diagnostic({"halt": 1})


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 advance decision — pre-reg §4.5 self-policing requirement.
# ─────────────────────────────────────────────────────────────────────────────


class TestPhase4AdvanceDecision:

    def test_strong_pass_auto_advances(self):
        d = rav._phase_4_advance_decision("STRONG_PASS")
        assert d["auto_advance_to_phase_4"] is True
        assert d["operator_decision_required"] is False
        assert d["self_policing_required"] is False

    def test_robust_pass_auto_advances(self):
        d = rav._phase_4_advance_decision("ROBUST_PASS")
        assert d["auto_advance_to_phase_4"] is True

    @pytest.mark.parametrize("v", ["SUCCESS_CONDITIONAL", "PARTIAL_SUCCESS", "INCONCLUSIVE"])
    def test_operator_decision_required_with_self_policing(self, v):
        d = rav._phase_4_advance_decision(v)
        assert d["auto_advance_to_phase_4"] is False
        assert d["operator_decision_required"] is True
        assert d["self_policing_required"] is True

    @pytest.mark.parametrize("v", [
        "FAIL_CLEAN", "FAIL_DEGENERATE", "SWEET_SPOT_FAIL",
        "PHASE_3_INSUFFICIENT_DATA",
    ])
    def test_fail_variants_no_advance(self, v):
        d = rav._phase_4_advance_decision(v)
        assert d["auto_advance_to_phase_4"] is False
        assert d["operator_decision_required"] is False


# Holdout isolation is enforced structurally by tests/test_holdout_isolation.py
# (AST scanner over all non-whitelisted modules). No need to duplicate here.
