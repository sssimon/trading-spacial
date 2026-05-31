---
name: computing-portfolio-dd
description: Runbook for computing live portfolio drawdown — use the canonical ledger-based helper, never re-walk closed trades. Fixes the #397 double-counting bug.
triggers:
  - "portfolio drawdown"
  - "portfolio DD"
  - "current equity"
  - "kill switch"
  - "compute_portfolio_dd_from_ledger"
  - "dd_formula_version"
last_updated: 2026-05-31
---

# Pattern: Computing live portfolio drawdown

## Purpose

The capital ledger `balance` already folds realized PnL of every closed trade via `apply_pnl_to_capital`. Re-walking closed trades on top of it **double-counts** closed PnL and **under-reports** drawdown — making the kill-switch too permissive (the #397 bug).

`compute_portfolio_dd_from_ledger` is the **single canonical helper** for all live-DD computations. See [[../context/architecture.md]] §Portfolio drawdown — single source of truth.

## When to use

- Any code that needs current portfolio equity or drawdown % for a live path (shadow, calibrator, dashboard, kill-switch).
- Adding a new consumer of portfolio DD.
- Reviewing code that inspects drawdown (audit for double-counting).

## Steps

```python
from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger

result = compute_portfolio_dd_from_ledger(
    balance=capital_state.balance,           # realized equity (closed PnL already inside)
    peak_balance=capital_state.peak_balance,
    open_positions=list_of_open_position_dicts,
    now_price_by_symbol={"BTCUSDT": 67000.0, ...},
)

# result keys:
#   portfolio_dd    — float, NEGATIVE in drawdown (e.g. -0.05 = 5% DD)
#   current_equity  — balance + open_mtm
#   peak_equity     — max(peak_balance, current_equity)
```

Sign convention: `portfolio_dd` is **negative** when in drawdown. Zero when flat or at a new high.

### Current consumers (as of #397)

| Site | File |
|---|---|
| `emit_shadow_decision` | `strategy/kill_switch_v2_shadow.py` |
| `_compute_current_portfolio_dd` | `strategy/kill_switch_v2_calibrator.py` |
| `get_dashboard_state` | `health.py` |

### Fail-closed contract for live-DD reads (#543)

`_compute_current_portfolio_dd(cfg, *, tenant_id, conn=None, strict=False)` is the
helper safety callers go through. `0.0` means "no drawdown" — the **most
permissive** answer for a circuit-breaker input — so a computation failure must
never silently return it to a safety path. Two failure sub-classes, handled
distinctly:

- **Ledger read failure** (capital row / open positions unreadable): nothing can
  be computed. `strict=True` **propagates** the exception so the caller fails
  closed; `strict=False` (default, display callers) returns `0.0`.
- **Price-snapshot / MTM failure**: the realized drawdown is ledger-derived and
  price-independent. Degrade to the **ledger-only DD** `-(peak - balance)/peak` —
  never `0.0`, never re-raise (even under `strict`). A transient price hiccup
  must not erase a real drawdown (#397 class) nor freeze the system every tick.

Caller posture:

| Caller | `strict` | Rationale |
|---|---|---|
| `_is_portfolio_normal` (B5 auto-recovery gate) | `True` | outer guard returns `False` → blocks reactivation during a failure window |
| degradation trigger loop (calibrator) | `True` | a failing tenant is **excluded** from the `min()`, never injected as a phantom `0.0` that masks others' real DD |
| `get_dashboard_state` | `False` | display-only; a DB blip must not 500 the dashboard |

Two hardening details (#543 follow-up): the ledger-only degrade is **clamped at
`-1.0`** (a negative balance would otherwise produce a sub-100% drawdown); and
when the degradation loop finds **every** active tenant unreadable it emits one
`log.error` (aggregate "blind safety input") rather than only the scattered
per-tenant WARN lines — `current_dd` still falls back to `0.0`.

## Gotchas

- **NEVER pass `closed_trades` to a live-path DD computation.** The anti-pattern is:
  ```python
  # WRONG — double-counts closed PnL that is already in balance
  compute_portfolio_equity_curve(
      capital_base=balance,
      closed_trades=closed_trades,
      ...
  )
  ```
  `compute_portfolio_equity_curve` is a backtest/walk-forward helper where `capital_base` is a *starting* capital that has NOT yet absorbed those closed trades. On a live path `balance` already absorbed them — passing `closed_trades` again inflates the equity curve and under-reports DD.

- **`dd_formula_version` flag on shadow rows.** New `v2_shadow` decision-log rows carry `dd_formula_version="ledger_v1"`. Historical (~199k) rows predate the fix and lack the flag. Consumers must distinguish by flag presence, not absence.

- **`open_mtm` is computed inside the helper** from `open_positions` + `now_price_by_symbol`. Callers do not need to compute it separately.

- **`portfolio_dd` is negative in drawdown.** Kill-switch thresholds are stored as negative floats (e.g. `-0.10` = 10% DD limit). Compare with `<=`, not `>=`.

## Verify Checklist

Before merging any code that computes portfolio equity or DD on a live path:

- [ ] Uses `compute_portfolio_dd_from_ledger` — not an inline formula.
- [ ] Does NOT pass `closed_trades` to any live-path equity curve.
- [ ] Sign convention checked: `portfolio_dd` is negative in drawdown; comparison uses `<=`.
- [ ] New shadow/log rows carry `dd_formula_version="ledger_v1"` if writing to the decision log.
- [ ] If adding a new consumer, the consumer is listed in the Steps table above (update this pattern).
