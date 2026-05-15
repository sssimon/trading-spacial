# Pre-registration (light) — Multi-tenant B.1 schema design (#254)

**Fecha:** 2026-05-15
**Status:** DRAFT — pre-reg before schema work. Locks WHICH tables get tenant_id + design decisions, NOT migration timing or API enforcement.
**Autor:** Claude Opus 4.7 en colaboración con sssamuelll
**Tipo:** schema design pre-reg for Epic B #253 (multi-tenancy)
**Trigger:** Operator decision 2026-05-15 to start Epic B B.1 (#254) after Epic A archive + #271 override.
**Issue:** #254

---

## §1 · Contexto y alcance

### §1.1 — Trigger

Epic B (#253) ready to execute after today's events:
- Epic A (#246) archived as not-passing (PR #316 "no demonstrable edge"; #338 regime-allocation pivot also failed)
- Direction A Phase D2 (PR #357) verdict EDGE_WEAK confirmed Q2 (operator MANUAL discretion = real edge)
- #271 invitation guardrail overridden today with documented rationale

Operator needs per-user data isolation for papá + María + Samuel to use the system with their own positions/capital without crossing data.

### §1.2 — B.1 scope (this PR)

**Adds:**
- `tenant_id` columns (nullable initially) to per-user tables
- New tables: `capital`, `user_preferences`
- Indexes for tenant-scoped query patterns
- `backfill_tenant(user_id)` function (sets NULL tenant_ids to given user_id)
- Synthetic-fixture tests for schema creation + migration + backfill

**Does NOT add:**
- API enforcement (B.5 — #258)
- Frontend user context (B.6 — #259)
- IDOR test suite (B.7 — #260)
- Per-user capital tracker logic (B.2 — #255)
- Per-user position lifecycle integration (B.3 — #256)
- Production data migration (B.8 — #261)
- NOT NULL constraint hardening (deferred until B.5 + B.8 done; SQLite can't ALTER NOT NULL on existing column without table rebuild)
- kill_switch_* tables tenant scoping (deferred — keep global for safety conservatism)

### §1.3 — Architectural decisions LOCKED per #253

- Scanner stays global (one process, same OHLCV cache, same signals)
- Capital + positions per-user
- Notification preferences per-user
- Threat model: JWT-derived user_id, never from request

This pre-reg implements the SCHEMA layer of those decisions. Enforcement of "never from request" is B.5 scope.

---

## §2 · Table classification

### §2.1 — Per-user (add `tenant_id` nullable in B.1)

| Table | Reason |
|---|---|
| `positions` | Each user has their own positions |
| `signal_outcomes` | Per-user reaction tracking (which user acted on which signal) |
| `notifications_sent` | Each user has their own notification queue |
| `portfolio_health_events` | Portfolio is per-user |

### §2.2 — Global (no `tenant_id`)

| Table | Reason |
|---|---|
| `scans` | Scanner output is universal market data |
| `webhooks_sent` | Audit log of webhook deliveries (system-level) |
| `tune_results` | Auto-tune is system-wide parameter tuning |
| `symbol_health` | Data quality state (universal) |
| `symbol_health_events` | Universal events |

### §2.3 — Deferred (kept global for now, may move per-user later)

| Table | Reason for defer |
|---|---|
| `kill_switch_decisions` | Could be per-user but conservative default = global (safety) |
| `kill_switch_v2_state` | Per-symbol state; orthogonal to user |
| `kill_switch_v2_baseline` | Per-symbol baseline; orthogonal to user |
| `kill_switch_recommendations` | Could be per-user; defer |

Document in code + this pre-reg. Future sub-issue can move these per-user if needed (no breaking change since adding tenant_id is non-destructive).

### §2.4 — New tables in B.1

#### `capital`

```sql
CREATE TABLE capital (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         INTEGER NOT NULL,           -- FK to users.id (enforced in app)
    balance           REAL NOT NULL,              -- current notional capital
    peak_balance      REAL NOT NULL,              -- running max for drawdown calc
    max_drawdown_pct  REAL,                       -- worst drawdown observed
    updated_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_capital_tenant ON capital(tenant_id);
```

Design rationale:
- Single row per user (enforced by UNIQUE INDEX)
- `peak_balance` tracks high-water mark for drawdown calc
- `max_drawdown_pct` stored separately so it persists even after equity recovers
- `tenant_id NOT NULL` — new table, no migration concern, immediate constraint

#### `user_preferences`

```sql
CREATE TABLE user_preferences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL,
    symbol_filter_json  TEXT,                      -- JSON array of symbols user wants signals for
    min_score           INTEGER DEFAULT 4,         -- min score threshold for user's notifications
    notify_channels_json TEXT,                     -- JSON {telegram_chat_id, email, etc.}
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_user_prefs_tenant ON user_preferences(tenant_id);
```

Design rationale:
- Single row per user
- JSON fields for flexible filter sets without further schema churn
- `min_score` default 4 matches current global default

---

## §3 · Migration approach

### §3.1 — Idempotent ALTER for existing tables

For each per-user table (positions, signal_outcomes, notifications_sent, portfolio_health_events):

```python
try:
    con.execute("ALTER TABLE <table> ADD COLUMN tenant_id INTEGER")
    log.info(f"DB migration: added tenant_id to <table>")
except sqlite3.OperationalError:
    pass  # column already exists
```

Same pattern as existing migrations in `db/schema.py` (e.g., `atr_entry`, `be_mult` for positions; `probation_*` for symbol_health).

### §3.2 — Indexes (idempotent)

```sql
CREATE INDEX IF NOT EXISTS idx_positions_tenant ON positions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_signal_outcomes_tenant ON signal_outcomes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_notif_tenant_unread
    ON notifications_sent(tenant_id, sent_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_portfolio_events_tenant_ts
    ON portfolio_health_events(tenant_id, ts DESC);
```

Note: `idx_notif_tenant_unread` REPLACES the existing `idx_notif_sent_unread` since the new index includes tenant_id (better selectivity). Old index dropped if present.

### §3.3 — Backfill function

```python
def backfill_tenant(user_id: int) -> dict[str, int]:
    """Set tenant_id = user_id for all rows where it is currently NULL.

    Idempotent: running twice is a no-op (no NULL rows after first run).
    Returns dict of {table_name: rows_updated} for observability.

    NOT called from init_db(). Caller (B.8 migration script or test setup)
    invokes explicitly with the user_id to backfill into.
    """
    affected = {}
    for table in PER_USER_TABLES:
        result = con.execute(
            f"UPDATE {table} SET tenant_id = ? WHERE tenant_id IS NULL",
            (user_id,),
        )
        affected[table] = result.rowcount
    con.commit()
    return affected
```

### §3.4 — NOT NULL enforcement (deferred)

SQLite cannot `ALTER COLUMN ... NOT NULL` on existing columns without recreating the table. Standard pattern:
1. CREATE TABLE new_X (same columns + NOT NULL)
2. INSERT INTO new_X SELECT * FROM X
3. DROP TABLE X; ALTER TABLE new_X RENAME TO X

This is invasive and risks breaking ongoing queries. **B.1 leaves tenant_id nullable** and documents:
- Enforce non-null at INSERT site via app code (B.5 — JWT-derived, can't be NULL)
- After B.8 migration completes (all historical rows backfilled), a future sub-issue can do the table-rebuild dance to add NOT NULL constraint

This is acceptable trade-off: app layer enforces via JWT requirement; schema constraint hardening is optimization.

### §3.5 — Foreign key constraint (informal)

`tenant_id INTEGER REFERENCES users(id)` would be ideal but:
- SQLite FK enforcement requires `PRAGMA foreign_keys = ON` (not always on)
- ALTER TABLE ADD COLUMN with REFERENCES has SQLite-version edge cases
- Existing rows have NULL tenant_id; FK to NULL is fine but the constraint adds maintenance burden

**B.1 uses INTEGER (no REFERENCES clause)**. Relationship is informal — app layer (B.5) enforces tenant_id is always a valid user_id when inserting.

---

## §4 · Test plan

`tests/test_multi_tenant_b1_schema.py` covers:

1. **Fresh DB creation**: init_db() + init_auth_db() on empty DB → all tables present + tenant_id column on per-user tables + new tables exist
2. **Migration on existing-style DB**: synthesize a "pre-B.1" DB (no tenant_id, no capital/prefs tables), run init_db() → migration adds tenant_id + new tables without breaking existing rows
3. **Idempotency**: run init_db() twice → no errors, no schema duplication
4. **Backfill correctness**: insert rows with NULL tenant_id, call backfill_tenant(99) → all rows updated, second call is no-op
5. **Index existence**: verify all 4 new indexes created
6. **New table constraints**: capital + user_preferences UNIQUE on tenant_id (can't have two capital rows for same user)
7. **NULL tenant_id allowed pre-backfill**: insert position with NULL tenant_id succeeds (B.1 doesn't enforce non-null)
8. **Schema introspection**: PRAGMA table_info verifies column types match spec

---

## §5 · Locked decisions (summary)

| Decision | Lock |
|---|---|
| Column name | `tenant_id` (matches #253 naming) |
| Type | `INTEGER` (no REFERENCES — informal FK) |
| Initial nullability | Nullable (B.1 doesn't enforce NOT NULL) |
| Per-user tables in B.1 | positions, signal_outcomes, notifications_sent, portfolio_health_events |
| Global tables | scans, webhooks_sent, tune_results, symbol_health(_events) |
| Deferred (global for now) | kill_switch_* (4 tables) |
| New tables | `capital`, `user_preferences` |
| Backfill semantics | Idempotent UPDATE WHERE tenant_id IS NULL |
| NOT NULL hardening | Deferred to post-B.8 follow-up |
| FK constraint | Informal (app-enforced) |

---

## §6 · Methodology limitations

1. **Nullable tenant_id is a soft constraint.** Production deployment with multiple users requires API enforcement (B.5) to ensure no NULL inserts. Without B.5, a buggy insert could land NULL and silently leak across tenants on query.
2. **No `REFERENCES users(id)` FK** means orphan tenant_ids (user_id pointing to deleted user) won't be caught at DB layer. Deletion semantics for users (cascade vs orphan) deferred.
3. **Kill switch tables stay global**. If a future use case needs per-user kill switch, that's a separate schema change.
4. **Backfill assumes single Samuel user.** If multi-user has already happened in dev DBs, backfill needs explicit user_id param (which is good — no ambiguity).
5. **`idx_notif_tenant_unread` replaces `idx_notif_sent_unread`**. If existing queries rely on the old index name, may need adjustment. Not expected to be load-bearing in current code (verified pre-execution).

---

## §7 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Pre-reg light initial draft. Table classification locked per architectural decisions in #253 + #254. Migration approach: idempotent ALTER + backfill function (no NOT NULL hardening). Kill switch tables deferred to follow-up. | Claude Opus 4.7 + sssamuelll |
| TBD | B.1 implementation + tests + draft PR | Claude Opus 4.7 + sssamuelll |
