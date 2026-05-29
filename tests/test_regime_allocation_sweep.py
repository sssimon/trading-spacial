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

import inspect
import json
import math
from pathlib import Path
from types import SimpleNamespace

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

    def test_partial_windows_without_halt_returns_insufficient_data(self):
        """BLOCK #1 review fix 2026-05-14 — defensive gate.

        Pre-reg §4 + §4.6 cover only halt=True under partial windows. The
        opposite case (n_windows<3 + halt=False) is an invalid sweep state
        (e.g., operator did --window A standalone, or pipeline crashed
        without writing halt_diagnostic.json). Must default to
        PHASE_3_INSUFFICIENT_DATA, NOT to STRONG_PASS via the partial-window
        favorable branch.

        Previously this case slipped through to naive=STRONG_PASS (per
        line 427-431 of the partial-window branch) and the §4.6 guard
        didn't apply because halt=False.
        """
        ppw = self._build_primary_per_window({"A": True})  # n_windows=1
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0,
            halt=False,  # no halt fired
        )
        assert c["verdict"] == "PHASE_3_INSUFFICIENT_DATA"
        assert (
            c["naive_verdict_before_halt_guard"]
            == "INVALID_STATE_partial_without_halt"
        )
        assert c.get("defensive_gate_fired") == "partial_windows_without_halt"

    def test_partial_windows_without_halt_n_windows_2(self):
        """Defensive gate fires for any n_windows<3 without halt."""
        ppw = self._build_primary_per_window({"A": True, "B": True})  # n=2
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0,
            halt=False,
        )
        assert c["verdict"] == "PHASE_3_INSUFFICIENT_DATA"
        assert c.get("defensive_gate_fired") == "partial_windows_without_halt"

    def test_full_3_windows_no_halt_no_defensive_gate(self):
        """Defensive gate does NOT fire when all 3 windows ran (no halt)."""
        ppw = self._build_primary_per_window({"A": True, "B": True, "C": True})
        c = rav._classify_verdict(
            primary_per_window=ppw,
            sensitivity_label="STRONG",
            n_sensitivity_pass=4,
            halt=False,
        )
        assert c["verdict"] == "STRONG_PASS"
        assert "defensive_gate_fired" not in c


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
        """FAIL_DEGENERATE requires predominance of per-window degenerate flag.

        With insufficient_count=6 across all 3 windows:
          Window A (8 in-cov): 6/8 = 0.75 ≥ 0.75 → degenerate ✓
          Window B (8 in-cov): 6/8 = 0.75 ≥ 0.75 → degenerate ✓
          Window C (9 in-cov): 6/9 = 0.667 < 0.75 → NOT degenerate
        n_degenerate_windows = 2; predominance threshold = max(1, 3//2+1) = 2.
        2 ≥ 2 → predominant → FAIL_DEGENERATE.

        Comment corrected per NIT review fix 2026-05-14: previous comment
        claimed "n_degenerate_windows = 3 (all windows have 6/8 ≥ 0.75)"
        but Window C has 9 in-coverage symbols, so 6/9=0.667<0.75 → not
        degenerate. Test still passed (2 ≥ predominance threshold) but
        for different math than the comment claimed.
        """
        ppw = self._build_ppw(
            {"A": False, "B": False, "C": False}, insufficient_count=6,
        )
        c = rav._classify_verdict(
            primary_per_window=ppw, sensitivity_label="FAIL_CLEAN",
            n_sensitivity_pass=0, halt=False,
        )
        assert c["verdict"] == "FAIL_DEGENERATE"
        # Confirm the actual math (2 of 3 windows degenerate, not 3).
        assert c["n_degenerate_windows"] == 2
        assert c["degenerate_predominant"] is True


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


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK #2 review fix 2026-05-14 — sweep tool refuses --window B/C standalone
# without prior Window A halt evaluation.
# ─────────────────────────────────────────────────────────────────────────────


