"""Tests for emit_observability_metrics + apply_subset_lookbacks (epic C Phase 1).

Locks pre-reg §2.1 schema literal + Q-PR6 A1 subset = (5, 10, 20).

Schema reference (pre-reg `docs/superpowers/plans/2026-05-15-signal-calibration-pre-reg.md` §2.1, lines 99-126):

per_lookback[N] dict keys:
  lookback_days, count_long, count_short, count_flat, firing_count,
  magnitude_mean, magnitude_std, magnitude_p50, magnitude_p95

sum_distribution dict keys:
  mean, std, p25, p50, p75, p95, bars_total

Magnitude fields under per_lookback are aggregate |sum| stats replicated (pre-reg
comment line 105: `// mean of |sum| values`). Same value across all N.
"""
import numpy as np
import pandas as pd
import pytest

from strategy.donchian_ensemble import (
    ZARATTINI_LOOKBACKS,
    apply_subset_lookbacks,
    compute_ensemble_history,
    emit_observability_metrics,
)


PER_LOOKBACK_KEYS = {
    "lookback_days",
    "count_long",
    "count_short",
    "count_flat",
    "firing_count",
    "magnitude_mean",
    "magnitude_std",
    "magnitude_p50",
    "magnitude_p95",
}

SUM_DISTRIBUTION_KEYS = {
    "mean",
    "std",
    "p25",
    "p50",
    "p75",
    "p95",
    "bars_total",
}


@pytest.fixture
def daily_index_120():
    """120 consecutive daily timestamps — enough for 90 + warmup tests."""
    return pd.date_range("2024-01-01", periods=120, freq="D")


@pytest.fixture
def linear_uptrend_120(daily_index_120):
    """Linear uptrend 100 → 219 over 120 days."""
    closes = np.arange(100.0, 220.0)
    return pd.DataFrame(
        {"close": closes, "high": closes + 0.5, "low": closes - 0.5},
        index=daily_index_120,
    )


@pytest.fixture
def linear_downtrend_120(daily_index_120):
    closes = np.arange(220.0, 100.0, -1.0)
    return pd.DataFrame(
        {"close": closes, "high": closes + 0.5, "low": closes - 0.5},
        index=daily_index_120,
    )


@pytest.fixture
def constant_price_120(daily_index_120):
    return pd.DataFrame(
        {"close": [100.0] * 120, "high": [100.0] * 120, "low": [100.0] * 120},
        index=daily_index_120,
    )


@pytest.fixture
def ensemble_history_uptrend(linear_uptrend_120):
    """compute_ensemble_history output for a clean uptrend with 3 lookbacks."""
    df = linear_uptrend_120
    return compute_ensemble_history(
        closes=df["close"], highs=df["high"], lows=df["low"],
        lookbacks=(5, 10, 20),
    )


@pytest.fixture
def ensemble_history_constant(constant_price_120):
    df = constant_price_120
    return compute_ensemble_history(
        closes=df["close"], highs=df["high"], lows=df["low"],
        lookbacks=(5, 10, 20),
    )


# ---------------------------------------------------------------------------
# emit_observability_metrics — schema lock
# ---------------------------------------------------------------------------


