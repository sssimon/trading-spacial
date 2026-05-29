"""Property tests for `walk_forward.compute_windows`.

Four invariants the harness must guarantee for #276:

  1. Anchored: every window.train_start == history_start.
  2. Non-overlap: consecutive test ranges are disjoint.
  3. Holdout-exclusion: no window's test span touches holdout_start.
  4. Warmup-gap: test_start - train_end >= warmup_gap_days.

These tests are intentionally small and deterministic — no strategy
execution, no data files, no DB. The scaffold passes them and any future
edit to the window math has to keep passing them.
"""

from __future__ import annotations

from datetime import date

import pytest

from walk_forward import Window, compute_windows, compute_warmup_bars


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def base_config() -> dict:
    """Three-year history, 12-month initial train, 3-month test, 3-month step.

    Yields six folds before bumping against the holdout edge.
    """
    return dict(
        history_start=date(2023, 1, 1),
        history_end=date(2025, 12, 31),
        holdout_start=date(2026, 1, 1),
        initial_train_months=12,
        test_months=3,
        step_months=3,
    )


# --------------------------------------------------------------------------- #
# Property 1: anchored
# --------------------------------------------------------------------------- #


def test_anchored_mode_pins_train_start(base_config):
    windows = compute_windows(**base_config)
    assert len(windows) > 0, "fixture should yield at least one fold"
    for w in windows:
        assert w.train_start == base_config["history_start"], (
            f"window {w.index} train_start={w.train_start} drifted from "
            f"history_start={base_config['history_start']}"
        )


# --------------------------------------------------------------------------- #
# Property 2: non-overlap
# --------------------------------------------------------------------------- #


def test_test_ranges_do_not_overlap(base_config):
    windows = compute_windows(**base_config)
    assert len(windows) >= 2, "need at least two folds to test overlap"
    for prev, curr in zip(windows, windows[1:]):
        assert prev.test_end <= curr.test_start, (
            f"overlap: window {prev.index} test_end={prev.test_end} > "
            f"window {curr.index} test_start={curr.test_start}"
        )


def test_test_ranges_strictly_advance(base_config):
    """Stronger than non-overlap: each test_start is past the previous one."""
    windows = compute_windows(**base_config)
    for prev, curr in zip(windows, windows[1:]):
        assert curr.test_start > prev.test_start
        assert curr.test_end > prev.test_end


# --------------------------------------------------------------------------- #
# Property 3: holdout exclusion
# --------------------------------------------------------------------------- #


def test_no_window_touches_holdout(base_config):
    windows = compute_windows(**base_config)
    for w in windows:
        assert w.test_end <= base_config["holdout_start"], (
            f"window {w.index} test_end={w.test_end} crosses holdout_start="
            f"{base_config['holdout_start']}"
        )
        assert w.train_end <= base_config["holdout_start"]


def test_holdout_clips_history_end(base_config):
    """history_end past holdout_start gets silently clipped to holdout_start."""
    cfg = {**base_config, "history_end": date(2030, 1, 1)}
    windows = compute_windows(**cfg)
    for w in windows:
        assert w.test_end <= base_config["holdout_start"]


def test_holdout_at_history_start_yields_no_folds():
    windows = compute_windows(
        history_start=date(2024, 1, 1),
        history_end=date(2025, 1, 1),
        holdout_start=date(2024, 1, 1),
        initial_train_months=12,
        test_months=3,
        step_months=3,
    )
    assert windows == []


# --------------------------------------------------------------------------- #
# Property 4: warmup gap
# --------------------------------------------------------------------------- #


def test_warmup_gap_zero_default(base_config):
    windows = compute_windows(**base_config)
    for w in windows:
        assert (w.test_start - w.train_end).days == 0
        assert w.warmup_gap_days == 0


def test_warmup_gap_respected_when_positive(base_config):
    gap = 7
    windows = compute_windows(**base_config, warmup_gap_days=gap)
    assert len(windows) > 0
    for w in windows:
        delta_days = (w.test_start - w.train_end).days
        assert delta_days >= gap, (
            f"window {w.index} warmup gap {delta_days}d < requested {gap}d"
        )
        assert w.warmup_gap_days == gap


