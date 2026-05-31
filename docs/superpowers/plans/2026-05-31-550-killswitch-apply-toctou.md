# #550 — kill-switch apply/ignore TOCTOU

Third #543-audit follow-up, split into its own PR (different predicate:
concurrency on the admin endpoints, not the live-DD fail-closed contract).

## Problem

`api/kill_switch.py` apply (`kill_switch_apply_recommendation`) and ignore
(`kill_switch_ignore_recommendation`):

1. Pre-check read (`snapshot_connection`): 404 if missing, 400 if not pending.
2. **apply** then `save_config(...)` — writes the slider to `config.json`.
3. UPDATE (separate `transaction()`): `SET status=... WHERE id=?` — **no
   `AND status='pending'`, no rowcount check.**

The UPDATE has no concurrency guard, and in apply the config write precedes it,
gated only on the stale pre-check. An `apply` racing an `ignore` (or the
calibrator's `supersede`) lets both pass the pre-check; apply mutates config;
both UPDATEs run unguarded (last writer wins). The row can end up `ignored` while
the aggressiveness slider was already changed.

## Fix

Make the atomic UPDATE the authority; the config side effect runs only for the
request that actually transitions the row.

1. Pre-check stays (fast 404 / 400 + slider-range validation; not authoritative).
2. Atomic UPDATE first: `... WHERE id=? AND status='pending'`; check `rowcount`.
3. `rowcount == 0` -> **409 Conflict**; no config change.
4. apply only: `save_config(...)` after winning the transition.

Residual (documented): if `save_config` throws after the UPDATE commits, the row
is `applied` but the slider isn't persisted — a loud 500, single request, no
silent double-apply. File+DB atomicity would need a 2-phase commit we are not
building.

## Tests (TDD, red->green)

`tests/test_api_kill_switch_parity.py`:
- `test_apply_lost_race_returns_409_and_does_not_write_config` — seed a row, a
  concurrent transition flips it to `ignored`, a faked `snapshot_connection`
  still reports `pending`; the guarded UPDATE affects 0 rows -> 409 AND
  `save_config` is never called. (Against the old unguarded code this returns 200
  + writes config, so it fails on the old path.)
- `test_ignore_lost_race_returns_409` — same for ignore (real row `applied`).

The existing happy-path tests (`test_apply_recommendation_marks_applied_and_writes_config`,
`test_apply_already_applied_returns_400`, `test_ignore_recommendation_marks_ignored`,
`test_ignore_recommendation_not_found_returns_404`) regress the reorder.

## Verify

- `python -m pytest tests/test_api_kill_switch_parity.py -q` (13 pass).
- 161 regression green (parity + calibrator + health dashboard + endpoints).
- Independent review. 1 PR, Closes #550. Merge requested separately.
