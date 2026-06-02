# Cost-model v3 (two-body upper bound) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the v2 single-anchor sqrt cost model with a two-body upper bound — a size-independent FLOOR (spread+fee+funding) plus a decoupled daily-basis sqrt impact TAIL — so the backtest stops inverting per-symbol sign conclusions, while keeping v1/v2 byte-identical for parity tests.

**Architecture:** All cost math stays in `backtest_costs.py`. v3 adds: `GlobalParams`, dual-field `TierParams` (v2 `base_bps`/`size_factor` + v3 `stress_mult`/`sigma_daily_bps`, cross-fields = NaN poison via factories), `compute_tail_bps`, and a `model='v3'` branch in `compute_trade_costs` (floor + tail + total-cap). The active model is driven by `costs_calibration.json` `active_model`, not scattered literals. The v2 numbers are frozen in a sibling `costs_calibration.v2.json`; `load_calibration` becomes version-aware. The backtest paths (LRC + RA) are wired to v3 in ONE atomic commit with the JSON swap, because the moment `load_calibration()` returns v3 params, any v2-hardcoded path computes NaN. A read-only falsification harness validates no-sign-inversion against the server `signals.db` (cannot run in CI — unit-tested with synthetic rows).

**Tech Stack:** Python 3, pytest, dataclasses, SQLite (read-only for the harness). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-02-cost-model-v3-design.md` — read it before starting. Non-Negotiables in play: #3 (holdout — harness never reads pre-2025-04-29 frames, never calls `open_holdout`), #4 (`RISK_PER_TRADE` fixed; `stress_mult` is a cost dial, NOT a sizing risk-scaler), #6 (`costs_calibration.json` is production-governing).

**Branch:** `feat/cost-model-v3` (spec already committed at `2410d0e`).

**Verified line numbers** (2026-06-02, may drift as edits land — re-grep if a hunk doesn't match): `backtest_costs.py` — `compute_slippage_bps` def `:114`/param `:121`/guard `:172`; `compute_funding_cost_bps` def `:176` (leading `*`); `TierParams` `:219-229`; `Calibration` `:232-240`; `load_calibration` `:246-270`; `compute_trade_costs` def `:273`/param `model` `:285`. `backtest.py` — `_apply_costs_to_trade` def `:495`; RA `_costs_active` (incl. funding) `:656`; RA `_calibration` `:657`; RA close hardcode `model="v2"` `:737`; LRC `_costs_active` (NO funding) `:1003`; LRC `_calibration` `:1010`; LRC liquidity proxy `:1018-1019`; LRC callsite `:1169`; LRC tail-close callsite `:1474`. `tools/cost_diagnosis/recompute.py` — `load_calibration()` `:13`, `replace(..., size_factor=...)` `:34`, `model="v2"` `:39`.

---

## File Structure

- **Modify `backtest_costs.py`** — add `GlobalParams`, dual `TierParams` + factories, `compute_tail_bps`, `compute_trade_costs` v3 branch, version-aware `load_calibration`, `active_model` on `Calibration`, module constants. (One file owns all cost math — established pattern.)
- **Create `costs_calibration.v2.json`** — byte-frozen copy of today's v2 calibration (`version: 2`).
- **Modify `costs_calibration.json`** — swap to v3 schema (`version: 3`, `active_model: "v3"`, `global` block, nested `floor`/`impact_tail` per tier).
- **Modify `backtest.py`** — RA close path → `active_model`; LRC path → thread `model`/`enable_funding`/`holding_hours` + add funding to `_costs_active`; both LRC callsites.
- **Modify `tools/cost_diagnosis/recompute.py`** — pin to the v2 sibling.
- **Create `tools/ks_stress_replay/falsify_cost_bound.py`** — read-only falsification harness.
- **Modify `tests/test_backtest_costs_v2.py`** — fix the `v3` invalid-model sentinel; point anchor-parity at the sibling.
- **Modify `tests/test_backtest_costs.py`** — calibration-marker tests version-tolerant / nested-schema aware.
- **Create `tests/test_backtest_costs_v3.py`** — all new v3 unit tests.
- **Create `tests/test_falsify_cost_bound.py`** — harness unit tests with synthetic rows.
- **Modify `tests/test_holdout_isolation.py`** — add the harness to `HOLDOUT_LEGITIMATE_MODULES` if it imports any holdout-adjacent module (verify; likely NOT needed since it only reads prod `signals.db` + 2026 OHLCV).
- **Modify `.mex/context/architecture.md:84-90`** — reframe the sqrt formula as TAIL-only (same PR).

---

## PHASE 1 — v3 cost primitives (calibration untouched, default stays v2, repo stays green)

Everything here is tested against **directly-constructed** `TierParams`/`GlobalParams`. `load_calibration` and the JSON are NOT touched, so all existing callers and the default `model='v2'` path are byte-identical. Repo green throughout.

### Task 1: `GlobalParams` dataclass + module constants

**Files:**
- Modify: `backtest_costs.py` (after `EXTREME_PARTICIPATION_CAP_BPS`, ~`:111`)
- Test: `tests/test_backtest_costs_v3.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_costs_v3.py
"""v3 two-body cost model — see docs/superpowers/specs/2026-06-02-cost-model-v3-design.md."""
import math
import pytest
from backtest_costs import (
    GlobalParams, PUBLISHED_TAKER_FEE_BPS, DEFAULT_TOTAL_COST_CAP_BPS,
)


class TestGlobalParams:
    def test_defaults_match_spec(self):
        g = GlobalParams()
        assert g.Y_impact_constant == 1.5
        assert g.total_cost_cap_bps == 1000.0
        assert g.liquidity_fallback_floor_bps == 100.0
        assert g.v_daily_minutes_per_day == 1440.0

    def test_module_constants(self):
        assert PUBLISHED_TAKER_FEE_BPS == 5.0
        assert DEFAULT_TOTAL_COST_CAP_BPS == 1000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestGlobalParams -v`
Expected: FAIL — `ImportError: cannot import name 'GlobalParams'`.

- [ ] **Step 3: Write minimal implementation**

In `backtest_costs.py`, after the `EXTREME_PARTICIPATION_CAP_BPS = 500.0` block:

```python
# ── v3 two-body upper bound ─────────────────────────────────────────────────
# Exchange-published taker fee, used as the model-INDEPENDENT mandatory lower
# bound in the falsification harness (NOT read from calibration).
PUBLISHED_TAKER_FEE_BPS = 5.0

DEFAULT_Y_IMPACT = 1.5                  # top of the empirical O(1) band (type-coherent bound)
DEFAULT_TOTAL_COST_CAP_BPS = 1000.0     # total round-trip cap (re-spec; v2's 500 was per-leg)
DEFAULT_LIQUIDITY_FALLBACK_FLOOR_BPS = 100.0
DEFAULT_V_DAILY_MINUTES_PER_DAY = 1440.0


