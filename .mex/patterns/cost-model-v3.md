---
name: cost-model-v3
description: The backtest cost model is a two-body UPPER BOUND (floor spread+fee+funding + decoupled daily-basis sqrt impact tail), driven by active_model in costs_calibration.json; NOT an estimator. Load before touching cost calibration or backtest pricing.
triggers:
  - "cost model"
  - "costs_calibration.json"
  - "slippage"
  - "v3 calibration"
  - "stress_mult"
  - "compute_trade_costs"
last_updated: 2026-06-02
---

# Pattern: Cost-model v3 (two-body upper bound)

## Purpose

The cost model is an intentional conservative **UPPER BOUND**, not an unbiased estimator —
falsifiable against live P&L, not fit to it (R1: the bound must never be inverted by a
net-positive price-winner that turned net-loser after costs). v3 replaced v2's single-anchor
sqrt with **two DECOUPLED bodies**, each responsible for a distinct regime.

Design spec: `docs/superpowers/specs/2026-06-02-cost-model-v3-design.md`.

## When to use

- Editing `costs_calibration.json` (any tier, any parameter).
- Changing `backtest_costs.py` (formulas, flags, version routing).
- Adding a new symbol or tier.
- Interpreting backtest cost numbers (why they changed, whether they are too high/low).
- Running or extending the falsification harness
  (`tools/ks_stress_replay/falsify_cost_bound.py`).

NOT for: the separate (future) unbiased empirical cost estimator epic, which lives *beside*
the bound and does not replace it.

## Steps / Key facts

### Two-body structure

**FLOOR** (size-independent, dominant body — the operating regime for near-instantaneous fills):

```
stress_mult * (2*half_spread + 2*fee_per_side) + funding
```

Round-trip floor by tier (bps): MAJOR ≈ 13 / MID ≈ 18 / SMALL ≈ 30.

**TAIL** (size-dependent guardrail — metaorder impact, DAILY basis, decoupled anchor):

```
Y * sigma_daily_bps * sqrt(order / (liquidity_per_min * 1440))
```

`Y = 1.5`. The sqrt law is reserved for the TAIL (metaorder regime); the strategy fires
near-instantaneous fills, so the operating-regime body is spread+fee, which the sqrt law
does not govern.

### Version routing

- `active_model` in `costs_calibration.json` drives production; currently `"v3"`.
- v1 and v2 stay **byte-identical and callable** for parity tests. v2 anchor numbers are
  FROZEN in the sibling `costs_calibration.v2.json`.
- `load_calibration` is version-aware — it routes to the correct param set based on
  `active_model`.

### `TierParams` dual-shape + poison NaN

`TierParams` is dual-shaped: v2 fields are present in a v3 struct (and vice versa) as NaN
"poison" sentinels. Feeding a v2 `TierParams` into a v3 formula, or the reverse, raises or
produces NaN — **never a silent 0**. Always construct via `from_v2_flat` (v2 structs) or
`from_v3_tier` (v3 structs).

### Bound guarantee

The UPPER BOUND property rests on three pillars:

1. **Non-negativity** — each body is non-negative by construction.
2. **Total-cost cap** — 1 000 bps round-trip hard ceiling.
3. **`stress_mult`** — a cost pessimism dial (default 1.0); stress-replay runs set it > 1.

The bound guarantee does NOT require floor > tail at all order sizes — each body guards its
own regime independently.

### `stress_mult` and fee constants

- `stress_mult` is a **cost pessimism dial only** — orthogonal to the kill-switch
  `size_factor`. It must never be used as a sizing risk-scaler.
- `fee_bps_per_side = 5.0` = published Binance taker fee, no cushion.
  `PUBLISHED_TAKER_FEE_BPS` is the external mandatory lower bound for the falsification
  gate — the floor cannot be set below it.

### Falsification harness

`tools/ks_stress_replay/falsify_cost_bound.py` verifies R1: no per-symbol price-winner
inverts to a net loser after costs. It also enforces a fee-floor tripwire. Constraints:

- Read-only against the server `signals.db` (post-cutoff window, NN#3-clean).
- Requires `n >= 20` filled trades per symbol (precondition).
- **Cannot run in CI** — local `signals.db` has 0 rows.
- Running it against the server DB is a **MERGE PRECONDITION** for any calibration change
  (spec §9).

## Gotchas

- **Live P&L is a sanity CEILING, never a fit target.** Net = gross − cost; with single-
  regime live data the two are mutually identified. Do not regress cost parameters against
  live net P&L.
- **The sqrt law belongs to the TAIL, not the FLOOR.** The strategy fires fills too fast for
  metaorder impact to govern; the dominant cost is spread+fee. Using sqrt for the floor would
  underestimate costs at small sizes and overestimate at large — which is the wrong direction
  for an upper bound.
- **LRC `_costs_active` guard EXCLUDES `enable_funding`** (unlike RA), deliberately.
  Funding is priced via threading; including it in the LRC flag would break the three-flag
  costs-off idiom. A pure gross run sets **ALL FOUR** cost flags False.
- **Editing `costs_calibration.json` is production-governing (Non-Negotiable #6).** Any
  calibration PR must: bump the version field, update the cost section in
  `.mex/context/architecture.md`, and pass the falsification harness against the server DB.

## Verify Checklist

Before merging any change touching costs or calibration:

- [ ] `active_model` in `costs_calibration.json` is set to the intended version (`"v3"`
      unless deliberately reverting).
- [ ] v1/v2 parity tests pass against their frozen sibling calibrations (no regressions).
- [ ] Falsification harness (`falsify_cost_bound.py`) run against server DB with `n >= 20`
      per symbol — no price-winner inversion found.
- [ ] No holdout access (NN#3): falsification reads use the live `signals.db` post-cutoff
      window only.
- [ ] `RISK_PER_TRADE` unchanged (NN#4): `stress_mult` is a cost dial, not a sizing lever.

## Out of scope

- **Unbiased empirical cost estimator** — a separate future epic that lives *beside* the
  bound; it does not replace it.
- **Per-symbol rolling sigma** — fast-follow work, not part of v3.