class TestEmitObservabilityMetricsSchema:
    def test_returns_dict_with_per_lookback_and_sum_distribution_keys(
        self, ensemble_history_uptrend
    ):
        out = emit_observability_metrics(ensemble_history_uptrend)
        assert isinstance(out, dict)
        assert "per_lookback" in out
        assert "sum_distribution" in out

    def test_per_lookback_keys_exact_match_schema(self, ensemble_history_uptrend):
        """Schema locked verbatim from pre-reg §2.1 — set equality, no superset tolerance."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        for n, per_n in out["per_lookback"].items():
            assert set(per_n.keys()) == PER_LOOKBACK_KEYS, (
                f"per_lookback[{n}] keys mismatch: got {set(per_n.keys())}, "
                f"expected {PER_LOOKBACK_KEYS}"
            )

    def test_sum_distribution_keys_exact_match_schema(self, ensemble_history_uptrend):
        out = emit_observability_metrics(ensemble_history_uptrend)
        assert set(out["sum_distribution"].keys()) == SUM_DISTRIBUTION_KEYS

    def test_per_lookback_includes_all_history_lookbacks(self, ensemble_history_uptrend):
        """All dir_N columns in input df should produce a per_lookback[N] entry."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        assert set(out["per_lookback"].keys()) == {5, 10, 20}

    def test_lookback_days_key_matches_dict_key(self, ensemble_history_uptrend):
        """per_lookback[N]['lookback_days'] must equal N."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        for n, per_n in out["per_lookback"].items():
            assert per_n["lookback_days"] == n


# ---------------------------------------------------------------------------
# Counts — per-lookback firing semantics
# ---------------------------------------------------------------------------


class TestCounts:
    def test_constant_price_count_flat_dominant(self, ensemble_history_constant):
        """Constant price never breaks any range → count_flat = bars_total per lookback."""
        out = emit_observability_metrics(ensemble_history_constant)
        bars_total = out["sum_distribution"]["bars_total"]
        for n, per_n in out["per_lookback"].items():
            assert per_n["count_flat"] == bars_total
            assert per_n["count_long"] == 0
            assert per_n["count_short"] == 0
            assert per_n["firing_count"] == 0

    def test_uptrend_count_long_dominant_post_warmup(self, ensemble_history_uptrend):
        """In a clean linear uptrend, post-warmup all bars vote LONG."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        for n, per_n in out["per_lookback"].items():
            assert per_n["count_long"] > 0
            assert per_n["count_short"] == 0
            assert per_n["count_long"] >= per_n["count_flat"], (
                f"lookback {n}: expected count_long >= count_flat in uptrend; "
                f"got long={per_n['count_long']} flat={per_n['count_flat']}"
            )

    def test_downtrend_count_short_dominant(self, linear_downtrend_120):
        df = linear_downtrend_120
        history = compute_ensemble_history(
            closes=df["close"], highs=df["high"], lows=df["low"],
            lookbacks=(5, 10, 20),
        )
        out = emit_observability_metrics(history)
        for n, per_n in out["per_lookback"].items():
            assert per_n["count_short"] > 0
            assert per_n["count_long"] == 0
            assert per_n["count_short"] >= per_n["count_flat"]

    def test_firing_count_equals_long_plus_short(self, ensemble_history_uptrend):
        """firing_count := count_long + count_short (pre-reg §2.1 line 104)."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        for n, per_n in out["per_lookback"].items():
            assert per_n["firing_count"] == per_n["count_long"] + per_n["count_short"]

    def test_counts_sum_to_bars_total(self, ensemble_history_uptrend):
        """count_long + count_short + count_flat == bars_total."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        bars_total = out["sum_distribution"]["bars_total"]
        for n, per_n in out["per_lookback"].items():
            total = per_n["count_long"] + per_n["count_short"] + per_n["count_flat"]
            assert total == bars_total, f"lookback {n}: counts sum {total} != bars_total {bars_total}"


# ---------------------------------------------------------------------------
# Magnitudes — aggregate |sum| distribution semantics
# ---------------------------------------------------------------------------


class TestMagnitudes:
    def test_constant_price_magnitude_zero(self, ensemble_history_constant):
        """Constant price → vote always 0 → all magnitude stats are 0."""
        out = emit_observability_metrics(ensemble_history_constant)
        sd = out["sum_distribution"]
        assert sd["mean"] == 0.0
        assert sd["p25"] == 0.0
        assert sd["p50"] == 0.0
        assert sd["p75"] == 0.0
        assert sd["p95"] == 0.0

    def test_uptrend_aggregate_magnitude_max_lookback_count(self, ensemble_history_uptrend):
        """3 lookbacks all LONG in uptrend → |sum| reaches max value 3."""
        out = emit_observability_metrics(ensemble_history_uptrend)
        sd = out["sum_distribution"]
        assert sd["p95"] == pytest.approx(3.0), (
            f"Expected aggregate p95 == 3 (all 3 lookbacks LONG); got {sd['p95']}"
        )

    def test_magnitudes_replicated_in_per_lookback_dicts(self, ensemble_history_uptrend):
        """Per-reg §2.1 schema: magnitude_* in per_lookback are aggregate |sum| stats replicated.

        per_lookback[N]['magnitude_mean'] must equal sum_distribution['mean'] for all N.
        Similarly for std, p50, p95.
        """
        out = emit_observability_metrics(ensemble_history_uptrend)
        sd = out["sum_distribution"]
        for n, per_n in out["per_lookback"].items():
            assert per_n["magnitude_mean"] == pytest.approx(sd["mean"]), f"lookback {n}"
            assert per_n["magnitude_std"] == pytest.approx(sd["std"]), f"lookback {n}"
            assert per_n["magnitude_p50"] == pytest.approx(sd["p50"]), f"lookback {n}"
            assert per_n["magnitude_p95"] == pytest.approx(sd["p95"]), f"lookback {n}"

    def test_bars_total_equals_dataframe_length(self, ensemble_history_uptrend):
        out = emit_observability_metrics(ensemble_history_uptrend)
        assert out["sum_distribution"]["bars_total"] == len(ensemble_history_uptrend)

    def test_p25_le_p50_le_p75_le_p95(self, ensemble_history_uptrend):
        """Percentiles must be ordered monotonically."""
        sd = emit_observability_metrics(ensemble_history_uptrend)["sum_distribution"]
        assert sd["p25"] <= sd["p50"] <= sd["p75"] <= sd["p95"]


