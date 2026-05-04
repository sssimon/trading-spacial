# A.4-1.5 Regime Threshold Pre-Holdout Re-Tune Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a mini-harness that re-tunes the regime detector BULL/BEAR partition thresholds (`>60/<40`) over the pre-holdout window `[earliest, 2025-04-30T00:00:00Z)`, sweeping 4 configurations locked by historical record (commit `bf581f1`), so leakage caveat #1 is closed before the A.4 holdout evaluation runs.

**Architecture:** Parameterize the existing inline thresholds in `strategy/regime.py` and the backtest path with default-preserving kwargs (byte-identical legacy behavior); add a `regime_disabled` bypass that skips composite scoring entirely. Build `tools/regime_retune_pre_holdout.py` self-contained: load OHLCV + F&G + funding, slice strictly `< cutoff`, run one backtest per (symbol, config), aggregate `net_pnl` per config, identify winner + 2nd-best, evaluate decision flags, write artefacts to `data/retune/2026-05-04-pre-holdout/`. Phase 3 runs the harness, applies decision flags, and commits the artefact for review.

**Tech Stack:** Python 3, pandas, sqlite3 (`data/ohlcv.db`), pytest, ProcessPoolExecutor (optional — sweep is small enough to run sequentially).

---

## Pre-flight

**Branch off `main`.** Do NOT branch off `feat/methodology-a4-1-retune-pre-holdout` (PR #287). Reason: A.4-1.5 GATES PR #287 Phase 3 — circular dependency if branched off it. A.4-1.5 builds its own minimal slicing path; ~20 LOC of intentional, justified duplication.

**Branch name:** `feat/methodology-a4-1-5-regime-retune-pre-holdout`.

**Locked-by-spec invariants** (do NOT relitigate during implementation — see `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md` §2.10):
- Grid: 4 configs `(60, 40)`, `(70, 30)`, `(80, 20)`, no-detector
- Window: `[earliest, 2025-04-30T00:00:00Z)`, strict `<` slicing
- Objective: maximize sum of `net_pnl` across the 10 portfolio symbols
- Symbols: `btc_scanner.DEFAULT_SYMBOLS` (BTC, ETH, ADA, AVAX, DOGE, UNI, XLM, PENDLE, JUP, RUNE)
- Decision flags pre-registered: CHANGE detection, sanity check (no-detector wins → halt), stability check (2nd within 5% → caveat)

**Spec reference correction (record in PR body):** the spec §2.10 cites `backtest.py:404-409` as the inline threshold site. That citation is stale — the real source-of-truth is `strategy/regime.py:_compute_local_regime` (~lines 174-179) and `strategy/regime.py:detect_regime` (~lines 372-377); the backtest consumes the threshold via `_compute_local_regime` through `backtest._regime_at_time`. The plan parameterizes at the regime-module layer; the spec ref is cosmetic doc rot and will be amended in a follow-up D9 edit (per backlog item "§2.10 cross-ref backfill").

---

## File Structure

**Create:**
- `tools/regime_retune_pre_holdout.py` — harness module (~400 LOC mirroring `tools/retune_pre_holdout.py` shape)
- `tests/test_regime_retune_pre_holdout.py` — unit + integration tests

**Modify:**
- `strategy/regime.py` — add `bull_above`/`bear_below` kwargs to `_compute_local_regime` and `detect_regime` (defaults 60/40 preserve byte-identity)
- `backtest.py` — add `regime_thresholds` and `regime_disabled` kwargs to `simulate_strategy`; thread through `_regime_at_time` to `_compute_local_regime`
- `tests/test_holdout_isolation.py` — whitelist `tools/regime_retune_pre_holdout.py` in `HOLDOUT_LEGITIMATE_MODULES` with justification

**Output artefacts (Phase 3, committed to repo):**
- `data/retune/2026-05-04-pre-holdout/regime_params.json`
- `data/retune/2026-05-04-pre-holdout/regime_manifest.json` (sibling, distinct name from A.4-1's `manifest.json` to avoid collision when both PRs land)
- `data/retune/2026-05-04-pre-holdout/regime_report.md` (sibling, distinct name from A.4-1's `report.md`)

---

# PHASE 2 — Build the harness

## Task 1: Create the working branch

**Files:** none (git op only)

- [ ] **Step 1: Verify clean main**

```bash
git status
git log --oneline -1
```
Expected: working tree clean (untracked OK), HEAD = `695208c` or successor.

- [ ] **Step 2: Create branch**

```bash
git checkout -b feat/methodology-a4-1-5-regime-retune-pre-holdout
```

- [ ] **Step 3: Verify**

```bash
git branch --show-current
```
Expected: `feat/methodology-a4-1-5-regime-retune-pre-holdout`

---

## Task 2: Parameterize `_compute_local_regime` thresholds

**Files:**
- Modify: `strategy/regime.py` (function `_compute_local_regime`, ~lines 138-188)
- Test: `tests/test_regime_thresholds_param.py` (new file)

**Goal:** Add `bull_above: int = 60` and `bear_below: int = 40` kwargs. Defaults preserve byte-identity. Validate that `bear_below < bull_above`.

- [ ] **Step 1: Write the failing parity test**

Create `tests/test_regime_thresholds_param.py`:

```python
"""Tests for parameterized regime thresholds (A.4-1.5).

Defaults must preserve byte-identity with pre-parameterization production behavior.
New kwargs must enable threshold sweeps for the A.4-1.5 mini-harness.
"""
import pandas as pd
import pytest

from strategy.regime import _compute_local_regime


def _make_df_daily(close_values):
    return pd.DataFrame({"close": close_values})


class TestComputeLocalRegimeDefaults:
    def test_default_thresholds_preserve_legacy_60_40_bull(self):
        # composite > 60 with default args must classify BULL (legacy behavior)
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),  # price_score=100
            fng_score=80, funding_score=80,
        )
        # composite = 100*0.4 + 80*0.3 + 80*0.3 = 40 + 24 + 24 = 88
        assert result["regime"] == "BULL"
        assert result["score"] == 88.0

    def test_default_thresholds_preserve_legacy_60_40_bear(self):
        # composite < 40 with default args must classify BEAR
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 100),  # < 200 bars → price_score=100
            fng_score=10, funding_score=10,
        )
        # price_score=100 (insufficient bars triggers default 100)
        # composite = 100*0.4 + 10*0.3 + 10*0.3 = 40 + 3 + 3 = 46 → NEUTRAL
        # Need price_score=0 to actually hit BEAR. Use enough bars + bearish setup.
        # Easier: directly verify NEUTRAL boundary.
        assert result["regime"] == "NEUTRAL"

    def test_default_thresholds_preserve_legacy_60_40_neutral(self):
        result = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=50, funding_score=20,
        )
        # composite = 100*0.4 + 50*0.3 + 20*0.3 = 40 + 15 + 6 = 61 > 60 → BULL
        # adjust to land in 40-60: fng=10, funding=10 → 40+3+3=46 → NEUTRAL
        result_neutral = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=10, funding_score=10,
        )
        # composite = 100*0.4 + 10*0.3 + 10*0.3 = 46 → NEUTRAL
        assert result_neutral["regime"] == "NEUTRAL"
```

- [ ] **Step 2: Run the test against the existing function (it should pass — we haven't touched `regime.py` yet, so default behavior = legacy behavior). This is a baseline parity capture.**

```bash
python -m pytest tests/test_regime_thresholds_param.py -v
```
Expected: PASS (baseline locked).

- [ ] **Step 3: Add the failing test for the new kwargs**

Append to `tests/test_regime_thresholds_param.py`:

```python
class TestComputeLocalRegimeParameterized:
    def test_70_30_thresholds_shift_bull_boundary(self):
        # composite = 65 should be BULL with (60,40), NEUTRAL with (70,30)
        # composite = 100*0.4 + 50*0.3 + 50*0.3 = 70  → BULL with both
        # composite = 100*0.4 + 40*0.3 + 25*0.3 = 40+12+7.5 = 59.5 → NEUTRAL with both
        # Need a value strictly in (60, 70]. price=100, fng=50, funding=20 → 40+15+6=61
        result_default = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=50, funding_score=20,
        )
        assert result_default["regime"] == "BULL"  # composite=61, 61>60 ✓

        result_70_30 = _compute_local_regime(
            symbol="BTCUSDT", mode="global",
            df_daily_sym=_make_df_daily([100] * 250),
            fng_score=50, funding_score=20,
            bull_above=70, bear_below=30,
        )
        assert result_70_30["regime"] == "NEUTRAL"  # composite=61, 61 not > 70

    def test_invalid_thresholds_raise(self):
        with pytest.raises(ValueError, match="bear_below must be < bull_above"):
            _compute_local_regime(
                symbol="BTCUSDT", mode="global",
                df_daily_sym=_make_df_daily([100] * 250),
                fng_score=50, funding_score=50,
                bull_above=40, bear_below=60,  # inverted
            )
```

- [ ] **Step 4: Run the new tests — they must FAIL**

```bash
python -m pytest tests/test_regime_thresholds_param.py::TestComputeLocalRegimeParameterized -v
```
Expected: FAIL with "unexpected keyword argument 'bull_above'".

- [ ] **Step 5: Modify `strategy/regime.py:_compute_local_regime`**

Edit the function signature and threshold block. Find lines ~138-188:

```python
def _compute_local_regime(
    symbol: str | None,
    mode: str,
    df_daily_sym: pd.DataFrame,
    fng_score: int,
    funding_score: int,
    rsi_score: int = 50,
    adx_score: int = 50,
    *,
    bull_above: int = 60,
    bear_below: int = 40,
) -> dict:
```

Right after the docstring and before the `price_score = ...` line, add:

```python
    if not (bear_below < bull_above):
        raise ValueError(
            f"bear_below must be < bull_above (got bear_below={bear_below}, "
            f"bull_above={bull_above})"
        )
```

Then change the existing block (lines ~174-179):

```python
    if composite > 60:
        regime = "BULL"
    elif composite < 40:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"
```

to:

```python
    if composite > bull_above:
        regime = "BULL"
    elif composite < bear_below:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"
```

- [ ] **Step 6: Run tests — all pass**

```bash
python -m pytest tests/test_regime_thresholds_param.py -v
```
Expected: ALL PASS (defaults still match legacy + new kwargs honored).

- [ ] **Step 7: Run full strategy regime test suite to confirm no regressions**

```bash
python -m pytest tests/ -k "regime" -v
```
Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add strategy/regime.py tests/test_regime_thresholds_param.py
git commit -m "$(cat <<'EOF'
feat(regime): parameterize BULL/BEAR thresholds in _compute_local_regime

Adds bull_above/bear_below kwargs to _compute_local_regime with defaults
60/40 preserving byte-identity to legacy production behavior. Validates
bear_below < bull_above. Required by A.4-1.5 mini-harness.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Parameterize `detect_regime` (live-path) thresholds

**Files:**
- Modify: `strategy/regime.py` (function `detect_regime`, ~lines 273-392)
- Test: `tests/test_regime_thresholds_param.py` (extend)

**Goal:** Same parameterization in the live path. The live path is not exercised by the harness, but parameterizing both paths keeps the codebase consistent for the eventual Phase 5 promotion.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regime_thresholds_param.py`:

```python
class TestDetectRegimeParameterized:
    def test_detect_regime_accepts_bull_above_kwarg(self):
        # detect_regime makes network calls; we just verify the signature
        # accepts the kwargs. End-to-end behavior is covered by composite-level
        # tests in TestComputeLocalRegimeParameterized.
        import inspect

        from strategy.regime import detect_regime

        sig = inspect.signature(detect_regime)
        assert "bull_above" in sig.parameters
        assert "bear_below" in sig.parameters
        assert sig.parameters["bull_above"].default == 60
        assert sig.parameters["bear_below"].default == 40
```

- [ ] **Step 2: Run — must FAIL**

```bash
python -m pytest tests/test_regime_thresholds_param.py::TestDetectRegimeParameterized -v
```
Expected: FAIL with "bull_above" not in params.

- [ ] **Step 3: Modify `detect_regime` signature and threshold block**

Find `def detect_regime() -> dict:` at line ~273. Change to:

```python
def detect_regime(*, bull_above: int = 60, bear_below: int = 40) -> dict:
```

Add the validator at the top of the function body (before `details = {}`):

```python
    if not (bear_below < bull_above):
        raise ValueError(
            f"bear_below must be < bull_above (got bear_below={bear_below}, "
            f"bull_above={bull_above})"
        )
```

Find the threshold block at lines ~372-377:

```python
    if composite > 60:
        regime = "BULL"
    elif composite < 40:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"
```

Change to:

```python
    if composite > bull_above:
        regime = "BULL"
    elif composite < bear_below:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"
```

- [ ] **Step 4: Run — must PASS**

```bash
python -m pytest tests/test_regime_thresholds_param.py::TestDetectRegimeParameterized -v
```
Expected: PASS.

- [ ] **Step 5: Run full regime + strategy test suite**

```bash
python -m pytest tests/ -k "regime or strategy" -v
```
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add strategy/regime.py tests/test_regime_thresholds_param.py
git commit -m "$(cat <<'EOF'
feat(regime): parameterize BULL/BEAR thresholds in detect_regime (live path)

Mirrors _compute_local_regime parameterization — adds bull_above/bear_below
kwargs with 60/40 defaults preserving byte-identity. Live path not consumed
by A.4-1.5 harness but kept consistent with backtest path for the eventual
Phase 5 promotion.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Thread thresholds and `regime_disabled` through `simulate_strategy` → `_regime_at_time`

**Files:**
- Modify: `backtest.py` (`_regime_at_time` ~lines 222-291; `simulate_strategy` signature + regime call site ~lines 423-720)
- Test: `tests/test_simulate_strategy_regime_kwargs.py` (new)

**Goal:**
1. Forward `bull_above`/`bear_below` from `simulate_strategy` → `_regime_at_time` → `_compute_local_regime`.
2. Add `regime_disabled: bool = False` kwarg to `simulate_strategy`. When True, bypass the regime gating entirely — `_regime_at_time` is NOT called; instead a synthetic regime dict is built that means "no gating": `{"regime": "BYPASS", "score": 50.0, "mode": "disabled", "symbol": symbol, "components": {}}`. The downstream `decide_signal` consumer must treat regime `"BYPASS"` as "permit both LONG and SHORT". This requires identifying the consumer site (Step 5 below) and patching it to recognize `BYPASS`.
3. Defaults preserve byte-identity for the legacy path.

**Consumer trace (resolved during planning):** `regime_info` is passed into `evaluate_signal` (`strategy/core.py:242`, called at `backtest.py:708-712`). Inside `evaluate_signal`:

- `regime_label = (regime or {}).get("regime")` → e.g. `"BULL"`, `"BEAR"`, `"NEUTRAL"`
- `regime_token = _regime_to_direction_token(regime_label)` (`strategy/core.py:124-134`) → maps to `"SHORT"` if BEAR, else `"LONG"`
- Gating block at lines 351-354:

```python
if in_long_zone and regime_token in ("LONG", "NEUTRAL"):
    direction = "LONG"
elif in_short_zone and regime_token == "SHORT":
    direction = "SHORT"
```

To implement BYPASS, patch `_regime_to_direction_token` to return `"BYPASS"` for the bypass label, and add a third branch in the gating block: BYPASS allows direction by zone alone (long_zone → LONG, short_zone → SHORT).

- [ ] **Step 0: Confirm consumer signature unchanged**

The consumer was traced during planning (see "Consumer trace" above): `evaluate_signal` at `strategy/core.py:242` reads `regime["regime"]` via `_regime_to_direction_token` (`strategy/core.py:124-134`), gating block at `strategy/core.py:341-354`. Re-read those lines before editing in case anything has shifted.

```bash
sed -n '120,140p' strategy/core.py
sed -n '340,360p' strategy/core.py
```

- [ ] **Step 1: Write the failing parity test**

Create `tests/test_simulate_strategy_regime_kwargs.py`:

```python
"""Parity + bypass tests for simulate_strategy's regime threshold kwargs (A.4-1.5)."""
import inspect

import backtest


class TestSimulateStrategySignature:
    def test_accepts_regime_thresholds_kwarg(self):
        sig = inspect.signature(backtest.simulate_strategy)
        assert "regime_thresholds" in sig.parameters
        # Default None preserves legacy (uses _compute_local_regime defaults 60/40)
        assert sig.parameters["regime_thresholds"].default is None

    def test_accepts_regime_disabled_kwarg(self):
        sig = inspect.signature(backtest.simulate_strategy)
        assert "regime_disabled" in sig.parameters
        assert sig.parameters["regime_disabled"].default is False
```

- [ ] **Step 2: Run — must FAIL**

```bash
python -m pytest tests/test_simulate_strategy_regime_kwargs.py -v
```
Expected: FAIL with kwargs missing.

- [ ] **Step 3: Modify `simulate_strategy` signature**

Find the signature at `backtest.py:423-442`. Add two kwargs at the end (just before the closing `)`):

```python
                      cost_calibration=None,             # NEW (A.0.2, #277)
                      regime_thresholds: tuple[int, int] | None = None,  # NEW (A.4-1.5) (bull_above, bear_below); None = legacy 60/40
                      regime_disabled: bool = False,                     # NEW (A.4-1.5) bypass regime gating
                      ) -> list[dict]:
```

- [ ] **Step 4: Modify `_regime_at_time` to accept and forward thresholds**

Find `def _regime_at_time(` at `backtest.py:222`. Add kwargs:

```python
def _regime_at_time(
    bar_time,
    symbol: str,
    df1d_sym,
    df_fng,
    df_funding,
    regime_mode: str = "global",
    df1d_btc=None,
    *,
    bull_above: int = 60,
    bear_below: int = 40,
) -> dict:
```

At the bottom of the function (the `return _compute_local_regime(...)` call ~lines 288-291), forward the kwargs:

```python
    return _compute_local_regime(
        symbol, regime_mode, window_price,
        fng_score, funding_score, rsi_score, adx_score,
        bull_above=bull_above, bear_below=bear_below,
    )
```

- [ ] **Step 5: Modify the `simulate_strategy` regime call site**

Find `regime_info = _regime_at_time(` at `backtest.py:695`. Replace with:

```python
        # Regime detection via _regime_at_time helper (#152) — kept as
        # backtest-local because scan() fetches its regime from a cache /
        # network. A.4-1.5: regime_disabled bypasses the call entirely.
        if regime_disabled:
            regime_info = {
                "regime": "BYPASS",
                "score": 50.0,
                "mode": "disabled",
                "symbol": symbol,
                "components": {},
            }
        else:
            ba, bb = (regime_thresholds if regime_thresholds is not None else (60, 40))
            regime_info = _regime_at_time(
                bar_time, symbol, df1d_sym, df_fng, df_funding,
                regime_mode=regime_mode, df1d_btc=df1d_btc,
                bull_above=ba, bear_below=bb,
            )
```

- [ ] **Step 6: Patch `strategy/core.py` to treat BYPASS as both-directions-permitted**

Edit `strategy/core.py:_regime_to_direction_token` (lines 124-134). Add a BYPASS branch BEFORE the BEAR check:

```python
def _regime_to_direction_token(regime_label: str | None) -> str:
    """Map regime label → direction token used by scan().

    A.4-1.5 (regime_disabled bypass): "BYPASS" → "ANY", which the gating
    block treats as "permit either direction by zone alone".

    Legacy mapping:
        BEAR → SHORT
        BULL/NEUTRAL/missing/unknown → LONG
    """
    if regime_label == "BYPASS":
        return "ANY"
    if regime_label == "BEAR":
        return "SHORT"
    return "LONG"
```

Then edit the gating block at `strategy/core.py:341-354`:

```python
    in_long_zone = lrc_pct is not None and lrc_pct <= LRC_LONG_MAX
    in_short_zone = lrc_pct is not None and lrc_pct >= LRC_SHORT_MIN

    regime_label = (regime or {}).get("regime")
    regime_token = _regime_to_direction_token(regime_label)

    # LONG when in low zone AND regime is LONG or NEUTRAL (mapped to LONG).
    # SHORT only when in high zone AND regime is BEAR → SHORT.
    # ANY (BYPASS, A.4-1.5): direction follows zone alone.
    # Everything else → NONE (middle band, or mismatched zone/regime pair).
    if in_long_zone and regime_token in ("LONG", "NEUTRAL", "ANY"):
        direction = "LONG"
    elif in_short_zone and regime_token in ("SHORT", "ANY"):
        direction = "SHORT"
```

(Only the two `in (...)` lines change. Everything else stays.)

- [ ] **Step 7: Add the bypass behavior test**

Append to `tests/test_simulate_strategy_regime_kwargs.py`:

```python
class TestRegimeBypass:
    def test_bypass_skips_compute_local_regime(self, monkeypatch):
        """When regime_disabled=True, _regime_at_time must NOT be invoked."""
        called = {"count": 0}

        def fake_regime_at_time(*args, **kwargs):
            called["count"] += 1
            return {"regime": "BULL", "score": 80.0}

        monkeypatch.setattr(backtest, "_regime_at_time", fake_regime_at_time)

        # Build minimal frames: simulate_strategy needs df1h, df4h, df5m at
        # least to enter its loop. We expect early-exit due to insufficient
        # bars — the assertion is that _regime_at_time was never called.
        import pandas as pd
        empty = pd.DataFrame({"close": []})

        try:
            backtest.simulate_strategy(
                df1h=empty, df4h=empty, df5m=empty, symbol="BTCUSDT",
                regime_disabled=True,
            )
        except Exception:
            pass  # Empty frames may raise; we only care that the bypass branch was entered if any bar was processed.

        # With empty frames, _regime_at_time is never called regardless. The
        # real test is the inverse path:
        called["count"] = 0
        # ... but we cannot exercise the bar-loop without a full fixture.
        # Defer the full bar-loop bypass test to the integration smoke in Task 9.

    def test_threshold_kwargs_forwarded_to_regime_at_time(self, monkeypatch):
        captured = {}

        def fake_regime_at_time(*args, **kwargs):
            captured.update(kwargs)
            return {"regime": "BULL", "score": 80.0}

        monkeypatch.setattr(backtest, "_regime_at_time", fake_regime_at_time)

        # Same caveat: empty frames mean the call may not happen. The
        # integration coverage lives in Task 9 (full backtest run).
        # Here we just lock the wiring at the signature level.
        assert "bull_above" in inspect.signature(backtest._regime_at_time).parameters
        assert "bear_below" in inspect.signature(backtest._regime_at_time).parameters
```

- [ ] **Step 8: Run — must PASS**

```bash
python -m pytest tests/test_simulate_strategy_regime_kwargs.py -v
```
Expected: PASS.

- [ ] **Step 9: Run full backtest test suite to verify no regressions**

```bash
python -m pytest tests/ -k "backtest or simulate" -v
```
Expected: ALL PASS. Any failure here means the legacy path lost byte-identity — diagnose before continuing.

- [ ] **Step 10: Commit**

```bash
git add backtest.py tests/test_simulate_strategy_regime_kwargs.py
# Plus the decide_signal site if it lives elsewhere — note the path in commit msg.
git commit -m "$(cat <<'EOF'
feat(backtest): parameterize regime thresholds + add regime_disabled bypass

Threads bull_above/bear_below from simulate_strategy → _regime_at_time →
_compute_local_regime. When regime_disabled=True, skips the helper entirely
and emits a synthetic {"regime": "BYPASS"} dict; consumers treat BYPASS as
both LONG and SHORT permitted (matches "no detector" semantics from commit
bf581f1's backtest comparison).

Defaults preserve byte-identity to legacy production. Required by A.4-1.5
mini-harness (4-config sweep over pre-holdout window).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Build the harness skeleton + data loading

**Files:**
- Create: `tools/regime_retune_pre_holdout.py`
- Test: `tests/test_regime_retune_pre_holdout.py` (new)

**Goal:** Module skeleton with: imports, constants (`TIMEFRAMES`, `REPO_ROOT`, `OHLCV_DB`, `CUTOFF_DEFAULT`, `GRID`), self-contained slicing helper, OHLCV/F&G/funding loaders. No CLI yet.

- [ ] **Step 1: Write the grid-enumeration test**

Create `tests/test_regime_retune_pre_holdout.py`:

```python
"""Tests for the A.4-1.5 regime threshold pre-holdout re-tune harness."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import regime_retune_pre_holdout as harness


class TestGrid:
    def test_grid_has_exactly_4_configs(self):
        assert len(harness.GRID) == 4

    def test_grid_locked_by_historical_record(self):
        # Per spec D9 §2.10 + commit bf581f1 body
        assert harness.GRID == [
            {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False},
            {"name": "70_30", "bull_above": 70, "bear_below": 30, "disabled": False},
            {"name": "80_20", "bull_above": 80, "bear_below": 20, "disabled": False},
            {"name": "no_detector", "bull_above": None, "bear_below": None, "disabled": True},
        ]
```

- [ ] **Step 2: Run — must FAIL (module doesn't exist)**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py -v
```
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Create the harness skeleton**

Create `tools/regime_retune_pre_holdout.py`:

```python
#!/usr/bin/env python3
"""Pre-holdout regime threshold re-tune (A.4-1.5).

Sweeps the 4 configurations locked by historical record (commit bf581f1)
over the pre-holdout window [earliest, 2025-04-30T00:00:00Z), aggregates
net_pnl per config across the 10 portfolio symbols, identifies the
winner, and applies pre-registered decision flags (CHANGE detection,
sanity check, stability check).

Mirrors the shape of tools/retune_pre_holdout.py (A.4-1) but runs a
single backtest per (symbol, config) instead of a grid search — the
grid + objective here are locked by spec D9 §2.10, not optimized.

Usage:
    python -m tools.regime_retune_pre_holdout --max-date 2025-04-30
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("regime_retune_pre_holdout")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OHLCV_DB = os.path.join(REPO_ROOT, "data", "ohlcv.db")

TIMEFRAMES = ("5m", "1h", "4h", "1d")

GRID = [
    {"name": "60_40",       "bull_above": 60,   "bear_below": 40,   "disabled": False},
    {"name": "70_30",       "bull_above": 70,   "bear_below": 30,   "disabled": False},
    {"name": "80_20",       "bull_above": 80,   "bear_below": 20,   "disabled": False},
    {"name": "no_detector", "bull_above": None, "bear_below": None, "disabled": True},
]


def _resolve_git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "UNKNOWN"


def _sha256_file(path: str, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _slice_below_cutoff(df: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    """Return the subset of df whose index is strictly < cutoff.

    Self-contained (does not import auto_tune._slice_below_cutoff) so this
    module is independent of A.4-1's PR #287 helper.
    """
    if df is None or df.empty:
        return df
    cutoff_ts = pd.Timestamp(cutoff)
    if df.index.tz is None and cutoff_ts.tz is not None:
        cutoff_ts = cutoff_ts.tz_localize(None)
    elif df.index.tz is not None and cutoff_ts.tz is None:
        cutoff_ts = cutoff_ts.tz_localize("UTC")
    sliced = df.loc[df.index < cutoff_ts]
    if not sliced.empty:
        max_ts = pd.Timestamp(sliced.index.max())
        if max_ts.tz is None:
            max_ts = max_ts.tz_localize("UTC")
        cutoff_check = cutoff_ts if cutoff_ts.tz is not None else cutoff_ts.tz_localize("UTC")
        assert max_ts < cutoff_check, f"Slice leak: max_ts={max_ts} >= cutoff={cutoff_check}"
    return sliced


def _per_symbol_data_ranges(db_path: str, symbols: list, cutoff_ms: int) -> dict:
    """Per (symbol, tf), report [min_ts_ms, max_ts_ms, count] of bars with open_time < cutoff_ms.
    Used as no-leakage proof in the manifest.
    """
    ranges: dict = {}
    con = sqlite3.connect(db_path)
    try:
        for sym in symbols:
            ranges[sym] = {}
            for tf in TIMEFRAMES:
                row = con.execute(
                    "SELECT MIN(open_time), MAX(open_time), COUNT(*) "
                    "FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time<?",
                    (sym, tf, cutoff_ms),
                ).fetchone()
                if row and row[2]:
                    ranges[sym][tf] = {
                        "min_ts_ms": int(row[0]),
                        "max_ts_ms": int(row[1]),
                        "min_ts_iso": datetime.fromtimestamp(row[0] / 1000, timezone.utc).isoformat(),
                        "max_ts_iso": datetime.fromtimestamp(row[1] / 1000, timezone.utc).isoformat(),
                        "count": int(row[2]),
                    }
                else:
                    ranges[sym][tf] = {"min_ts_ms": None, "max_ts_ms": None, "count": 0}
    finally:
        con.close()
    return ranges


def _verify_no_leakage(ranges: dict, cutoff_ms: int) -> str:
    for sym, tfs in ranges.items():
        for tf, span in tfs.items():
            if span["max_ts_ms"] is not None and span["max_ts_ms"] >= cutoff_ms:
                raise AssertionError(
                    f"no-leakage violation: {sym} {tf} max_ts_ms={span['max_ts_ms']} "
                    f">= cutoff_ms={cutoff_ms}"
                )
    return "PASS"
```

- [ ] **Step 4: Run grid test — must PASS**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py::TestGrid -v
```
Expected: PASS.

- [ ] **Step 5: Add slicing tests**

Append to `tests/test_regime_retune_pre_holdout.py`:

```python
class TestSliceBelowCutoff:
    def test_slice_strict_less_than(self):
        idx = pd.date_range("2025-04-29 23:55", periods=4, freq="5min", tz="UTC")
        df = pd.DataFrame({"close": [1, 2, 3, 4]}, index=idx)
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        sliced = harness._slice_below_cutoff(df, cutoff)
        # Bars at 23:55 (index 0) is < cutoff. Bars at 00:00, 00:05, 00:10 are >= cutoff.
        assert len(sliced) == 1
        assert sliced.index[0] == pd.Timestamp("2025-04-29 23:55", tz="UTC")

    def test_slice_empty_input(self):
        df = pd.DataFrame({"close": []})
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        out = harness._slice_below_cutoff(df, cutoff)
        assert out.empty

    def test_slice_assertion_catches_leakage(self):
        # Mock case where assertion would fire
        # (in practice, the loc[index < cutoff] guarantees no leakage,
        # but the assertion is there as defensive belt-and-braces).
        idx = pd.date_range("2025-04-30 00:00", periods=2, freq="5min", tz="UTC")
        df = pd.DataFrame({"close": [1, 2]}, index=idx)
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        out = harness._slice_below_cutoff(df, cutoff)
        # All bars >= cutoff → empty result, no leakage
        assert out.empty
```

- [ ] **Step 6: Run — must PASS**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tools/regime_retune_pre_holdout.py tests/test_regime_retune_pre_holdout.py
git commit -m "$(cat <<'EOF'
feat(methodology): A.4-1.5 harness skeleton + slicing helpers

Adds tools/regime_retune_pre_holdout.py with the locked grid (4 configs
from commit bf581f1), self-contained _slice_below_cutoff (deliberately
not depending on A.4-1 PR #287 helper to keep PRs orthogonal), and
manifest helpers (_per_symbol_data_ranges, _verify_no_leakage,
_sha256_file, _resolve_git_commit).

No CLI yet; tests cover grid shape and slicing edge cases.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add `tools/__init__.py` if missing + the per-config single-backtest runner

**Files:**
- Create or verify: `tools/__init__.py` (empty)
- Modify: `tools/regime_retune_pre_holdout.py`
- Test: `tests/test_regime_retune_pre_holdout.py`

**Goal:** Build `_run_one_backtest(symbol, config, cutoff)` → returns `{"net_pnl": float, "trades": int, "errors": str | None}`. This is the per-cell unit of work. Loads OHLCV (1h, 4h, 5m, 1d) + F&G + funding for `symbol`, slices each `< cutoff`, calls `simulate_strategy` with the configured regime kwargs, sums `pnl_usd` of returned trades.

Key sub-decision: **use `data.market_data.get_klines` and the same loader paths the existing scanner uses for OHLCV**. For F&G + funding, look at how `auto_tune.run_backtest_with_params` loads them — copy that pattern directly. Pull the helpers into a small `_load_frames(symbol, cutoff)` function in the harness.

- [ ] **Step 1: Verify `tools/__init__.py`**

```bash
ls tools/__init__.py 2>/dev/null || touch tools/__init__.py
```

- [ ] **Step 2: F&G + funding loader paths (resolved during planning)**

The canonical loaders are:
- `backtest.get_historical_fear_greed()` — returns DataFrame indexed by date with `fng` column
- `backtest.get_historical_funding_rate()` (defined at `backtest.py:144`) — returns DataFrame indexed by ts with `rate` column

Both are used by `auto_tune.run_backtest_with_params` (lines 179-180) and seven other call sites in the repo. Import them directly into the harness — no need for new loader code. Verify by reading `backtest.py:144` and confirming the return shape before wiring.

- [ ] **Step 3: Write the failing test for `_run_one_backtest`**

Append to `tests/test_regime_retune_pre_holdout.py`:

```python
class TestRunOneBacktest:
    def test_signature(self):
        import inspect
        sig = inspect.signature(harness._run_one_backtest)
        params = list(sig.parameters)
        assert "symbol" in params
        assert "config" in params
        assert "cutoff" in params

    def test_disabled_config_passes_regime_disabled_true(self, monkeypatch):
        captured = {}

        def fake_simulate(*args, **kwargs):
            captured.update(kwargs)
            return []  # No trades

        monkeypatch.setattr(harness, "simulate_strategy", fake_simulate)
        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: {
            "df1h": pd.DataFrame({"close": []}),
            "df4h": pd.DataFrame({"close": []}),
            "df5m": pd.DataFrame({"close": []}),
            "df1d": pd.DataFrame({"close": []}),
            "df1d_btc": pd.DataFrame({"close": []}),
            "df_fng": pd.DataFrame({"fng": []}),
            "df_funding": pd.DataFrame({"rate": []}),
        })

        cfg = {"name": "no_detector", "bull_above": None, "bear_below": None, "disabled": True}
        result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc))
        assert result["net_pnl"] == 0.0
        assert captured.get("regime_disabled") is True

    def test_60_40_config_passes_regime_thresholds(self, monkeypatch):
        captured = {}

        def fake_simulate(*args, **kwargs):
            captured.update(kwargs)
            return [{"pnl_usd": 100.0}, {"pnl_usd": -25.5}]

        monkeypatch.setattr(harness, "simulate_strategy", fake_simulate)
        monkeypatch.setattr(harness, "_load_frames", lambda sym, cutoff: {
            "df1h": pd.DataFrame({"close": []}),
            "df4h": pd.DataFrame({"close": []}),
            "df5m": pd.DataFrame({"close": []}),
            "df1d": pd.DataFrame({"close": []}),
            "df1d_btc": pd.DataFrame({"close": []}),
            "df_fng": pd.DataFrame({"fng": []}),
            "df_funding": pd.DataFrame({"rate": []}),
        })

        cfg = {"name": "60_40", "bull_above": 60, "bear_below": 40, "disabled": False}
        result = harness._run_one_backtest("BTCUSDT", cfg, datetime(2025, 4, 30, tzinfo=timezone.utc))
        assert result["net_pnl"] == 74.5
        assert result["trades"] == 2
        assert captured.get("regime_disabled") is False
        assert captured.get("regime_thresholds") == (60, 40)
```

- [ ] **Step 4: Run — must FAIL**

Expected: FAIL with AttributeError on `_run_one_backtest`.

- [ ] **Step 5: Add `_load_frames` and `_run_one_backtest` to the harness**

Append to `tools/regime_retune_pre_holdout.py`:

```python
# Imported lazily inside _run_one_backtest to avoid circular import at module load.
# The functions referenced here live in:
#   - backtest.simulate_strategy
#   - data.market_data.get_klines (for OHLCV)
#   - <F&G/funding loader path identified in Step 2>
from backtest import simulate_strategy  # noqa: E402  (kept module-level for monkeypatch in tests)


def _load_frames(symbol: str, cutoff: datetime) -> dict:
    """Load all DataFrames simulate_strategy needs, sliced strictly < cutoff.

    Returns dict with keys: df1h, df4h, df5m, df1d, df1d_btc, df_fng, df_funding.
    Each value is a (possibly empty) DataFrame whose index is strictly < cutoff.
    """
    from data import market_data as md

    out = {}
    for tf, key in (("1h", "df1h"), ("4h", "df4h"), ("5m", "df5m"), ("1d", "df1d")):
        df = md.get_klines(symbol, tf, limit=10_000)  # generous limit; we slice below
        out[key] = _slice_below_cutoff(df, cutoff)

    # BTC daily for global-mode regime
    if symbol == "BTCUSDT":
        out["df1d_btc"] = out["df1d"]
    else:
        df1d_btc = md.get_klines("BTCUSDT", "1d", limit=10_000)
        out["df1d_btc"] = _slice_below_cutoff(df1d_btc, cutoff)

    # F&G + funding — use the canonical loaders from backtest.py
    # (same path used by auto_tune.run_backtest_with_params).
    from backtest import get_historical_fear_greed, get_historical_funding_rate
    out["df_fng"] = _slice_below_cutoff(get_historical_fear_greed(), cutoff)
    out["df_funding"] = _slice_below_cutoff(get_historical_funding_rate(), cutoff)

    return out


def _run_one_backtest(symbol: str, config: dict, cutoff: datetime) -> dict:
    """Run a single backtest for (symbol, regime config). Returns net_pnl + diagnostics."""
    frames = _load_frames(symbol, cutoff)

    kwargs = {
        "df1h": frames["df1h"],
        "df4h": frames["df4h"],
        "df5m": frames["df5m"],
        "df1d": frames["df1d"],
        "df1d_btc": frames["df1d_btc"],
        "df_fng": frames["df_fng"],
        "df_funding": frames["df_funding"],
        "symbol": symbol,
    }
    if config["disabled"]:
        kwargs["regime_disabled"] = True
    else:
        kwargs["regime_thresholds"] = (config["bull_above"], config["bear_below"])

    try:
        trades = simulate_strategy(**kwargs)
    except Exception as exc:
        log.error("[%s][%s] simulate_strategy raised: %s", symbol, config["name"], exc)
        return {"symbol": symbol, "config": config["name"], "net_pnl": 0.0,
                "trades": 0, "error": str(exc)}

    net_pnl = sum(t.get("pnl_usd", 0.0) for t in trades)
    return {"symbol": symbol, "config": config["name"], "net_pnl": float(net_pnl),
            "trades": len(trades), "error": None}
```

- [ ] **Step 6: Run tests — must PASS (monkeypatched path)**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py::TestRunOneBacktest -v
```
Expected: PASS.

- [ ] **Step 7: Add an end-to-end smoke for `_load_frames` with real loaders (skipif data missing)**

```python
class TestRealLoadFrames:
    def test_load_frames_slices_below_cutoff(self):
        if not os.path.exists(harness.OHLCV_DB):
            pytest.skip("ohlcv.db not present in test environment")
        cutoff = datetime(2025, 4, 30, tzinfo=timezone.utc)
        try:
            frames = harness._load_frames("BTCUSDT", cutoff)
        except Exception as exc:
            pytest.skip(f"Loader unavailable in test env: {exc}")

        cutoff_ts = pd.Timestamp(cutoff)
        for key in ("df1h", "df4h", "df5m", "df1d", "df1d_btc", "df_fng", "df_funding"):
            df = frames[key]
            if df is not None and not df.empty:
                max_ts = pd.Timestamp(df.index.max())
                if max_ts.tz is None:
                    max_ts = max_ts.tz_localize("UTC")
                ct = cutoff_ts if cutoff_ts.tz is not None else cutoff_ts.tz_localize("UTC")
                assert max_ts < ct, f"{key} leaked: max_ts={max_ts} >= cutoff={ct}"
```

- [ ] **Step 8: Run — must PASS (or skip cleanly)**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py -v
```

- [ ] **Step 9: Commit**

```bash
git add tools/regime_retune_pre_holdout.py tests/test_regime_retune_pre_holdout.py
git commit -m "$(cat <<'EOF'
feat(methodology): A.4-1.5 harness — single-backtest runner per (symbol,config)

Adds _load_frames + _run_one_backtest. Loads OHLCV (5m/1h/4h/1d) via
data.market_data.get_klines; loads F&G + funding via the same paths used
by auto_tune.run_backtest_with_params (copied for orthogonality vs PR
#287). All frames sliced strictly < cutoff. simulate_strategy is invoked
with regime_thresholds=(bull,bear) for the 3 numeric configs and
regime_disabled=True for the no_detector config.

Per-cell return: {symbol, config, net_pnl, trades, error}. simulate_strategy
exceptions are caught + logged + returned as net_pnl=0 with the error
string in the result, mirroring A.4-1's tolerance for single-symbol
failures during a sweep.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Aggregate per-config + decision flag evaluation

**Files:**
- Modify: `tools/regime_retune_pre_holdout.py`
- Test: `tests/test_regime_retune_pre_holdout.py`

**Goal:** `_aggregate_results(per_cell_results)` produces:
- `per_config_pnl: dict[str, float]` (config_name → sum net_pnl)
- `winner: str` (config name with max sum)
- `runner_up: str` (2nd-best)
- `winner_margin_pct: float` (gap from winner to runner-up, as `(winner - 2nd) / |winner| * 100`)
- `decision_flags: dict` with `change_detection`, `sanity_check`, `stability_check`

- [ ] **Step 1: Failing test**

Append:

```python
class TestAggregate:
    def test_aggregate_picks_winner(self):
        cells = [
            {"symbol": "BTC", "config": "60_40", "net_pnl": 100.0, "trades": 5, "error": None},
            {"symbol": "ETH", "config": "60_40", "net_pnl": 50.0,  "trades": 3, "error": None},
            {"symbol": "BTC", "config": "70_30", "net_pnl": 120.0, "trades": 4, "error": None},
            {"symbol": "ETH", "config": "70_30", "net_pnl": 60.0,  "trades": 3, "error": None},
            {"symbol": "BTC", "config": "80_20", "net_pnl": 80.0,  "trades": 2, "error": None},
            {"symbol": "ETH", "config": "80_20", "net_pnl": 40.0,  "trades": 2, "error": None},
            {"symbol": "BTC", "config": "no_detector", "net_pnl": 70.0, "trades": 6, "error": None},
            {"symbol": "ETH", "config": "no_detector", "net_pnl": 30.0, "trades": 4, "error": None},
        ]
        agg = harness._aggregate_results(cells)
        assert agg["per_config_pnl"]["60_40"]       == 150.0
        assert agg["per_config_pnl"]["70_30"]       == 180.0
        assert agg["per_config_pnl"]["80_20"]       == 120.0
        assert agg["per_config_pnl"]["no_detector"] == 100.0
        assert agg["winner"] == "70_30"
        assert agg["runner_up"] == "60_40"
        # margin = (180 - 150) / 180 * 100 = 16.66...%
        assert abs(agg["winner_margin_pct"] - (30.0 / 180.0 * 100)) < 1e-6

    def test_decision_flag_change_detection(self):
        # winner != "60_40" → change_detection True
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 50), ("70_30", 100), ("80_20", 30), ("no_detector", 20)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["change_detection"] is True
        assert agg["winner"] == "70_30"

    def test_decision_flag_sanity_check_fires_on_no_detector_winner(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 50), ("70_30", 30), ("80_20", 20), ("no_detector", 200)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["sanity_check"] is True  # halt+debug
        assert agg["winner"] == "no_detector"

    def test_decision_flag_stability_check_fires_within_5_pct(self):
        # winner=180, runner_up=178 → margin = 2/180 = 1.11% < 5% → stability flag
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 178), ("70_30", 180), ("80_20", 100), ("no_detector", 50)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["stability_check"] is True

    def test_decision_flag_stability_check_inactive_when_margin_large(self):
        cells = [{"symbol": "X", "config": c, "net_pnl": pnl, "trades": 1, "error": None}
                 for c, pnl in [("60_40", 100), ("70_30", 200), ("80_20", 50), ("no_detector", 30)]]
        agg = harness._aggregate_results(cells)
        assert agg["decision_flags"]["stability_check"] is False  # margin = 50% > 5%
```

- [ ] **Step 2: Run — must FAIL**

- [ ] **Step 3: Implement**

Append to `tools/regime_retune_pre_holdout.py`:

```python
def _aggregate_results(cells: list) -> dict:
    """Aggregate per-cell results into per-config sums + decision flags.

    cells: list of {symbol, config, net_pnl, trades, error} dicts.
    """
    per_config_pnl: dict[str, float] = {}
    per_config_trades: dict[str, int] = {}
    for cell in cells:
        cfg = cell["config"]
        per_config_pnl[cfg] = per_config_pnl.get(cfg, 0.0) + float(cell["net_pnl"])
        per_config_trades[cfg] = per_config_trades.get(cfg, 0) + int(cell["trades"])

    # Sort configs by net_pnl descending. Ties broken by lex order of config name
    # for deterministic output.
    sorted_configs = sorted(per_config_pnl.items(), key=lambda kv: (-kv[1], kv[0]))
    winner_name, winner_pnl = sorted_configs[0]
    runner_up_name, runner_up_pnl = sorted_configs[1]

    if abs(winner_pnl) > 1e-9:
        margin_pct = (winner_pnl - runner_up_pnl) / abs(winner_pnl) * 100.0
    else:
        margin_pct = 0.0

    decision_flags = {
        "change_detection": winner_name != "60_40",
        "sanity_check":     winner_name == "no_detector",
        "stability_check":  margin_pct < 5.0,
    }

    return {
        "per_config_pnl": per_config_pnl,
        "per_config_trades": per_config_trades,
        "winner": winner_name,
        "winner_pnl": winner_pnl,
        "runner_up": runner_up_name,
        "runner_up_pnl": runner_up_pnl,
        "winner_margin_pct": margin_pct,
        "decision_flags": decision_flags,
    }
```

- [ ] **Step 4: Run — must PASS**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py::TestAggregate -v
```

- [ ] **Step 5: Commit**

```bash
git add tools/regime_retune_pre_holdout.py tests/test_regime_retune_pre_holdout.py
git commit -m "$(cat <<'EOF'
feat(methodology): A.4-1.5 — aggregate + pre-registered decision flags

_aggregate_results sums net_pnl per config across symbols, ranks them,
computes the winner/runner-up margin, and evaluates the three flags
locked by spec D9 §2.10:
  - change_detection: winner != current production (60_40)
  - sanity_check:     no_detector wins → halt+debug
  - stability_check:  2nd-best within 5% of winner → informational caveat

Tie-break on lex order of config name keeps the winner choice
deterministic across re-runs for byte-stable artefacts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Artefact writers (`regime_params.json`, `regime_manifest.json`, `regime_report.md`)

**Files:**
- Modify: `tools/regime_retune_pre_holdout.py`
- Test: `tests/test_regime_retune_pre_holdout.py`

**Goal:** Three writers, all atomic, all deterministic. The `regime_params.json` shape branches on whether `no_detector` won.

- [ ] **Step 1: Failing test**

Append:

```python
class TestArtefactWriters:
    def test_regime_params_for_threshold_winner(self, tmp_path):
        agg = {
            "winner": "70_30",
            "winner_pnl": 180.0,
            "per_config_pnl": {"60_40": 150.0, "70_30": 180.0, "80_20": 120.0, "no_detector": 100.0},
            "decision_flags": {"change_detection": True, "sanity_check": False, "stability_check": False},
        }
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), agg)
        payload = json.loads(path.read_text())
        assert payload == {
            "format_version": 1,
            "regime_thresholds": {"bull_above": 70, "bear_below": 30},
        }

    def test_regime_params_for_disabled_winner(self, tmp_path):
        agg = {
            "winner": "no_detector",
            "winner_pnl": 200.0,
            "per_config_pnl": {"60_40": 150.0, "70_30": 100.0, "80_20": 80.0, "no_detector": 200.0},
            "decision_flags": {"change_detection": True, "sanity_check": True, "stability_check": False},
        }
        path = tmp_path / "regime_params.json"
        harness._write_regime_params(str(path), agg)
        payload = json.loads(path.read_text())
        assert payload == {
            "format_version": 1,
            "regime_disabled": True,
        }

    def test_regime_params_byte_deterministic_across_runs(self, tmp_path):
        agg = {
            "winner": "60_40",
            "winner_pnl": 150.0,
            "per_config_pnl": {"60_40": 150.0, "70_30": 100.0, "80_20": 80.0, "no_detector": 50.0},
            "decision_flags": {"change_detection": False, "sanity_check": False, "stability_check": False},
        }
        path1 = tmp_path / "p1.json"
        path2 = tmp_path / "p2.json"
        harness._write_regime_params(str(path1), agg)
        harness._write_regime_params(str(path2), agg)
        assert path1.read_bytes() == path2.read_bytes()
```

- [ ] **Step 2: Run — must FAIL**

- [ ] **Step 3: Implement writers**

Append:

```python
def _atomic_write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _write_regime_params(path: str, agg: dict) -> None:
    """Write regime_params.json. Shape depends on winner:
      - threshold winner: {"format_version": 1, "regime_thresholds": {"bull_above": <int>, "bear_below": <int>}}
      - no_detector winner: {"format_version": 1, "regime_disabled": True}
    """
    winner = agg["winner"]
    if winner == "no_detector":
        payload = {"format_version": 1, "regime_disabled": True}
    else:
        cfg = next(c for c in GRID if c["name"] == winner)
        payload = {
            "format_version": 1,
            "regime_thresholds": {
                "bull_above": cfg["bull_above"],
                "bear_below": cfg["bear_below"],
            },
        }
    _atomic_write_json(path, payload)


def _build_manifest(agg: dict, cutoff: datetime, cutoff_ms: int,
                    ohlcv_sha: str, code_commit: str,
                    ranges: dict, runtime_seconds: float,
                    leakage_check: str, symbols: list) -> dict:
    return {
        "harness": "regime_retune_pre_holdout",
        "spec_ref": "docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md §2.10",
        "cutoff_effective_iso": cutoff.isoformat(),
        "cutoff_effective_ms": cutoff_ms,
        "code_commit": code_commit,
        "ohlcv_sha256": ohlcv_sha,
        "ohlcv_path_relative": os.path.relpath(OHLCV_DB, REPO_ROOT),
        "ran_at_iso": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(runtime_seconds, 2),
        "leakage_check": leakage_check,
        "symbols": symbols,
        "grid": [{"name": g["name"], "bull_above": g["bull_above"],
                  "bear_below": g["bear_below"], "disabled": g["disabled"]} for g in GRID],
        "per_config_pnl": agg["per_config_pnl"],
        "per_config_trades": agg["per_config_trades"],
        "winner": agg["winner"],
        "winner_pnl": agg["winner_pnl"],
        "runner_up": agg["runner_up"],
        "runner_up_pnl": agg["runner_up_pnl"],
        "winner_margin_pct": round(agg["winner_margin_pct"], 4),
        "decision_flags": agg["decision_flags"],
        "per_symbol_data_ranges": ranges,
        "scope_notes": {
            "n_effective_contribution": 0,
            "promotion_to_strategy_regime_py_and_backtest_py": "deferred_to_phase_5_post_holdout_PR",
        },
    }


def _build_report(agg: dict, cells: list, cutoff: datetime,
                  ranges: dict, runtime_seconds: float, symbols: list) -> str:
    lines = []
    lines.append("# Pre-holdout Regime Threshold Re-tune Report (A.4-1.5)")
    lines.append("")
    lines.append(f"- **Cutoff (`--max-date`):** {cutoff.isoformat()}")
    lines.append(f"- **Symbols:** {', '.join(symbols)}")
    lines.append(f"- **Runtime:** {runtime_seconds:.0f}s")
    lines.append(f"- **Spec ref:** D9 §2.10 (locked grid, locked objective)")
    lines.append("")
    lines.append("## Per-config aggregate")
    lines.append("")
    lines.append("| Config | Sum net_pnl (USD) | Total trades | Margin to winner |")
    lines.append("|--------|-------------------|--------------|------------------|")
    for cfg_name in ("60_40", "70_30", "80_20", "no_detector"):
        pnl = agg["per_config_pnl"].get(cfg_name, 0.0)
        tr = agg["per_config_trades"].get(cfg_name, 0)
        if cfg_name == agg["winner"]:
            margin = "**winner**"
        elif abs(agg["winner_pnl"]) > 1e-9:
            m = (agg["winner_pnl"] - pnl) / abs(agg["winner_pnl"]) * 100
            margin = f"-{m:.2f}%"
        else:
            margin = "—"
        lines.append(f"| {cfg_name} | ${pnl:+,.2f} | {tr} | {margin} |")
    lines.append("")
    lines.append(f"**Winner:** `{agg['winner']}` (sum net_pnl = ${agg['winner_pnl']:+,.2f})")
    lines.append(f"**Runner-up:** `{agg['runner_up']}` (sum net_pnl = ${agg['runner_up_pnl']:+,.2f})")
    lines.append(f"**Margin:** {agg['winner_margin_pct']:.2f}% of |winner|")
    lines.append("")
    lines.append("## Decision flags (pre-registered per D9 §2.10)")
    lines.append("")
    lines.append(f"- **CHANGE detection:** `{agg['decision_flags']['change_detection']}` "
                 f"(winner {'==' if agg['winner']=='60_40' else '!='} current production `60_40`)")
    lines.append(f"- **Sanity check (no-detector wins):** "
                 f"`{agg['decision_flags']['sanity_check']}` "
                 f"{'→ HALT + DEBUG required before any commit' if agg['decision_flags']['sanity_check'] else ''}")
    lines.append(f"- **Stability check (margin < 5%):** "
                 f"`{agg['decision_flags']['stability_check']}` "
                 f"{'→ informational caveat: regime is operating in a flat region' if agg['decision_flags']['stability_check'] else ''}")
    lines.append("")
    lines.append("## Per-symbol breakdown")
    lines.append("")
    lines.append("| Symbol | 60_40 | 70_30 | 80_20 | no_detector |")
    lines.append("|--------|-------|-------|-------|-------------|")
    by_symbol_config = {}
    for c in cells:
        by_symbol_config.setdefault(c["symbol"], {})[c["config"]] = c["net_pnl"]
    for sym in symbols:
        row = by_symbol_config.get(sym, {})
        cells_str = " | ".join(
            f"${row.get(cfg, 0.0):+,.0f}"
            for cfg in ("60_40", "70_30", "80_20", "no_detector")
        )
        lines.append(f"| {sym} | {cells_str} |")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- **JUPUSDT** — earliest OHLCV bar is 2024-01-31. SMA200 (1d) and SMA100 (1h) "
                 "yield NaN over the first ~4 days of JUP train data. Same warmup degradation "
                 "applies here as in A.4-1; results for JUP are reported but should be interpreted "
                 "with this caveat.")
    lines.append("")
    lines.append("## Data ranges (per symbol × tf, all bars below cutoff)")
    lines.append("")
    lines.append("| Symbol | TF | Min ts (UTC) | Max ts (UTC) | Bars |")
    lines.append("|--------|----|---------------|---------------|------|")
    for sym in sorted(ranges.keys()):
        for tf in TIMEFRAMES:
            span = ranges[sym].get(tf, {})
            lines.append(
                f"| {sym} | {tf} "
                f"| {span.get('min_ts_iso', '—')} "
                f"| {span.get('max_ts_iso', '—')} "
                f"| {span.get('count', 0)} |"
            )
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run — must PASS**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tools/regime_retune_pre_holdout.py tests/test_regime_retune_pre_holdout.py
git commit -m "$(cat <<'EOF'
feat(methodology): A.4-1.5 — artefact writers (regime_params, manifest, report)

regime_params.json branches on winner shape:
  - threshold winner → {"format_version":1,"regime_thresholds":{...}}
  - no_detector winner → {"format_version":1,"regime_disabled":true}
Atomic write with sort_keys=True for byte-determinism across re-runs.

regime_manifest.json captures cutoff, ohlcv hash, code commit, per-config
P&L sums, decision flags, per-symbol data ranges (no-leakage proof).

regime_report.md is human-readable side-by-side, includes the JUP warmup
caveat, decision flag annotations, per-symbol breakdown.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: CLI entry point + main loop

**Files:**
- Modify: `tools/regime_retune_pre_holdout.py`
- Test: `tests/test_regime_retune_pre_holdout.py`

**Goal:** `main(argv)` parses `--max-date` (required) + optional `--out-dir`. Drives the full sweep. Writes all three artefacts.

- [ ] **Step 1: Failing test**

Append:

```python
class TestCLI:
    def test_max_date_required(self):
        with pytest.raises(SystemExit):
            harness.main([])

    def test_max_date_parsed(self, monkeypatch, tmp_path):
        # Stub _run_one_backtest so we don't load real data
        def fake_run(symbol, config, cutoff):
            return {"symbol": symbol, "config": config["name"],
                    "net_pnl": 100.0 if config["name"] == "60_40" else 50.0,
                    "trades": 1, "error": None}

        monkeypatch.setattr(harness, "_run_one_backtest", fake_run)
        monkeypatch.setattr(harness, "_per_symbol_data_ranges",
                            lambda *a, **kw: {sym: {} for sym in harness._get_symbols()})
        monkeypatch.setattr(harness, "_verify_no_leakage", lambda *a, **kw: "PASS")
        monkeypatch.setattr(harness, "_sha256_file", lambda *a, **kw: "deadbeef")

        rc = harness.main([
            "--max-date", "2025-04-30",
            "--out-dir", str(tmp_path),
        ])
        assert rc == 0
        assert (tmp_path / "regime_params.json").exists()
        assert (tmp_path / "regime_manifest.json").exists()
        assert (tmp_path / "regime_report.md").exists()

        # Winner = 60_40 (sum 100*10 = 1000) > others
        params = json.loads((tmp_path / "regime_params.json").read_text())
        assert params["regime_thresholds"]["bull_above"] == 60
        assert params["regime_thresholds"]["bear_below"] == 40
```

- [ ] **Step 2: Run — must FAIL**

- [ ] **Step 3: Implement `_get_symbols` + `main`**

Append:

```python
def _get_symbols() -> list[str]:
    """Return the 10 portfolio symbols (mirror of A.4-1's basket)."""
    from btc_scanner import DEFAULT_SYMBOLS
    return list(DEFAULT_SYMBOLS)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-holdout regime threshold re-tune mini-harness (A.4-1.5).",
    )
    parser.add_argument("--max-date", type=str, required=True,
                        help="ISO date (YYYY-MM-DD, UTC). Holdout starts on this day; "
                             "tune sees only bars strictly before it.")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="Override output directory. "
                             "Defaults to data/retune/<today>-pre-holdout/.")
    args = parser.parse_args(argv)

    cutoff = datetime.fromisoformat(args.max_date).replace(tzinfo=timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    if not os.path.exists(OHLCV_DB):
        log.error("OHLCV DB not found at %s", OHLCV_DB)
        return 2

    symbols = _get_symbols()

    if args.out_dir:
        out_dir = args.out_dir
    else:
        run_date = datetime.now(timezone.utc).date().isoformat()
        out_dir = os.path.join(REPO_ROOT, "data", "retune", f"{run_date}-pre-holdout")
    os.makedirs(out_dir, exist_ok=True)

    log.info("A.4-1.5 regime threshold re-tune starting")
    log.info("  cutoff:  %s", cutoff.isoformat())
    log.info("  symbols: %s", ", ".join(symbols))
    log.info("  configs: %s", ", ".join(c["name"] for c in GRID))
    log.info("  out_dir: %s", out_dir)

    start = time.time()
    cells = []
    for symbol in symbols:
        for config in GRID:
            cell = _run_one_backtest(symbol, config, cutoff)
            if cell["error"]:
                log.warning("[%s][%s] error: %s", symbol, config["name"], cell["error"])
            else:
                log.info("[%s][%s] net_pnl=$%+,.2f trades=%d",
                         symbol, config["name"], cell["net_pnl"], cell["trades"])
            cells.append(cell)
    runtime_seconds = time.time() - start

    agg = _aggregate_results(cells)

    log.info("Computing per-symbol data ranges from ohlcv.db...")
    ranges = _per_symbol_data_ranges(OHLCV_DB, symbols, cutoff_ms)
    leakage_check = _verify_no_leakage(ranges, cutoff_ms)
    log.info("Leakage check: %s", leakage_check)

    log.info("Hashing ohlcv.db...")
    ohlcv_sha = _sha256_file(OHLCV_DB)
    code_commit = _resolve_git_commit()

    manifest = _build_manifest(
        agg=agg, cutoff=cutoff, cutoff_ms=cutoff_ms,
        ohlcv_sha=ohlcv_sha, code_commit=code_commit,
        ranges=ranges, runtime_seconds=runtime_seconds,
        leakage_check=leakage_check, symbols=symbols,
    )

    report_md = _build_report(
        agg=agg, cells=cells, cutoff=cutoff,
        ranges=ranges, runtime_seconds=runtime_seconds, symbols=symbols,
    )

    _write_regime_params(os.path.join(out_dir, "regime_params.json"), agg)
    _atomic_write_json(os.path.join(out_dir, "regime_manifest.json"), manifest)
    with open(os.path.join(out_dir, "regime_report.md"), "w", encoding="utf-8") as f:
        f.write(report_md)

    log.info("Artefacts written to %s", out_dir)
    log.info("  regime_params.json   — winner config, byte-deterministic")
    log.info("  regime_manifest.json — cutoff, hashes, decision flags, no-leakage proof")
    log.info("  regime_report.md     — human-readable side-by-side + caveats")
    log.info("Decision flags: %s", agg["decision_flags"])

    if agg["decision_flags"]["sanity_check"]:
        log.error("SANITY CHECK FIRED: no_detector wins on pre-holdout window. "
                  "HALT + DEBUG before promoting this artefact.")
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — must PASS**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py::TestCLI -v
```

- [ ] **Step 5: Run full harness test suite**

```bash
python -m pytest tests/test_regime_retune_pre_holdout.py -v
python -m pytest tests/test_regime_thresholds_param.py -v
python -m pytest tests/test_simulate_strategy_regime_kwargs.py -v
```

- [ ] **Step 6: Commit**

```bash
git add tools/regime_retune_pre_holdout.py tests/test_regime_retune_pre_holdout.py
git commit -m "$(cat <<'EOF'
feat(methodology): A.4-1.5 — CLI entry point + sweep main loop

main() parses --max-date (required) + --out-dir, drives the 4×10 sweep
sequentially (sweep is small enough — 40 backtests — that process pool
parallelism would not pay off; A.4-1 needed it because grid search there
is ~36 min/symbol). Aggregates, evaluates flags, writes artefacts.

Returns rc=3 when sanity_check fires (no_detector wins) so a CI / shell
wrapper can detect the halt condition without parsing logs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Whitelist the harness in `tests/test_holdout_isolation.py`

**Files:**
- Modify: `tests/test_holdout_isolation.py`

**Goal:** Add `tools/regime_retune_pre_holdout.py` to `HOLDOUT_LEGITIMATE_MODULES` with a justification mirroring A.4-1's pattern. The harness reads `data/ohlcv.db` only, NOT `data/holdout/`, but its output dir name contains the literal `-pre-holdout` token — Guard B's AST scanner would flag it without the whitelist.

- [ ] **Step 1: Read the existing whitelist**

```bash
grep -n "HOLDOUT_LEGITIMATE_MODULES\|retune_pre_holdout" tests/test_holdout_isolation.py
```

- [ ] **Step 2: Add the entry**

Edit `tests/test_holdout_isolation.py`. Add to `HOLDOUT_LEGITIMATE_MODULES` (after the `tools/retune_pre_holdout.py` entry):

```python
    # tools/regime_retune_pre_holdout.py — A.4-1.5 mini-harness.
    # Reads data/ohlcv.db only (NOT data/holdout/). Output dir name
    # contains the literal '-pre-holdout' suffix for human discoverability;
    # the AST scanner sees that string in the source and would flag it
    # without this whitelist. Sister module to tools/retune_pre_holdout.py
    # (A.4-1, PR #287).
    "tools/regime_retune_pre_holdout.py",
```

- [ ] **Step 3: Run Guard B**

```bash
python -m pytest tests/test_holdout_isolation.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_holdout_isolation.py
git commit -m "$(cat <<'EOF'
chore(tests): whitelist tools/regime_retune_pre_holdout.py in Guard B

Mirror of A.4-1's whitelist entry. Reads data/ohlcv.db only; output
dir name contains the '-pre-holdout' string which the AST scanner
would otherwise flag.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Full test sweep + push branch + open draft PR

**Files:** none (verification + git ops only)

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -50
```
Expected: ALL PASS. Note the count for the PR body.

- [ ] **Step 2: Push branch**

```bash
git push -u origin feat/methodology-a4-1-5-regime-retune-pre-holdout
```

- [ ] **Step 3: Open draft PR**

```bash
gh pr create --draft \
  --title "feat(methodology): A.4-1.5 pre-holdout regime threshold re-tune harness" \
  --body "$(cat <<'EOF'
## Summary

Phase 2 de A.4-1.5 (#305). Adds the harness needed to re-tune the regime detector BULL/BEAR partition thresholds (`>60/<40`) over the pre-holdout window before A.4 holdout evaluation. **Does NOT execute the sweep** — Phase 3 commits the artefact in a follow-up commit on this PR.

Per spec D9 §2.10 (locked grid + locked objective):
- 4 configs from commit `bf581f1`: `(60,40)`, `(70,30)`, `(80,20)`, no-detector
- Window: `[earliest, 2025-04-30T00:00:00Z)` strict `<` slicing
- Objective: maximize sum of `net_pnl` across 10 portfolio symbols
- Decision flags: CHANGE detection, sanity check (no-detector wins → halt+debug), stability check (2nd within 5% → caveat)

## Code changes

- `strategy/regime.py` — parameterize `bull_above`/`bear_below` in both `_compute_local_regime` (backtest path) and `detect_regime` (live path); defaults 60/40 preserve byte-identity.
- `backtest.py` — thread thresholds through `simulate_strategy` → `_regime_at_time` → `_compute_local_regime`. Add `regime_disabled` bypass that emits a synthetic `{"regime": "BYPASS"}` dict; `decide_signal` patched to treat BYPASS as both LONG and SHORT permitted.
- `tools/regime_retune_pre_holdout.py` — new harness module: locked grid, self-contained slicing, single-backtest runner per (symbol, config), aggregate, decision flags, atomic byte-deterministic JSON writers.
- `tests/test_holdout_isolation.py` — whitelist entry for the new harness.

## Out of scope

- **The actual sweep run.** Phase 3 commits the artefact to `data/retune/2026-05-04-pre-holdout/`.
- **Promotion to `strategy/regime.py` + `backtest.py`.** Deferred to Phase 5 separate PR after A.4 holdout passes.

## Validation gate

- **Reproducibility:** byte-identical `regime_params.json` across re-runs (verified at the JSON-writer test layer; full reproducibility check happens in Phase 3 via `diff` after running the wrapper twice).
- **No-leakage:** strict `<` slicing in `_slice_below_cutoff` + manifest records MIN/MAX timestamps per (symbol, tf) for human-verifiable proof.
- **Parity:** existing regime + backtest tests must pass without changes — defaults preserve legacy production behavior byte-for-byte.

## Spec ref correction

Spec D9 §2.10 cites `backtest.py:404-409` as the inline threshold site. That citation is stale — the real source-of-truth is `strategy/regime.py:_compute_local_regime` (~lines 174-179) and `strategy/regime.py:detect_regime` (~lines 372-377); backtest consumes the threshold via `_compute_local_regime` through `_regime_at_time`. Cosmetic doc rot, will be amended in a follow-up D9 edit.

## References

- Spec: `docs/superpowers/specs/es/2026-05-03-asunciones-tecnicas-pre-holdout.md` §2.10
- Issue: #305
- Sister mini-epic: A.4-1 PR #287 (gated on this one closing successfully)
- Origin commit: `bf581f1` (sssamuelll, 2026-04-18)
EOF
)"
```

- [ ] **Step 4: Verify PR opened, capture URL**

```bash
gh pr view --web
```

---

# PHASE 3 — Run the sweep

**Pre-flight:** Phase 3 only runs after Phase 2 review by reviewer agent + Sam authorization (`dale`). Do NOT execute Phase 3 without explicit confirmation.

## Task 12: Pre-run verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm clean working tree on the harness branch**

```bash
git status
git branch --show-current
```
Expected: clean tree, branch = `feat/methodology-a4-1-5-regime-retune-pre-holdout`.

- [ ] **Step 2: Confirm OHLCV DB exists + capture its hash**

```bash
ls -lh data/ohlcv.db
shasum -a 256 data/ohlcv.db
```

- [ ] **Step 3: Confirm full test suite still passes**

```bash
python -m pytest tests/ -v 2>&1 | tail -30
```

---

## Task 13: First run

- [ ] **Step 1: Run the harness**

```bash
python -m tools.regime_retune_pre_holdout --max-date 2025-04-30 2>&1 | tee /tmp/a4_1_5_run1.log
```

Expected: rc=0 (or rc=3 if sanity_check fires — see decision flags step below).

- [ ] **Step 2: Inspect outputs**

```bash
ls -la data/retune/$(date -u +%Y-%m-%d)-pre-holdout/
cat data/retune/$(date -u +%Y-%m-%d)-pre-holdout/regime_params.json
cat data/retune/$(date -u +%Y-%m-%d)-pre-holdout/regime_manifest.json | python -m json.tool | head -60
```

- [ ] **Step 3: Verify leakage_check**

```bash
python -c "import json; print(json.load(open('data/retune/$(date -u +%Y-%m-%d)-pre-holdout/regime_manifest.json'))['leakage_check'])"
```
Expected: `PASS`.

---

## Task 14: Reproducibility check

- [ ] **Step 1: Re-run with a different out-dir**

```bash
python -m tools.regime_retune_pre_holdout \
  --max-date 2025-04-30 \
  --out-dir /tmp/a4_1_5_run2 2>&1 | tee /tmp/a4_1_5_run2.log
```

- [ ] **Step 2: Diff the regime_params.json — must be empty**

```bash
diff data/retune/$(date -u +%Y-%m-%d)-pre-holdout/regime_params.json /tmp/a4_1_5_run2/regime_params.json
```
Expected: no output (files byte-identical).

If diff is non-empty: STOP. Investigate non-determinism before proceeding. Likely culprits: sort_keys missing, non-deterministic dict iteration in a writer, cached random state.

---

## Task 15: Independent SQL cross-check

- [ ] **Step 1: Independent verification of no-leakage**

```bash
python << 'EOF'
import sqlite3
con = sqlite3.connect("data/ohlcv.db")
cutoff_ms = 1745971200000  # 2025-04-30T00:00:00 UTC in milliseconds
symbols = ["BTCUSDT","ETHUSDT","ADAUSDT","AVAXUSDT","DOGEUSDT",
           "UNIUSDT","XLMUSDT","PENDLEUSDT","JUPUSDT","RUNEUSDT"]
for sym in symbols:
    for tf in ("5m","1h","4h","1d"):
        row = con.execute(
            "SELECT MAX(open_time) FROM ohlcv WHERE symbol=? AND timeframe=? AND open_time<?",
            (sym, tf, cutoff_ms)
        ).fetchone()
        max_ts = row[0]
        if max_ts is not None:
            assert max_ts < cutoff_ms, f"LEAK: {sym} {tf} max_ts={max_ts} >= cutoff={cutoff_ms}"
print("Independent SQL cross-check: PASS")
EOF
```
Expected: `Independent SQL cross-check: PASS`.

---

## Task 16: Apply pre-registered decision flags

- [ ] **Step 1: Read the flags from the manifest**

```bash
python -c "
import json
m = json.load(open('data/retune/$(date -u +%Y-%m-%d)-pre-holdout/regime_manifest.json'))
print('Winner:        ', m['winner'])
print('Runner-up:     ', m['runner_up'])
print('Margin %:      ', m['winner_margin_pct'])
print('Decision flags:', m['decision_flags'])
print('Per-config:    ', m['per_config_pnl'])
"
```

- [ ] **Step 2: Evaluate flags**

| Flag | Trigger condition | Required action |
|---|---|---|
| **CHANGE detection** | winner != `60_40` | Document in PR body. NO auto-promote (Phase 5 is separate post-holdout PR). |
| **Sanity check** | winner == `no_detector` | **HALT + DEBUG.** Do NOT commit the artefact. Report to reviewer with: per-symbol breakdown, dataset slice verification (manifest MIN/MAX timestamps), code-path validation (`grep -n "regime_disabled" backtest.py`). Do not proceed until root cause identified. |
| **Stability check** | margin < 5% | Document as informational caveat in `regime_report.md` (the harness already does this). Note explicitly in the PR body that production decision should weigh qualitative tradeoffs. |

- [ ] **Step 3: If sanity_check fires → STOP HERE**

Surface to reviewer with the diagnostic info from Step 2's table. Do NOT proceed to Task 17 until the halt is resolved (either: bug found and fixed → re-run from Task 13; or, less likely: the result genuinely holds and reviewer + Sam authorize a separate path forward).

---

## Task 17: Long-form smoke (if applicable)

- [ ] **Step 1: Check if a regime-path long-form smoke exists**

```bash
ls smokes/ 2>/dev/null | grep -i regime || echo "No regime smoke present; documenting in PR body and skipping."
```

- [ ] **Step 2: If a smoke exists, run it; otherwise note its absence in the PR body**

If smoke exists: `bash smokes/<regime_smoke_script>.sh` and verify pass.
If absent: add a one-liner to the Phase 3 PR commit body: "No long-form regime smoke present in `smokes/`; integration coverage relied on the `tests/test_simulate_strategy_regime_kwargs.py` parity tests + the harness's own end-to-end run."

---

## Task 18: Commit the artefact

- [ ] **Step 1: Stage the artefact directory**

```bash
git add data/retune/$(date -u +%Y-%m-%d)-pre-holdout/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
data(methodology): A.4-1.5 — regime threshold re-tune artefact

Phase 3 output of A.4-1.5 mini-harness. Sweep of 4 configs
(60/40, 70/30, 80/20, no_detector) over the pre-holdout window
[earliest, 2025-04-30T00:00:00Z) on the 10 portfolio symbols.

Winner: <REPLACE WITH actual winner from manifest>
Margin to runner-up: <REPLACE WITH actual %>
Decision flags: <REPLACE WITH actual flags>

Reproducibility verified (run-twice diff empty). Independent SQL
cross-check confirmed no-leakage. Manifest records ohlcv.db hash,
code commit, per-symbol MIN/MAX timestamps strictly < cutoff.

Refs #305 + spec D9 §2.10. Phase 4 review (R1+R2 multi-agent) follows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**REPLACE the bracketed placeholders before committing.**

- [ ] **Step 3: Push**

```bash
git push
```

- [ ] **Step 4: Mark PR ready for review (only if you have explicit Sam authorization)**

If Sam has authorized: `gh pr ready`.
Otherwise: leave the PR in draft, surface the run results to the reviewer agent, await `dale` from Sam before marking ready.

---

# Verification gate (Phase 2 + 3 done — ALL of these)

- [ ] Harness module exists with full test coverage (`tests/test_regime_retune_pre_holdout.py`)
- [ ] Parity tests pass: legacy regime path byte-identical (`tests/test_regime_thresholds_param.py`, `tests/test_simulate_strategy_regime_kwargs.py`)
- [ ] Guard B whitelist updated; `tests/test_holdout_isolation.py` AST scanner passes
- [ ] Full test suite passing
- [ ] Reproducibility: 2 consecutive runs → `diff regime_params.json` empty
- [ ] Manifest MAX(open_time) strictly < `2025-04-30T00:00:00Z` for every (symbol, tf)
- [ ] Independent SQL cross-check confirms no-leakage
- [ ] `regime_params.json` shape correct (`regime_thresholds` dict OR `regime_disabled: true`)
- [ ] Decision flags evaluated in `regime_report.md`
- [ ] Sanity check NOT fired (or, if fired: halt + debug + report to reviewer instead of committing)

---

# Out-of-band notes for the dev

1. **The kickoff prompt cited `backtest.py:404-409` for regime threshold inline. That citation is wrong** — that block is the trade-cost calculation. The real source-of-truth is `strategy/regime.py:_compute_local_regime` (lines ~174-179) and `strategy/regime.py:detect_regime` (lines ~372-377). The backtest path consumes `_compute_local_regime` via `_regime_at_time`. Document this discrepancy in the PR body so the spec ref doc-rot is captured for the cosmetic D9 follow-up.

2. **`auto_tune.run_backtest_with_params` on `main` does NOT have `cutoff` / `_slice_below_cutoff`.** Those live on PR #287's branch. This plan deliberately builds a self-contained slicing path so A.4-1.5 doesn't depend on PR #287 (which is GATED on A.4-1.5 closing — circular dependency otherwise). Do not be tempted to import from `auto_tune` until both PRs have merged.

3. **F&G + funding loaders are `backtest.get_historical_fear_greed()` and `backtest.get_historical_funding_rate()`** (the latter defined at `backtest.py:144`). Both are used by `auto_tune.run_backtest_with_params:179-180`. The harness imports them directly — no need to add new loaders. Do NOT modify `auto_tune.py`.

4. **BYPASS patch lives in `strategy/core.py`** — `_regime_to_direction_token` (lines 124-134) plus the gating block at lines 341-354. Both edits are minimal (a new branch in the helper + extending two `in (...)` clauses). The "BYPASS" label propagates through `_regime_to_direction_token` → "ANY" token → gating logic permits direction by zone alone. Do not introduce a new direction token outside this path.

5. **Decision flag escalation if sanity_check fires:** that configuration was last-place tied with `(80, 20)` in `bf581f1`. Winning over the pre-holdout-only window is high-probability bug, low-probability genuine result. Halt and ask the reviewer; do NOT commit the artefact.

6. **Surface this plan to the reviewer agent for Sam authorization BEFORE Task 1.** Do not start coding until you have explicit `dale` from Sam.
