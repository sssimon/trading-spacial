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

## Orthogonal-flake list

The following failures are known intermittent issues with documented tracking. They may be bypassed via admin merge if they are the ONLY failures in a CI run AND each appears in the list below.

### `tests/test_setup.py::*` — `sqlite3.OperationalError: database is locked`

- **Tracking:** #495
- **Pattern:** Any test in `tests/test_setup.py` failing with `sqlite3.OperationalError: database is locked`, typically at `db/schema.py:52 PRAGMA journal_mode=WAL` in `init_db` lifespan.
- **Root cause (partial):** `init_db()` opens a raw connection for the PRAGMA in lifespan; the `kill_switch_v2_calibrator` background thread may hold a connection that races with this on a fresh test DB. PR #499 swept the most-common read-only `transaction()` callsites that were contributing to contention but did not eliminate the PRAGMA-WAL race itself.
- **Affected test names (observed): ** `test_2_get_setup_without_token_404`, `test_3_get_setup_wrong_token_404`, `test_4_get_setup_correct_token_returns_html`, `test_5_post_setup_creates_admin_and_marks_completed`, `test_7_post_setup_weak_password_400`, `test_8_disable_web_setup_returns_404`, `test_10_env_vars_xor_only_email_fails_at_boot`, `test_setup_status_endpoint_public`.
- **Removal condition:** when a fix for the PRAGMA-WAL race lands and `tests/test_setup.py` is observed green on 20+ consecutive CI runs across at least 3 distinct PRs, this entry is removed.

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