@dataclass(frozen=True)
class GlobalParams:
    """v3 calibration globals (the `global` block of costs_calibration.json)."""
    Y_impact_constant: float = DEFAULT_Y_IMPACT
    total_cost_cap_bps: float = DEFAULT_TOTAL_COST_CAP_BPS
    liquidity_fallback_floor_bps: float = DEFAULT_LIQUIDITY_FALLBACK_FLOOR_BPS
    v_daily_minutes_per_day: float = DEFAULT_V_DAILY_MINUTES_PER_DAY
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestGlobalParams -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backtest_costs.py tests/test_backtest_costs_v3.py
git commit -m "feat(cost-v3): GlobalParams + module constants"
```

---

### Task 2: `compute_tail_bps` — the daily-basis sqrt impact tail

**Files:**
- Modify: `backtest_costs.py` (after `compute_slippage_bps`, before `compute_funding_cost_bps`)
- Test: `tests/test_backtest_costs_v3.py`

- [ ] **Step 1: Write the failing test**

```python
class TestComputeTailBps:
    def test_zero_order_zero_tail(self):
        from backtest_costs import compute_tail_bps
        assert compute_tail_bps(
            order_usd=0.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
        ) == 0.0

    def test_daily_basis_value(self):
        # order=1000, liq/min=1e6, v_min=1440 -> V_daily=1.44e9
        # participation=1000/1.44e9=6.944e-7; sqrt=8.333e-4
        # tail = 1.5 * 300 * 8.333e-4 = 0.375 bps per fill
        from backtest_costs import compute_tail_bps
        t = compute_tail_bps(
            order_usd=1_000.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
        )
        assert t == pytest.approx(0.375, abs=0.001)

    def test_monotonic_in_order(self):
        from backtest_costs import compute_tail_bps
        kw = dict(liquidity_usd_per_min=1_000_000.0, sigma_daily_bps=500.0,
                  Y=1.5, v_daily_minutes_per_day=1440.0)
        assert (compute_tail_bps(order_usd=10_000.0, **kw)
                > compute_tail_bps(order_usd=1_000.0, **kw))

    def test_bad_liquidity_returns_nan(self):
        from backtest_costs import compute_tail_bps
        for bad in (0.0, -5.0, float("nan"), float("inf")):
            r = compute_tail_bps(
                order_usd=1_000.0, liquidity_usd_per_min=bad,
                sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
            )
            assert math.isnan(r)

    def test_negative_participation_clamped(self):
        from backtest_costs import compute_tail_bps
        assert compute_tail_bps(
            order_usd=-1_000.0, liquidity_usd_per_min=1_000_000.0,
            sigma_daily_bps=300.0, Y=1.5, v_daily_minutes_per_day=1440.0,
        ) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestComputeTailBps -v`
Expected: FAIL — `cannot import name 'compute_tail_bps'`.

- [ ] **Step 3: Write minimal implementation**

```python
def compute_tail_bps(
    *,
    order_usd: float,
    liquidity_usd_per_min: float,
    sigma_daily_bps: float,
    Y: float,
    v_daily_minutes_per_day: float,
) -> float:
    """v3 impact tail (per fill), in bps. sqrt on the DAILY participation basis.

    tail = Y * sigma_daily_bps * sqrt(order_usd / (liquidity_usd_per_min * v_daily_minutes_per_day))

    Returns NaN when liquidity is non-positive/non-finite — the caller
    (`compute_trade_costs` v3 branch) detects NaN and applies the floor-anchored
    leg fallback. Negative participation (degenerate input) is clamped to 0.
    """
    if (
        liquidity_usd_per_min is None
        or not math.isfinite(liquidity_usd_per_min)
        or liquidity_usd_per_min <= 0.0
    ):
        return float("nan")
    v_daily = liquidity_usd_per_min * v_daily_minutes_per_day
    participation = order_usd / v_daily
    if participation < 0.0:
        participation = 0.0
    return Y * sigma_daily_bps * math.sqrt(participation)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestComputeTailBps -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backtest_costs.py tests/test_backtest_costs_v3.py
git commit -m "feat(cost-v3): compute_tail_bps (daily-basis sqrt impact tail)"
```

---

### Task 3: Dual-field `TierParams` + NaN-poison factories

**Files:**
- Modify: `backtest_costs.py` `TierParams` (`:219-229`)
- Test: `tests/test_backtest_costs_v3.py`

- [ ] **Step 1: Write the failing test**

```python
class TestTierParamsDual:
    def test_v2_positional_still_works(self):
        # The existing v2 construction (5 positional args) must be unchanged.
        from backtest_costs import TierParams
        tp = TierParams(5.0, 1423.02, 7.5, 10.0, 2.0)
        assert tp.base_bps == 5.0
        assert tp.size_factor == 1423.02
        assert tp.half_spread_bps == 7.5
        assert tp.fee_bps_per_side == 10.0
        assert tp.funding_rate_bps_per_8h == 2.0
        # v3 cross-fields default to NaN poison
        assert math.isnan(tp.stress_mult)
        assert math.isnan(tp.sigma_daily_bps)

    def test_from_v3_tier_poisons_v2_fields(self):
        from backtest_costs import TierParams
        tp = TierParams.from_v3_tier(
            floor={"half_spread_bps": 1.5, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 1.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 300.0},
        )
        assert tp.half_spread_bps == 1.5
        assert tp.fee_bps_per_side == 5.0
        assert tp.funding_rate_bps_per_8h == 1.0
        assert tp.stress_mult == 1.0
        assert tp.sigma_daily_bps == 300.0
        # v2 fields are NaN so any accidental v2 consumption is LOUD, not 0.
        assert math.isnan(tp.base_bps)
        assert math.isnan(tp.size_factor)

    def test_from_v2_flat_poisons_v3_fields(self):
        from backtest_costs import TierParams
        tp = TierParams.from_v2_flat(
            base_bps=5.0, size_factor=1423.02, half_spread_bps=7.5,
            fee_bps_per_side=10.0, funding_rate_bps_per_8h=2.0,
        )
        assert tp.base_bps == 5.0
        assert math.isnan(tp.stress_mult)
        assert math.isnan(tp.sigma_daily_bps)

    def test_v3_params_in_v2_slippage_is_nan_not_zero(self):
        # The poison guarantee: a v3 TierParams fed to the v2 slippage path
        # produces NaN (detectable), NOT 0.0 (silent split-brain).
        from backtest_costs import TierParams, compute_slippage_bps
        tp = TierParams.from_v3_tier(
            floor={"half_spread_bps": 1.5, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 1.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 300.0},
        )
        slip = compute_slippage_bps(
            order_usd=1_000.0, liquidity_usd_per_min=1_000_000.0,
            base_bps=tp.base_bps, size_factor=tp.size_factor, model="v2",
        )
        assert math.isnan(slip)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestTierParamsDual -v`
Expected: FAIL — `TypeError`/`AttributeError` (`stress_mult` not a field; `from_v3_tier` missing).

- [ ] **Step 3: Write minimal implementation**

Replace the `TierParams` dataclass (`:219-229`) with:

```python
@dataclass(frozen=True)
class TierParams:
    """Per-tier cost parameters. Dual-shaped to carry BOTH v2 and v3 fields.

    v2 fields: base_bps, size_factor (NaN when loaded from a v3 calibration).
    v3 fields: stress_mult, sigma_daily_bps (NaN when loaded from v2).
    Shared:    half_spread_bps, fee_bps_per_side, funding_rate_bps_per_8h.

    Cross-version fields default to NaN ("poison"): a v3-loaded TierParams fed to
    the v2 slippage path yields NaN cost (loud, detectable) rather than 0.0
    (silent split-brain). Construct via from_v2_flat / from_v3_tier; the 5-arg
    positional form is preserved for legacy v2 tests.
    """
    base_bps: float
    size_factor: float
    half_spread_bps: float
    fee_bps_per_side: float
    funding_rate_bps_per_8h: float = 0.0
    stress_mult: float = float("nan")
    sigma_daily_bps: float = float("nan")

    @classmethod
    def from_v2_flat(cls, *, base_bps, size_factor, half_spread_bps,
                     fee_bps_per_side, funding_rate_bps_per_8h=0.0):
        return cls(
            base_bps=float(base_bps), size_factor=float(size_factor),
            half_spread_bps=float(half_spread_bps),
            fee_bps_per_side=float(fee_bps_per_side),
            funding_rate_bps_per_8h=float(funding_rate_bps_per_8h),
        )

    @classmethod
    def from_v3_tier(cls, *, floor: dict, impact_tail: dict):
        return cls(
            base_bps=float("nan"), size_factor=float("nan"),
            half_spread_bps=float(floor["half_spread_bps"]),
            fee_bps_per_side=float(floor["fee_bps_per_side"]),
            funding_rate_bps_per_8h=float(floor["funding_rate_bps_per_8h"]),
            stress_mult=float(floor["stress_mult"]),
            sigma_daily_bps=float(impact_tail["sigma_daily_bps"]),
        )
