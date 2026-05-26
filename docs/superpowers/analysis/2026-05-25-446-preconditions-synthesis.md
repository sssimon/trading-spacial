# Issue #446 — Pre-conditions Synthesis

**Branch:** `feat/fix-tx-or-use-dual-contract-446`
**Commit base:** `de70052` (analysis + direction commit)
**Date:** 2026-05-25
**Purpose:** Evidence base for the executable plan. Voronov's 3 deferred questions from `2026-05-25-446-tx-or-use-analysis-and-direction.md` answered here.

---

## Pre-condition 1 — Audit of 44 call sites

### Classification taxonomy (per Voronov)

- **Cat. 1 — Pure SQL operator:** receives `con`, runs SQL, returns. No side-effects.
- **Cat. 2 — Hidden business operator:** composes >1 SQL helper AND/OR triggers side-effects.
- **Cat. 3 — Edge case:** requires human judgment.

### Totals

| Category | Count |
|---|---|
| Cat. 1 — Pure SQL | **34** |
| Cat. 2 — Hidden operator | **5** |
| Cat. 3 — Edge case | **7** |
| **Total** | **46** (vs 44 estimated — extra helpers found in `db/signals.py`, `db/schema.py`, `notifier/dispatch_per_user.py`) |

### Cat. 2 — Hidden business operators (candidates for migration to business operator layer)

