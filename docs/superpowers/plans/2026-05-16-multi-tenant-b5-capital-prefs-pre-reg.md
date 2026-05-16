# Pre-registration (light) — B.5 follow-up B: capital + user_preferences endpoints

**Fecha:** 2026-05-16
**Status:** DRAFT — design pre-reg for new endpoint families on tables introduced by B.1 (#254).
**Autor:** Claude Opus 4.7 + sssamuelll
**Tipo:** API endpoint design for new tables; applies established B.5 JWT enforcement pattern.
**Predecessors:** B.1 schema (PR #362), B.5 main (PR #363), B.5 follow-up A (PR #364).

---

## §1 · Contexto y alcance

### §1.1 — Trigger

B.1 (#254) added `capital` + `user_preferences` tables but no API surface. B.5 main + follow-up A established the JWT enforcement pattern on existing endpoints. This PR creates NEW endpoints for these tables from scratch, applying the same pattern.

### §1.2 — Scope locked

**Adds:**
- `db/capital.py` — `db_get_capital(tenant_id)`, `db_upsert_capital(tenant_id, ...)`
- `db/user_preferences.py` — `db_get_user_preferences(tenant_id)`, `db_upsert_user_preferences(tenant_id, ...)`
- `api/capital.py` — `GET /capital`, `PUT /capital`
- `api/user_preferences.py` — `GET /preferences`, `PUT /preferences`
- Register both routers in `btc_api.py`
- Tests for db + endpoints (~12 tests)

**Does NOT add:**
- Capital history table or endpoint (no schema for it yet — separate sub-issue if needed)
- Capital deposit/withdraw separate endpoints (PUT covers full state replacement; transactional semantics in B.2 #255)
- Notification channel verification (e.g., does Telegram channel actually work) — out of scope
- Symbol filter validation against curated 10 (defer to caller / app logic)
- Min_score validation against scanner thresholds (defer)
- Frontend integration (B.6 #259)
- DELETE endpoints (no removal semantic needed; UPSERT replaces)

### §1.3 — Architectural decisions

**Endpoint shapes:**
- Both resources are **single-row-per-user** (enforced by UNIQUE INDEX on tenant_id from B.1).
- GET returns the row (or 404 if not yet initialized).
- PUT is upsert — creates if absent, replaces if present.
- No POST since there's only one row possible per user (PUT semantic correct).
- No DELETE since "delete capital" is meaningless (user would always have some capital state).

**Capital write semantics:**
- PUT replaces full state (balance + peak_balance + max_drawdown_pct).
- Caller controls peak_balance + max_drawdown_pct tracking. B.2 (#255) will own the lifecycle logic.
- For this PR, endpoint is a transparent CRUD passthrough — no derivation.

**User_preferences write semantics:**
- PUT replaces. Partial updates done client-side by reading + merging + writing back.
- Alternative (PATCH for partial) deferred — simpler scope.

### §1.4 — Out of scope but worth flagging

- B.2 (#255) will own balance derivation from realized P&L. This PR doesn't auto-sync capital from positions.
- B.4 (#257) will own notification routing based on user_preferences. This PR just stores prefs.

---

## §2 · DB layer design

### §2.1 — `db/capital.py`

```python
def db_get_capital(tenant_id: int) -> dict | None:
    """Returns current row or None if uninitialized."""
    
def db_upsert_capital(
    tenant_id: int,
    *,
    balance: float,
    peak_balance: float | None = None,
    max_drawdown_pct: float | None = None,
) -> dict:
    """Insert or replace single capital row for tenant.
    
    If peak_balance is None and row exists, preserves existing peak.
    If peak_balance is None and row absent, peak_balance = balance.
    max_drawdown_pct similar (preserve or None).
    """
```

### §2.2 — `db/user_preferences.py`

```python
def db_get_user_preferences(tenant_id: int) -> dict | None:
    """Returns current row or None if not yet set."""
    
def db_upsert_user_preferences(
    tenant_id: int,
    *,
    symbol_filter: list[str] | None = None,
    min_score: int | None = None,
    notify_channels: dict | None = None,
) -> dict:
    """Insert or replace. None values mean 'don't change this field'
    on update; 'use default' on insert.
    """
```

`symbol_filter` stored as JSON array; `notify_channels` as JSON dict.

---

## §3 · API endpoint design

### §3.1 — `api/capital.py`

```
GET  /capital                       — Get current capital state for tenant
PUT  /capital  {balance, ...}       — Upsert capital state
```

Both use `Depends(get_current_tenant_id)`. Strict tenant enforcement.

GET response if not initialized: 404 with message "Capital not initialized for this tenant. Use PUT to set."

PUT body schema (Pydantic):
```
{
  "balance": float (required, ≥ 0),
  "peak_balance": float (optional),
  "max_drawdown_pct": float (optional)
}
```

### §3.2 — `api/user_preferences.py`

```
GET  /preferences                   — Get current preferences for tenant
PUT  /preferences  {fields...}      — Upsert preferences
```

PUT body schema:
```
{
  "symbol_filter": list[str] (optional),
  "min_score": int (optional, 0-9),
  "notify_channels": dict (optional)
}
```

GET response if not set: returns defaults (don't 404 — preferences have sensible defaults):
```
{
  "tenant_id": <user_id>,
  "symbol_filter": null,
  "min_score": 4,
  "notify_channels": null
}
```

---

## §4 · Test plan

`tests/test_multi_tenant_b5_capital_prefs.py`:

1. db_upsert_capital creates row for new tenant
2. db_upsert_capital replaces existing
3. db_upsert_capital preserves peak_balance when not specified
4. db_get_capital returns None for missing
5. db_get_capital returns row for existing
6. db_upsert_user_preferences creates / replaces
7. db_upsert_user_preferences preserves fields when None passed
8. db_get_user_preferences returns None for missing
9. GET /capital 404 when uninitialized (synthetic test user)
10. PUT /capital + GET /capital roundtrip
11. PUT /capital tenant isolation (user 1 PUT doesn't affect user 2)
12. GET /preferences returns defaults when not set
13. PUT /preferences + GET roundtrip
14. PUT /preferences tenant isolation

---

## §5 · Locked decisions (summary)

| Decision | Lock |
|---|---|
| Endpoint methods | GET + PUT only (no POST, no DELETE) |
| Capital uninitialized GET response | 404 |
| Preferences uninitialized GET response | Returns sensible defaults (not 404) |
| Both endpoints tenant scope | `Depends(get_current_tenant_id)` |
| Body validation | Pydantic models |
| symbol_filter shape | JSON array of symbol strings |
| notify_channels shape | JSON dict (e.g., `{"telegram_chat_id": "..."}`) |
| Partial updates | PUT replaces all (no PATCH) — caller merges client-side |
| Capital derivation from positions | NOT in this PR (B.2 #255) |
| Notification routing based on preferences | NOT in this PR (B.4 #257) |

---

## §6 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-16 | Pre-reg light initial draft. Endpoint design + db function signatures locked. | Claude Opus 4.7 + sssamuelll |
| TBD | Implementation + tests + draft PR | Claude Opus 4.7 + sssamuelll |
