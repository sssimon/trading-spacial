# Multi-tenant threat model — Epic B post-B.5 enforcement

**Fecha:** 2026-05-16
**Status:** Living document — updated as new endpoints or attack vectors are surfaced.
**Autor:** Claude Opus 4.7 + sssamuelll
**Audience:** Auditor + operator. Pre-condition for closing #271 + inviting papá/María.
**Scope:** Tenant isolation surface only. Auth-level threats (JWT forgery, session theft) covered by Epic auth-hardening #262.

---

## §1 · Methodology

STRIDE applied per asset that contains per-user state. Assets identified post-B.1 (#254) + B.5 (#363, #364, #365):

- `positions` (B.5 main)
- `signal_outcomes` (B.5 follow-up A, via `/signals/performance`)
- `notifications_sent` (B.5 follow-up A, via `/notifications/*`)
- `capital` (B.5 follow-up B, new in B.1)
- `user_preferences` (B.5 follow-up B, new in B.1)

For each asset, document:
1. **STRIDE category** — what threat class
2. **Attack vector** — how an attacker would try
3. **Current mitigation** — what's enforced today
4. **Residual risk** — what remains, with mitigation plan or accepted risk

---

## §2 · Architectural invariants enforced

Locked across B.5 + follow-ups (PRs #363/#364/#365):

| Invariant | How enforced |
|---|---|
| `tenant_id` derived from JWT — never from request | `get_current_tenant_id` dependency reads `request.state.user.id` (set by AuthMiddleware from JWT). No code path reads tenant_id from query/header/body. |
| API endpoints filter reads by tenant_id | All per-user GET endpoints pass `tenant_id` from `Depends(get_current_tenant_id)` to db layer; db filters `WHERE tenant_id = ?`. |
| API endpoints inject tenant_id on writes | All per-user POST/PUT endpoints insert with explicit `tenant_id` from Depends. Body fields named `tenant_id` are silently dropped (not in Pydantic model OR not consumed by db function). |
| API endpoints verify ownership before mutate | UPDATE / DELETE operations include `WHERE tenant_id = ?` ownership clause; return 404 when no row matches (no info leak via 403 vs 404). |
| NULL `tenant_id` rows invisible to per-user queries | Strict filter: `WHERE tenant_id = ?` does not match NULL. Pre-backfill data needs explicit `backfill_tenant(user_id)` from B.1 helper. |
| New tables `capital` + `user_preferences` have `NOT NULL tenant_id` from B.1 | Insert path requires explicit tenant_id from app layer. |

---

## §3 · Per-asset threat analysis

### §3.1 — `positions`

| ID | STRIDE | Vector | Mitigation | Residual |
|---|---|---|---|---|
| P-T-1 | Tampering | `GET /positions?tenant_id=999` (query param) | FastAPI Depends precedence over query — query param NOT bound to the same name; tenant comes from JWT | Low: covered by IDOR suite test |
| P-T-2 | Tampering | `POST /positions {tenant_id: 999, ...}` (body field) | `db_create_position(body, tenant_id=...)` uses explicit tenant_id arg, not body field | Low: B.5 test_body_tenant_id_is_dropped_on_create |
| P-T-3 | Tampering | `X-User-Id: 999` (custom header) | `get_current_user` reads only `request.state.user` (AuthMiddleware-set from JWT); custom headers ignored | Low |
| P-I-1 | Information disclosure | `GET /positions/{other_user_pos_id}` direct access | `db_get_position` (inline SELECT or future helper) returns None on tenant mismatch → 404. Same response shape as "not found" → no info leak | Low |
| P-I-2 | Info disclosure | `PUT /positions/{other_user_pos_id}` | `db_update_position` ownership pre-check → returns None → 404 | Low |
| P-I-3 | Info disclosure | `DELETE /positions/{other_user_pos_id}` | Inline SELECT in api/positions.py `WHERE id=? AND tenant_id=?` → 404 | Low; refactor to `db_delete_position` helper pending |
| P-E-1 | Elevation of privilege | role tampering to access positions as admin | Existing role-check via `require_role("admin")` retained; tenant_id orthogonal to role | Low |

### §3.2 — `signal_outcomes` (via `/signals/performance`)

| ID | STRIDE | Vector | Mitigation | Residual |
|---|---|---|---|---|
| SO-I-1 | Info disclosure | `GET /signals/performance` returns other users' performance | Endpoint uses `Depends(get_current_tenant_id)` + SQL filter `WHERE tenant_id = ?` | Low |
| SO-T-1 | Tampering | Query/body manipulation to escape tenant filter | Same as P-T-1/2/3 | Low |
| SO-I-2 | Info disclosure | scans table (global) leaks signal frequency info per symbol | scans is INTENTIONALLY GLOBAL (universal market data). No tenant info leaked since scans has no tenant_id and no PII | Accepted by design (#253 architecture) |

### §3.3 — `notifications_sent`

| ID | STRIDE | Vector | Mitigation | Residual |
|---|---|---|---|---|
| N-I-1 | Info disclosure | `GET /notifications` returns other users' notifications | `list_unread(tenant_id=...)` filters; `unread=false` SQL also includes `WHERE tenant_id = ?` | Low |
| N-T-1 | Tampering | `POST /notifications/{other_user_notif_id}/read` | `mark_read(id, tenant_id=...)` ownership clause; returns False → 404 | Low |
| N-T-2 | Tampering | `POST /notifications/read-all` affects other users' queue | `mark_all_read(tenant_id=...)` scope-limited | Low |
| N-I-2 | Info disclosure | System-broadcast notifications (NULL tenant_id) | Strict filter: NULL invisible to per-user queries → broadcasts not delivered. **B.4 (#257) is the proper home for fan-out** | Accepted (broadcasts deferred); current `notifier._storage::record_delivery` callers from system code create NULL rows that won't be seen until fan-out implemented |

### §3.4 — `capital`

| ID | STRIDE | Vector | Mitigation | Residual |
|---|---|---|---|---|
| C-I-1 | Info disclosure | `GET /capital` returns other user's balance | `db_get_capital(tenant_id=...)` strict filter | Low |
| C-T-1 | Tampering | `PUT /capital {balance: ..., tenant_id: 999}` body | Pydantic model `CapitalPutBody` does NOT include `tenant_id` field; FastAPI ignores extra fields by default. tenant_id comes from Depends | Low |
| C-E-1 | Elevation | User PUTs negative balance to corrupt | Pydantic `Field(..., ge=0)` validates ≥ 0 → 422 | Low |
| C-T-2 | Tampering | `PUT /capital` overwrites peak_balance to inflate equity history | App layer (B.2) should enforce peak monotonic increase. **B.1 schema doesn't constrain** | **Open**: B.2 (#255) capital tracker logic will enforce monotonic peak_balance + audit changes. Until then, user can manipulate own peak (only affects own metrics, not other users) |

### §3.5 — `user_preferences`

| ID | STRIDE | Vector | Mitigation | Residual |
|---|---|---|---|---|
| UP-I-1 | Info disclosure | `GET /preferences` returns other user's prefs | `db_get_user_preferences(tenant_id=...)` strict filter | Low |
| UP-T-1 | Tampering | `PUT /preferences {notify_channels: {telegram: other_user_chat_id}}` | App layer: notify_channels stored verbatim. User could enter a chat_id that's not theirs, but the channel verification (TBD in B.4 #257) would fail | **Open**: notification routing in B.4 #257 must verify channel ownership (e.g., Telegram bot confirms user via /start) |
| UP-E-1 | Elevation | `min_score=-1` or `min_score=100` | Pydantic `Field(None, ge=0, le=9)` validates → 422 | Low |

---

## §4 · Cross-cutting threats

### §4.1 — JWT forgery (S — Spoofing)

If attacker steals or forges JWT, they impersonate the user. Mitigations:
- JWT signed with HS256 (or RS256 in prod) — secret stored in env (`AUTH_JWT_SECRET`)
- 15-minute access token expiry
- Refresh token rotation with family theft detection (existing `auth/tokens.py`)
- Audit log of `login_success` / `refresh` / `logout` events (`auth.audit`)

Residual: depends on secret rotation discipline + secret-management hygiene. Covered by Epic auth-hardening #262.

### §4.2 — Middleware bypass (S — Spoofing)

If a route forgets `Depends(get_current_user)` AND AuthMiddleware doesn't enforce auth on its path, the endpoint runs anonymously.

Mitigations:
- AuthMiddleware enforces auth globally except explicit whitelist (auth/middleware.py)
- `get_current_user` raises 401 if `request.state.user` missing — defense in depth
- New endpoint code uses Depends pattern consistently (this PR adds the test suite that detects regression)

Residual: human error introducing un-protected endpoint. Mitigation: B.7 IDOR suite has a meta-test that lists all per-user paths and verifies `Depends(get_current_tenant_id)` is wired.

### §4.3 — Audit log integrity (R — Repudiation)

Per-user actions (position open/close, capital adjust, prefs update) should be auditable to a specific user.

Current state:
- `auth_events` table logs auth-only events
- Position lifecycle has `notes` field but no audit table
- Capital + preferences updates have `updated_at` timestamp but no who-changed-what trail

Residual: **Open** — no per-action audit log for non-auth events. Mitigation: Epic auth-hardening #262 (#264 audit log signing chain) covers this; per-tenant audit deferred.

### §4.4 — Information disclosure via error messages

404 returns consistent shape for "not found" and "not yours" (no leak).
422 validation errors include field name → minor info leak about schema (acceptable).
500 errors → no tenant context leaked.

Residual: Low.

### §4.5 — DoS / Resource exhaustion

Per-user queries don't have explicit rate limit. Attacker can hammer endpoints.

Mitigations:
- `auth.rate_limit` rate-limits login attempts (existing)
- Query LIMIT clauses cap row returns (`/notifications?limit=200` max)
- DB queries indexed by tenant_id (B.1)

Residual: **Open** — no per-user request rate limit on endpoints. Mitigation: Epic auth-hardening #262 (no specific issue yet).

### §4.6 — Migration leak window (T — Tampering)

During B.8 (#261) production migration:
- Existing data (Samuel's positions) gets `tenant_id = samuel_user_id` via backfill
- Brief window where backfill running could expose data inconsistently

Mitigations:
- B.8 will run during maintenance window (no concurrent traffic)
- Idempotent backfill (B.1 `backfill_tenant`) — safe to re-run
- Pre/post row count validation (B.8 plan)

Residual: Accepted (B.8 plan).

---

## §5 · Acceptance criteria for closing #271

Original #271 closure criteria (from issue):
- Epic A passed validation → **WAIVED** (Epic A archived, see #271 override comment)
- Epic B implemented, tested, stable

Updated criteria (post-override):
- ✅ B.1 schema (#254, PR #362)
- ✅ B.5 main (#258, PR #363) — positions enforcement
- ✅ B.5 follow-up A (PR #364) — notifications + signals/performance
- ✅ B.5 follow-up B (PR #365) — capital + user_preferences
- ⏳ **B.7 IDOR suite green** (this PR) — comprehensive test pass
- ⏳ B.6 frontend user context (#259) — UI consumes per-user endpoints
- ⏳ B.2 capital tracker (#255), B.3 position lifecycle (#256), B.4 signal subscriptions (#257) — operational features
- ⏳ B.8 production data migration (#261)
- ⏳ First non-Samuel user onboarded WITHOUT data leakage

This document closes when all the above are checked AND no residual marked **Open** in §3/§4 remain unaddressed (or operator explicitly accepts).

---

## §6 · Residual risks summary

Currently **Open** items that need resolution before inviting non-Samuel users:

1. **C-T-2 peak_balance manipulation** → B.2 #255 (capital tracker logic enforces monotonic peak)
2. **UP-T-1 notify_channels claim attack** → B.4 #257 (notification routing verifies channel ownership)
3. **R repudiation — no per-action audit log** → Epic auth-hardening #262 (#264 audit log signing chain)
4. **DoS — no per-user rate limit on endpoints** → Epic auth-hardening #262
5. **System-broadcast notifications drop** → B.4 #257 (fan-out logic)

These do NOT block B.7 test suite landing — they are forward-looking gaps documented for traceability.

---

## §7 · Historial

| Fecha | Cambio | Autor |
|---|---|---|
| 2026-05-16 | Initial threat model post-B.5 enforcement. Per-asset STRIDE + cross-cutting threats. Residual risks indexed. | Claude Opus 4.7 + sssamuelll |
| TBD | Updated post-B.7 test suite execution + B.6 frontend ship | TBD |
