---
name: firing-the-holdout
description: The falsification gate. A falsification read of data/holdout/ is reachable ONLY via open_holdout_for_falsification(rel_path, hypothesis_id=...), which refuses unless a hypotheses row (db/hypotheses.py) is locked, authorized, sealed, and in budget. Custodial reads keep using open_holdout. Load before evaluating any strategy against the locked holdout.
triggers:
  - "holdout falsification"
  - "open_holdout_for_falsification"
  - "fire the holdout"
  - "A.4-3"
  - "#322"
  - "hypothesis lock"
last_updated: 2026-06-01
---

# Pattern: Firing the holdout (the falsification gate)

## Purpose

The locked holdout (`data/holdout/`) is the bala única — a single out-of-sample
falsification shot (Non-Negotiable #3, [[../context/decisions.md]] §Caveat 5). This
gate makes a falsification read **impossible** without a pre-registered, deflation-
passing, deliberately-fired hypothesis. It turns the prose #322 blockage into
machine-verifiable state: "#322 closure criteria met" == `lock_hypothesis` succeeded.

Two access paths exist, and they are different:

- **Custodial reads** (MANIFEST, drift re-fetch+diff, integrity) — use
  `open_holdout(rel_path, evaluation_mode=True)`. No hypothesis needed; they do not
  consume the bullet.
- **Falsification reads** (run `simulate_strategy` over holdout frames) — use
  `open_holdout_for_falsification(rel_path, hypothesis_id=...)`. This is the bullet.

## When to use

- Evaluating a candidate strategy against the locked holdout window
  `[2025-04-30, 2026-04-30]` (A.4-3).
- Any code path that reads holdout frames to measure strategy performance.

NOT for: drift checks, manifest reads, integrity audits (those are custodial —
`open_holdout(..., evaluation_mode=True)`).

## Steps

The lifecycle is `draft → locked → fired → refuted/not_refuted` (`db/hypotheses.py`).

1. **`claim_hypothesis(...)`** — create a DRAFT with the frozen claim
   (`metric/threshold/direction`), the selection gate (`deflated_metric/
   deflated_threshold`), and the candidate's deflation inputs
   (`cand_sharpe/cand_n_returns/cand_skew/cand_kurt_raw`, sourced from the winning
   exploratory trial).
2. **Attach lower-tier evidence**: `preholdout_trial_ids`, `walkforward_ref` and
   `drift_check_ref` (each JSON `{ref, verdict, ts}` with `verdict='pass'`).
3. **`lock_hypothesis(hid, today=...)`** — enforces the FIVE criteria, captures
   `n_at_lock`, seals the row (immutable thereafter via seal + DB trigger):
   - 4a provenance — `config_hash` matches a registered exploratory `ok` trial.
   - 4b deflation — `sharpe_deflated` over the full registry N ≥ `deflated_threshold`.
   - 4c walk-forward — `walkforward_ref` verdict == 'pass'.
   - 4d drift — `drift_check_ref` verdict == 'pass'.
   - 4e complete claim — all metric/threshold/direction fields set.
4. **Cooldown.** Wait `HOLDOUT_FIRE_COOLDOWN` between lock and authorization.
5. **`authorize_fire(hid, now=...)`** — the SEPARATE deliberate act (the "que ya"
   decision). Refuses before the cooldown. `mex log` the authorization (no silent fire).
6. **`open_holdout_for_falsification(rel_path, hypothesis_id=hid)`** — runs the gate
   chain (`assert_fireable`), marks the fire BEFORE the read (a partial peek burns
   the bullet, Caveat 5), then reads.
7. **`record_outcome(hid, realized_metric=...)`** — `refuted` or `not_refuted`
   (NEVER `confirmed` — a single shot cannot confirm a future distribution). Sets
   `outcome_ts`, closes the read window; further reads of this hypothesis are refused.

## Gotchas

- **Lock and fire are TWO decisions.** Lock answers *what* to falsify; `authorize_fire`
  answers *that the moment is now*. The cooldown is the friction between them — the
  guard against the authorized owner firing a valid hypothesis one day too early.
- **Provenance (4a) is necessary, NOT sufficient.** It closes naive post-peek hand-
  tuning; it does not close competent selection bias. That is what the deflation gate
  (4b) is for. Provenance is rigor-of-existence; deflation is rigor-of-selection.
- **Deflation N is a LOWER bound** ([[../../docs/deflation.md]]): crashed trials and
  sweeps outside the four wired ones do not enter N, and `n_effective = max(N, 50)`
  until 2026-11-29. The gate raises the floor of rigor; it does not make N omniscient.
  Mitigation is registry discipline ([[registering-a-trial.md]]).
- **A single shot never `confirms`.** `verdict` is `refuted | not_refuted`. Passing the
  threshold means the shot *failed to refute*, not that edge was established.
- **Budget = 1** (`HOLDOUT_FIRE_BUDGET`): the locked holdout is a one-shot gate; the
  renewable validation is live shadow (epic B). Exceeding the budget requires an
  explicit operator override logged via `mex log`.
- **`record_fire` self-defends**: it re-runs `assert_fireable`, so a direct call cannot
  bypass the gate.
- **The locked row is immutable**: a SQLite trigger aborts any UPDATE of a frozen field
  after lock, and `assert_fireable` recomputes the seal to detect tampering.

## Verify Checklist

Before merging code that fires the holdout:

- [ ] Falsification reads go through `open_holdout_for_falsification(...)` only — never
      `open_holdout(..., evaluation_mode=True)` for performance measurement.
- [ ] The calling module is whitelisted in
      `tests/test_holdout_isolation.py::HOLDOUT_LEGITIMATE_MODULES` with a justification.
- [ ] `tests/test_holdout_isolation.py` passes (the new entry point is recognized).
- [ ] The hypothesis was locked (5 criteria), authorized after the cooldown, and the
      authorization was `mex log`-ged.
- [ ] `verdict` only ever takes `refuted`/`not_refuted` — never `confirmed`.

## Out of scope

Migrating `walk_forward.evaluate_winner_on_holdout` (today gated by
`_HOLDOUT_322_CLOSED`) onto this function is a separate #322-closure PR. Shadow→active
code-enforcement is epic B.

See the spec: `docs/superpowers/specs/2026-06-01-holdout-falsification-gate-design.md`.