class TestPartialWindowSequencing:
    """Pre-reg §10.4 + BLOCK #2 — _validate_partial_window_sequencing helper."""

    def test_window_a_always_allowed(self, tmp_path):
        args = SimpleNamespace(window="A")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is True
        assert err is None

    def test_window_all_always_allowed(self, tmp_path):
        args = SimpleNamespace(window="all")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is True

    def test_window_b_refused_without_primary_a(self, tmp_path):
        args = SimpleNamespace(window="B")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is False
        assert "sweep_primary_A.json" in err

    def test_window_c_refused_without_primary_a(self, tmp_path):
        args = SimpleNamespace(window="C")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is False
        assert "sweep_primary_A.json" in err

    def test_window_b_refused_without_halt_diagnostic(self, tmp_path):
        """sweep_primary_A.json exists but halt_diagnostic.json missing."""
        (tmp_path / "sweep_primary_A.json").write_text("[]")
        args = SimpleNamespace(window="B")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is False
        assert "halt_diagnostic.json" in err

    def test_window_b_refused_when_halt_fired(self, tmp_path):
        """halt_diagnostic.halt=True → B/C halted per pre-reg §10.4."""
        (tmp_path / "sweep_primary_A.json").write_text("[]")
        (tmp_path / "halt_diagnostic.json").write_text(json.dumps({
            "halt": True,
            "halt_reasons": ["H1_universal_bankruptcy"],
        }))
        args = SimpleNamespace(window="B")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is False
        assert "halt_diagnostic.json reports halt=True" in err

    def test_window_b_allowed_when_a_complete_no_halt(self, tmp_path):
        """A primary ran, no halt fired → B/C sequencing permitted."""
        (tmp_path / "sweep_primary_A.json").write_text("[]")
        (tmp_path / "halt_diagnostic.json").write_text(json.dumps({
            "halt": False,
            "halt_reasons": [],
        }))
        args = SimpleNamespace(window="B")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is True
        assert err is None

    def test_window_b_refused_with_malformed_halt_diagnostic(self, tmp_path):
        """Defensive: malformed halt_diagnostic.json → refuse rather than guess."""
        (tmp_path / "sweep_primary_A.json").write_text("[]")
        (tmp_path / "halt_diagnostic.json").write_text("not valid json {{{")
        args = SimpleNamespace(window="B")
        ok, err = ras._validate_partial_window_sequencing(args, tmp_path)
        assert ok is False
        assert "Cannot read halt_diagnostic.json" in err


# ─────────────────────────────────────────────────────────────────────────────
# CHANGES_REQUESTED #4 review fix 2026-05-14 — _load_btc_bh_baseline raises
# FileNotFoundError if missing (silent default to FAIL was hiding misconfig).
# ─────────────────────────────────────────────────────────────────────────────


