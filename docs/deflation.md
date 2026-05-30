# Deflated metrics (A.0.3 / #278)

## What deflation does
When you test N strategy variants and report the best one's Sharpe, that Sharpe
is biased upward by selection (~`sqrt(log N)`). The Deflated Sharpe Ratio (DSR,
López de Prado 2018) corrects for this: it is the probability the true Sharpe
exceeds the expected best-of-N under the null, given the trial's higher moments
(skew, kurtosis) and sample length.

Two values ship in Part 2:
- **`prob_sr_gt_0`** — PSR against a zero benchmark: the probability the true
  Sharpe is positive. Intrinsic to a single trial; always computed (no N needed).
- **`sharpe_deflated`** — the DSR: PSR against the expected best-of-N selection
  benchmark `SR*`. A probability in `[0, 1]`. Computed only when the caller injects
  both `n_effective` and `sigma_sr_trials` (the honesty-diff does this from the
  registry). `None` otherwise.

Both are probabilities, NOT rescaled Sharpe numbers. A `sharpe_deflated` of 0.7
means "70% chance the true Sharpe beats the best-of-N null", not "Sharpe × 0.7".

## Why N matters
N = the number of distinct variants tried. We count it from the trial registry
(`db/trials.py`), over `study_type='exploratory'` trials, deduplicated by
`DISTINCT(source, combo_json, window_label)`. `N_effective = max(N_registered, floor)`
with `floor = 50` until 2026-11-29 (registry bootstrap), then 0.

## sigma_sr_trials vs sigma_returns (do not confuse)
- **`sigma_sr_trials`**: stdev of the Sharpe values ACROSS trials (selection
  variance). Drives the expected best-of-N benchmark `SR*` via
  `expected_max_sharpe(N, sigma_sr_trials)`.
- **`sigma_returns`**: stdev of per-trade returns WITHIN a trial. Drives the raw
  Sharpe itself and the PSR estimator variance (via skew/kurtosis).

`calculate_metrics` derives the within-trial moments from the closed-trade
`pnl_pct` series; `sigma_sr_trials` is injected by the caller (the registry).

## Kurtosis convention
The PSR formula needs RAW (Pearson) kurtosis where a normal distribution = 3.0.
pandas `.kurt()` returns EXCESS kurtosis (normal = 0), so `calculate_metrics`
adds 3.0 before passing it to `deflation.py`. The `deflation.py` functions take
`kurt_raw` (normal = 3.0).

## v1 limitations (documented, not bugs)
- **Annualization basis.** PSR's estimator-variance term assumes a per-period
  Sharpe; v1 uses the annualized Sharpe throughout. The selection penalty
  (`SR*`) is unaffected and dominates; the variance correction is approximate.
- **Trial correlation not modeled.** Grid search produces correlated trials;
  standard DSR over-deflates for grids (conservative). Effective-N corrections
  (Bailey–López de Prado–Pope–Pratt) are a v2 enhancement.
- **N is a known lower bound.** Only the wired exploratory sweeps register;
  historical pre-A.0.3 trials are absent. The floor mitigates the bootstrap.
- **DSR-first scope.** `sortino_deflated` and `calmar_deflated_approx` are
  deferred (names reserved; their formulas are undefined in the literature).
  Only `sharpe_deflated` + `prob_sr_gt_0` ship.
- **Calmar** here is `total_return_pct / |max_drawdown_pct|` (a02-consistent),
  not annualized CAGR.

## Where it lives
- `deflation.py` — pure statistics (normal CDF/inverse-CDF, PSR, expected-max
  Sharpe, DSR, prob-sharpe-positive). No I/O, no repo imports, scipy-free.
- `db/trials.py` — `selection_population_stats()` (N + sigma over the registry)
  and `n_effective()` (floor/decay).
- `backtest.py` `calculate_metrics` — emits `calmar`, `calmar_ratio`,
  `prob_sr_gt_0`, `sharpe_deflated`, `n_effective`, `sigma_sr_trials`. Stays
  registry-free (runs in `multiprocessing.Pool` children); deflation is injected.
- `scripts/a03_deflation_honesty_diff.py` — post-hoc raw-vs-deflated table,
  holdout-bounded (`sim_end < 2025-04-30`).
