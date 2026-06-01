# Holdout Falsification Gate — Design

**Date:** 2026-06-01
**Status:** Design approved, pending implementation plan
**Scope:** Capa 2 of the edge-discovery decomposition (the falsification gate). NOT cost-model v3 (Capa 0), NOT edge-search methodology (Capa 1).

## Origin / decision chain

This design is the answer to a reframe, not a feature request. The chain:

1. "Promote kill-switch v2 shadow→active" → "prove v2>v1" → **"is the base strategy even a winner?"** (Samuel's reframe, see `data/retune/2026-06-01-base-edge-diag/FINDINGS.md`).
2. The cost-model diagnosis concluded the backtest is an instrument falsified by live data (cost overcharged ~30-40×, `findings.md` ratio 6.84). Recommended next work: recalibrate cost model (v3).
3. Samuel re-reframed: **find edge before recalibrating costs** — no point polishing the accounting of a strategy that never wins.
4. Voronov (ontological critique) reframed both positions:
   - The FINDINGS conflates two independent propositions: "cost model is wrong" and "there is edge." Gross-flat (−$720 at zero cost) ≠ edge; it is **absence of signal**. The doc refutes itself (lines 61-64): *"Live is not proven profitable... +$30 = noise. It refutes the catastrophe; it does not establish edge."* and *"Recalibration is necessary, not sufficient."*
   - **The category error both share:** confusing the estimator with the estimand. Edge is a claim about a future distribution, not a property the strategy *has*. Backtest, live, and holdout are three estimators of the same unobservable estimand; the "which first" debate presupposes one of them *is* the estimand. None is.
   - **The invisible constraint:** the organization has budgeted exactly one (1) clean out-of-sample test (the bala única) and has never written the hypothesis that test will falsify, nor the gate that prevents search from discharging it prematurely. A single-shot test is a falsification instrument, not a search instrument. *"Están debatiendo qué cazar parados frente al cañón."*
   - **"Deterministic edge" is incoherent:** determinism is a property of a mapping; edge is a property of a future distribution. The word that must die is "deterministic"; the word that survives is **"accounted-for"** — a hypothesis-generation procedure whose degrees of freedom are accounted before firing.
   - **Dependency arrow inverted:** you cannot search for edge in a backtest whose cost model makes every strategy lose by construction. Cost recalibration is the precondition for "find edge" to be *computable*, not a competing priority.

### The decomposition

"An accounted-for way to have edge" decomposes into three layers with a dependency order:

- **Capa 0 — Instrument (cost-model v3).** Calibrate cost against real execution. Precondition for any credible search. Bounded; touches `costs_calibration.json`. Has its own brainstorm→spec→plan (FINDINGS "Recommended next work"). **Separate track.**
- **Capa 1 — Search (what to look for).** Given gross-flat, "find edge" means a *new* signal source, not rescuing the current one. Open-ended research; depends on Capa 0. **Separate track, runs on top.**
- **Capa 2 — The gate (THIS spec).** Pre-registration → single holdout fire → lockout, plus the candidate→hypothesis bridge. The only piece genuinely **missing** (the registry and deflation already exist; the holdout lock is decree, not machine). Instrument-agnostic; designable now, independent of Capa 0.

### The validation ladder (adopted architecture)

The "what is the bala única" question resolved to a **hybrid tier ladder**, not a single conception:

```
backtest (search)    →  pre-holdout         →  HOLDOUT locked      →  live shadow        →  active
instrument, broken      out-of-sample           ONE shot              RENEWABLE bullet      production
(Capa 0 fixes)          intermediate, repeatable (conception 1)        (conception 3)
                                                 final pre-shadow gate  weeks/months
──────────────── deflation + pre-registration accounting at EVERY tier (conception 2) ────────────────
```

- The **locked holdout = conception 1** (one shot), repositioned: it is the **one-shot final gate** a candidate crosses only after surviving everything cheaper. Fired rarely.
- The **renewable resource is live shadow = conception 3.** Time, not the file, is the scarce thing; every day produces fresh out-of-sample data. This is what the system already does (KS v2 shadow→active; live reconciliation in FINDINGS).
- The **honesty of conception 2** (count fires, deflate) is the *accounting* that applies at every tier — not a rival conception.

The gate is the machine that governs ascent between tiers. Selected enforcement model: **Approach A (the keystone)** — code-enforce only the irreversible step (the locked holdout); everything renewable/judgment stays protocol. Rationale: build machinery only where failure is irreversible (process-proportionality). Approach B (full ladder code-enforced) is gold-plating until a deployable candidate exists; Approach C (protocol-first) under-protects the bullet (AST scanner detects illegitimate access but does not force a legitimate access to have a pre-registered hypothesis behind it).

## Non-negotiables honored

- **#3 (holdout = bala única, single-shot, A.4-3 blocked).** This design *replaces the prose blockage with verifiable state*: A.4-3 unblocks exactly when a locked pre-registered hypothesis exists. A partial peek still burns the bullet — encoded as fire-before-read (claim-then-execute applied to the holdout).
- **#2 (holdout only via `open_holdout`; allow-list edits reviewed in PR).** The new falsification entry point lives in the already-whitelisted `data/holdout_access.py`. The AST scanner is extended to recognize it; the A.4-3 harness allow-list entry is reviewed in the PR.
- **#7 (admin-merge discipline).** Exceeding the fire budget requires explicit operator override logged via `mex log` — same no-silent-bypass discipline.

## Reused infrastructure (not rebuilt)

- `db/trials.py` — claim-then-execute trial registry. Counts degrees of freedom. `study_type` distinguishes `exploratory` (inflates selection bias) from `confirmatory` (pre-registered). See `.mex/patterns/registering-a-trial.md`.
- `deflation.py` / `docs/deflation.md` — DSR / PSR; N from the registry (`DISTINCT(source, combo, window)`, floor 50 until 2026-11-29).
- `data/holdout_access.py` — `open_holdout(rel_path, evaluation_mode=True)` + AST scanner in `tests/test_holdout_isolation.py`.

---

## Section 1 — Architecture and the custodial/falsification distinction

The keystone is **a precondition layer over falsification access to the holdout**, plus a **frozen-hypothesis ledger**. It rewrites nothing.

Reading the current `open_holdout` surfaces a critical conflation: today a single `evaluation_mode=True` mixes two ontologically distinct accesses:

1. **Falsification read** — running `simulate_strategy` over holdout frames to measure a strategy's performance. **This is the bullet.** It consumes the resource.
2. **Custodial read** — reading `MANIFEST.json`, the drift re-fetch+diff, integrity checks. This does **not** consume the bullet (it does not look at strategy performance); it is part of *preparing* the shot and is itself a #322 closure criterion.

Requiring a pre-registered hypothesis for *all* holdout access would deadlock the drift check (which must run *before* a hypothesis exists). So the design **splits the two paths:**

```
open_holdout(rel_path, evaluation_mode=True)              ← unchanged, now ONLY custodial
                                                            (manifest, drift, integrity)

open_holdout_for_falsification(rel_path, hypothesis_id=…) ← NEW. The only path to the bullet.
        │
        └─► the gate verifies BEFORE returning the Path (full chain in Section 3):
              1. a hypothesis row with that id exists
              2. its status is 'locked' (frozen) or 'fired' (re-read allowed)
              3. the integrity seal matches (covers the lower-tier evidence, which
                 was validated and sealed at lock time — Section 4)
              4. the fire budget is not exceeded (default 1 — conception 1)
            any failure → HoldoutFalsificationError; the bullet is not touched
```

The AST scanner remains the structural net (detects access outside both functions). The new gate adds what Voronov asked for: a *legitimate* falsification access must have a pre-registered hypothesis behind it, machine-verified.

**Components** (following repo convention — `db/trials.py`, `data/holdout_access.py`):

- **`db/hypotheses.py`** — the frozen-hypothesis ledger (new).
- **`data/holdout_access.py`** — gains `open_holdout_for_falsification(...)` and the custodial/falsification distinction.
- Reuses `db/trials.py` and `deflation.py` untouched.

Layering: `data/holdout_access.py` → imports `db/hypotheses.py` → imports `db/transaction.py`. No cycle (`data` → `db` is fine; `db` does not import `data`).

## Section 2 — The hypothesis ledger (`db/hypotheses.py`)

A hypothesis is a **frozen confirmatory study**: the punctual claim the shot will kill or confirm. It lives in its own table because its lifecycle (`draft → locked → fired → confirmed/refuted`) differs from `trials` (`pending → ok/failed`). It links to `trials` for provenance.

**Schema (`hypotheses` table in `signals.db`):**

```
id              INTEGER PK
created_ts      TEXT        -- draft created
locked_ts       TEXT        -- frozen (null until lock)
fired_ts        TEXT        -- holdout read for falsification (null until fire)
status          TEXT        -- 'draft' | 'locked' | 'fired' | 'confirmed' | 'refuted'

-- THE FROZEN CLAIM (immutable after lock):
strategy_config_json  TEXT  -- exact config to test
config_hash           TEXT  -- sha256(strategy_config_json) — the seal anchor
symbols_json          TEXT  -- symbol universe
window_label          TEXT  -- the holdout window [2025-04-30, 2026-04-30]
metric                TEXT  -- e.g. 'sharpe_deflated' | 'net_pnl' | 'prob_sr_gt_0'
threshold             REAL  -- the pre-registered threshold
direction             TEXT  -- '>' | '<'  (passes if realized_metric <direction> threshold)

-- LOWER-TIER EVIDENCE (required to lock):
preholdout_trial_ids_json TEXT  -- refs to exploratory trials that passed pre-holdout
walkforward_ref           TEXT  -- {ref, verdict, ts} for the A.4-2 walk-forward result
drift_check_ref           TEXT  -- {ref, verdict, ts} for the snapshot re-fetch+diff

-- OUTCOME (written after firing):
realized_metric  REAL
verdict          TEXT        -- 'confirmed' | 'refuted'
seal             TEXT        -- integrity seal (see below)

-- PROVENANCE:
source_note      TEXT        -- where the candidate came from + operator note
```

**Lock semantics (what makes the thing immutable):**

- **`draft`** — mutable. Assemble the hypothesis and gather evidence.
- **`lock_hypothesis(id)`** — the freeze. Validates that all required evidence is present (the three refs non-null and valid per Section 4), computes `config_hash` and `seal = sha256(all frozen fields + config_hash)`, sets `status='locked'`, `locked_ts`. The row is now immutable.
- **`record_fire(id)`** — called by the gate when opening for falsification. Sets `fired_ts`. Idempotent per hypothesis (re-reads for crash recovery are fine — same test).
- **`record_outcome(id, realized_metric)`** — computes `verdict` against `threshold`/`direction`, sets terminal `status`.

**Immutability enforcement (guardrail-critical — machine, not trust):**

- Mutation functions refuse if `status != 'draft'` (except `record_fire`/`record_outcome`, which write only their designated fields).
- **Double lock: seal + trigger.** `lock_hypothesis` writes the `seal`. The gate, before each fire, **recomputes the seal over the frozen fields and compares** — if the row was edited outside the API (direct UPDATE), the seal mismatches → the bullet is not touched. Plus a SQLite `BEFORE UPDATE` trigger that aborts if a frozen field changes while `status='locked'`. The seal detects; the trigger prevents. Both, because it is irreversible.

**Fire budget (conception 1 = one shot):**

- Config `HOLDOUT_FIRE_BUDGET = 1`. The gate counts rows with `fired_ts NOT NULL` over `window_label`; if the budget is reached and this hypothesis is not among the fired ones → refuse.
- Exceeding the budget requires explicit operator override, logged via `mex log` — same discipline as admin-merge (#7). No silent bypass.

**Link to deflation:** on fire, `record_fire` also writes a `trials` row with `study_type='confirmatory'`, so #278's N sees it and the second-order deflation (how many confirmatory hypotheses were fired) is accounted. Each table keeps one responsibility.

## Section 3 — The gate in `holdout_access.py`

`open_holdout_for_falsification` is thin: it delegates state logic to `db/hypotheses.py` and reuses the existing `open_holdout` for path resolution.

```python
# data/holdout_access.py  (new)
class HoldoutFalsificationError(HoldoutAccessError):
    """Falsification access without a pre-registered/locked hypothesis, or budget exhausted."""

def open_holdout_for_falsification(rel_path: str, *, hypothesis_id: int) -> Path:
    from db.hypotheses import assert_fireable, record_fire
    assert_fireable(hypothesis_id)          # 1-4: refuse BEFORE touching anything
    record_fire(hypothesis_id)              # mark the fire BEFORE returning the path
    return open_holdout(rel_path, evaluation_mode=True)   # reused path resolution
```

**The chain inside `assert_fireable(hypothesis_id)`** (all-or-nothing — any failure, the bullet is untouched):

1. **Exists** — a row with that id, else error.
2. **`status in ('locked', 'fired')`** — if `draft`, error: "lock it first." (`'fired'` allowed for re-reads.)
3. **Seal intact** — recompute `sha256(frozen fields + config_hash)` and compare to `seal`. Mismatch → tamper error. (Lower-tier evidence was sealed at lock, so this covers it.)
4. **Budget** — count rows with `fired_ts NOT NULL` over `window_label`. If `HOLDOUT_FIRE_BUDGET` reached and this hypothesis not among them → error (override only via `mex log`).

**The point that honors Caveat 5 — the fire is marked BEFORE reading:**

`record_fire` runs *before* returning the Path, not after. Deliberate: claim-then-execute applied to the bullet. *"A partial peek burns the bala única just as surely as a full run"* (Caveat 5). If the process reads the first frame and crashes mid-evaluation, **the fire is already recorded** — correct, because the peek happened. Marking after would let a crash "undo" a peek that did occur.

- `record_fire` is **idempotent** per hypothesis: sets `fired_ts` only if null; multiple `rel_path` reads of the same hypothesis = one fire. On that first call it writes the confirmatory `trials` row for deflation.

**Compatibility:**

- `open_holdout(evaluation_mode=True)` is unchanged — now the **custodial** path (manifest, drift, integrity). The drift-check harness needs no hypothesis.
- `HoldoutFalsificationError` subclasses `HoldoutAccessError`, so existing handlers still catch it.
- The A.4-3 falsification harness migrates from `open_holdout(...)` to `open_holdout_for_falsification(..., hypothesis_id=…)`. That is the only call-site whose semantics change.

## Section 4 — The promotion contract: #322 stops being prose

Today A.4-3 is blocked *"until #322 closure criteria are all met"* — prose someone must remember and judge. With the keystone, **"criteria met" = `lock_hypothesis` succeeded.** The blockage goes from decree to verifiable state.

The three #322 criteria (Caveat 5: *"re-tune produces candidates AND walk-forward passes AND drift check completed"*) become validations `lock_hypothesis` enforces before freezing:

**a. Config provenance — the strongest check, 100% machine.**
`lock_hypothesis` refuses unless `config_hash` matches **at least one exploratory `trials` row with `status='ok'`**. The config you are about to fire **must be one that actually emerged from registered search** — not hand-tuned after peeking, not invented at lock time. This closes the p-hacking path Voronov worried about at the root: the candidate is, at minimum, **counted in the deflation N**. The keystone of the keystone.

**b. Walk-forward passed — attested ref with verdict.**
`walkforward_ref` is structured JSON `{ref, verdict, ts}`. `lock_hypothesis` refuses if `verdict != 'pass'`. The operator fills the ref from the real A.4-2 run; the machine enforces presence + verdict; the real ref is reviewed by a human in the locking PR (same "human gate" as the allow-list).

**c. Drift check completed — attested ref with verdict.**
`drift_check_ref` likewise: `{ref, verdict, ts}`, refuses if `verdict != 'pass'` (drift in the F&G/funding snapshots). The re-fetch+diff runs via the **custodial** path (`open_holdout(evaluation_mode=True)`), before and without spending the bullet.

**d. Complete claim.** `metric`, `threshold`, `direction` set (you cannot lock a hypothesis without saying what counts as passing).

All four pass → compute seal, `status='locked'`. Else → `HypothesisLockError` naming the missing criterion.

**Deliberately outside the machine lock:**

- *What threshold* counts as "pre-holdout passed" is operator judgment, pre-registered in the hypothesis via `preholdout_trial_ids` (the machine verifies the refs are real `ok` trials with matching config; it does not opine on whether the threshold was wise).
- Renewable-tier transitions (shadow→active) — Section 5, protocol, not code.

Net effect: a falsification access to the holdout is **impossible** without a row proving, by machine, that the candidate came from registered search, that walk-forward passed, and that there is no drift.

## Section 5 — What stays protocol (the renewable tiers)

Deliberate, not laziness: **machine only where failure is irreversible.** An error in the renewable tiers is recoverable (wait for more shadow data; demote from active). So for this spec they are documented, not coded.

- **Pre-holdout threshold** (what promotes search → lockable candidate): runbook + operator judgment, pre-registered in the hypothesis's `preholdout_trial_ids`. The machine already verifies those refs are real `ok` trials (4a); it does not opine on the threshold.
- **Shadow → active**: documented protocol pointing at the **existing** KS v2 shadow→active machinery. Promotion to shadow *should* register confirmatory trials to keep renewable deflation honest — but enforcing that is **future epic B** (the full code-enforced ladder), explicitly parked, not forgotten.
- **The gate runbook** materializes as pattern `.mex/patterns/firing-the-holdout.md` (GROW step) — the draft → gather evidence → lock → fire → outcome sequence with its failure modes.

Boundary in one line: **this spec code-enforces the single irreversible shot (the locked holdout); everything renewable and judgment-based stays protocol + existing infra, with epic B parked for when a deployable candidate exists.**

## Section 6 — Testing (test the gate without ever touching the bullet)

Principle: **all gate logic is tested against a throwaway `hypotheses` table + a temporary holdout root**, never the real `data/holdout/`. Same pattern as `test_trials_registry.py` (`tmp_path`, `monkeypatch` of `DB_FILE`).

**Contract-pinning tests (`tests/test_hypotheses_gate.py`, new):**

- `lock` refuses if `config_hash` matches no `ok` exploratory trial → **provenance (4a)**.
- `lock` refuses if `walkforward_ref` or `drift_check_ref` have `verdict != 'pass'` → **(4b/4c)**.
- `lock` refuses if `metric`/`threshold`/`direction` missing → **(4d)**.
- A `locked` hypothesis is **immutable**: mutating a frozen field is refused; editing the row by direct UPDATE makes the seal mismatch and the gate refuses → **tamper**.
- `assert_fireable` refuses `draft` / nonexistent id / broken seal.
- **Budget**: with `HOLDOUT_FIRE_BUDGET=1`, a second fire of a distinct hypothesis is refused.
- `record_fire` **idempotent**: re-reading the same hypothesis does not count twice.
- **Claim-then-execute (the Caveat 5 test)**: `record_fire` sets `fired_ts` *before* returning the path — verified by simulating a read failure after the fire and asserting `fired_ts` is already recorded. A partial peek counts as a fire.
- `record_fire` writes the confirmatory `trials` row → deflation sees it.

**Holdout isolation stays green:**

- `db/hypotheses.py` does **not** import `open_holdout` nor reference `data/holdout/` as a string — it only touches state in `signals.db`. No allow-list needed.
- `open_holdout_for_falsification` lives in `data/holdout_access.py`, already whitelisted — no new module to list.
- **Two PR-explicit adjustments** (reviewed as the human gate, #2):
  1. The AST scanner in `test_holdout_isolation.py` must recognize `open_holdout_for_falsification` as a legitimate entry point (today it keys on `open_holdout` / root references).
  2. The A.4-3 falsification harness, when it migrates to the new function, is added to `HOLDOUT_LEGITIMATE_MODULES` with justification.
- The suite goes from 15/15 to 15 or 16 (depending on whether the harness lands in this PR or the consuming one). The exact number is fixed in the plan.

**Meta guard:** a test asserting the real `_HOLDOUT_ROOT` is never read during the suite (gate tests point at temporary roots or assert the `raise` happens before file access).

---

## Out of scope (parked, not forgotten)

- **Capa 0 — cost-model v3.** Separate brainstorm→spec→plan (FINDINGS "Recommended next work"). Precondition for credible search; orthogonal to this gate.
- **Capa 1 — edge-search methodology.** Open research; runs on top of Capa 0 and this gate.
- **Epic B — full code-enforced ladder** (shadow→active transitions gated by code, per-tier deflation enforcement). Build when a deployable candidate exists.

## Pre-push (guardrail-critical)

The gate guards the irreplaceable resource. Per the project's adversarial-audit-before-push pattern: with all commits green locally, dispatch an independent adversarial audit (separate from the implementer) on the diff before pushing. Lenses: (1) can falsification access succeed without a locked hypothesis? (2) does a partial peek truly record a fire (fire-before-read)? (3) can the seal/trigger be bypassed by a direct UPDATE? (4) does the budget count fires correctly across distinct hypotheses? (5) does `test_holdout_isolation` stay green and does the AST scanner truly recognize the new entry point? Amend on findings, then push and open the PR.