# ---------------------------------------------------------------------------
# apply_subset_lookbacks — A1 intervention wrapper
# ---------------------------------------------------------------------------


class TestApplySubsetLookbacks:
    def test_default_subset_is_5_10_20(self, linear_uptrend_120):
        """Q-PR6 lock: A1 default subset = (5, 10, 20)."""
        df = linear_uptrend_120
        out = apply_subset_lookbacks(
            closes=df["close"], highs=df["high"], lows=df["low"],
        )
        for n in (5, 10, 20):
            assert f"dir_{n}" in out.columns
        # Should NOT include larger lookbacks from ZARATTINI default
        for n in (30, 60, 90, 150, 250, 360):
            assert f"dir_{n}" not in out.columns

    def test_custom_subset_propagates(self, linear_uptrend_120):
        df = linear_uptrend_120
        out = apply_subset_lookbacks(
            closes=df["close"], highs=df["high"], lows=df["low"],
            lookbacks_subset=(10, 30),
        )
        assert "dir_10" in out.columns
        assert "dir_30" in out.columns
        assert "dir_5" not in out.columns

    def test_subset_aggregate_vote_uses_only_subset(self, linear_uptrend_120):
        """vote column == sum of subset dir_N only (max |vote| == len(subset))."""
        df = linear_uptrend_120
        out = apply_subset_lookbacks(
            closes=df["close"], highs=df["high"], lows=df["low"],
            lookbacks_subset=(5, 10, 20),
        )
        # In linear uptrend, terminal bar has all 3 subset lookbacks LONG
        assert out["vote"].iloc[-1] == 3
        assert out["confidence"].iloc[-1] == pytest.approx(1.0)
        assert out["direction"].iloc[-1] == 1

    def test_empty_subset_raises(self, linear_uptrend_120):
        df = linear_uptrend_120
        with pytest.raises(ValueError, match="empty"):
            apply_subset_lookbacks(
                closes=df["close"], highs=df["high"], lows=df["low"],
                lookbacks_subset=(),
            )

    def test_subset_must_be_subset_of_zarattini_or_arbitrary(self, linear_uptrend_120):
        """Function accepts ANY tuple of valid lookbacks ≥ 2 (not limited to ZARATTINI).

        Permits A2/A3/A4 future override paths per pre-reg §4.5 self-policing.
        """
        df = linear_uptrend_120
        # Arbitrary lookback not in ZARATTINI default — should still work
        out = apply_subset_lookbacks(
            closes=df["close"], highs=df["high"], lows=df["low"],
            lookbacks_subset=(7, 14, 28),
        )
        assert "dir_7" in out.columns
        assert "dir_14" in out.columns
        assert "dir_28" in out.columns


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_emit_on_empty_dataframe_raises(self):
        """Empty DataFrame is a programming error — explicit raise."""
        empty = pd.DataFrame(columns=["dir_5", "dir_10", "dir_20", "vote"])
        with pytest.raises(ValueError, match="empty"):
            emit_observability_metrics(empty)

    def test_emit_without_dir_columns_raises(self, daily_index_120):
        """DataFrame missing dir_N columns is a malformed input — explicit raise."""
        df = pd.DataFrame({"vote": [0, 1, -1]}, index=daily_index_120[:3])
        with pytest.raises(ValueError, match="dir_"):
            emit_observability_metrics(df)

    def test_emit_without_vote_column_raises(self, daily_index_120):
        """DataFrame missing 'vote' column → magnitudes undefined → explicit raise."""
        df = pd.DataFrame(
            {"dir_5": [0, 1, -1], "dir_10": [0, 1, -1]},
            index=daily_index_120[:3],
        )
        with pytest.raises(ValueError, match="vote"):
            emit_observability_metrics(df)

    def test_emit_with_zarattini_9_lookbacks_all_present(self, linear_uptrend_120):
        """Full ZARATTINI ensemble (9 lookbacks) — emit covers all 9 per_lookback entries."""
        df = linear_uptrend_120
        history = compute_ensemble_history(
            closes=df["close"], highs=df["high"], lows=df["low"],
            # Use ZARATTINI_LOOKBACKS default
        )
        out = emit_observability_metrics(history)
        assert set(out["per_lookback"].keys()) == set(ZARATTINI_LOOKBACKS)
        for n in ZARATTINI_LOOKBACKS:
            assert out["per_lookback"][n]["lookback_days"] == n
