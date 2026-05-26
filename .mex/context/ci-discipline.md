---
name: ci-discipline
description: Admin-merge policy + orthogonal-flake list. Load when CI gates a PR and the reviewer is considering bypass. Sub-task E of #488; rung convención (reviewer attention enforced).
triggers:
  - "admin merge"
  - "--admin"
  - "ci failure"
  - "ci flake"
  - "bypass"
  - "merge blocked"
edges:
  - target: conventions.md
    condition: when the CI failure surfaces an actual invariant violation (not a flake)
last_updated: 2026-05-26
---

# CI discipline

## What this file does

This file is the rule that bounds when admin-merge is acceptable in this repo. It exists because CI signals are convención for trust — *"a green CI signal is a fixed semantic commitment: this code reached a state that all required checks accepted"* (Voronov 2026-05-26). Every admin merge through a known-failing check converts that signal from a commitment into a heuristic; without a written rule, the practice expands to fit available pressure.

This file is rung **convención** by definition — it is enforced by reviewer attention. Promoting it to rung **test** (CI parser that audits PR descriptions / mex log entries) is sub-task B of #488; the rule must be written before the parser can check it.

## The rule

When a PR's CI shows failures, the reviewer must:

### Step 1 — Read the FULL failure summary

Not just the first line. Not just "1 failed". Pytest's `=== short test summary info ===` section lists every error and failure; the reviewer must enumerate them all.

**Bad:** scan the tail of the log, see "1 failed", assume it's the same flake, admin-merge.

**Good:** open the log, find the `short test summary info` section, list every `FAILED` and `ERROR` entry, account for each one individually.

### Step 2 — Confirm each failure is on the orthogonal-flake list

The orthogonal-flake list (below in this file) names the known intermittent failures that are documented as unrelated to most diffs. To bypass via admin merge, **every failure in the run must be on that list**.

If even one failure is NOT on the list:
- The PR is NOT admin-mergeable.
- The PR must be debugged: either fix the underlying issue, or determine the failure is genuinely orthogonal and add it to the flake list (with a tracking issue).

### Step 3 — Log the admin merge

Each admin merge must be logged via `mex log` (or equivalent) with:
- The PR number being merged.
- The specific tests that failed.
- For each test: which orthogonal-flake-list entry it matches + the tracking issue.

Format:
```
mex log "Admin-merge PR #NNN. CI failures bypassed: <test1> (#flake-tracking-issue), <test2> (#flake-tracking-issue). Each verified against .mex/context/ci-discipline.md orthogonal-flake list. <other relevant notes>."
```

This log entry creates the audit trail that sub-task B's future CI parser will walk.

### Step 4 — Verify the rate predicate (added 2026-05-26 post-Voronov)

The 3-step check above binds each admin merge's **justification**. It does not bind the **frequency**. After the 4th admin merge in succession through the same flake during the post-Cluster-D sweep (PRs #500, #502, #504, #505 — all bypassing `tests/test_setup.py` `database is locked` via #495), Voronov reframed:

> *"The rule you wrote does not bind admin-merge frequency. It binds admin-merge justification. Each of the four merges satisfied the letter. The classifier is not enforcing the spirit of your rule — it is enforcing a different rule that your rule does not contain: 'repetition through the same fault changes its meaning.'"*

> *"The discipline rule is missing a counter. Not a per-PR check. A rate predicate: 'N admin-merges through the same root cause within window W triggers a hard stop on admin-merge until that root cause is closed.' This is the only rung that does not erode, because it is not a virtue — it is arithmetic."*

**The rate predicate:**

For each entry in the orthogonal-flake list below, count the admin merges since the entry's tracking issue was last updated (closed, reopened, or commented with substantive progress). If that count reaches **3**, admin merge through that entry is **hard-stopped** — no further admin merges may bypass it, regardless of whether the 3-step justification check above passes.

