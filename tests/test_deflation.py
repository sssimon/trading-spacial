import math

import pytest

from deflation import (
    normal_cdf, normal_ppf, probabilistic_sharpe_ratio,
    expected_max_sharpe, deflated_sharpe_ratio, prob_sharpe_positive,
)


def test_normal_cdf_known_points():
    assert normal_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    assert normal_cdf(1.0) == pytest.approx(0.8413447, abs=1e-6)
    assert normal_cdf(-1.0) == pytest.approx(0.1586553, abs=1e-6)


def test_normal_ppf_inverts_cdf():
    for p in (0.01, 0.25, 0.5, 0.84, 0.99):
        assert normal_cdf(normal_ppf(p)) == pytest.approx(p, abs=1e-6)


def test_prob_sharpe_positive_zero_sharpe_is_half():
    # A strategy with observed SR == 0 has exactly 50% probability its true SR > 0.
    assert prob_sharpe_positive(sr=0.0, n_returns=120, skew=0.0, kurt_raw=3.0) == pytest.approx(0.5, abs=1e-9)


def test_psr_constructed_z_equals_one():
    # Construct inputs so the z-statistic is exactly 1.0, then PSR == Phi(1) ~= 0.8413.
    # z = (sr - sr*) * sqrt(T-1) / sqrt(1 - skew*sr + ((kurt_raw-1)/4)*sr**2)
    # Pick skew=0, kurt_raw=1 (so the variance term = sqrt(1 - 0 + 0) = 1), T=2 (sqrt(T-1)=1),
    # sr - sr* = 1.0  => z = 1.0.
    psr = probabilistic_sharpe_ratio(sr=1.0, sr_benchmark=0.0, n_returns=2, skew=0.0, kurt_raw=1.0)
    assert psr == pytest.approx(0.8413447, abs=1e-6)


def test_expected_max_sharpe_grows_with_n():
    sigma = 0.5
    sr2 = expected_max_sharpe(n_trials=2, sigma_sr_trials=sigma)
    sr50 = expected_max_sharpe(n_trials=50, sigma_sr_trials=sigma)
    sr500 = expected_max_sharpe(n_trials=500, sigma_sr_trials=sigma)
    assert 0 < sr2 < sr50 < sr500  # more trials -> higher expected best-of-N


def test_expected_max_sharpe_n_one_is_zero():
    # With a single trial there is no selection, so the benchmark is 0.
    assert expected_max_sharpe(n_trials=1, sigma_sr_trials=0.5) == 0.0


def test_deflated_sharpe_decreases_with_more_trials():
    # Same observed SR; deflating against more trials lowers the probability.
    kw = dict(sr=1.5, sigma_sr_trials=0.4, n_returns=200, skew=-0.2, kurt_raw=4.0)
    dsr_few = deflated_sharpe_ratio(n_trials=5, **kw)
    dsr_many = deflated_sharpe_ratio(n_trials=500, **kw)
    assert 0.0 <= dsr_many < dsr_few <= 1.0


def test_psr_degenerate_variance_returns_none():
    # If the variance term is non-positive, the PSR is undefined -> None (not a crash).
    assert probabilistic_sharpe_ratio(sr=10.0, sr_benchmark=0.0, n_returns=120, skew=5.0, kurt_raw=1.0) is None


def test_algo_version_present():
    import deflation
    assert isinstance(deflation.ALGO_VERSION, int)
    assert deflation.ALGO_VERSION >= 1
