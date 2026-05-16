# Pre-reg: Multi-tenant B.8 — production data migration (#261)

**Date:** 2026-05-16
**Branch:** `feat/multi-tenant-b8-migration`
**Parent epic:** #253
**Unblocks:** `trading.sdar.dev` user invitations per #271

## 1. Background

B.1 added nullable `tenant_id` columns to `positions`, `signal_outcomes`,
`notifications_sent`, `portfolio_health_events`, plus new tables `capital` and
`user_preferences`. B.2–B.7 enforce tenant scoping on every read/write. But
the production database still has rows from before multi-tenancy that carry
`tenant_id IS NULL` — invisible to per-tenant queries today.

B.8 is the one-shot script that stamps Samuel's user_id onto those legacy rows
and creates his initial capital row, so the production system goes from
"Samuel sees nothing" to "Samuel sees his historical state, unchanged."

## 2. Locked decisions

### 2.1 Dry-run default

The script's default mode is **dry-run**. Real writes require `--execute`.
Rationale: a production DB is the highest-stakes target in this codebase
besides the holdout. Defaulting to dry-run protects against a typo or muscle
memory; the operator must consciously opt into the write.

### 2.2 Required arguments

```
scripts/migrate_to_multitenant.py
  --user-id INT              required, must exist in auth.users table
  --initial-balance FLOAT    default: 10_000.0 (matches INITIAL_CAPITAL_DEFAULT)
  --dry-run                  default behavior (no writes). Mutually exclusive with --execute
  --execute                  required for real writes. Mutually exclusive with --dry-run
  --force                    overwrite existing capital row (default: refuse)
```

Validation: the script MUST verify `--user-id` resolves to a real row in
`users` BEFORE any other work. If absent, exit 2 with a list of valid user IDs.

### 2.3 What the script does (real mode)

1. **Validate** user exists (`SELECT id, email FROM users WHERE id = ?`).
2. **Snapshot counts** per per-user table: `(total_rows, null_tenant_rows)`.
3. **Call `backfill_tenant(user_id)`** — the existing helper in `db/schema.py`
   that does `UPDATE … SET tenant_id = ? WHERE tenant_id IS NULL`.
4. **Create capital row** via `db_upsert_capital` IF no row exists, or skip if
   exists without `--force`, or overwrite with `--force`. Anchor balance =
   `--initial-balance`. `peak_balance = balance`. `max_drawdown_pct = NULL`.
5. **Re-snapshot counts** — verify `null_tenant_rows == 0` post and
   `total_rows` unchanged.
6. **Spot check:** run `SELECT * FROM positions WHERE tenant_id = ? ORDER BY id DESC LIMIT 10` and print symbols/dates so the operator can sanity-check.

### 2.4 Idempotency contract

Re-running the script in either mode is a no-op for already-migrated tables:
- `backfill_tenant` only touches `tenant_id IS NULL` rows. Second run updates 0.
- Capital row creation refuses overwrite without `--force`.

The script logs "Already migrated" if `null_tenant_rows == 0` for all tables
AND the capital row exists. It still completes with exit 0 in that case
(idempotent re-run is success, not failure).

### 2.5 What the script does NOT do

| Out of scope | Rationale |
|---|---|
| Replay closed-position P&L into capital | B.2 anchor convention: hand-set initial balance, not retroactive |
| Migrate `users` table rows | users.id is the authority; migration consumes it, never modifies |
| Modify schema | B.1 already added the columns; this script touches data only |
| Backup the DB before running | Operator responsibility — script logs the path it's writing to and exits if `DB_FILE` env var unset |
| Lock the DB during migration | SQLite serializes writes; the operator runs this during a quiet window |
| Handle multi-user splits (e.g., move some rows to user A, others to user B) | Single-user migration is all #261 asks for. If we ever need a split, that's a separate ticket |

### 2.6 Failure semantics

If validation step 6 finds `null_tenant_rows > 0` post-migration OR
`total_rows` changed: exit 1 with the count diff and the affected table.
The data layer's idempotent design means a rollback isn't needed — just
re-run after diagnosing.

If the script crashes mid-write: SQLite's transaction semantics mean either
all `backfill_tenant` UPDATEs commit (single commit at end of the function)
or none. Capital insert is a separate transaction. Re-running picks up where
it left off.

## 3. Tests (locked before writing)

| Test | Asserts |
|---|---|
| `test_dry_run_does_not_write` | Pre-populate NULL rows, run dry-run, verify rows still NULL |
| `test_execute_stamps_tenant_id` | Pre-populate NULL rows, run --execute, verify all rows have tenant_id = user_id |
| `test_idempotent_second_run_noop` | Run --execute twice, second run updates 0 rows, exit 0 |
| `test_rejects_unknown_user_id` | Run with --user-id 99999, exit code 2 |
| `test_creates_capital_row` | After --execute, capital table has row with balance = --initial-balance |
| `test_refuses_capital_overwrite_without_force` | Pre-create capital row, --execute without --force exits 1 |
| `test_force_overwrites_capital` | Pre-create capital row, --execute --force replaces it |
| `test_dry_run_and_execute_mutually_exclusive` | `--dry-run --execute` exit code 2 |
| `test_neither_flag_defaults_to_dry_run` | No `--execute` flag → dry-run (no writes) |
| `test_validates_row_count_unchanged` | Spot: total positions count pre = post |

## 4. Single-iteration discipline

If a test fails: lock-violation → STOP, escalate. Implementation bug → fix.
Do not soften test expectations to make impl pass.

## 5. Done when

- All 10 tests above pass
- Targeted regression scope green (B.1 + B.2 + B.5 + B.7 + B.8 + parity)
- PR description quotes locks §2.1–§2.6 verbatim
- README in script docstring matches CLI behavior
