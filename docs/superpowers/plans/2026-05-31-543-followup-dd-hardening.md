# #543 follow-up A — live-DD hardening (clamp + all-unreadable alert)

Two non-blocking items the #543 adversarial audit raised. Same predicate as #543
(robustness of the live-DD computation / degradation path). One PR.

## Item 1 — clamp degraded DD at -1.0 (cosmetic)

The Phase-2 ledger-only degrade in `_compute_current_portfolio_dd` computes
`-(peak_eff - balance)/peak_eff`. With a NEGATIVE balance the raw ratio drops
below `-1.0` (worse than a -100% total loss), which is nonsensical as a drawdown
figure. Clamp to `max(-1.0, ledger_only_dd)`. Purely presentational — tier
evaluation already treats anything past the threshold as FROZEN, so behaviour is
unchanged; this just stops a sub-100% DD leaking into logs/dashboard.

## Item 2 — log.error when ALL tenants' DD is unreadable

Today, if every active tenant's DD raises, the degradation loop sets
`current_dd = 0.0` and the degradation *recommendation* is silently suppressed —
the only trace is scattered per-tenant WARN logs (one per tenant). Escalate the
*aggregate* condition to a single, higher-severity line:

- When there were active tenants but `per_tenant_dd` ended up empty (every tenant
  raised) → emit one `log.error` for the tick, naming how many tenants were
  unreadable. Severity bump from WARN→ERROR is the whole point: monitoring/alerting
  keys off ERROR, and this is a real "the safety input is blind" condition.
- **No counter, no Telegram, no threshold** (user decision): immediate, stateless,
  restart-safe. A single transient hourly hiccup will log one ERROR; a persistent
  outage logs one per tick. That noise is acceptable — an unreadable safety input
  every tick *should* be loud.
- The degradation behaviour is unchanged: `current_dd` still falls back to `0.0`
  (the trigger only emits an operator-reviewed recommendation, not protection;
  the real gate `_is_portfolio_normal` fails closed independently — #543).

### Pieces

- In `kill_switch_calibrator_loop`'s degradation branch: after the per-tenant
  loop, `if tenant_ids and not per_tenant_dd: log.error(...)`. One added branch,
  no new functions, no module state.

## Tests (TDD)

`tests/test_strategy_kill_switch_v2_calibrator.py`:
1. `test_compute_dd_degrade_clamps_at_negative_one` — negative balance + price
   failure → degraded DD == -1.0 (not < -1.0).
2. `test_calibrator_logs_error_when_all_tenants_unreadable` — 2 tenants both
   raising, one iteration → exactly one `log.error` naming the unreadable count;
   loop does not crash; no recommendation persisted.
3. `test_calibrator_no_error_when_some_tenant_readable` — 2 tenants, one readable
   → no `log.error` (the aggregate condition didn't trigger).

## Verify

- `python -m pytest tests/test_strategy_kill_switch_v2_calibrator.py -q`
- Regression across calibrator + probation + shadow/core.
- Code review / light audit (safety loop). 1 PR, references #543. Merge separate.
