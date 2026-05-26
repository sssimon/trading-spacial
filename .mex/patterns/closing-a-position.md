---
name: closing-a-position
description: Runbook for closing a position via the PositionClosure business operator — atomic capital roll-in + post-commit health/notify/event-log/snapshot.
triggers:
  - "close position"
  - "PositionClosure"
  - "exit reason"
  - "TP_HIT"
  - "SL_HIT"
  - "TIME_LIMIT"
last_updated: 2026-05-26
---

# Pattern: Closing a position

## Purpose

`PositionClosure` is the **only** legal entry point for closing a position. It owns the write transaction that performs: `db_close_position_sql` + `apply_pnl_to_capital` (atomic), then post-commit fires `health_check` + `notify` + `event_log` + `update_positions_json`. This is the canonical example of the [[../context/conventions.md]] §2 business-operator rung.

## When to use

- USER mode: a user requested the close via API or UI (`POST /positions/{id}/close`).
- SYSTEM mode: `check_position_stops` fired SL / TP / TIME_LIMIT for an open position.

Anywhere else writes `status='closed'` directly into `positions` is a contract violation and a [[../context/conventions.md]] §Capas-de-enforcement audit finding.

## Steps

```python
from operators.position_closure import PositionClosure

with PositionClosure(
    pos_id=42,
    exit_price=110.0,
    exit_reason="TP_HIT",       # or "SL_HIT", "TIME_LIMIT", "USER_CLOSE", ...
    mode="USER",                # or "SYSTEM"
    caller_tenant_id=tenant_id, # required, validated against the row's tenant_id
) as closure:
    outcome = closure.execute()
```

The operator:

1. Opens `precheck_connection()` to fetch the row, build a `PositionSnapshot`, and validate `caller_tenant_id == row.tenant_id`. See [[precheck-vs-snapshot.md]].
2. Opens `transaction()` (`BEGIN IMMEDIATE`) and re-validates the snapshot's mutable fields (tenant_id, status) against a fresh `SELECT`.
3. Inside the same transaction: `db_close_position_sql` + `apply_pnl_to_capital`.
4. On commit: fires `health_check`, `notify`, `event_log`, and `update_positions_json` post-commit.

## Gotchas

- **Tenant mismatch raises `TenantViolationError` (HTTP 403).** Caller must supply `caller_tenant_id`; the operator does not derive it from the row.
- **Phase 2 partial-failure observability gap (#453):** `check_position_stops` Phase 2 wraps each `PositionClosure(SYSTEM)` in `try/except: continue`. If one close fails, the loop keeps going. F-05 ("every mutation from one tick belongs to one serializable tx") therefore applies **per-close**, not per-tick. See [[../context/conventions.md]] §Known scope gap.
- **Do not bypass to `db_close_position_sql` directly.** That helper is pure SQL — it does not roll capital, does not notify, does not snapshot. Bypassing the operator silently breaks invariants the rest of the system assumes.
- **`exit_reason="BANKRUPT"`** is reserved for the per-symbol bankruptcy halt in `simulate_strategy` (PR #313 / #280). Do not emit it from the live close path.

## Verify Checklist

Before merging any code that closes a position:

- [ ] Uses `PositionClosure` as a context manager (`with PositionClosure(...) as closure:`).
- [ ] Passes `mode` and `caller_tenant_id` explicitly.
- [ ] Does NOT call `db_close_position_sql` or `apply_pnl_to_capital` directly.
- [ ] Does NOT open its own `transaction()` around the close — the operator owns the tx.
- [ ] If running inside `check_position_stops` Phase 2, the `try/except: continue` is preserved (the partial-failure observability gap is tracked in #453, not patched in-line).
