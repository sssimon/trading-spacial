---
name: registering-a-trial
description: Claim-then-execute contract for recording backtest trials in the `trials` ledger (#278 Part 1). Use when wiring a parameter/window SWEEP so its runs count toward the deflation denominator N.
last_updated: 2026-05-29
---

# Registering a trial

## Purpose

`db/trials.py` is the audit ledger for backtest **trials**. #278 Part 2 deflates
the best Sharpe by the number of trials N that competed for selection (López de
Prado 2018). For N to be honest, every *selection* trial must be recorded
**before** it runs, so a process that crashes mid-run still counts.

## When

Use when you add or modify a sweep that runs `calculate_metrics` over a grid of
(combo, window) candidates AND those candidates feed parameter selection.

Currently wired (exploratory selection sweeps): `auto_tune`, `grid_search_tf`,
`optimize_new_tokens`, `regime_allocation_sweep`.

Do **not** register from:
- **Evaluation** runs (e.g. `walk_forward`'s test-window eval) — they verify
  already-selected params; counting them corrupts N.
- **Confirmatory pre-registered** studies (e.g. epic C `signal_calibration_*`) —
  if you ever record them, use `study_type="confirmatory"` so Part 2 can filter.
- One-shot gates / diagnostics (`gate_*`, `a02_honesty_diff`, the `r*`/`q*`
  research tools).

## Steps

1. `from db.trials import claim_trial, finalize_trial` at the orchestrator top
   (so tests can monkeypatch `<module>.claim_trial` / `.finalize_trial`).
2. **Claim before the simulator runs:**
   `trial_id = claim_trial(source="<sweep_name>", combo=<small identity dict>, symbol=<sym>, window_label=<str>)`.
   Place it AFTER any invalid-combo skip (skipped combos never run → not trials).
3. **Finalize on every normal exit path, exactly once:**
   - success → `finalize_trial(trial_id, status="ok", metrics=<metrics dict>)`
   - error/no-trades (where that is a *failure* for this sweep) →
     `finalize_trial(trial_id, status="failed", error=<str>)`
   - uncaught exception → finalize `failed` then re-raise.
4. **Parallel (`multiprocessing.Pool`) sweeps:** do the claim/finalize in the
   PARENT, never in the child worker. Claim each job into `trial_ids[i]` before
   `pool.map`; finalize `zip(trial_ids, results)` after (`pool.map` preserves
   order). A child crash then leaves a `pending` row that still counts.
5. **Shared chokepoint functions** (a helper called by both selection and
   non-selection callers, e.g. `auto_tune.run_backtest_with_params`): gate
   registration behind a `trial_source: str | None = None` kwarg. Register only
   when `trial_source is not None`. Selection callers opt in; everyone else
   (default `None`) registers nothing.

## Gotchas

- **Claim-then-execute, not register-after.** A process that dies before
  `finalize_trial` cannot register itself. The orphan `pending` row IS the
  evidence the trial existed — that is the whole design. Never move the claim to
  after the run.
- **A transient `database is locked` must NOT abort a sweep.** `signals.db` has
  concurrent writers (scanner, API). `db/trials.py` retries with bounded backoff
  and only raises on durability exhaustion. Do not wrap registry calls in your
  own try/except-and-abort.
- **Zero-trade semantics differ by sweep, on purpose.** Serial sweeps
  (`grid_search_tf`, `optimize_new_tokens`) finalize a no-trades run as `failed`.
  `regime_allocation_sweep` cells with zero trades finalize as `ok` (a completed
  cell with a valid zero-metrics dict, no `error` key — not a failure). Both
  still count toward N. Part 2 must read `status` with this in mind.
- **Store only small identity keys in `combo`** (e.g. `symbol`, `sub_window`,
  `vol_target`) — never the whole job dict if it carries DataFrames or config
  blobs. `json.dumps(..., default=str)` won't crash but will bloat the row.
- **`db/trials.py` writes ONLY to `signals.db`.** It must never import
  `open_holdout` or touch `data/holdout/` (Non-Negotiable #2/#3). It is correctly
  absent from `HOLDOUT_LEGITIMATE_MODULES`.

## Verify Checklist

- [ ] `source` string is exact and accurate for the sweep.
- [ ] Claim is before the simulator and after any invalid-combo skip.
- [ ] Every normal exit path finalizes exactly once; no double-finalize.
- [ ] Parallel path: registry calls are parent-side only (grep the worker body
      for `claim_trial`/`finalize_trial` → zero matches).
- [ ] `python -m pytest tests/test_holdout_isolation.py -q` → 15/15.
- [ ] A no-`trial_source` (or non-selection) caller registers nothing.