```

(`compute_slippage_bps` v2 with `base_bps=nan` returns `nan + nan*sqrt(...) = nan` — no code change needed; the existing arithmetic propagates NaN.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestTierParamsDual -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full v2 cost suite to confirm no regression**

Run: `python -m pytest tests/test_backtest_costs.py tests/test_backtest_costs_v2.py -q`
Expected: PASS (TierParams positional construction in `_params_mid` etc. unaffected — the two new fields have defaults).

- [ ] **Step 6: Commit**

```bash
git add backtest_costs.py tests/test_backtest_costs_v3.py
git commit -m "feat(cost-v3): dual-field TierParams with NaN-poison factories"
```

---

### Task 4: `compute_trade_costs` v3 branch (floor + tail + total-cap + fallback)

**Files:**
- Modify: `backtest_costs.py` `compute_trade_costs` (`:273-365`)
- Test: `tests/test_backtest_costs_v3.py`

- [ ] **Step 1: Write the failing test**

```python
class TestComputeTradeCostsV3:
    G = None  # set in each test via GlobalParams()

    def _v3_mid(self):
        from backtest_costs import TierParams
        return TierParams.from_v3_tier(
            floor={"half_spread_bps": 4.0, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 2.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 500.0},
        )

    def test_floor_dominates_at_operating_size(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3",
            global_params=GlobalParams(), holding_hours=5.3,
        )
        # FLOOR mid RT = 2*4 + 2*5 = 18 bps; funding 0 (5.3h < 8h floor)
        assert c["floor_bps"] == pytest.approx(18.0, abs=0.01)
        # tail tiny at $644 daily-basis: dominated by floor
        assert c["tail_bps"] < c["floor_bps"]
        assert c["total_cost_bps"] == pytest.approx(c["floor_bps"] + c["tail_bps"], abs=1e-6)
        assert c["cap_hit"] is False

    def test_funding_charged_only_past_8h(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3",
            global_params=GlobalParams(), holding_hours=24.0,
        )
        # 24h -> 3 intervals * 2bps = 6 funding; floor = 18 + 6 = 24
        assert c["funding_cost_bps"] == pytest.approx(6.0)
        assert c["floor_bps"] == pytest.approx(24.0, abs=0.01)

    def test_total_cap_binds(self):
        from backtest_costs import TierParams, compute_trade_costs, GlobalParams
        small = TierParams.from_v3_tier(
            floor={"half_spread_bps": 10.0, "fee_bps_per_side": 5.0,
                   "funding_rate_bps_per_8h": 5.0, "stress_mult": 1.0},
            impact_tail={"sigma_daily_bps": 800.0},
        )
        # Huge order vs thin liquidity -> tail explodes past 1000 cap.
        c = compute_trade_costs(
            entry_notional_usd=5_000_000.0, exit_notional_usd=5_000_000.0,
            entry_liquidity_usd_per_min=10_000.0,
            exit_liquidity_usd_per_min=10_000.0,
            tier_params=small, model="v3", global_params=GlobalParams(),
        )
        assert c["total_cost_bps"] == pytest.approx(1000.0)
        assert c["cap_hit"] is True

    def test_leg_fallback_composes_above_floor(self):
        from backtest_costs import compute_trade_costs, GlobalParams
        # entry liquidity dead -> entry leg = max(stress*(hs+fe)=9, fallback=100) = 100
        c = compute_trade_costs(
            entry_notional_usd=644.0, exit_notional_usd=644.0,
            entry_liquidity_usd_per_min=float("nan"),
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=self._v3_mid(), model="v3", global_params=GlobalParams(),
        )
        # entry leg fallback 100; exit leg = floor_leg(9) + tiny tail; funding 0
        assert c["total_cost_bps"] > 100.0
        # dead bar never cheaper than a live thin bar
        assert c["total_cost_bps"] >= 100.0

    def test_default_model_is_still_v2(self):
        # Function-signature default UNCHANGED (prod flips via active_model, Phase 2b).
        from backtest_costs import compute_trade_costs, TierParams
        c = compute_trade_costs(
            entry_notional_usd=1_000.0, exit_notional_usd=1_000.0,
            entry_liquidity_usd_per_min=1_000_000.0,
            exit_liquidity_usd_per_min=1_000_000.0,
            tier_params=TierParams(5.0, 1423.02, 7.5, 10.0, 2.0),
            enable_spread=False, enable_fees=False, enable_funding=False,
        )
        assert c["entry_slippage_bps"] == pytest.approx(50.0, abs=0.1)  # v2 anchor

    def test_unknown_model_raises_in_trade_costs(self):
        from backtest_costs import compute_trade_costs, TierParams
        with pytest.raises(ValueError, match="Unknown cost model"):
            compute_trade_costs(
                entry_notional_usd=1_000.0, exit_notional_usd=1_000.0,
                entry_liquidity_usd_per_min=1e6, exit_liquidity_usd_per_min=1e6,
                tier_params=TierParams(5.0, 1423.02, 7.5, 10.0, 2.0),
                model="bogus",
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestComputeTradeCostsV3 -v`
Expected: FAIL — v3 branch absent; `global_params`/`floor_bps`/`cap_hit` unknown.

- [ ] **Step 3: Write minimal implementation**

Add a module-default global near the constants:

```python
_DEFAULT_GLOBAL = GlobalParams()
```

Add a private v3 leg helper above `compute_trade_costs`:

```python
def _v3_leg_cost(order_usd, liquidity_usd_per_min, tp, g):
    """Return (leg_bps, slip_component_bps, is_fallback) for one v3 fill.

    Normal leg:   floor_leg(spread+fee, stress-scaled) + tail.
    Fallback leg: max(floor_leg, fallback_floor) when liquidity is unusable.
    slip_component_bps is the portion reported as *_slippage_bps (the tail, or
    the fallback excess above the floor leg) so the output dict still sums.
    """
    floor_leg = tp.stress_mult * (tp.half_spread_bps + tp.fee_bps_per_side)
    tail = compute_tail_bps(
        order_usd=order_usd, liquidity_usd_per_min=liquidity_usd_per_min,
        sigma_daily_bps=tp.sigma_daily_bps, Y=g.Y_impact_constant,
        v_daily_minutes_per_day=g.v_daily_minutes_per_day,
    )
    if math.isnan(tail):
        leg = max(floor_leg, g.liquidity_fallback_floor_bps)
        return leg, max(0.0, leg - floor_leg), True
    return floor_leg + tail, tail, False
```

Then modify `compute_trade_costs`: add `model` validation + `global_params` kwarg + the v3 branch. At the top of the function body (after the docstring), add:

```python
    if model not in ("v1", "v2", "v3"):
        raise ValueError(f"Unknown cost model {model!r}; expected 'v1', 'v2', or 'v3'")

    if model == "v3":
        g = global_params if global_params is not None else _DEFAULT_GLOBAL
        entry_leg, entry_slip, _ = _v3_leg_cost(
            entry_notional_usd, entry_liquidity_usd_per_min, tier_params, g)
        exit_leg, exit_slip, _ = _v3_leg_cost(
            exit_notional_usd, exit_liquidity_usd_per_min, tier_params, g)
        sm = tier_params.stress_mult
        entry_spread = sm * tier_params.half_spread_bps if enable_spread else 0.0
        exit_spread = sm * tier_params.half_spread_bps if enable_spread else 0.0
        fee_bps = sm * 2.0 * tier_params.fee_bps_per_side if enable_fees else 0.0
        funding_bps = (
            compute_funding_cost_bps(
                holding_hours=holding_hours,
                funding_rate_bps_per_8h=tier_params.funding_rate_bps_per_8h,
            )
            if (enable_funding and holding_hours > 0.0) else 0.0
        )
        entry_tail = entry_slip if enable_slippage else 0.0
        exit_tail = exit_slip if enable_slippage else 0.0
        floor_bps = entry_spread + exit_spread + fee_bps + funding_bps
        tail_bps = entry_tail + exit_tail
        uncapped = floor_bps + tail_bps
        cap_hit = uncapped >= g.total_cost_cap_bps
        total_cost_bps = min(uncapped, g.total_cost_cap_bps)
        avg_notional = 0.5 * (entry_notional_usd + exit_notional_usd)
        return {
            "entry_slippage_bps": entry_tail, "exit_slippage_bps": exit_tail,
            "entry_spread_bps": entry_spread, "exit_spread_bps": exit_spread,
            "fee_bps": fee_bps, "funding_cost_bps": funding_bps,
            "floor_bps": floor_bps, "tail_bps": tail_bps, "cap_hit": cap_hit,
            "total_cost_bps": total_cost_bps,
            "total_cost_usd": total_cost_bps * avg_notional / 10_000.0,
        }
```

Add `global_params: "GlobalParams | None" = None` to the `compute_trade_costs` signature (keyword, after `model`). Keep `model: Literal["v1", "v2", "v3"] = "v2"` (extend the Literal; default stays v2).

Note: the v1/v2 output dict does NOT have `floor_bps`/`tail_bps`/`cap_hit`. That is fine — they are v3-only add-on keys (the output contract is add-only per leg-path). Callers that read shared keys are unaffected.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestComputeTradeCostsV3 -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Confirm v2 path untouched**

Run: `python -m pytest tests/test_backtest_costs_v2.py -q`
Expected: PASS — except `TestModelDispatch::test_unknown_model_raises` may now behave differently (it passes `model="v3"` to `compute_slippage_bps`). Verify: `compute_slippage_bps` Literal is still `['v1','v2']` and its guard still raises on `'v3'` → test still PASSES at this point. (It is hardened in Task 5.)

- [ ] **Step 6: Commit**

```bash
git add backtest_costs.py tests/test_backtest_costs_v3.py
git commit -m "feat(cost-v3): compute_trade_costs v3 branch (floor+tail+cap+fallback)"
```

---

### Task 5: Harden the invalid-model sentinel (pre-req for any v3 wiring)

The `test_unknown_model_raises` test uses `model="v3"` as the *invalid* sentinel against `compute_slippage_bps`. v3 is now a real model at the `compute_trade_costs` level; keep the slippage-level guard test unambiguous by switching the sentinel to a string that is invalid everywhere.

**Files:**
- Modify: `tests/test_backtest_costs_v2.py:516-526`

- [ ] **Step 1: Change the sentinel**

In `tests/test_backtest_costs_v2.py`, `test_unknown_model_raises`, change `model="v3",  # noqa` to:

```python
                model="v9",  # invalid sentinel (v3 is now a real model)
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_backtest_costs_v2.py::TestModelDispatch -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_backtest_costs_v2.py
git commit -m "test(cost-v3): retire 'v3' as invalid-model sentinel (use 'v9')"
```

---

## PHASE 2 — atomic calibration swap + path wiring

### Task 6: Version-aware `load_calibration` + `active_model` + v2 sibling (calibration shape UNCHANGED → green)

This task makes the loader version-aware and adds the sibling, while the **main** `costs_calibration.json` stays `version: 2`. So `load_calibration()` still returns the v2 calibration byte-identically → repo green. The v3 JSON swap is the NEXT task (atomic).

**Files:**
- Create: `costs_calibration.v2.json`
- Modify: `backtest_costs.py` `Calibration` (`:232-240`) + `load_calibration` (`:246-270`)
- Test: `tests/test_backtest_costs_v3.py`

- [ ] **Step 1: Create the frozen v2 sibling**

Copy the current `costs_calibration.json` verbatim to `costs_calibration.v2.json`. It already has `"version": 2`. Do NOT edit its contents.

```bash
cp costs_calibration.json costs_calibration.v2.json
```

- [ ] **Step 2: Write the failing test**

```python
class TestVersionAwareLoader:
    def test_v2_sibling_loads_flat(self):
        from backtest_costs import load_calibration
        cal = load_calibration(path="costs_calibration.v2.json")
        assert cal.version == 2
        assert cal.active_model == "v2"   # absent in v2 JSON -> defaults to "v2"
        mid = cal.tiers["mid"]
        assert mid.size_factor == pytest.approx(1423.02)  # v2 field present
        import math
        assert math.isnan(mid.stress_mult)                # v3 field poisoned

    def test_v3_fixture_loads_nested(self, tmp_path):
        import json
        from backtest_costs import load_calibration
        v3 = {
            "version": 3, "model": "two-body", "active_model": "v3",
            "global": {"Y_impact_constant": 1.5, "total_cost_cap_bps": 1000.0,
                       "liquidity_fallback_floor_bps": 100.0,
                       "v_daily_minutes_per_day": 1440},
            "tiers": {"mid": {"symbols": ["ADAUSDT"],
                "floor": {"half_spread_bps": 4.0, "fee_bps_per_side": 5.0,
                          "funding_rate_bps_per_8h": 2.0, "stress_mult": 1.0},
                "impact_tail": {"sigma_daily_bps": 500.0}}},
            "sources": {}, "sensitivity_note": "x",
        }
        p = tmp_path / "v3.json"
        p.write_text(json.dumps(v3))
        cal = load_calibration(path=str(p))
        assert cal.version == 3
        assert cal.active_model == "v3"
        assert cal.global_.Y_impact_constant == 1.5
        mid = cal.tiers["mid"]
        assert mid.sigma_daily_bps == 500.0
        import math
        assert math.isnan(mid.size_factor)  # v2 field poisoned
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestVersionAwareLoader -v`
Expected: FAIL — `Calibration` has no `active_model`/`global_`; loader is flat-only.

- [ ] **Step 4: Write minimal implementation**

Extend `Calibration` (`:232-240`):

```python
@dataclass(frozen=True)
class Calibration:
    version: int
    model: str
    v2_planned: str
    tiers: dict
    sources: dict
    sensitivity_note: str
    active_model: str = "v2"
    global_: "GlobalParams | None" = None
```

Rewrite `load_calibration` to branch on version:

```python
def load_calibration(path: str | Path | None = None) -> Calibration:
    """Load + validate calibration. version==2 -> flat parser (byte-identical to
    legacy); version==3 -> nested floor/impact_tail + global block. Refuses to
    silently fall back to hardcoded defaults (FileNotFoundError propagates)."""
    p = Path(path) if path is not None else _CALIBRATION_PATH
    with p.open() as f:
        raw = json.load(f)
    version = int(raw["version"])

    if version >= 3:
        gb = raw["global"]
        tiers = {
            name: TierParams.from_v3_tier(floor=t["floor"], impact_tail=t["impact_tail"])
            for name, t in raw["tiers"].items()
        }
        return Calibration(
            version=version, model=raw["model"],
            v2_planned=raw.get("v3_planned", raw.get("v2_planned", "")),
            tiers=tiers, sources=dict(raw["sources"]),
            sensitivity_note=raw["sensitivity_note"],
            active_model=raw.get("active_model", "v3"),
            global_=GlobalParams(
                Y_impact_constant=float(gb["Y_impact_constant"]),
                total_cost_cap_bps=float(gb["total_cost_cap_bps"]),
                liquidity_fallback_floor_bps=float(gb["liquidity_fallback_floor_bps"]),
                v_daily_minutes_per_day=float(gb["v_daily_minutes_per_day"]),
            ),
        )

    # version 2 (and below): flat parser — byte-identical to the legacy path.
    tiers = {
        name: TierParams.from_v2_flat(
            base_bps=t["base_bps"], size_factor=t["size_factor"],
            half_spread_bps=t["half_spread_bps"],
            fee_bps_per_side=t["fee_bps_per_side"],
            funding_rate_bps_per_8h=t.get("funding_rate_bps_per_8h", 0.0),
        )
        for name, t in raw["tiers"].items()
    }
    return Calibration(
        version=version, model=raw["model"],
        v2_planned=raw.get("v2_planned", raw.get("v3_planned", "")),
        tiers=tiers, sources=dict(raw["sources"]),
        sensitivity_note=raw["sensitivity_note"],
        active_model=raw.get("active_model", "v2"),
        global_=None,
    )
```

- [ ] **Step 5: Run test + full cost suite**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestVersionAwareLoader tests/test_backtest_costs.py tests/test_backtest_costs_v2.py -q`
Expected: PASS — main JSON still v2, flat parse byte-identical.

- [ ] **Step 6: Commit**

```bash
git add backtest_costs.py costs_calibration.v2.json tests/test_backtest_costs_v3.py
git commit -m "feat(cost-v3): version-aware load_calibration + frozen v2 sibling"
```

---

### Task 7: THE ATOMIC SWAP — main JSON → v3 + wire RA & LRC paths + pin recompute + re-point marker tests

**This is the irreducible atomic commit.** The instant `load_calibration()` returns v3 params (NaN `size_factor`), any path still calling `model="v2"` against them computes NaN. So the JSON swap, the RA wire, the LRC wire, the recompute pin, and the marker-test re-pointing all land in ONE commit. Write all failing tests first, make them green, then commit once.

**Files:**
- Modify: `costs_calibration.json` (→ v3)
- Modify: `backtest.py:737` (RA `model="v2"` → `active_model`), `:1003` (LRC `_costs_active` + funding), `:495-528` (`_apply_costs_to_trade` signature), `:1169` + `:1474` (LRC callsites)
- Modify: `tools/cost_diagnosis/recompute.py:13` (pin to sibling)
- Modify: `tests/test_backtest_costs_v2.py` (anchor-parity → sibling), `tests/test_backtest_costs.py` (marker tests)
- Test: `tests/test_backtest_costs_v3.py` (path-integration)

- [ ] **Step 1: Write the failing integration tests**

```python
class TestBacktestPathsUseV3:
    def test_loaded_main_calibration_is_v3(self):
        from backtest_costs import load_calibration
        cal = load_calibration()
        assert cal.version == 3
        assert cal.active_model == "v3"
        assert cal.global_ is not None and cal.global_.Y_impact_constant == 1.5
        # v3 floor numbers per spec §2
        assert cal.tiers["major"].half_spread_bps == 1.5
        assert cal.tiers["major"].fee_bps_per_side == 5.0
        assert cal.tiers["mid"].sigma_daily_bps == 500.0

    def test_floor_rt_values(self):
        from backtest_costs import load_calibration
        cal = load_calibration()
        def floor_rt(t):
            return cal.tiers[t].stress_mult * (2*cal.tiers[t].half_spread_bps
                                               + 2*cal.tiers[t].fee_bps_per_side)
        assert floor_rt("major") == pytest.approx(13.0)
        assert floor_rt("mid") == pytest.approx(18.0)
        assert floor_rt("small") == pytest.approx(30.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestBacktestPathsUseV3 -v`
Expected: FAIL — main JSON still v2 (`version==2`).

- [ ] **Step 3: Swap `costs_calibration.json` to v3**

Replace the entire file with the v3 schema from spec §5 (`version: 3`, `active_model: "v3"`, `global` block with `Y_impact_constant: 1.5`, `total_cost_cap_bps: 1000.0`, `liquidity_fallback_floor_bps: 100.0`, `v_daily_minutes_per_day: 1440`; per-tier `floor` {half_spread 1.5/4.0/10.0, fee 5.0, funding 1.0/2.0/5.0, stress_mult 1.0} + `impact_tail` {sigma 300/500/800}). Populate `sources` honestly per spec §2-§3 (fee = published taker no cushion; tail reduction ~100x = sqrt(1440) base × ~1.9x slope at Y=1.5; half_spread conservative perp quartile). Keep `symbols` arrays identical to v2.

- [ ] **Step 4: Wire the RA close path (`backtest.py:737`)**

`_calibration` is in scope at `:657`. Change `model="v2",` (`:737`) to:

```python
                model=_calibration.active_model,
                global_params=_calibration.global_,
```

- [ ] **Step 5: Wire the LRC path — add funding to `_costs_active` (`backtest.py:1003`)**

```python
    _costs_active = bool(enable_slippage or enable_spread or enable_fees or enable_funding)
```

- [ ] **Step 6: Thread model/funding/holding through `_apply_costs_to_trade` (`backtest.py:495-528`)**

Extend the signature (add after `enable_fees`):

```python
    enable_funding: bool = True,
    model: str = "v2",
    global_params=None,
```

In the body, compute holding hours from the trade dict and pass the new args to `compute_trade_costs_fn` (`:518`):

```python
    cost = compute_trade_costs_fn(
        entry_notional_usd=entry_notional,
        exit_notional_usd=exit_notional,
        entry_liquidity_usd_per_min=position.get("entry_liquidity_per_min", float("nan")),
        exit_liquidity_usd_per_min=exit_liquidity_per_min,
        tier_params=tier_params,
        enable_slippage=enable_slippage,
        enable_spread=enable_spread,
        enable_fees=enable_fees,
        enable_funding=enable_funding,
        holding_hours=float(trade.get("duration_hours", 0.0) or 0.0),
        model=model,
        global_params=global_params,
    )
```

- [ ] **Step 7: Update BOTH LRC callsites (`:1169` and `:1474`)**

At `:1169`:

```python
                    _apply_costs_to_trade(
                        trade, position, exit_price, _exit_liq,
                        compute_trade_costs, _tier_params,
                        enable_slippage, enable_spread, enable_fees,
                        enable_funding=enable_funding,
                        model=_calibration.active_model,
                        global_params=_calibration.global_,
                    )
```

At `:1474` (tail-close — identical change with `_exit_liq_final`):

```python
            _apply_costs_to_trade(
                trade, position, exit_price, _exit_liq_final,
                compute_trade_costs, _tier_params,
                enable_slippage, enable_spread, enable_fees,
                enable_funding=enable_funding,
                model=_calibration.active_model,
                global_params=_calibration.global_,
            )
```

Note: `_calibration` is created inside `if _costs_active:` at `:1010` and is in scope at both callsites (both are guarded by `if _costs_active:`).

- [ ] **Step 8: Pin recompute to the v2 sibling (`tools/cost_diagnosis/recompute.py:13`)**

```python
_CAL = load_calibration(path="costs_calibration.v2.json")
```

(`:34` `replace(tp, size_factor=...)` stays valid — the sibling carries `size_factor`. `:39` `model="v2"` stays — it is a v2 diagnostic by construction.)

- [ ] **Step 9: Re-point the v2 anchor-parity + marker tests**

In `tests/test_backtest_costs_v2.py`, `test_committed_calibration_v2_anchor_parity_with_v1_baseline` (`:92`): change `cal = load_calibration()` to `cal = load_calibration(path="costs_calibration.v2.json")`. (Any other test in this file reading `load_calibration()` and asserting v2 `size_factor` gets the same change — grep `load_calibration()` in the file.)

In `tests/test_backtest_costs.py`: `test_calibration_records_v2_model_marker` → assert against the sibling (`load_calibration(path="costs_calibration.v2.json")`, version==2, 'sqrt-participation'). `test_loads_committed_calibration` / `test_calibration_documents_source_per_param` → either re-point to the sibling (if they assert v2 flat keys) OR add a parallel v3 assertion; grep these tests and make each assert against the file whose shape it checks.

- [ ] **Step 10: Run the integration tests + full cost + backtest suites**

Run:
```bash
python -m pytest tests/test_backtest_costs_v3.py tests/test_backtest_costs.py tests/test_backtest_costs_v2.py -q
python -m pytest tests/test_backtest_refactor_parity.py tests/test_backtest_costs_v2.py tests/test_backtest_regime_allocation.py -q
```
Expected: PASS. If a backtest test asserts specific v2-priced PnL numbers, it will shift to v3 — those are the "backtests to re-run" (spec §8); update or xfail with a tracking note, do NOT fudge.

- [ ] **Step 11: Commit (atomic)**

```bash
git add costs_calibration.json backtest.py tools/cost_diagnosis/recompute.py \
        tests/test_backtest_costs.py tests/test_backtest_costs_v2.py tests/test_backtest_costs_v3.py
git commit -m "feat(cost-v3): atomic swap to v3 — JSON + RA/LRC wiring + funding fix + recompute pin"
```

---

### Task 8: `.mex/context/architecture.md` — reframe the cost prose as TAIL-only

**Files:**
- Modify: `.mex/context/architecture.md:84-90`

- [ ] **Step 1: Read the current block**

Run: read `.mex/context/architecture.md` lines 80-95 to see the exact v2 sqrt/anchor-parity/Almgren-Chriss prose.

- [ ] **Step 2: Edit surgically**

Replace the v2 cost description with the v3 two-body framing: FLOOR (spread+fee+funding) is the dominant operating-regime bound; the sqrt impact (Almgren-Chriss / Tóth / Donier-Bonart) is now the decoupled TAIL only, daily participation basis, Y=1.5. Point to `docs/superpowers/specs/2026-06-02-cost-model-v3-design.md`. Keep it ≤ the original length (surgical, not a rewrite of the whole file).

- [ ] **Step 3: Verify mex doesn't flag drift**

Run: `mex check` — confirm no NEW real `MISSING_PATH`/drift for the cost section (per CLAUDE.md, most findings are noise; verify the cost lines specifically).

- [ ] **Step 4: Commit**

```bash
git add .mex/context/architecture.md
git commit -m "docs(mex): reframe cost prose as v3 two-body (tail-only sqrt)"
```

---

## PHASE 3 — falsification harness

### Task 9: `falsify_cost_bound.py` core (synthetic-row unit tests; cannot run vs server DB in CI)

**Files:**
- Create: `tools/ks_stress_replay/falsify_cost_bound.py`
- Test: `tests/test_falsify_cost_bound.py` (create)

The harness reads prod `signals.db` **read-only** (`mode=ro`), scores closed shorts in the 2026-05-21→06-01 window, and asserts (a) `n_closed_shorts >= 20` precondition (else abort loudly), (b) mandatory external fee-floor tripwire, (c) no per-symbol sign inversion. **NN#3:** it never reads frames pre-2025-04-29 and never imports `data.holdout_access`/`open_holdout`. Unit-tested with synthetic position dicts so it is CI-runnable without the server DB; the real server run is the merge precondition (spec §9).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_falsify_cost_bound.py
import math
import pytest
from tools.ks_stress_replay.falsify_cost_bound import (
    score_positions, assert_no_sign_inversion, MANDATORY_LOWER_BOUND_BPS,
    EXPECTED_MIN, InsufficientDataError,
)


def _pos(symbol, pnl_usd, pnl_pct, size_usd=644.0, liq=1_000_000.0):
    return {"symbol": symbol, "direction": "SHORT", "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct, "size_usd": size_usd, "liquidity_per_min": liq}


class TestFalsifyHarness:
    def test_mandatory_lower_bound_is_external_fee(self):
        # 2 * published taker fee (5.0) = 10.0 RT, NOT the model floor.
        assert MANDATORY_LOWER_BOUND_BPS == 10.0

    def test_insufficient_data_aborts(self):
        with pytest.raises(InsufficientDataError):
            assert_no_sign_inversion(score_positions([_pos("AVAXUSDT", 5.0, 0.5)]))

    def test_v3_preserves_winner_sign(self):
        # A winner in price stays a winner after v3 cost (floor-dominated).
        rows = [_pos("AVAXUSDT", 6.0, 0.5) for _ in range(20)]
        scored = score_positions(rows)
        # should NOT raise
        assert_no_sign_inversion(scored)

    def test_inflated_cost_inverts_sign_is_caught(self):
        # Simulate a model that overcharges (inject huge cost) -> inversion -> raises.
        rows = [_pos("AVAXUSDT", 0.2, 0.03) for _ in range(20)]
        scored = score_positions(rows, force_cost_bps=500.0)  # v2-like overcharge
        with pytest.raises(AssertionError, match="sign inversion"):
            assert_no_sign_inversion(scored)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_falsify_cost_bound.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/ks_stress_replay/falsify_cost_bound.py
"""Falsify the v3 cost bound against live realized P&L (read-only).

R1: live data is a sanity CEILING, never a fit target. The bound is falsified
ONLY when it underestimates an indisputable cost, or when it INVERTS a per-symbol
price-winner into a backtest loser. NN#3: reads prod signals.db (mode=ro) + 2026
OHLCV only; NEVER pre-2025-04-29 frames; NEVER imports holdout access.
"""
from __future__ import annotations

from backtest_costs import (
    load_calibration, tier_for_symbol, compute_trade_costs, PUBLISHED_TAKER_FEE_BPS,
)

EXPECTED_MIN = 20
NOISE_BAND_USD = 5.0
MANDATORY_LOWER_BOUND_BPS = 2 * PUBLISHED_TAKER_FEE_BPS   # 10.0 RT, exchange-published


class InsufficientDataError(RuntimeError):
    pass


def _v3_cost_usd(symbol, size_usd, liq, *, force_cost_bps=None):
    if force_cost_bps is not None:
        return force_cost_bps * size_usd / 10_000.0
    cal = load_calibration()
    tp = cal.tiers[tier_for_symbol(symbol)]
    c = compute_trade_costs(
        entry_notional_usd=size_usd, exit_notional_usd=size_usd,
        entry_liquidity_usd_per_min=liq, exit_liquidity_usd_per_min=liq,
        tier_params=tp, model=cal.active_model, global_params=cal.global_,
    )
    return c["total_cost_usd"]


def score_positions(rows, *, force_cost_bps=None):
    """Attach v3 cost to each closed-short row. Returns list of scored dicts."""
    scored = []
    for r in rows:
        cost_usd = _v3_cost_usd(r["symbol"], r["size_usd"], r["liquidity_per_min"],
                                force_cost_bps=force_cost_bps)
        cost_bps = cost_usd / r["size_usd"] * 10_000.0
        scored.append({**r, "v3_cost_usd": cost_usd, "v3_cost_bps": cost_bps})
    return scored


def assert_no_sign_inversion(scored):
    n = len(scored)
    if n < EXPECTED_MIN:
        raise InsufficientDataError(
            f"falsification needs >={EXPECTED_MIN} closed shorts, got {n}")
    # Secondary tripwire: model never below the external mandatory fee floor.
    for s in scored:
        assert s["v3_cost_bps"] >= MANDATORY_LOWER_BOUND_BPS, (
            f"{s['symbol']}: v3 cost {s['v3_cost_bps']:.2f}bps below mandatory "
            f"{MANDATORY_LOWER_BOUND_BPS}bps (fee mis-config)")
    # Primary test: no per-symbol sign inversion.
    by_symbol: dict[str, list] = {}
    for s in scored:
        by_symbol.setdefault(s["symbol"], []).append(s)
    for symbol, ss in by_symbol.items():
        gross = sum(s["pnl_usd"] for s in ss)
        if abs(gross) <= NOISE_BAND_USD:
            continue
        net = sum(s["pnl_usd"] - s["v3_cost_usd"] for s in ss)
        assert (gross > 0) == (net > 0), (
            f"{symbol}: v3 cost causes sign inversion (gross {gross:.2f} -> net {net:.2f})")
```

(A separate `main()` that opens `signals.db` with `sqlite3.connect("file:...?mode=ro", uri=True)`, queries closed shorts in the 2026-05-21→06-01 window, and calls these functions is added in Task 10 — it is the server-only entrypoint, not unit-tested.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_falsify_cost_bound.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Confirm NN#3 isolation**

Run: `python -m pytest tests/test_holdout_isolation.py -q`
Expected: PASS — the harness imports only `backtest_costs`, no holdout access. If `test_holdout_isolation.py` flags the new file, the import is illegitimate — re-check (it should not need an allow-list entry).

- [ ] **Step 6: Commit**

```bash
git add tools/ks_stress_replay/falsify_cost_bound.py tests/test_falsify_cost_bound.py
git commit -m "feat(cost-v3): falsification harness core (no-sign-inversion + fee tripwire)"
```

---

### Task 10: Server-DB entrypoint + scope-caveat output (read-only `main`)

**Files:**
- Modify: `tools/ks_stress_replay/falsify_cost_bound.py` (add `main`)

- [ ] **Step 1: Add the read-only entrypoint**

```python
def _load_closed_shorts_from_db(db_path: str):
    """Read-only load of closed SHORT positions in the post-cutoff window.
    NN#3: window starts 2026-05-21 (>> holdout cutoff 2025-04-29); reads only
    signals.db (mode=ro) — no OHLCV before the window, no holdout access."""
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT symbol, direction, pnl_usd, pnl_pct, size_usd "
            "FROM positions WHERE status='closed' AND direction='SHORT' "
            "AND exit_ts >= '2026-05-21' ORDER BY exit_ts")
        rows = []
        for r in cur.fetchall():
            rows.append({"symbol": r["symbol"], "direction": r["direction"],
                         "pnl_usd": r["pnl_usd"], "pnl_pct": r["pnl_pct"],
                         "size_usd": r["size_usd"],
                         "liquidity_per_min": float("nan")})  # see Step 2 note
        return rows
    finally:
        con.close()


def main(db_path: str = "signals.db"):
    rows = _load_closed_shorts_from_db(db_path)
    scored = score_positions(rows)
    print(f"scored {len(scored)} closed shorts")
    assert_no_sign_inversion(scored)
    print("SCOPE CAVEAT: SHORT-only, ~$644 notional, NORMAL regime May-2026, "
          "low participation. Does NOT license long cost, crisis regimes, "
          "high-participation fills, any edge claim, or 'validated'.")
    print("PASS: v3 preserves all per-symbol price-winner signs.")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "signals.db")
```

Note for Step 2 (documented, not a code TODO): `liquidity_per_min` is not stored on positions; the server run must supply it from the same 30d rolling proxy the backtest uses (`(close*volume)/60` rolling-720 on 1H bars for each symbol over the window). The exact wiring is settled when the server DB + OHLCV are available; until then `main` runs with NaN liquidity → leg fallback (conservative), which only *raises* modelled cost, so a PASS under NaN-liquidity is a strictly conservative PASS.

- [ ] **Step 2: Manual smoke against synthetic DB is covered by Task 9 tests; no new unit test for `main` (DB-dependent).**

- [ ] **Step 3: Commit**

```bash
git add tools/ks_stress_replay/falsify_cost_bound.py
git commit -m "feat(cost-v3): falsification harness server-DB entrypoint (read-only, NN#3-clean)"
```

---

## PHASE 4 — pinned v3 invariants + scaffold growth

### Task 11: Pin v3 invariants (no-negativity, tail(0)=0, fee tripwire, reduction-vs-v2)

**Files:**
- Test: `tests/test_backtest_costs_v3.py`

- [ ] **Step 1: Write the tests**

```python
class TestV3Invariants:
    def _tiers(self):
        from backtest_costs import load_calibration
        return load_calibration()

    def test_every_component_non_negative(self):
        from backtest_costs import compute_trade_costs
        cal = self._tiers()
        for t in ("major", "mid", "small"):
            c = compute_trade_costs(
                entry_notional_usd=644.0, exit_notional_usd=644.0,
                entry_liquidity_usd_per_min=500_000.0,
                exit_liquidity_usd_per_min=500_000.0,
                tier_params=cal.tiers[t], model="v3", global_params=cal.global_)
            for k in ("floor_bps", "tail_bps", "fee_bps", "total_cost_bps"):
                assert c[k] >= 0.0

    def test_fee_floor_meets_published_taker(self):
        from backtest_costs import PUBLISHED_TAKER_FEE_BPS
        cal = self._tiers()
        for t in ("major", "mid", "small"):
            assert cal.tiers[t].fee_bps_per_side >= PUBLISHED_TAKER_FEE_BPS

    def test_tail_reduction_vs_v2_at_y15(self):
        # Spec §3: at Y=1.5 the slope-source change is ~1.7-2.0x; with the
        # sqrt(1440)=37.95x base correction, total tail reduction is ~65-75x.
        # Pin the major slope factor: size_factor_v2 / (Y*sigma) = 885.44/(1.5*300).
        cal = self._tiers()
        v2_major_sf = 885.44
        slope_factor = v2_major_sf / (cal.global_.Y_impact_constant
                                      * cal.tiers["major"].sigma_daily_bps)
        assert slope_factor == pytest.approx(1.97, abs=0.02)
        assert slope_factor * 37.95 == pytest.approx(74.7, abs=0.5)
```

- [ ] **Step 2: Run**

Run: `python -m pytest tests/test_backtest_costs_v3.py::TestV3Invariants -v`
Expected: PASS (3 passed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_backtest_costs_v3.py
git commit -m "test(cost-v3): pin invariants (non-neg, fee floor, tail reduction @Y=1.5)"
```

---

### Task 12: Grow the scaffold — cost-model pattern + INDEX

**Files:**
- Create: `.mex/patterns/cost-model-v3.md`
- Modify: `.mex/patterns/INDEX.md`

- [ ] **Step 1: Write the pattern**

Create `.mex/patterns/cost-model-v3.md` (Purpose / When / Steps / Gotchas / Verify) describing: the two-body bound; `active_model` drives prod (not literals); the v2 sibling is frozen for parity; NaN-poison TierParams; `stress_mult` mandatory for stress-replay; falsification = no-sign-inversion + external fee tripwire (server DB, NN#3-clean); v3 is a BOUND not an estimator (the empirical estimator is a separate future epic).

- [ ] **Step 2: Add the INDEX row**

In `.mex/patterns/INDEX.md`, add:

```markdown
| Touch the backtest cost model / calibration (v3 two-body bound) | [cost-model-v3.md](cost-model-v3.md) |
```

- [ ] **Step 3: Commit**

```bash
git add .mex/patterns/cost-model-v3.md .mex/patterns/INDEX.md
git commit -m "docs(mex): cost-model-v3 pattern + INDEX row"
```

- [ ] **Step 4: mex log the work**

Run: `mex log "cost-model v3 (two-body upper bound) implemented on feat/cost-model-v3; default flips to v3 via active_model; v2 frozen in sibling; falsification harness pending server-DB run (merge precondition)"`

---

## MERGE PRECONDITION (NOT a task — operator gate, spec §9)

Before opening/merging the PR: run `python -m tools.ks_stress_replay.falsify_cost_bound <server-signals.db>` against the **server** DB with real liquidity wired (Task 10 Step 1 note), confirm `n_closed_shorts >= 20` and PASS, and `mex log` the result. A green CI is necessary but NOT sufficient — the harness cannot run in CI (local `signals.db` has 0 rows). Samuel provides server-DB access or runs it and supplies the output.

---

## Self-Review

**1. Spec coverage** (spec section → task):
- §1 two-body form → Task 4. §2 FLOOR → Tasks 4, 7 (JSON), 11. §3 TAIL + Y=1.5 → Tasks 2, 4, 7, 11. §4 combined formula + cap + fallback → Task 4. §5 JSON + version-aware loader + sibling + dual TierParams + NaN poison → Tasks 3, 6, 7. §6 falsification (sign-inversion + external fee tripwire + n≥20 abort) → Tasks 9, 10. §7 decisions (Y=1.5, default→v3, stress_mult, cap 1000, σ static) → Tasks 1, 4, 7. §8 migration (Literal, RA :737, LRC funding/threading, callsites :1169/:1474, recompute pin, marker tests, architecture.md, backtests-to-rerun) → Tasks 5, 7, 8. §9 merge precondition → Merge Precondition section. §10 scope-out → noted in Task 12 pattern. §11 residual risks → carried in spec.
- **stress_mult mandatory in stress-replay** (spec §7): the mechanism lives in the KS stress-replay harness, which is OUT of this plan's scope (it consumes v3; the spec defers the exact coupling to the KS harness spec). Flagged here so it is not silently dropped: a follow-up task on the KS harness must require an explicit `stress_mult > 1` or refuse to run.
- **σ rolling fast-follow** (spec §3): explicitly deferred; static 300/500/800 shipped in Task 7. Not a gap.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". The Task 10 `liquidity_per_min` note is a documented server-run wiring detail (NaN → conservative fallback), not a code placeholder — the code runs as written.

**3. Type consistency:** `compute_trade_costs(model=..., global_params=...)`, `GlobalParams.Y_impact_constant`, `TierParams.from_v3_tier(floor=, impact_tail=)` / `.from_v2_flat(...)`, `Calibration.active_model` / `.global_`, `compute_tail_bps(order_usd=, liquidity_usd_per_min=, sigma_daily_bps=, Y=, v_daily_minutes_per_day=)`, harness `score_positions` / `assert_no_sign_inversion` / `MANDATORY_LOWER_BOUND_BPS` / `EXPECTED_MIN` / `InsufficientDataError` — names consistent across all tasks.