# --------------------------------------------------------------------------- #
# Sanity / construction tests
# --------------------------------------------------------------------------- #


def test_window_is_frozen_dataclass(base_config):
    windows = compute_windows(**base_config)
    w = windows[0]
    assert isinstance(w, Window)
    with pytest.raises(Exception):
        w.train_start = date(1999, 1, 1)  # type: ignore[misc]


def test_string_dates_accepted():
    windows = compute_windows(
        history_start="2023-01-01",
        history_end="2025-12-31",
        holdout_start="2026-01-01",
        initial_train_months=12,
        test_months=3,
        step_months=3,
    )
    assert all(w.train_start == date(2023, 1, 1) for w in windows)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(initial_train_months=0),
        dict(test_months=0),
        dict(step_months=0),
        dict(warmup_gap_days=-1),
    ],
)
def test_invalid_arguments_raise(base_config, kwargs):
    cfg = {**base_config, **kwargs}
    with pytest.raises(ValueError):
        compute_windows(**cfg)


def test_rolling_mode_not_implemented(base_config):
    with pytest.raises(NotImplementedError):
        compute_windows(**base_config, anchored=False)


# --------------------------------------------------------------------------- #
# Warmup bars (commit 2 of #276)
# --------------------------------------------------------------------------- #
#
# Indicator lookbacks are sourced from strategy/constants.py and the active
# call sites in strategy/core.py / strategy/regime.py / strategy/patterns.py.
# The expected values below are the maximum lookback per TF that the harness
# must respect when slicing fold history.


def test_warmup_bars_4h_dominated_by_sma100():
    """4h path runs SMA100 (strategy/core.py:576). Nothing on 4h goes deeper."""
    assert compute_warmup_bars("4h") == 100


def test_warmup_bars_1h_dominated_by_sma200():
    """1h path runs LRC100 + SMA200 (computed unconditionally, core.py:556-559).

    SMA200 1h is computed on every bar (the value is exposed in
    `decision.indicators` regardless of the `trend_pullback_enabled` flag,
    which only feature-gates its downstream *use*). Compute-warmup must
    therefore cover it. If SMA200 1h is removed from the compute path,
    drop this back to 100.
    """
    assert compute_warmup_bars("1h") == 200


def test_warmup_bars_5m_dominated_by_bb20():
    """5m path runs RSI14 + BB20 + VOL20 (patterns.py / core.py)."""
    assert compute_warmup_bars("5m") == 20


@pytest.mark.parametrize("tf", ["4h", "1h", "5m"])
def test_warmup_bars_returns_positive_int(tf):
    bars = compute_warmup_bars(tf)
    assert isinstance(bars, int)
    assert bars > 0


@pytest.mark.parametrize(
    "tf,floor",
    [
        ("4h", 100),   # SMA100
        ("1h", 200),   # SMA200 1h computed unconditionally (use is feature-gated)
        ("5m", 20),    # BB20 / VOL20
    ],
)
def test_warmup_bars_at_least_declared_max(tf, floor):
    """Sanity: the return value is never below the declared max for the TF."""
    assert compute_warmup_bars(tf) >= floor


@pytest.mark.parametrize("tf", ["4H", "1H", "5M", " 1h "])
def test_warmup_bars_case_and_whitespace_insensitive(tf):
    """Operators type these by hand. Be lenient with case + surrounding space."""
    assert compute_warmup_bars(tf) > 0


@pytest.mark.parametrize("bad_tf", ["", "1d", "15m", "2h", "1w", "foo"])
def test_warmup_bars_rejects_unknown_timeframe(bad_tf):
    with pytest.raises(ValueError):
        compute_warmup_bars(bad_tf)


@pytest.mark.parametrize("bad", [None, 60, 1.0, ("1h",)])
def test_warmup_bars_rejects_non_string(bad):
    with pytest.raises(ValueError):
        compute_warmup_bars(bad)  # type: ignore[arg-type]
