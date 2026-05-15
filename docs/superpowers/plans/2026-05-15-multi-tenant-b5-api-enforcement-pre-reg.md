# Pre-registration (light) — Multi-tenant B.5 API enforcement (#258)

**Fecha:** 2026-05-15
**Status:** DRAFT — pre-reg before API enforcement work. Locks helper design + endpoint scope + transition strategy. NO verdict tree (mechanism design).
**Autor:** Claude Opus 4.7 en colaboración con sssamuelll
**Tipo:** API enforcement design — Epic B #253 sub-issue B.5
**Trigger:** Post-B.1 schema merge (PR #362). B.5 is the security gate per ordering in #253 comment.
**Issue:** #258

---

## §1 · Contexto y alcance

### §1.1 — Trigger

B.1 (#254, PR #362) added schema layer: `tenant_id` columns + `capital` + `user_preferences` tables + `backfill_tenant()` helper. Schema is non-breaking (nullable tenant_id). Now B.5 enforces tenant_id is derived from JWT and never from request.

### §1.2 — B.5 scope (this PR)

**Adds:**
- `get_current_tenant_id` dependency in `auth/dependencies.py` (JWT-derived only)
- Modify `db/positions.py` — 5 functions accept optional `tenant_id` param (default `None` for legacy callers)
- Modify `api/positions.py` — 5 endpoints use `Depends(get_current_tenant_id)` + pass to db
- Tests: helper correctness + IDOR scenarios + tampering attempts ignored + legacy backward-compat (None tenant_id)

**Does NOT add (B.5 follow-up sub-issues):**
- New endpoints for `capital` + `user_preferences` (B.2 / B.4 territory — these tables exist but no CRUD yet)
- Notifications endpoint enforcement (separate follow-up)
- signal_outcomes endpoint enforcement (separate follow-up)
- Frontend changes (B.6 — #259)
- IDOR test suite proper (B.7 — #260; B.5 has spot tests, B.7 is the comprehensive suite)
- Production data migration (B.8 — #261)

### §1.3 — Transition strategy

**Optional `tenant_id` param with default `None`**: non-breaking for existing tests and internal callers (scanner uses `db_last_exit_ts(symbol)` for cooldown — system-wide, no tenant context).

| Caller type | tenant_id semantic | Justification |
|---|---|---|
| API endpoint (via JWT) | `int` (user id) | Mandatory enforcement at security surface |
| Internal scanner | `None` | Scanner is system-wide; "any user's exit" for cooldown |
| Internal tests | `None` or `int` | Both paths exercised |
| Pre-B.8 data | NULL in DB | Invisible until backfill (correct security default) |

**Pre-backfill data invisibility**: existing positions with `tenant_id IS NULL` are NOT returned when API filters by tenant_id. Operator must run `backfill_tenant(samuel_user_id)` once locally after this PR merges before existing data shows up. This is acceptable for dev; B.8 handles production migration.

### §1.4 — Architectural lock from #253

> "API layer enforces tenant_id from JWT, never from request"

Operationalized: `get_current_tenant_id` reads ONLY from `request.state.user.id` (populated by AuthMiddleware from JWT). No reading from query params, headers, body fields, or path params.

---

## §2 · Methodology

### §2.1 — Dependency helper

```python
# auth/dependencies.py

def get_current_tenant_id(user: User = Depends(get_current_user)) -> int:
    """Return tenant_id derived from JWT.

    The tenant_id is ALWAYS the authenticated user's id. Never read from
    request params/headers/body — that's the threat surface explicitly
    closed per Epic B (#253) threat model.
    """
    return user.id
```

Trivially small. Chains through `get_current_user` (existing) which reads `request.state.user` set by `AuthMiddleware` from JWT. No new auth code; new helper is a typed extractor that surfaces "tenant_id" as the semantic name.

### §2.2 — DB function changes (`db/positions.py`)

Add optional `tenant_id: int | None = None` parameter to each function. Filter/inject when provided; legacy when `None`.

**`db_create_position(data, tenant_id=None)`:**
- If `tenant_id is not None`: insert with `tenant_id = ?` in VALUES
- Else: insert with `tenant_id = NULL`

**`db_get_positions(status, tenant_id=None)`:**
- If `tenant_id is not None`: `WHERE tenant_id = ?` (combined with status filter)
- Else: no tenant filter (legacy)

**`db_last_exit_ts(symbol, tenant_id=None)`:**
- Same pattern.

**`db_close_position(pos_id, exit_price, exit_reason, tenant_id=None)`:**
- If `tenant_id is not None`: SELECT must match `(id=? AND tenant_id=?)`. Return None if no match (IDOR protection).
- Else: legacy SELECT by id only.

**`db_update_position(pos_id, data, tenant_id=None)`:**
- Same ownership check as close.

### §2.3 — API endpoint changes (`api/positions.py`)

5 endpoints modified to use `Depends(get_current_tenant_id)`:

```python
@router.get("")
def list_positions(
    status: str = Query("all"),
    tenant_id: int = Depends(get_current_tenant_id),  # NEW
):
    positions = db_get_positions(status, tenant_id=tenant_id)
    ...

@router.post("", dependencies=[Depends(verify_api_key), Depends(require_role("admin"))])
def open_position(
    body: dict = Body(...),
    tenant_id: int = Depends(get_current_tenant_id),  # NEW
):
    pos = db_create_position(body, tenant_id=tenant_id)
    ...

# Same pattern for PUT, POST /close, DELETE
```

Critical: `tenant_id` parameter is NEVER read from `body` (request body), `path` (URL), or `query` (?tenant_id=). Always `Depends(get_current_tenant_id)` which is JWT-only.

### §2.4 — Internal callers (unchanged)

`btc_scanner.py` calls `db_last_exit_ts(symbol)` without `tenant_id` → returns most-recent exit across ANY user. This is correct for cooldown logic (scanner is system-wide).

`btc_api.py` re-exports the functions for legacy compatibility — no signature change visible.

---

## §3 · Tampering threat scenarios (tests pre-registered)

### §3.1 — URL param tampering

Endpoint signature `def list_positions(status, tenant_id: int = Depends(...))`. FastAPI auto-binds query params by name. If user sends `?tenant_id=999`, does FastAPI override the JWT-derived value?

**Mitigation lock**: `Depends(get_current_tenant_id)` takes precedence over query param when both exist (Depends has higher priority than query). Verified via test: GET `/positions?tenant_id=999` while JWT user_id=1 → returns user 1's positions, NOT user 999's.

If FastAPI version behavior changed, the test would catch.

### §3.2 — Body field tampering

POST `/positions` with body `{"tenant_id": 999, "symbol": "BTC", ...}`. The Body model is `dict`, so it doesn't validate. But the endpoint passes `tenant_id` from Depends, not from body. Body's tenant_id is silently dropped.

Test: POST with body containing tenant_id=999 while JWT user_id=1 → position is inserted with tenant_id=1, NOT 999.

### §3.3 — Header tampering

`X-User-Id: 999` or similar custom header. The dependency reads only `request.state.user` (set by AuthMiddleware from JWT). Custom headers are not consulted.

Test: GET with malicious header → header ignored.

### §3.4 — IDOR — direct access to another user's position

User 1 (JWT) tries `GET /positions/{pos_id}` for a pos_id owned by user 2. `db_get_position(pos_id, tenant_id=1)` returns None → endpoint returns 404.

Same for PUT, POST /close, DELETE.

Test: pos owned by user 2; user 1's JWT requests it → 404.

### §3.5 — Missing JWT

Endpoint with `Depends(get_current_tenant_id)` chains through `get_current_user`, which raises 401 if `request.state.user` is None. Existing behavior preserved.

---

## §4 · Test plan

`tests/test_multi_tenant_b5_enforcement.py`:

1. **Helper extracts tenant_id from JWT user**
   - Mock user with id=42; `get_current_tenant_id(user)` returns 42

2. **db_create_position with tenant_id**
   - Pass tenant_id=99; row inserted with tenant_id=99

3. **db_create_position without tenant_id (legacy)**
   - No tenant_id; row inserted with tenant_id=NULL

4. **db_get_positions filters by tenant_id**
   - Insert 2 positions with different tenant_ids; query with tenant_id=1 returns only 1 row

5. **db_get_positions without tenant_id (legacy)**
   - Returns all rows including NULL tenant_id

6. **db_close_position enforces ownership**
   - Position owned by user 1; close with tenant_id=2 → returns None

7. **db_update_position enforces ownership**
   - Position owned by user 1; update with tenant_id=2 → returns None

8. **db_last_exit_ts filters by tenant_id when provided**
   - Two users' exits; query with tenant_id=1 returns only user 1's last exit

9. **API endpoint passes JWT user_id to db function**
   - Mock user with id=1; GET /positions returns only user 1's data

10. **API ignores body tenant_id tampering**
    - POST with body tenant_id=999, JWT user=1 → position has tenant_id=1

11. **API ignores query tenant_id tampering**
    - GET /positions?tenant_id=999, JWT user=1 → returns user 1's data only

12. **IDOR — 404 on other user's position**
    - PUT /positions/{pos_id_owned_by_user_2} from user 1 → 404

13. **Missing JWT — 401**
    - Endpoint without authentication → 401

14. **Pre-backfill data invisible**
    - Pre-existing position with tenant_id=NULL; query with tenant_id=1 → not returned

15. **Backfill restores visibility**
    - Run backfill_tenant(1); query with tenant_id=1 → returns previously-NULL row

---

## §5 · Locked decisions (summary)

| Decision | Lock |
|---|---|
| Helper location | `auth/dependencies.py` (alongside existing dependencies) |
| Helper name | `get_current_tenant_id` |
| Return type | `int` (user.id) |
| Source of truth | `request.state.user.id` (set by AuthMiddleware from JWT) |
| API endpoints in B.5 | positions only (5 endpoints) |
| Other endpoints | Deferred to B.5 follow-ups |
| DB function signatures | Non-breaking — optional `tenant_id` param |
| Default `tenant_id` value | `None` (legacy behavior) |
| Filter semantic when None | No filter (legacy) |
| Filter semantic when int | Strict — return None / empty if no match |
| Pre-backfill data | Invisible until `backfill_tenant(user_id)` run |

---

## §6 · NOT in this PR

- Capital endpoints (new — separate B.5 follow-up or B.2)
- User_preferences endpoints (same)
- Notifications endpoint enforcement
- signal_outcomes endpoint enforcement
- Frontend (B.6)
- Full IDOR test suite (B.7)
- Production data migration (B.8)
- `verify_api_key` removal (deprecation TBD; B.5 retains existing `verify_api_key + require_role` patterns)
- NOT NULL constraint on tenant_id (post-B.8)
- New `db_get_position(pos_id, tenant_id)` single-getter (current code does inline SELECT in `delete_position`; refactor deferred)

---

## §7 · Methodology limitations

1. **Spot tests, not comprehensive IDOR suite.** B.7 will be the comprehensive one. B.5 has critical-path tests (positions only).
2. **Legacy `None` tenant_id stays unfiltered.** Internal callers (scanner) rely on this. Risk: a future caller passes `None` accidentally and exposes data. Mitigation: code review + B.7 IDOR tests + eventual NOT NULL hardening post-B.8.
3. **`tenant_id` is `int`, not validated as valid user_id.** App layer trusts the JWT decoder. If JWT signing key compromised, attacker can forge user_id. Mitigation: secure JWT secret (existing); rotation (existing); detect-and-revoke (existing audit log).
4. **Existing `verify_api_key + require_role("admin")` not removed.** Coexist with new tenant enforcement. Future cleanup deferred (TODO comments preserved in code).
5. **API endpoints don't inspect tenant_id for write paths**: assumes tenant_id from Depends is always the user's own. Correct by construction (Depends is JWT-only) but worth re-flagging: NEVER add a path/body field named "tenant_id" or "owner_id" parsed from request.
6. **No rate-limit on IDOR attempts.** Bad actors could enumerate position IDs to learn how many exist. Mitigation: response is 404 (not 403), so attackers can't distinguish "not yours" from "doesn't exist". Acceptable.

---

## §8 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-15 | Pre-reg light initial draft post-B.1 merge. Scope: positions endpoints only. Helper design + transition strategy locked. Tampering threat scenarios pre-registered. | Claude Opus 4.7 + sssamuelll |
| TBD | B.5 implementation + tests + draft PR | Claude Opus 4.7 + sssamuelll |
