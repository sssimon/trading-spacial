# #543 — kill-switch live-DD fail-closed

## Problem

`_compute_current_portfolio_dd` (`strategy/kill_switch_v2_calibrator.py`) collapses
**every** exception to `return 0.0`. `0.0` = "no drawdown" = the most permissive
answer for a safety input, and it is indistinguishable from a genuinely healthy
portfolio. On a DB hiccup / malformed capital row / price-snapshot failure the
kill-switch sees health exactly when the system is least healthy. Fail-**open** on
a safety organ. Surfaced by the #542 (#397) adversarial audit, Lens 4.

## Consumers (verified)

| Site | Function | Effect of phantom 0.0 | Posture needed |
|---|---|---|---|
| `calibrator.py:453` | degradation trigger (`min(per_tenant_dd)`) | 0.0 dominates `min` → trigger silent | fail-closed (exclude failing tenant) |
| `health.py:432` | `_is_portfolio_normal` (B5 auto-recovery gate) | tier=NORMAL → reactivates PAUSED symbol during the failure window | **fail-closed (the dangerous one)** |
| `health.py:1201` | `get_dashboard_state` no-capital display | 0.0 legitimate (fresh tenant) | unchanged (display) |

`_is_portfolio_normal` already has an outer `try/except → return False`
(fail-closed), but the helper swallows its own exception **before** that guard can
see it. The phantom 0.0 leaks under an existing fail-closed guard.

## Contract (approved: strict flag + raise)

`_compute_current_portfolio_dd(cfg, *, tenant_id, conn=None, strict=False) -> float`

Two failure sub-classes, handled distinctly:

- **Phase 1 — ledger reads** (`db_get_capital`, `_load_open_positions`): cannot
  compute anything. `strict=True` → re-raise (caller decides). `strict=False` →
  `0.0` + `log.warning` (legacy display behavior preserved).
- **Phase 2 — full DD with MTM** (`_snapshot_prices` + `compute_portfolio_dd_from_ledger`):
  the realized DD is ledger-derived and does NOT depend on prices. A price-feed
  outage must NOT erase a real drawdown (the #397 failure class). Degrade to
  **ledger-only DD** = `-(peak - balance)/peak`, never `0.0`. This does NOT
  re-raise even under `strict=True` — a transient price hiccup freezing the
  system on every tick is worse than a one-tick MTM gap. Mirrors `get_dashboard_state`
  (#542, health.py:1175-1191).

Drop the docstring line "Returns 0.0 if anything fails (conservative — won't fire
degradation trigger)" — that conservatism is backwards for a circuit breaker.

## Callers

- `health.py:432` `_is_portfolio_normal` → pass `strict=True`. Outer guard already
  returns False → fail-closed for free. **The key fix.**
- `calibrator.py:453` degradation trigger → wrap each per-tenant call in
  try/except with `strict=True`; on error EXCLUDE that tenant from the `min()` and
  `log.warning`. If the list empties after exclusions, `current_dd=0.0` (the
  trigger emits a *recommendation*, not direct protection).
- `health.py:1201` `get_dashboard_state` → leave `strict=False` (default). No
  behavior change.

## Tests (TDD, written first)

`tests/test_strategy_kill_switch_v2_calibrator.py`:
1. `test_compute_dd_strict_true_reraises_on_ledger_read_failure` — monkeypatch
   `db.capital.db_get_capital` → raise; `strict=True` raises, `strict=False` → 0.0.
2. `test_compute_dd_price_failure_degrades_to_ledger_only_not_zero` — capital
   balance<peak, monkeypatch `_snapshot_prices` → raise; DD == ledger-only (≠ 0.0)
   even with `strict=True`.

`tests/test_health_probation.py`:
3. `test_is_portfolio_normal_returns_false_when_dd_computation_fails` — seed 1
   tenant capital, monkeypatch `_compute_current_portfolio_dd` → raise; assert
   `_is_portfolio_normal(cfg) is False` (fail-closed).
4. `test_degradation_trigger_excludes_failing_tenant` — calibrator loop with 2
   tenants, one whose DD raises; the failing tenant is excluded, no phantom 0.0
   injected into `min()`.

## Out of scope

- Shadow `emit_shadow_decision` outer try/except (writes nothing on failure —
  that is already fail-safe: no decision row beats a wrong one).
- Last-known-good DD caching (a heavier v2 enhancement; ledger-only degrade
  already preserves realized DD without it).

## Verify

- `python -m pytest tests/test_strategy_kill_switch_v2_calibrator.py tests/test_health_probation.py -v`
- Adversarial 4-lens audit (independent subagent, default-to-suspicion).
- 1 PR. Merge requested separately (explicit per-op auth).
