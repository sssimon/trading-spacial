"""Deflated metrics — Probabilistic & Deflated Sharpe Ratio (López de Prado 2018).

Pure statistics. NO I/O, NO repo imports. scipy is not available in this repo,
so the normal CDF uses math.erf and the inverse CDF uses Acklam's rational
approximation.

Units: callers pass the ANNUALIZED Sharpe (the registry stores annualized
Sharpes). The PSR estimator-variance term strictly assumes a per-period Sharpe;
using the annualized SR here is a documented v1 approximation (see docs/deflation.md).
The selection-penalty term (expected_max_sharpe) is unaffected and dominates.

Formulas:
  PSR(SR*) = Phi( (SR - SR*) * sqrt(T - 1) / sqrt(1 - skew*SR + ((kurt_raw - 1)/4)*SR^2) )
  expected_max_sharpe = sigma_sr_trials * ((1-gamma)*Phi^-1(1 - 1/N) + gamma*Phi^-1(1 - 1/(N*e)))
  DSR = PSR(SR* = expected_max_sharpe(N, sigma_sr_trials))
  prob_sr_gt_0 = PSR(SR* = 0)
`kurt_raw` is the RAW (Pearson) kurtosis where a normal distribution = 3.0
(NOT the excess kurtosis pandas `.kurt()` returns — add 3.0 first).
"""
from __future__ import annotations

import math

_EULER_MASCHERONI = 0.5772156649015329


def normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to ~1.15e-9 in the central region. Raises on p outside (0, 1)."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"normal_ppf requires 0 < p < 1, got {p}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)


def probabilistic_sharpe_ratio(
    sr: float, sr_benchmark: float, n_returns: int, skew: float, kurt_raw: float,
) -> float | None:
    """PSR: probability the true Sharpe exceeds sr_benchmark. None if undefined.

    `kurt_raw` is RAW kurtosis (normal = 3.0). Returns None if T < 2 or the
    variance term is non-positive (degenerate inputs)."""
    if n_returns < 2:
        return None
    var_term = 1.0 - skew * sr + ((kurt_raw - 1.0) / 4.0) * (sr ** 2)
    if var_term <= 0.0:
        return None
    z = (sr - sr_benchmark) * math.sqrt(n_returns - 1) / math.sqrt(var_term)
    return normal_cdf(z)


def expected_max_sharpe(n_trials: int, sigma_sr_trials: float) -> float:
    """Expected maximum of N independent N(0, sigma_sr_trials^2) Sharpe estimates
    (the selection benchmark SR*). 0 when N < 2 (no selection)."""
    if n_trials < 2 or sigma_sr_trials <= 0.0:
        return 0.0
    e = math.e
    return sigma_sr_trials * (
        (1.0 - _EULER_MASCHERONI) * normal_ppf(1.0 - 1.0 / n_trials)
        + _EULER_MASCHERONI * normal_ppf(1.0 - 1.0 / (n_trials * e))
    )


def deflated_sharpe_ratio(
    sr: float, n_trials: int, sigma_sr_trials: float,
    n_returns: int, skew: float, kurt_raw: float,
) -> float | None:
    """DSR: PSR against the expected best-of-N selection benchmark. None if undefined."""
    sr_star = expected_max_sharpe(n_trials, sigma_sr_trials)
    return probabilistic_sharpe_ratio(sr, sr_star, n_returns, skew, kurt_raw)


def prob_sharpe_positive(
    sr: float, n_returns: int, skew: float, kurt_raw: float,
) -> float | None:
    """PSR against a zero benchmark: probability the true Sharpe is positive."""
    return probabilistic_sharpe_ratio(sr, 0.0, n_returns, skew, kurt_raw)