1. **`db/signals.py::save_scan`** — dual-transaction pattern + WARN log. Business policy.
2. **`db/schema.py::init_db`** — DB bootstrap orchestrator; multiple txns + sub-migrations.
3. **`auth/audit.py::log_auth_event`** — INSERT + sys.stderr fallback + "Never raises" contract.
4. **`notifier/dispatch_per_user.py::dispatch_signal_to_users`** — composes SQL helpers + fires `notify()` (network).
5. **`db/positions.py::db_close_position`** — Cat. 3 by primary classification but functionally Cat. 2 in standalone path (the canonical bug of #446).

### Cat. 3 — Edge cases (human judgment required)

1. **`db_close_position`** — dual personality based on `_caller_owned_con`. THE canonical issue.
2. **`apply_pnl_to_capital`** — auto-recursive composition with `inner_con`. Business semantics (peak/drawdown) in helper shape.
3. **`_migrate_multi_tenant_b1`** — DDL with `log.info`. Sub-category Cat. 1 (DDL)?
4. **`_migrate_agent_audit`** — DDL + UPDATE backfill (business logic in mapping).
5. **`_migrate_agent_history`** — DDL with logging. Same shape as B1.
6. **`mark_setup_completed`** — pure SQL with system-lifecycle semantics (`ip`, `method` as audit params).
7. **`_list_active_users`** — defensive catch of `sqlite3.OperationalError` for missing table.

### Migration scope estimate (per Voronov's option C)

- 34 Cat. 1 helpers: drop `con: Optional` → `con: Connection` (mandatory). Mechanical sweep.
- 5 Cat. 2 helpers: extract side-effects to operators or document as business operators in their own layer.
- 7 Cat. 3 helpers: case-by-case decision in the plan.

---

## Pre-condition 2 — Opening flow decision

### Decision: **DEFER `PositionOpening`**

### Evidence

`POST /positions` → `open_position()` (lines 354–368):
1. Validate `symbol`, `entry_price` → HTTP 422 if missing.
2. `db_create_position(body, tenant_id=tenant_id)` — single DB write.
3. `update_positions_json()` — file side-effect (atomic via `.tmp` + `os.replace`).
4. Return `{"ok": True, "position": pos}`.

| Voronov symptom | Present in opening flow? |
|---|---|
| Contract interrogation (`if con is None`) | **No** |
| Conditional side-effects | **No** |
| Helper composition + side-effect | Minimal: 1 helper + 1 unconditional side-effect |

### Reasoning

Voronov's evidential rule: create operator when caller already exhibits the symptoms. Opening doesn't yet. Create `PositionOpening` when (and only when) evidence emerges — e.g., a future caller adds capital adjustment on open, or a notification on open with conditional logic, or a multi-helper composition. Until then, the path stays as-is (mechanical migration to `db_create_position(con: Connection)` only).

**Implication for the plan:** scope is `PositionClosure` only in this PR.

---

## Pre-condition 3 — Multi-tenancy invariant map

### A) Validation locations

| Function | Validates tenant_id? | Mechanism | Failure mode if skipped |
|---|---|---|---|
| `get_current_tenant_id` (`auth/dependencies.py:55`) | **Always** | Reads from JWT-authenticated `request.state.user`; 401 if not auth | N/A (source of truth) |
| `close_position` endpoint (`api/positions.py:396`) | **Yes** | `Depends(get_current_tenant_id)` | If removed: any caller could close any position knowing only `pos_id` |
| `db_close_position` (`db/positions.py:169`) | **Conditional** — only when `tenant_id is not None` | `WHERE id=? AND tenant_id=?` | If called with `tenant_id=None`: closes without ownership check |
| `_apply_close_to_capital` (`api/positions.py:52`) | **Indirect** — uses tenant_id from closed row | Trusts row data; skips silently if `NULL` | P&L credited to wrong tenant or skipped |
| `check_position_stops` (`api/positions.py:135`) | **No** — opaque by design | `SELECT WHERE symbol=? AND status='open'` no tenant filter | Closes all tenants' positions on SL/TP/TIME_LIMIT |

### B) Pre-transaction vs intra-transaction

- **Pre-tx (JWT Depends):** clean early rejection. Risk: someone purifies the `Depends` of a new route.
- **Intra-tx (SQL WHERE):** second line. Risk: `db_close_position(tenant_id=None)` silently bypasses ownership.

**Real gap identified:** `check_position_stops` calls `db_close_position(... con=con)` without `tenant_id`. Intentional for scanner but structurally fragile — if an attacker controls `pos_list` (today: gated by symbol + status='open' filter), they could close any tenant's position.

### C) Three invariants for the operator

1. **ownership-before-lock:** USER mode must verify `position.tenant_id == caller_tenant_id` BEFORE `BEGIN IMMEDIATE`. Failure → operator returns without opening write tx.
2. **capital-consistency:** P&L applied to capital MUST match tenant who owns position. Same tenant_id, atomic within same transaction.
3. **system-mode no-IDOR-leak:** failures in SYSTEM mode observationally indistinguishable between "doesn't exist" and "exists under another tenant".

---

## Pre-condition 4 — PositionClosure operator contract spec

### Purpose

Materialize the business transition: one open `positions` row → `status='closed'` + P&L atomic in `capital` + post-commit observable side-effects (health, event log, notify, snapshot). The only legal entry point for closing a position from application code.

### Caller modes

**Mode 1: USER** — instantiated by user-auth endpoint. `caller_tenant_id` from JWT (never None). Ownership enforced. Capital roll-in targets `caller_tenant_id` (= `position.tenant_id` by invariant).

**Mode 2: SYSTEM** — instantiated by scanner. No caller tenant. Ownership skipped by construction. Capital roll-in targets `position.tenant_id` from row; `NULL` → skip with WARN.

Mode is **explicit** (`Literal["USER", "SYSTEM"]`), not derived from `caller_tenant_id is None`.

### Construction

```text
PositionClosure(
    pos_id: int,
    exit_price: float,                              # > 0
    exit_reason: Literal["MANUAL","SL_HIT","TP_HIT","TIME_LIMIT_HIT"],
    *,
    mode: Literal["USER", "SYSTEM"],
    caller_tenant_id: Optional[int] = None,         # required iff mode == "USER"
    cfg: Optional[dict] = None,                     # defaults to load_config() lazily
    now: Optional[datetime] = None,                 # injectable for tests
)
```

Validation raises `ValueError` before any DB work. **Single-use semantic:** an instance is bound to at most one `__enter__` and at most one `execute()`; both raise `RuntimeError("...single-use")` on re-use. After `__enter__` returns, a second `__enter__` on the same instance raises — regardless of whether `execute()` was called inside the first block.

### Lifecycle as context manager

**`__enter__`** — outside any tx. Open short read-only conn, `SELECT *`, cache as `_pre_row`. If row missing or USER-mode ownership mismatch → state `NOT_FOUND`. If `status != 'open'` → state `ALREADY_CLOSED`.

**`execute() -> CloseOutcome`** — branches on state:
- `NOT_FOUND` / `ALREADY_CLOSED` → return outcome, no tx, no side-effects.
- `OK_TO_PROCEED` → ONE `with transaction() as con:` block. Inside, in order:
  1. Re-SELECT inside write tx (covers race window).
  2. `_calc_pnl(...)`.
  3. `db_close_position_sql(con, ...)` — pure UPDATE.
  4. Re-SELECT → `closed_row`.
  5. If tenant_id present + pnl_usd present: `apply_pnl_to_capital(con, tenant_id, pnl_usd)` — **IN-TX**. (Design call resolving #450 in favor of atomicity.)

**`__exit__` on success** — post-commit side-effects in fixed order, each best-effort log-and-continue:
1. `_write_position_event_log(...)` — file append.
2. `trigger_health_evaluation(...)` — own conn, safe now (lock released).
3. `notify(PositionExitEvent(...), cfg)` — Telegram.
4. `update_positions_json()` — snapshot.

**`__exit__` on exception** — tx already rolled back. No side-effects. Log at ERROR with structured context. Exception propagates.

### Composed SQL helpers (pure Cat. 1)

- `db_get_position_by_id(con, pos_id) -> Optional[dict]`
- `db_close_position_sql(con, pos_id, exit_price, exit_reason, exit_ts, pnl_usd, pnl_pct) -> dict`
- `db_get_capital(con, tenant_id) -> Optional[dict]`
- `db_upsert_capital(con, tenant_id, *, balance, peak_balance, max_drawdown_pct) -> dict`
- `apply_pnl_to_capital(con, tenant_id, pnl_usd) -> dict` — `con` mandatory; `_tx_or_use` shim gone

`_calc_pnl(...)` stays pure arithmetic helper.

### Post-commit side-effects — compensation policy

| Side-effect | Phase | Compensation |
|---|---|---|
| `apply_pnl_to_capital` | **IN-TX** | Failure aborts close (caller sees error, position stays open). Resolves #450. |
| `trigger_health_evaluation` | POST-COMMIT | Best-effort, log WARN. Next scanner tick re-evaluates. |
| `notify` (Telegram) | POST-COMMIT | Best-effort, log WARN. Dedup is notifier's responsibility. |
| `_write_position_event_log` | POST-COMMIT | Best-effort, log WARN. DB is source of truth. |
| `update_positions_json` | POST-COMMIT | Best-effort, log WARN. Eventually consistent. |

### 9 testable invariants (anchor for #451)

1. **Atomicity of close + capital** — both visible or neither.
2. **Ownership-before-lock** — USER ownership mismatch → no `BEGIN IMMEDIATE`.
3. **IDOR-equivalence in USER mode** — "doesn't exist" and "another tenant's" → identical observable.
4. **System-mode no-IDOR-leak** — SYSTEM closes correct tenant's position with correct capital, no cross-leak.
5. **No post-commit side-effect on exception** — if exception propagates, all 4 side-effects: zero calls.
6. **Single side-effect firing on success** — each post-commit side-effect: exactly 1 call.
7. **Idempotent re-close** — `ALREADY_CLOSED` outcome, no re-fire, no double P&L.
8. **No writer-lock held across I/O** — all side-effects fire strictly after tx exit.
9. **No-tenant capital skip** — `tenant_id IS NULL` legacy row → close commits, capital skipped.

### Migration impact

**`close_position` endpoint** — ~10 lines removed, ~8 lines added. `_apply_close_to_capital` shim deleted.

**`check_position_stops` scanner** — ~60 lines removed (`post_tx_actions` machinery, deferred-health imports, inline notify dispatch with code-mapping), ~20 lines added. Trailing-SL writes stay in their own tx (per-tick mutation set); each close moves to its own `PositionClosure(mode="SYSTEM")` outside that tx.

**Note:** trailing-SL and close now in separate transactions. Atomicity #446 cares about is per-close, not per-tick. This is a deliberate concession.

### Open questions deferred (NOT blocking the plan)

1. **Outbox for true compensable delivery** — v1 is log-and-continue. Outbox + retry is separate epic.
2. **SYSTEM mode: flag vs subclass** — flag is simpler now; subclass when divergence is concrete.
3. **Retry hooks / observability surface** — defer to observability stack decision.
4. **Cross-tenant batch SYSTEM close** — SQLite has one writer; not relevant until different DB.
5. **Partial close / cancellation** — separate operators when they emerge, not flags on this one.

---

## Synthesis — what the executable plan now writes

With all 4 pre-conditions complete:

- **Scope locked:** only `PositionClosure` operator in this PR. No `PositionOpening`.
- **Helper layer redefined:** 34 Cat. 1 helpers become pure SQL operators (`con: Connection` mandatory). `_tx_or_use` disappears.
- **5 Cat. 2 hidden operators** are documented for migration but only `db_close_position` is in scope (extracted into `db_close_position_sql` + `PositionClosure`). The other 4 stay where they are with deferred operator-extraction tickets.
- **7 Cat. 3 edge cases** mostly resolve to Cat. 1 under the plan (`apply_pnl_to_capital` becomes pure SQL with `con` mandatory; `_migrate_*` stay as DDL helpers with documented exception for `log.info`).
- **Multi-tenancy:** operator owns ownership-before-lock + IDOR-safe outcomes + SYSTEM mode explicit.
- **#450 resolved:** `apply_pnl_to_capital` runs IN-TX. Trade-off explicit: capital failure aborts close.
- **#451 anchor:** 9 testable invariants written. Test goes against the operator, not the helpers.
- **#447 dissolved:** `PositionClosure` is the first inhabitant of the business operator layer. The layer itself is the issue's resolution.
- **#448 dissolved:** helpers no longer accept `con` from external callers; only the operator passes `con`. Validation question disappears structurally.
- **#449:** health trigger lives in operator's `__exit__`, eliminated as separate concern within close-flow.

Ready to write the executable plan.
