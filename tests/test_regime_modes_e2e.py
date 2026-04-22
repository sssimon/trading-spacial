import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestEvaluateRegimeGate:
    """Unit tests for evaluate_regime_gate function in gate_regime_modes.py."""

    def test_gate_pass_when_improvement(self):
        from scripts.gate_regime_modes import evaluate_regime_gate
        baseline = {
            "total_pnl": 20000, "max_dd_pct": -10.0,
            "per_symbol": {"BTCUSDT": {"pnl": 5000, "pf": 1.4},
                            "DOGEUSDT": {"pnl": 10000, "pf": 4.5}},
        }
        contenders = {
            "hybrid": {
                "total_pnl": 24000, "max_dd_pct": -9.0,
                "per_symbol": {"BTCUSDT": {"pnl": 5200, "pf": 1.4},
                                "DOGEUSDT": {"pnl": 11000, "pf": 4.7}},
            },
        }
        verdicts = evaluate_regime_gate(baseline, contenders)
        assert verdicts["hybrid"]["verdict"] == "PASS"

    def test_gate_fail_when_doge_pf_drops_materially(self):
        """Window-adjusted threshold: baseline DOGE PF 4.5 → threshold 3.6 (=4.5*0.8).
        Tuned at 2.5 fails (well below). Confirms the gate still catches material degradation."""
        from scripts.gate_regime_modes import evaluate_regime_gate
        baseline = {"total_pnl": 20000, "max_dd_pct": -10.0,
                    "per_symbol": {"DOGEUSDT": {"pnl": 10000, "pf": 4.5}}}
        contenders = {
            "hybrid": {
                "total_pnl": 25000, "max_dd_pct": -9.0,
                "per_symbol": {"DOGEUSDT": {"pnl": 10500, "pf": 2.5}},
            },
        }
        verdicts = evaluate_regime_gate(baseline, contenders)
        assert verdicts["hybrid"]["verdict"] == "FAIL"
        assert any("DOGE" in r for r in verdicts["hybrid"]["reasons"])

    def test_gate_doge_pf_threshold_is_window_adjusted(self):
        """Regression for the 2026-04-21 hunger-games run: baseline DOGE PF of
        2.73 should NOT rig the gate against every contender. A contender with
        DOGE PF 2.64 (close to baseline) should not fail on criterion [4]."""
        from scripts.gate_regime_modes import evaluate_regime_gate
        baseline = {
            "total_pnl": 5_000, "max_dd_pct": -30.0,
            "per_symbol": {"DOGEUSDT": {"pnl": 10_000, "pf": 2.73}},
        }
        contenders = {
            "hybrid": {
                "total_pnl": 8_000, "max_dd_pct": -30.0,
                "per_symbol": {"DOGEUSDT": {"pnl": 10_500, "pf": 2.64}},
            },
        }
        verdicts = evaluate_regime_gate(baseline, contenders)
        # DOGE threshold = min(4.0, 0.8 * 2.73) = 2.184. 2.64 >= 2.184 → criterion [4] OK.
        doge_reason = next(r for r in verdicts["hybrid"]["reasons"] if r.startswith("[4]"))
        assert "OK" in doge_reason, doge_reason

    def test_gate_picks_highest_pnl_tiebreak_variance(self):
        """Within 5%, tiebreak by lower per-symbol pnl variance."""
        from scripts.gate_regime_modes import rank_winners
        contenders_passing = {
            "hybrid": {
                "total_pnl": 24000,
                "per_symbol": {"BTCUSDT": {"pnl": 5000}, "DOGEUSDT": {"pnl": 10000},
                                "ADAUSDT": {"pnl": 4500}, "RUNEUSDT": {"pnl": 4500}},
            },
            "hybrid_momentum": {
                "total_pnl": 24500,
                "per_symbol": {"BTCUSDT": {"pnl": 2000}, "DOGEUSDT": {"pnl": 14000},
                                "ADAUSDT": {"pnl": 3500}, "RUNEUSDT": {"pnl": 5000}},
            },
        }
        winner = rank_winners(contenders_passing)
        # Within 5% → tiebreak by variance; hybrid has more uniform per-symbol pnl → wins
        assert winner == "hybrid"

    def test_gate_fails_sanity_check_on_global_drift(self):
        """Baseline ≠ contender 'global' (drift > $10) → sanity fail."""
        from scripts.gate_regime_modes import check_sanity
        baseline = {"total_pnl": 20000}
        global_contender = {"total_pnl": 20600}
        ok, msg = check_sanity(baseline, global_contender)
        assert not ok
        assert "$" in msg or "drift" in msg.lower()
