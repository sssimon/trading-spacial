# Pre-reg: Multi-tenant B.4 — per-user signal subscriptions + routing (#257)

**Date:** 2026-05-16
**Branch:** `feat/multi-tenant-b4-signal-routing`
**Parent epic:** #253
**Also closes:** B.3 #256 (already shipped by B.5 + B.7 + B.2)

## 1. Background

Today, when the scanner fires a SignalEvent, `notify(event, cfg)` sends a
single Telegram message to the single `telegram_chat_id` baked into
`config.json`. With multi-user (papá + Samuel + María), each user must:

- Decide which symbols they care about (`symbol_filter`)
- Decide the minimum score worth pinging (`min_score`)
- Provide their own delivery target (`notify_channels.telegram_chat_id`)

The schema for that already exists (B.1 `user_preferences` table + B.5-B
GET/PUT endpoints). What B.4 adds is the **fan-out**: turn one signal into
N filtered, per-user deliveries.

## 2. Locked decisions

### 2.1 Scope: SignalEvent only

Per-user fan-out applies **only** to `SignalEvent` (event_type=`signal`).
Other event types remain broadcast for now:

| Event type | Routing | Why |
|---|---|---|
| `signal` | **per-user fan-out (B.4)** | Each user opts in to their symbols/scores |
| `health` / `infra` / `system` | broadcast (unchanged) | Operator-level — all admins should see |
| `position_exit` | unchanged (tenant_id passed by caller) | Already per-position-owner; the position carries tenant_id |

If a future ticket wants per-user health/infra routing, it's a separate epic.

### 2.2 Hook point

A single call site changes: `api/telegram.py::push_telegram_direct` —
currently does `notify(SignalEvent(...), cfg)`. Replace with:

```python
from notifier.dispatch_per_user import dispatch_signal_to_users
dispatch_signal_to_users(event, cfg)
```

No other call sites change. `notify()` itself is extended (additive) with a
new optional `tenant_id` arg the dispatcher uses internally — existing
broadcasts (health/infra/system) pass nothing and behave identically.

### 2.3 Filter semantics

For each active user (`users.is_active = 1`):

```python
prefs = db_get_user_preferences(user.id)
if prefs is None:
    # No prefs row yet: use sane defaults
    symbol_filter = None       # = all symbols
    min_score = 4              # matches schema DB default
    notify_channels = {}       # falls back to global cfg
else:
    symbol_filter = prefs["symbol_filter"]
    min_score = prefs["min_score"]
    notify_channels = prefs["notify_channels"] or {}

# Filter
if symbol_filter is not None and event.symbol not in symbol_filter:
    skip
if event.score < min_score:
    skip
```

**`symbol_filter = None` means "all symbols"** (no filter applied). Empty list
`[]` means "no symbols" (skip everything). This matches the schema-stored
JSON semantics.

### 2.4 Per-user channel routing

The dispatcher builds a **patched cfg** for each user by overlaying their
`notify_channels` onto the base cfg:

```python
user_cfg = {**base_cfg, **(notify_channels or {})}
```

Recognised keys (anything the existing channel factories read):
- `telegram_chat_id` → routes Telegram to this user's chat
- `telegram_bot_token` → if user has their own bot (usually shared)
- `email` recipient → `EmailChannel` reads it

If `notify_channels` is empty/None, the user receives via the global cfg's
defaults (same as today — no per-user routing). This is the bridge mode
for the single-user-with-no-prefs-row case.

### 2.5 Dedupe semantics

Today's dedupe key is the event's `dedupe_key` (e.g., `signal:BTCUSDT`).
For per-user fan-out the key becomes `tenant:{id}:signal:BTCUSDT`. Each
user's stream is dedupe-independent: if A receives the alert and B is also
eligible, B's send is not suppressed by A's record.

Implementation: the new `notify(event, cfg, tenant_id=…)` call prefixes the
dedupe key with `tenant:{tenant_id}:` when `tenant_id is not None`. Existing
broadcasts (tenant_id=None) keep the bare key — byte-identical behavior.

### 2.6 Delivery records

Each per-user dispatch writes one `notifications_sent` row with that
user's `tenant_id`. The B.5-A endpoint `GET /notifications` already filters
by tenant_id, so users see only their own deliveries.

### 2.7 Out of scope

| Item | Where it goes |
|---|---|
| UI to edit user preferences | Frontend follow-up if requested (existing PUT endpoint works) |
| Per-event-type fan-out (health/infra/system) | Future epic |
| Email rendering tweaks | Existing template path |
| Concurrency: parallel send per user | Single-threaded — N users is small; SQLite serializes anyway |
| Inactive-user signal queueing | Skipped silently; if a user reactivates, they don't receive backlog |

## 3. Tests (locked before writing impl)

| Test | Asserts |
|---|---|
| `test_two_users_different_filters` | A: `["BTCUSDT"]`/min=5, B: null/min=2. Signal BTC sc=6 → both notified; BTC sc=3 → only B; ETH sc=6 → only B |
| `test_inactive_user_skipped` | `is_active=0` user receives nothing regardless of filters |
| `test_user_without_prefs_uses_defaults` | User with no `user_preferences` row → all symbols, min_score=4, global cfg channels |
| `test_per_user_telegram_chat_routing` | User A's `notify_channels={"telegram_chat_id":"X"}` → message sent to chat X, not global chat |
| `test_dedupe_per_user_independent` | Same signal: A's send doesn't suppress B's send (dedupe state isolated) |
| `test_record_delivery_has_tenant_id` | Per-user dispatch writes a `notifications_sent` row with that user's tenant_id |
| `test_legacy_notify_call_unchanged` | `notify(HealthEvent…)` with no tenant_id → broadcast behavior, NULL tenant_id in record, no key prefix |
| `test_dispatcher_handles_no_users` | Zero active users → returns `[]`, no errors |
| `test_symbol_filter_empty_list_means_none` | `symbol_filter=[]` → user skipped (explicit empty whitelist) |

## 4. Single-iteration discipline

Standard: lock violation → STOP. Impl bug → fix. No test-loosening.

## 5. Done when

- All 9 tests above pass
- Targeted regression scope green
- PR description quotes locks §2.1–§2.7 verbatim
- B.3 #256 closed with comment citing already-shipped PRs (post-merge)
