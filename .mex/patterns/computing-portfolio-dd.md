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
last_updated: 2026-05-30
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