class TestBaselineLoadingErrors:
    """CHANGES_REQUESTED #4 — missing baseline files."""

    def test_btc_bh_baseline_raises_when_missing(self, tmp_path, monkeypatch):
        """Missing baseline_btc_bh_X.json must raise FileNotFoundError loud."""
        monkeypatch.setattr(rav, "OUTPUT_DIR", tmp_path)
        with pytest.raises(FileNotFoundError, match="baseline_btc_bh_A.json"):
            rav._load_btc_bh_baseline("A")

    def test_btc_bh_baseline_loads_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rav, "OUTPUT_DIR", tmp_path)
        payload = {"sub_window": "A", "total_return_usd": 1000.0}
        (tmp_path / "baseline_btc_bh_A.json").write_text(json.dumps(payload))
        result = rav._load_btc_bh_baseline("A")
        assert result == payload

    def test_hubrich_baseline_returns_none_when_missing_with_warning(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Informational baselines (Hubrich, LRC archived) warn but return None
        — they don't affect primary criterion comparison."""
        monkeypatch.setattr(rav, "OUTPUT_DIR", tmp_path)
        result = rav._load_hubrich_baseline("A")
        assert result is None
        captured = capsys.readouterr()
        assert "baseline_hubrich_A.json missing" in captured.err

    def test_lrc_archived_baseline_returns_none_when_missing_with_warning(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setattr(rav, "OUTPUT_DIR", tmp_path)
        result = rav._load_lrc_archived_baseline("A")
        assert result is None
        captured = capsys.readouterr()
        assert "baseline_lrc_archived_A.json" in captured.err


# ─────────────────────────────────────────────────────────────────────────────
# CHANGES_REQUESTED #5 review fix 2026-05-14 — verdict.json schema includes
# operator_override block + schema documentation (pre-reg §4.5 element 4).
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictJsonOperatorOverrideSchema:

    def test_verdict_main_emits_operator_override_field(self):
        """Verify verdict tool's main() builds operator_override into out dict."""
        src = inspect.getsource(rav.main)
        assert '"operator_override": None' in src
        assert '"operator_override_schema"' in src
        # Schema documents the 4 fields per pre-reg §4.5 element 4
        assert "auditor_counter_signoff" in src
        assert "sub_spec_doc" in src
        assert "rationale" in src

    def test_verdict_schema_version_is_2(self):
        """Schema version bumped to 2 after CHANGES_REQUESTED #5."""
        src = inspect.getsource(rav.main)
        assert '"schema_version": 2' in src


# Holdout isolation is enforced structurally by tests/test_holdout_isolation.py


# ─────────────────────────────────────────────────────────────────────────────
# Worker tz-normalization — regression for bug surfaced by smoke test 2026-05-14:
# `_process_regime_allocation_cell` + `_process_lrc_archived_baseline_cell`
# normalize df.index to tz-naive but passed tz-aware sim_start/sim_end into
# simulate_strategy. backtest.py:675 does `df.index >= sim_start` which raises
# TypeError on tz-aware vs tz-naive mismatch. Fix: workers normalize
# sim_start/sim_end alongside df.index.
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkerTzNormalization:
    """Workers must pass tz-naive sim_start/sim_end to simulate_strategy when
    they normalize df.index to tz-naive. Otherwise backtest.py:675 crashes
    with `Invalid comparison between dtype=datetime64[ms] and datetime`."""

    @staticmethod
    def _build_tz_aware_df(n_days: int = 500):
        import pandas as pd
        idx = pd.date_range(
            "2024-01-01", periods=n_days, freq="D", tz="UTC",
        )
        return pd.DataFrame(
            {
                "open": [100.0] * n_days,
                "high": [110.0] * n_days,
                "low": [90.0] * n_days,
                "close": [105.0] * n_days,
                "volume": [1000.0] * n_days,
            },
            index=idx,
        )

    def _install_fakes(self, monkeypatch, captured: dict):
        import backtest
        df_aware = self._build_tz_aware_df(n_days=500)

        def fake_get_cached_data(symbol, tf, start_date=None):
            return df_aware.copy()

        def fake_simulate(**kwargs):
            captured.update(kwargs)
            return [], None

        def fake_calc_metrics(trades, equity_curve):
            return {}

        monkeypatch.setattr(backtest, "simulate_strategy", fake_simulate)
        monkeypatch.setattr(backtest, "get_cached_data", fake_get_cached_data)
        monkeypatch.setattr(backtest, "calculate_metrics", fake_calc_metrics)

    def test_regime_allocation_worker_passes_tz_naive_sim_start(
        self, monkeypatch, tmp_path,
    ):
        captured: dict = {}
        self._install_fakes(monkeypatch, captured)

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text('{"regime_allocation": {"enabled": false}}')

        args = {
            "symbol": "BTCUSDT",
            "sub_window": "C",
            "vol_target": 0.30,
            "sim_start_iso": "2025-01-30T00:00:00+00:00",
            "sim_end_iso": "2025-04-30T00:00:00+00:00",
            "cutoff_iso": "2025-04-30T00:00:00+00:00",
            "app_config_path": str(cfg_path),
        }
        ras._process_regime_allocation_cell(args)

        assert captured, "simulate_strategy was not called"
        # The bug: passing tz-aware sim_start with tz-naive df.index crashes
        # in backtest.py:_simulate_strategy_regime_allocation:675.
        assert captured["sim_start"].tzinfo is None, (
            "sim_start must be tz-naive when worker normalizes df.index "
            "to tz-naive (else backtest.py:675 raises TypeError)."
        )
        assert captured["sim_end"].tzinfo is None, "sim_end must be tz-naive"

    def test_lrc_archived_worker_passes_tz_naive_sim_start(
        self, monkeypatch, tmp_path,
    ):
        captured: dict = {}
        self._install_fakes(monkeypatch, captured)

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text('{"regime_allocation": {"enabled": false}}')

        args = {
            "symbol": "BTCUSDT",
            "sub_window": "C",
            "sim_start_iso": "2025-01-30T00:00:00+00:00",
            "sim_end_iso": "2025-04-30T00:00:00+00:00",
            "cutoff_iso": "2025-04-30T00:00:00+00:00",
            "app_config_path": str(cfg_path),
        }
        ras._process_lrc_archived_baseline_cell(args)

        assert captured, "simulate_strategy was not called"
        assert captured["sim_start"].tzinfo is None, (
            "LRC archived worker must pass tz-naive sim_start to match "
            "the tz-naive df.index normalization."
        )
        assert captured["sim_end"].tzinfo is None, "sim_end must be tz-naive"


# ─────────────────────────────────────────────────────────────────────────────
# Worker retry wrapper — regression for cache-fetch transients surfaced
# during Phase 3 sesión 2 (2026-05-14): Binance TCP RST (Windows 10054) +
# Bybit missing historical 5m → AllProvidersFailedError crashed the worker
# pool. _get_cached_data_with_retry absorbs the transient pattern with
# exponential backoff; exhaustion bubbles up as a soft error in the result.
# ─────────────────────────────────────────────────────────────────────────────


class TestWorkerRetryWrapper:
    """Pre-reg §11 R3 risk — _get_cached_data_with_retry verified end-to-end."""

    def _install_apfe(self, monkeypatch):
        """Install a fake AllProvidersFailedError-like exception type into
        the providers module (so the helper's import-inside-function picks
        it up correctly when caching is bypassed)."""
        import data.providers.base as pbase
        return pbase.AllProvidersFailedError

    def test_retry_helper_succeeds_after_transient_failures(
        self, monkeypatch,
    ):
        import backtest
        import pandas as pd

        AllProvidersFailedError = self._install_apfe(monkeypatch)
        call_counter = {"n": 0}

        def flaky_get_cached_data(symbol, timeframe, start_date=None):
            call_counter["n"] += 1
            if call_counter["n"] < 3:
                raise AllProvidersFailedError(
                    f"All providers failed for {symbol} {timeframe} "
                    f"(simulated attempt {call_counter['n']})"
                )
            return pd.DataFrame(
                {
                    "open": [100.0],
                    "high": [110.0],
                    "low": [90.0],
                    "close": [105.0],
                    "volume": [1000.0],
                },
                index=pd.date_range("2025-01-30", periods=1, freq="D"),
            )

        # No-op sleep so the test runs in <1s instead of paying real backoff.
        monkeypatch.setattr(backtest, "get_cached_data", flaky_get_cached_data)
        import time as time_mod
        monkeypatch.setattr(time_mod, "sleep", lambda _: None)

        df = ras._get_cached_data_with_retry(
            "BTCUSDT", "1h",
            __import__("datetime").datetime(2024, 1, 1),
        )
        assert call_counter["n"] == 3, (
            f"Expected 3 attempts (2 transient + 1 success), got {call_counter['n']}"
        )
        assert not df.empty

    def test_retry_helper_raises_after_exhausting_attempts(
        self, monkeypatch,
    ):
        import backtest

        AllProvidersFailedError = self._install_apfe(monkeypatch)

        def always_fails(symbol, timeframe, start_date=None):
            raise AllProvidersFailedError(
                f"All providers failed for {symbol} {timeframe} (permanent)"
            )

        monkeypatch.setattr(backtest, "get_cached_data", always_fails)
        import time as time_mod
        monkeypatch.setattr(time_mod, "sleep", lambda _: None)

        with pytest.raises(AllProvidersFailedError) as exc_info:
            ras._get_cached_data_with_retry(
                "BTCUSDT", "1h",
                __import__("datetime").datetime(2024, 1, 1),
            )
        assert "permanent" in str(exc_info.value)

    def test_retry_max_attempts_matches_pre_reg_envelope(self):
        """Pre-reg §11 — retry envelope should be small enough to amortize
        transient failures without blowing the compute budget. 6 attempts
        with exponential backoff (3, 6, 12, 24, 48, 96) = ~189s worst case
        per fetch, which is within the §11 LRC baseline estimate (~60s/cell
        × few-retry cells)."""
        assert ras._FETCH_RETRY_MAX_ATTEMPTS == 6
        assert ras._FETCH_RETRY_BASE_BACKOFF_SEC == 3.0


# ─────────────────────────────────────────────────────────────────────────────
# Inf/NaN sentinel coercion — regression for sweep crash surfaced during
# Phase 3 sesión 2 (2026-05-14): calculate_metrics emits profit_factor=inf
# when gross_loss==0 (all trades won, e.g., single-trade winning cell).
# _save_json has allow_nan=False per CHANGES_REQUESTED #5; worker must
# coerce inf before output dict construction.
# ─────────────────────────────────────────────────────────────────────────────


class TestProfitFactorInfCoercion:
    """Pre-reg §6 deliverable + CHANGES_REQUESTED #5 — workers never emit
    math.inf / NaN through to _save_json, which has allow_nan=False."""

    def test_finite_or_passes_through_finite(self):
        assert ras._finite_or(3.14, 99999.0) == 3.14
        assert ras._finite_or(0.0, 99999.0) == 0.0
        assert ras._finite_or(-1.5, 99999.0) == -1.5

    def test_finite_or_coerces_inf(self):
        import math
        assert ras._finite_or(math.inf, 99999.0) == 99999.0
        assert ras._finite_or(-math.inf, 99999.0) == 99999.0

    def test_finite_or_coerces_nan(self):
        import math
        assert ras._finite_or(math.nan, 99999.0) == 99999.0

    def test_profit_factor_inf_sentinel_value(self):
        """Pre-reg downstream readability — 99999 is a recognizable sentinel
        for 'no losses' (all trades won) without colliding with realistic
        profit_factor values which are typically <10."""
        assert ras._PROFIT_FACTOR_INF_SENTINEL == 99999.0

    def test_save_json_disallows_nan(self, tmp_path):
        """Pre-reg discipline — _save_json keeps allow_nan=False so any
        worker emitting inf/NaN crashes loudly (forcing the coerce fix)
        rather than silently emitting non-standard JSON tokens."""
        import math
        target = tmp_path / "test.json"
        with pytest.raises(ValueError, match="Out of range"):
            ras._save_json(target, {"x": math.inf})


# ─────────────────────────────────────────────────────────────────────────────
# Trial registry wiring (#278 Part 1) — parent-side claim/finalize around
# pool.map. The trial WRITE happens in the parent, never in the child worker:
# a child crash leaves a 'pending' row that still counts toward N. pool.map
# preserves order, so results[i] <-> jobs[i] <-> trial_ids[i]. Only the two
# trial-producing cell workers (which run calculate_metrics) register trials;
# arithmetic baselines (btc_bh / hubrich) are gated out.
# ─────────────────────────────────────────────────────────────────────────────


def test_run_jobs_parallel_registers_trials_for_cell_workers(monkeypatch):
    import tools.regime_allocation_sweep as ras

    # Avoid real multiprocessing: run the worker in-process, order-preserving.
    class _FakePool:
        def __init__(self, n):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def map(self, fn, jobs):
            return [fn(j) for j in jobs]

    monkeypatch.setattr(ras, "Pool", _FakePool)

    claims, finals = [], []
    monkeypatch.setattr(ras, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(ras, "finalize_trial",
                        lambda tid, **kw: finals.append((tid, kw)))

    # A trial-producing worker: first job ok, second errored.
    seq = iter([{"sharpe_ratio": 1.0}, {"error": "boom"}])

    def fake_cell(job):
        return next(seq)

    monkeypatch.setattr(ras, "_process_regime_allocation_cell", fake_cell)

    jobs = [
        {"symbol": "BTCUSDT", "sub_window": "A", "vol_target": 0.30},
        {"symbol": "ETHUSDT", "sub_window": "A", "vol_target": 0.30},
    ]
    ras._run_jobs_parallel(jobs, workers=1, label="test", worker_fn=fake_cell)

    assert len(claims) == 2
    assert all(c["source"] == "regime_allocation_sweep" for c in claims)
    assert [kw["status"] for _, kw in finals] == ["ok", "failed"]


def test_run_jobs_parallel_skips_trials_for_non_cell_workers(monkeypatch):
    import tools.regime_allocation_sweep as ras

    class _FakePool:
        def __init__(self, n): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, fn, jobs): return [fn(j) for j in jobs]

    monkeypatch.setattr(ras, "Pool", _FakePool)

    claims = []
    monkeypatch.setattr(ras, "claim_trial",
                        lambda **kw: (claims.append(kw), len(claims))[1])
    monkeypatch.setattr(ras, "finalize_trial", lambda tid, **kw: None)

    def bh_baseline(job):  # NOT a trial-producing worker
        return {"baseline": True}

    ras._run_jobs_parallel(
        [{"symbol": "BTCUSDT"}], workers=1, label="bh", worker_fn=bh_baseline,
    )
    assert claims == []  # baselines do not produce trials
# (AST scanner over all non-whitelisted modules). No need to duplicate here.
