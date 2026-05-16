# Pre-reg: Multi-tenant B.2 — capital tracker logic (#255)

**Date:** 2026-05-16
**Branch:** `feat/multi-tenant-b2-capital-tracker-logic`
**Parent epic:** #253
**Blocks:** B.8 production migration

## 1. Background

B.5 wired `tenant_id` enforcement into the positions endpoints. B.5 follow-up B
added `db/capital.py` + GET/PUT `/capital` so each user can read/write their
own capital row. What's still missing — and what #255 demands — is that
**closing a position automatically updates that user's capital**. Without this,
the capital row is just a writable display field; it doesn't reflect actual
trade outcomes.

## 2. Locked decisions

The five locks below are committed BEFORE writing any code. Deviating from any
of them after execution requires explicit reviewer sign-off; reviewer feedback
during code review is permitted to add LATER follow-ups but not to relax these
locks.

### 2.1 Hook points

| Path | Hook | Why |
|---|---|---|
| `POST /positions/{id}/close` (manual close) | After `db_close_position` returns a non-None position, call `apply_pnl_to_capital(tenant_id, pnl_usd)` if `tenant_id is not None and pnl_usd is not None` | The endpoint is where we know both the JWT-derived `tenant_id` and the freshly-computed `pnl_usd`. |
| `check_position_stops` auto-exit (SL/TP/TIME_LIMIT) | Same hook, called inline after the existing `db_close_position` call | Lifecycle parity — auto-exits affect capital identically to manual closes. |
| `DELETE /positions/{id}` (cancel) | **Skip** | `cancelled` positions never executed; no realized P&L. |
| Legacy positions with `tenant_id IS NULL` | **Skip** silently | Pre-multi-tenant data; the capital module has no record to update. Surfaces in audit log via `log.debug` only — not a warning, this is expected. |

### 2.2 Trigger condition

```python
if pos["tenant_id"] is not None and pos["pnl_usd"] is not None:
    apply_pnl_to_capital(pos["tenant_id"], pos["pnl_usd"])
```

`pos["pnl_usd"]` is computed by `db_close_position` via `_calc_pnl` and persisted to the row. If qty is missing the function may return `None` — in that case we skip silently (no capital update without a quantified outcome).

### 2.3 Math semantics

```python
def apply_pnl_to_capital(tenant_id: int, pnl_usd: float) -> dict:
    row = db_get_capital(tenant_id)
    if row is None:
        # First-close: auto-init from INITIAL_CAPITAL constant.
        prior_balance = INITIAL_CAPITAL  # $10,000 — same as backtest convention
        prior_peak = INITIAL_CAPITAL
    else:
        prior_balance = row["balance"]
        prior_peak = row["peak_balance"]

    new_balance = prior_balance + pnl_usd
    new_peak = max(prior_peak, new_balance)   # MONOTONIC: never decreases
    if new_peak > 0:
        new_dd_pct = (new_peak - new_balance) / new_peak * 100.0
    else:
        new_dd_pct = None  # peak ≤ 0 makes drawdown undefined

    return db_upsert_capital(
        tenant_id,
        balance=new_balance,
        peak_balance=new_peak,
        max_drawdown_pct=new_dd_pct,
    )
```

Invariants under this formula:

- **Peak monotonicity:** `new_peak >= prior_peak` always.
- **Drawdown is current** (not historical max). If you want "deepest drawdown ever recorded," that's `max_drawdown_pct` aggregated externally — out of scope here. The field stored is *current drawdown from peak*; renaming to `current_drawdown_pct` is a separate B.2.x cleanup if reviewer asks.
- **Negative balance** is permitted (matches the backtest's per-symbol $10K floor at $0 only when bankruptcy halt fires — but here we model a per-USER stream, no halt). Operator should see the negative balance and act.

### 2.4 Idempotency / concurrency

This pre-reg explicitly **does NOT** implement idempotency or row-locking. Rationale:

- SQLite serializes writes via filesystem lock; concurrent closes on the same `tenant_id` will queue, not race.
- Re-calling close on an already-closed position is short-circuited by `db_close_position` itself (returns `None` when `status != 'open'`), so the hook never fires twice for one position.
- Auditable provenance per close (capital snapshot at each close) is a separate follow-up — out of scope.

If, post-execution, an operator finds a desync between sum-of-closes and capital balance, that's a `capital_reconciliation` follow-up ticket, not a relaxation of this lock.

### 2.5 Backfill (one-shot CLI)

`scripts/backfill_capital_for_user.py`:

```
python scripts/backfill_capital_for_user.py --user-id 1 --initial-balance 10000 [--force]
```

Semantics:
- If `--force` is absent AND a capital row already exists for `user_id`, refuse with exit 1.
- Else: `db_upsert_capital(user_id, balance=initial_balance, peak_balance=initial_balance, max_drawdown_pct=None)`.
- Logs the action; idempotent under `--force`.
- Does NOT iterate closed positions and replay P&L. Initial balance is a hand-set anchor; closed positions BEFORE this script runs are not retroactively rolled into capital. (Operator's $10K snapshot is the contract.)

## 3. Out of scope (deferred follow-ups)

| Item | Where it goes |
|---|---|
| Real-time unrealized P&L (open positions affecting capital) | Future B.x — needs design |
| Cross-symbol portfolio aggregation | Epic-level; not B.2 |
| Per-close audit row (snapshot history) | B.2.1 follow-up if requested |
| `current_drawdown_pct` rename | B.2.1 follow-up if reviewer pushes back on schema name |
| Reconciliation script (verify sum-of-closes ≡ capital) | B.2.2 follow-up — useful for B.8 migration |
| Retroactive backfill from closed-positions history | Explicitly NO — hand-anchor only |

## 4. Tests (locked before writing them)

| Test | Asserts |
|---|---|
| `test_manual_close_increments_balance` | Open + close LONG with $250 profit → balance += 250, peak += 250, dd_pct = 0 |
| `test_manual_close_decrements_balance` | Open + close LONG with $100 loss → balance -= 100, peak unchanged, dd_pct > 0 |
| `test_peak_is_monotonic_after_loss_streak` | Three closes: +$300, -$200, -$50 → peak stays at 10300, dd_pct = (10300-10050)/10300 |
| `test_auto_close_via_check_position_stops` | SL_HIT path triggers capital update identically to manual close |
| `test_two_users_independent` | User A close +$100, User B close -$50 → A's balance = 10100, B's balance = 9950 (cross-isolation) |
| `test_cancel_does_not_touch_capital` | Open + DELETE → capital row unchanged or 404 |
| `test_legacy_tenant_null_skipped` | Position with tenant_id=NULL closes → no capital row created, no error |
| `test_first_close_auto_inits_capital` | First close for a tenant with no prior capital row → row created with balance = $10K + pnl |
| `test_drawdown_undefined_when_peak_zero` | Synthetic edge: peak = 0 → max_drawdown_pct returned as None, not div-by-zero |

## 5. Single-iteration discipline

If any of the 9 tests fails during execution, the failure mode is one of:
1. **Lock was wrong → STOP, escalate to reviewer.** Do not silently relax the lock.
2. **Implementation bug → fix and re-run.** Not a lock violation.

I will NOT iterate on test expectations to make implementation pass. If a test expectation is impossible under the locks, the lock review must happen explicitly in PR comments.

## 6. Done when

- All 9 tests above pass + no regression in existing test suite (1700+ tests)
- `scripts/backfill_capital_for_user.py` exists with idempotency contract
- PR description quotes locks §2.1–§2.5 verbatim so reviewer can spot drift
