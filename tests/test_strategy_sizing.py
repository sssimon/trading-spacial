"""Tests for strategy.sizing.compute_size (#186 A4)."""
import pytest


def test_compute_size_normal_premium_score():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=6, health_tier="NORMAL", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(150.0)


def test_compute_size_normal_standard_score():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=3, health_tier="NORMAL", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(100.0)


def test_compute_size_normal_low_score():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=1, health_tier="NORMAL", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(50.0)


def test_compute_size_alert_same_as_normal():
    """ALERT is notification-only; doesn't change sizing."""
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=3, health_tier="ALERT", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(100.0)


def test_compute_size_reduced_halves():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=3, health_tier="REDUCED", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(50.0)


def test_compute_size_paused_is_zero():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=6, health_tier="PAUSED", capital=10_000.0, cfg=cfg)
    assert size == 0.0


def test_compute_size_probation_halves():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.5}}
    size = compute_size(score=3, health_tier="PROBATION", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(50.0)


def test_compute_size_custom_reduce_factor():
    from strategy.sizing import compute_size
    cfg = {"kill_switch": {"reduce_size_factor": 0.3}}
    size = compute_size(score=3, health_tier="REDUCED", capital=10_000.0, cfg=cfg)
    assert size == pytest.approx(30.0)