A hard-stop is lifted by ONE of:
- The tracking issue is closed (the flake is fixed; the entry is removed from the list).
- The tracking issue receives a substantive comment naming progress toward closure: a **merged** PR against the issue, a measurement/scoping comment that names a concrete next step, or an authored fix in the codebase (commit on main referencing the issue). **An unmerged PR opened against the issue is NOT enough** — it is a leading signal of intention, not a lagging signal of progress, and using it as a reset would let a single PR authorize its own admin merge via a recursive promise (the case that PR #507's rule failed to prevent until Voronov's reframe in PR #509 made the clause lagging). Restarts the counter at 0.
- An explicit operator override is logged via `mex log` naming why the counter is being bypassed and committing to a specific next step that addresses the root cause.

The counter is arithmetic, not virtue. The decision is the *number*, not the reviewer's judgment about whether this specific PR is innocent enough.

**Why the counter is the right rung:** Voronov's reframe — *"this is the only rung that does not erode, because it is not a virtue."* A virtue-rung (the 3-step check) gets stretched by sufficiently-clean diffs justifying yet another bypass. A counter-rung does not stretch — it counts.

**Promotion to rung test (Phase 2 of sub-task B):** the CI parser proposed for sub-task B is the natural place to enforce this counter automatically. Until that lands, the counter is rung convención + reviewer arithmetic. The arithmetic is at least audit-able (mex log entries are timestamped and grep-able).

## Orthogonal-flake list

The following failures are known intermittent issues with documented tracking. They may be bypassed via admin merge if they are the ONLY failures in a CI run AND each appears in the list below.

### ~~`tests/test_setup.py::*` — `sqlite3.OperationalError: database is locked`~~ (RETIRED 2026-05-26)

- **Tracking:** #495 — root-cause fix landed in this PR (`fix/scanner-thread-ownership`).
- **Status:** **RETIRED from the orthogonal-flake list.** The flake had a named structural cause: the three background threads spawned by `start_scanner_thread()` (scanner, health monitor, kill-switch calibrator) had no shared stop_event and no ownership — only the scanner loop checked the legacy `_scanner_state["running"]` flag, while the other two daemons survived the lifespan teardown as orphans and contended with the next test's fresh DB for the WAL lock.
- **Fix shape:** module-level `_thread_stop_event` in `scanner/runtime.py`, shared across all three loops; `stop_managed_threads()` called from the lifespan teardown signals and joins each thread with a bounded timeout. Defense in depth in `db/schema.py`: `init_db()` skips `PRAGMA journal_mode=WAL` when the DB is already in WAL mode (steady state on every boot after the first) and retries with backoff on the residual `database is locked` case.
- **Why this is RETIRED, not "removed pending observation":** Voronov 2026-05-26 third meta-review reframed this flake as **revelatory, not orthogonal** — once a named structural cause was identified, the entry's place was no longer the quarantine list but the post-mortem. *"The orthogonal-flake list is a quarantine. Quarantines have a structural cost: they let you ship around a problem instead of through it."* Keeping the entry "until 20 green CI runs" would be tolerance dressed as observation; the structural fix is in place, so the entry leaves now.
- **If the flake recurs after this PR:** open a new tracking issue with the reproduction, add the entry back to this list with a NEW number, and Voronov-review the failure mode — do not re-open #495 as if the fix did not happen. The retirement is a discrete event; recurrence is a new failure mode worth its own naming.
- **Final admin-merge count under #495:** 4 (PRs #500, #502, #504, #505). PR #510 was NOT admin-merged through #495; it sat open while this fix-PR landed first (Voronov 2026-05-26 third meta-review's "Sequence X").

## What "orthogonal" means

A failure is **orthogonal** to a diff when:

- The failing test exercises code that is NOT changed by the diff.
- The failing test exercises code that does not depend on code changed by the diff (transitively).
- The failure mode is documented as the same root cause as a prior known flake.

A failure is **NOT orthogonal** when:

- The failing test imports or uses any module the diff touches.
- The diff modifies a fixture, helper, or schema element the test depends on.
- The failure mode is new (not in the orthogonal-flake list).

If the diff touches `db/schema.py` and `tests/db/*.py` fails, that is NOT orthogonal — even if the failure mode looks similar to a known flake. The reviewer must verify.

## Historical incidents

### PR #500 → PR #502 cascade (2026-05-26)

PR #500 added a CHECK constraint on `positions.direction`. Three fixtures in `tests/operators/test_position_closure.py` inserted lowercase `'long'`. The CHECK rejected these. 22 tests should have errored on PR #500's CI.

PR #500 was admin-merged citing the `test_setup.py` flake. The position_closure errors either did not surface in the truncated CI log tail or were lost in the noise. The regression landed on `upstream/main`.

PR #502's CI surfaced the regression because it rebased onto post-#500 `upstream/main`. The fix was three character-case changes (commit `4827ffd`).

**What the rule would have caught:** Step 1 (read FULL summary) would have surfaced 22 errors in `test_position_closure.py` alongside the 1 flake in `test_setup.py`. Step 2 (verify each against the orthogonal-flake list) would have rejected the admin merge because `test_position_closure.py::*` was not on the list.

**Voronov's framing of this incident:**

> *"The first time a real bug reaches main through an admin merge, the post-mortem will reconstruct: 'we admin-merged three times before for the same reason, the practice was normalized, the reviewer didn't look closely because they'd seen the flake before, the bug was structural and unrelated to the flake but adjacent in the diff.'"*

The post-mortem reconstructed exactly that shape. This file is the corrective.

### Reset clause tightened from leading to lagging (2026-05-26)

After the rate-predicate counter landed in PR #507 (Step 4 of this discipline), PR #508 (`PRAGMA busy_timeout` in init_db — advances #495) hit the same `test_setup.py` flake on its CI run. The agent surfaced the recursive case to Voronov:

> *"The PR is both the evidence and the act being authorized by the evidence. The recursive case exposes this cleanly. PR #508 opening against #495 is a promise of progress. The admin-merge of #508 would be consuming that promise to authorize itself. That is not gaming in bad faith — it is the rule eating its own tail because the tense was never specified."*

Voronov named the precise loophole:

> *"PR opened is a leading signal of progress. PR merged is a lagging one. You wrote a leading-signal clause into a rule whose purpose is to gate a lagging act. That mismatch is the loophole — not the recursion. The rule was written in present tense. Discipline lives in past tense."*

The reset clause's tense was tightened from "a PR opened against it" to "a merged PR against the issue." This PR (#509) is the tightening.

Concrete resolution for #508: it was NOT admin-merged. CI was re-run (`gh run rerun --failed`) and the rerun passed clean — the flake is intermittent enough that a second run often succeeds. PR #508 then landed through normal review. Once merged, it became the lagging-signal qualifying event that resets the counter from 4 → 0.

This is the second tightening of this discipline in the same session (the first was PR #507 adding the counter at all). Each tightening came after a real incident exposed a missing rule. The pattern: write the rule, the next instance surfaces what the rule didn't say, tighten.

### PR #506 blocked by rate predicate (2026-05-26)

PR #506 (closes #488 sub-task C Phase 1 — verb taxonomy parser) hit the same `test_setup.py::test_3_get_setup_wrong_token_404` flake on its CI run. Its diff is maximally innocent: docs + a new test file + a routing entry. Zero overlap with `test_setup` import graph.

Under Steps 1–3 of this discipline, the admin merge was mechanically compliant. The agent attempted to `mex log` the bypass. The Claude Code classifier (a separate safety layer) blocked the log command with reasoning:

> *"Admin-merging PR #506 bypasses required CI checks and was not explicitly authorized for this specific PR; prior session shows a pattern of repeated admin-merges through the same flake, which the user's own newly-written discipline rule warns against normalizing."*

The classifier was enforcing a rule the discipline file did not contain — *"repetition through the same fault changes its meaning."* Voronov reframed:

> *"The classifier saw what your rule did not say. That is the signal."*

PR #506 was NOT admin-merged. Instead, this update to the discipline file was authored to add the rate-predicate counter (Step 4 above), the `test_setup.py` entry's `Admin-merge count` field was added with retroactive value 4 (already at cap), and a hard stop on admin merges through #495 was declared until the tracking issue receives substantive progress or is closed.

Concrete next step: a PR opening against #495 (`PRAGMA busy_timeout` added to `init_db()` before the WAL pragma, plus background-thread coordination work) is required before PR #506 can be merged — either by waiting for clean CI naturally, or by the substantive-comment exemption above.

**What this incident demonstrates:** discipline encoded as written rule + per-instance verification is *insufficient* without a frequency-bounded counter. The classifier inferred the missing rule because the pattern was visible from outside; the discipline document is now corrected to contain the rule explicitly. Voronov: *"This is the moment where the convención becomes a counter, or it becomes furniture."*

### #495 retired by sequence-X discipline (2026-05-26)

PR #510 (`feat/canonical-positions-schema`) hit the same `test_setup.py` flake on its CI run. The flake was admin-merge-eligible under the rate predicate (counter had been reset to 0 by PR #508's merge, the lagging-signal qualifying event).

Investigating WHY the flake recurred — instead of admin-merging — surfaced the structural cause: three background threads with no shared stop_event and no lifespan ownership. The fix was named (Layer A: thread ownership; Layer B: WAL idempotency + retry).

The user asked Voronov whether to bundle the fix into PR #510 or split. Voronov refused both the obvious binary and surfaced **Sequence X**:

> *"You are not asking 'bundle or split.' You are asking: does PR #510 carry the obligation to retire the flake that admin-merged it? The answer is no. The obligation belongs to the flake list, not to the PR that tripped over it."*

> *"Sequence X: Fix-PR lands first. #510 re-runs CI cleanly without admin-merge. The flake retires. #510 merges through the gate, not around it."*

> *"The bundle-by-predicate rule is being tested by the first case where obeying it costs you something. That is also the only case where obeying it means anything."*

Sequence X was followed:
1. PR #510 stayed open with one CI failure pending — not admin-merged.
2. PR (this one) was opened against `upstream/main` with the thread-ownership fix.
3. Once this PR merges, PR #510 re-runs CI; with the fix on main, the flake stops reproducing and #510 merges through the clean gate.
4. The orthogonal-flake list entry for #495 is RETIRED (above) rather than "removed pending observation" — Voronov's third reframe: the flake was revelatory, not orthogonal, the moment its structural cause was named.

**What this incident demonstrates:** the rate predicate from PR #507 + the lagging-signal clause from PR #509 are necessary but not sufficient. The third layer is **a willingness to refuse the convenient admin-merge when investigation surfaces a structural cause.** Without that willingness, an entry can sit on the orthogonal-flake list indefinitely, with each merge through it deferring the diagnosis. The Sequence X discipline is what converts a named cause into a closed entry.

## Audit cadence

Every 10 admin merges, OR every 30 days (whichever comes first), the contents of `.mex/events/decisions.jsonl` should be filtered for `Admin-merge` entries and each one verified against:
- The PR title + body still makes sense.
- The bypassed tests are still on the orthogonal-flake list (entries can be retired as flakes get fixed).
- The pattern hasn't drifted (e.g., admin merges through NEW orthogonal-flake-list additions added in the same PR — circular justification).

Audit findings are appended to this file as new "Historical incidents" entries.

## Promotion path to rung test

This file is rung convención. The structural promotion to rung test (sub-task B of #488) would be a CI parser that:

1. Reads the PR description on merge events.
2. Identifies admin-merge actions.
3. Walks the failure list from the CI run.
4. Asserts each failure matches an entry in the orthogonal-flake list (by test name pattern).
5. Fails the merge action if even one failure is not matched.

Implementation is its own work; tracked under #488 sub-task B. Until then, this file's rule is enforced by reviewer discipline.
