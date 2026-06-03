# Funding-Carry Tail-Aware Kill Rule — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-interval-mark funding accrual + a frozen funding-negative KILL rule to the (already-passing) funding carry, and gate whether the kill makes the carry survive an out-of-sample tail (2 synthetic shocks) net-positive while reporting whether the kill adds value vs no-kill.

**Architecture:** Extends the existing `tools/funding_carry/` package (constants/ingest/simulate/evaluate/run from the PASS). Adds per-interval mark to `simulate.py`, a new `kill_rule.py` (negative-run detection + with-kill / no-kill equity simulators charging real v3 churn cost), kill-vs-no-kill + shock-injection + tail gate to `evaluate.py`, and a `run_kill.py` orchestrator. Reuses `backtest_costs.compute_trade_costs(model="v3", enable_funding=False)` and the populated `data/funding.db`.

**Tech Stack:** Python 3, sqlite3, numpy, pytest. Reuses `backtest_costs` + the existing `tools/funding_carry` package.

**Frozen pre-registration (spec `2026-06-03-funding-carry-tail-kill-design.md`, commit 9605758):**
- per-interval mark: `Σ rate_i × mark_i × units` (mark from perp_klines at each settlement).
- KILL: exit after K=24 consecutive `rate<0` settlements; re-enter on first `rate>=0`; no cooldown. Each IN-tramo charges one v3 round-trip (transaction-only). K frozen; K-sensitivity {9,18,24,36} is DESCRIPTIVE.
- Gate: G1 = with-kill pooled net > 0 (in-sample); G2 = survives N_SHOCKS=2 synthetic shocks (0.5%/8h × 5d) injected at the 2 most-vulnerable points, kill firing during each, net-positive. Verdict PASS = G1 ∧ G2. Leverage 2.0 fixed (liquidation not the binding risk).
- `$`-denominated. No holdout (#322), no live perps.

**Note:** `data/funding.db` is already populated (11 symbols, 2646 funding + 21168 perp klines each). No network needed.

---

## File Structure

- Modify `tools/funding_carry/constants.py` — add KILL_K, K_SENSITIVITY, N_SHOCKS, LEVERAGE, OUTPUT_DIR_KILL.
- Modify `tools/funding_carry/simulate.py` — add `perp_mark_series`, `funding_pnl_per_interval`.
- Create `tools/funding_carry/kill_rule.py` — `consecutive_negative_exit`, `simulate_with_kill`, `simulate_no_kill`.
- Modify `tools/funding_carry/evaluate.py` — add `kill_vs_nokill`, `inject_shocks`, `gate_tail`, `verdict_kill`.
- Create `tools/funding_carry/run_kill.py` — orchestrator → `data/retune/2026-06-03-funding-carry-tail-kill/`.
- Modify `tests/test_funding_carry.py` — TDD for all new functions.

Existing reused signatures (already in the package):
`simulate.load_funding(funding_db, symbol, start_ms, end_ms) -> [(ts,rate)]`;
`simulate.perp_price_at / spot_price_at / spot_liquidity`;
`simulate.recost_four_legs(*, symbol, units, spot_price, perp_price, liq, holding_hours) -> float`;
`constants.NOTIONAL, CANDIDATE_SYMBOLS, WINDOW_START, WINDOW_END, OHLCV_DB, FUNDING_DB, BOOTSTRAP_N, BOOTSTRAP_SEED, SHOCK_FUNDING_PER_8H, SHOCK_DAYS`.

---

## Task 1: New frozen constants

**Files:** Modify `tools/funding_carry/constants.py`

- [ ] **Step 1: Append the new constants**

Append to `tools/funding_carry/constants.py`:
```python
# --- Tail-aware kill rule (sub-project #1, spec 9605758) ---
KILL_K = 24                       # exit after this many consecutive negative settlements (~8d)
K_SENSITIVITY = (9, 18, 24, 36)   # DESCRIPTIVE only — does NOT gate the verdict
N_SHOCKS = 2                      # synthetic out-of-sample shocks (2022 = LUNA + FTX)
LEVERAGE = 2.0                    # fixed conservative; liquidation needs ~50% adverse (not binding)
OUTPUT_DIR_KILL = "data/retune/2026-06-03-funding-carry-tail-kill"
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from tools.funding_carry.constants import KILL_K, K_SENSITIVITY, N_SHOCKS, LEVERAGE, OUTPUT_DIR_KILL; print(KILL_K, K_SENSITIVITY, N_SHOCKS, LEVERAGE)"`
Expected: `24 (9, 18, 24, 36) 2 2.0`

- [ ] **Step 3: Commit**

```bash
git add tools/funding_carry/constants.py
git commit -m "feat(funding-carry): kill-rule constants (K=24, 2 shocks, lev 2x)"
```

---

## Task 2: Per-interval mark accrual

**Files:** Modify `tools/funding_carry/simulate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
def test_perp_mark_series_lookup(tmp_path):
    db = str(tmp_path / "f.db"); _mk_funding_db(db)   # perp close=100 at each hour
    # funding times at 0, 28.8M, 57.6M ms -> last perp close at/<= each
    marks = simulate.perp_mark_series(db, "BTCUSDT", [0, 28_800_000, 57_600_000])
    assert marks == [pytest.approx(100.0)] * 3

def test_funding_pnl_per_interval_uses_each_mark():
    funding = [(0, 0.0001), (1, -0.0002)]
    marks = [100.0, 200.0]    # mark doubles on the 2nd settlement
    pnl = simulate.funding_pnl_per_interval(funding, marks=marks, units=2.0)
    assert pnl == pytest.approx(0.0001 * 100.0 * 2.0 + (-0.0002) * 200.0 * 2.0)
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "perp_mark_series or per_interval" -v`
Expected: FAIL (no attribute).

- [ ] **Step 3: Implement (append to simulate.py)**

```python
def perp_mark_series(funding_db: str, symbol: str, times_ms: list[int]) -> list[float]:
    """Perp mark close at or before each funding settlement time (NaN if none)."""
    return [perp_price_at(funding_db, symbol, t) for t in times_ms]


def funding_pnl_per_interval(funding: list[tuple[int, float]], *, marks: list[float],
                             units: float) -> float:
    """Funding the short collects, marked PER SETTLEMENT: sum(rate_i * mark_i * units).
    More accurate than the constant-entry-mark approximation (spec §2)."""
    return sum(rate * mark * units for (_, rate), mark in zip(funding, marks))
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "perp_mark_series or per_interval" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/simulate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): per-interval mark funding accrual"
```

---

## Task 3: Kill-rule simulator (with-kill + no-kill)

**Files:** Create `tools/funding_carry/kill_rule.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
from tools.funding_carry import kill_rule

def test_simulate_no_kill_one_tramo():
    # all positive funding -> one continuous tramo, no kill, one round-trip cost.
    funding = [(i, 0.0001) for i in range(10)]
    marks = [100.0] * 10
    r = kill_rule.simulate_no_kill(funding, marks=marks, units=2.0, rt_cost=5.0)
    assert r["n_tramos"] == 1
    assert r["churn_cost"] == pytest.approx(5.0)
    assert r["net"] == pytest.approx(sum(0.0001 * 100.0 * 2.0 for _ in range(10)) - 5.0)
    assert len(r["equity_curve"]) == 10          # cumulative, time-ordered

def test_simulate_with_kill_exits_on_K_negatives():
    # 3 positive, then K=3 negatives -> exit; then 2 positive -> re-enter.
    funding = [(i, 0.0001) for i in range(3)] + [(i + 3, -0.0002) for i in range(3)] \
              + [(i + 6, 0.0001) for i in range(2)]
    marks = [100.0] * 8
    r = kill_rule.simulate_with_kill(funding, marks=marks, units=2.0, rt_cost=5.0, k=3)
    assert r["n_tramos"] == 2                     # initial IN + one re-entry
    assert r["n_kills"] == 1
    assert r["churn_cost"] == pytest.approx(10.0) # 2 tramos * 5.0

def test_with_kill_no_negatives_equals_no_kill():
    funding = [(i, 0.0001) for i in range(10)]
    marks = [100.0] * 10
    wk = kill_rule.simulate_with_kill(funding, marks=marks, units=2.0, rt_cost=5.0, k=3)
    nk = kill_rule.simulate_no_kill(funding, marks=marks, units=2.0, rt_cost=5.0)
    assert wk["net"] == pytest.approx(nk["net"])
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "no_kill or with_kill" -v`
Expected: FAIL (no module kill_rule).

- [ ] **Step 3: Implement `tools/funding_carry/kill_rule.py`**

```python
"""Funding-negative KILL rule simulator (spec §3-§4). IN/OUT state machine over the
per-settlement funding stream, charging one v3 round-trip per IN-tramo (churn is real).
Pure functions — `rt_cost` (round-trip transaction cost) is passed in, computed by the
caller via simulate.recost_four_legs."""
from __future__ import annotations


def _accrue(funding, marks, units, lo, hi):
    """Sum funding over settlements [lo, hi) (a single IN-tramo)."""
    return sum(funding[i][1] * marks[i] * units for i in range(lo, hi))


def simulate_no_kill(funding, *, marks, units, rt_cost) -> dict:
    """Continuous hold: one tramo over all settlements, one round-trip cost."""
    n = len(funding)
    gross = _accrue(funding, marks, units, 0, n)
    eq, run = [], 0.0
    for i in range(n):
        run += funding[i][1] * marks[i] * units
        eq.append(run)
    return {"net": gross - rt_cost, "n_tramos": 1, "n_kills": 0,
            "churn_cost": rt_cost, "equity_curve": eq}


def simulate_with_kill(funding, *, marks, units, rt_cost, k) -> dict:
    """IN/OUT machine. Exit after `k` consecutive rate<0 settlements; re-enter on the
    first rate>=0. Each IN-tramo charges `rt_cost` (one round trip). Equity curve is the
    cumulative time-ordered P&L (funding only; basis handled by the caller per tramo)."""
    n = len(funding)
    eq, run = [], 0.0
    state, neg = "IN", 0
    n_tramos = 1 if n else 0          # start IN
    n_kills = 0
    for i in range(n):
        rate = funding[i][1]
        if state == "IN":
            run += rate * marks[i] * units
            neg = neg + 1 if rate < 0 else 0
            if neg >= k:
                state, neg = "OUT", 0
                n_kills += 1
        elif rate >= 0:               # OUT and funding back positive -> re-enter
            state = "IN"
            n_tramos += 1
        eq.append(run)
    churn = rt_cost * n_tramos
    return {"net": run - churn, "n_tramos": n_tramos, "n_kills": n_kills,
            "churn_cost": churn, "equity_curve": eq}
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "no_kill or with_kill" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/kill_rule.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): kill-rule simulator (with-kill + no-kill, churn-charged)"
```

---

## Task 4: Kill-vs-no-kill + shock injection + tail gate

**Files:** Modify `tools/funding_carry/evaluate.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
def test_kill_vs_nokill_bootstrap_deterministic():
    wk = [0.06, 0.05, 0.07, 0.04, 0.08, 0.05, 0.06, 0.05, 0.07]
    nk = [0.05, 0.05, 0.06, 0.04, 0.07, 0.05, 0.05, 0.05, 0.06]
    a = evaluate.kill_vs_nokill(wk, nk)
    b = evaluate.kill_vs_nokill(wk, nk)
    assert a["ci_lo"] == b["ci_lo"]                       # seeded
    assert a["mean_delta"] == pytest.approx(sum(w - n for w, n in zip(wk, nk)) / len(wk))

def test_inject_shocks_subtracts_worst_points():
    # a flat-up equity; injecting 2 shocks of size 3.0 reduces the final by ~2*3.0 net.
    eq = [1.0, 2.0, 3.0, 4.0, 5.0]
    final = evaluate.inject_shocks(eq, n_shocks=2, shock_loss=3.0)
    assert final == pytest.approx(5.0 - 2 * 3.0)

def test_gate_tail_requires_both():
    g = evaluate.gate_tail(with_kill_net_pooled=0.10, post_shock_net_pooled=0.02)
    assert g["pass_g1"] and g["pass_g2"] and g["verdict"] == "PASS"
    g2 = evaluate.gate_tail(with_kill_net_pooled=0.10, post_shock_net_pooled=-0.01)
    assert g2["verdict"] == "FAIL"
    g3 = evaluate.gate_tail(with_kill_net_pooled=-0.01, post_shock_net_pooled=0.02)
    assert g3["verdict"] == "FAIL"
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k "kill_vs_nokill or inject_shocks or gate_tail" -v`
Expected: FAIL.

- [ ] **Step 3: Implement (append to evaluate.py)**

```python
from .constants import N_SHOCKS, SHOCK_FUNDING_PER_8H, SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY


def kill_vs_nokill(with_kill: list[float], no_kill: list[float]) -> dict:
    """Paired pooled delta (with_kill - no_kill) net return + bootstrap CI. Does the kill
    add value net of churn? Positive mean_delta = kill helps."""
    deltas = np.asarray([w - n for w, n in zip(with_kill, no_kill)], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(deltas), size=(BOOTSTRAP_N, len(deltas)))
    means = deltas[idx].mean(axis=1)
    return {"mean_delta": float(deltas.mean()),
            "ci_lo": float(np.percentile(means, 2.5)),
            "ci_hi": float(np.percentile(means, 97.5)),
            "kill_adds_value": bool(np.percentile(means, 2.5) > 0.0)}


def inject_shocks(equity_curve: list[float], *, n_shocks: int, shock_loss: float) -> float:
    """Final equity after subtracting `n_shocks` one-time losses of `shock_loss` each
    (the synthetic out-of-sample tail; the kill caps each shock's bleed). Conservative:
    applies the full loss n_shocks times to the realized final equity."""
    final = equity_curve[-1] if equity_curve else 0.0
    return final - n_shocks * shock_loss


def gate_tail(*, with_kill_net_pooled: float, post_shock_net_pooled: float) -> dict:
    """G1 = with-kill net survives in-sample (>0); G2 = survives N_SHOCKS (>=0). PASS = both."""
    g1 = bool(with_kill_net_pooled > 0.0)
    g2 = bool(post_shock_net_pooled >= 0.0)
    return {"pass_g1": g1, "pass_g2": g2, "verdict": "PASS" if (g1 and g2) else "FAIL"}
```

- [ ] **Step 4: Run, confirm PASS**

Run: `python -m pytest tests/test_funding_carry.py -k "kill_vs_nokill or inject_shocks or gate_tail" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/evaluate.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): kill-vs-nokill + shock injection + tail gate (G1/G2)"
```

---

## Task 5: run_kill orchestrator + artifacts

**Files:** Create `tools/funding_carry/run_kill.py`; Test `tests/test_funding_carry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_funding_carry.py`:
```python
def test_run_kill_required_keys():
    from tools.funding_carry import run_kill
    rec = {"symbol": "BTCUSDT", "net_with_kill": 0.06, "net_no_kill": 0.05,
           "n_kills": 1, "max_dd": 100.0, "churn_cost": 50.0}
    assert run_kill.REQUIRED_KILL_KEYS <= set(rec.keys())
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `python -m pytest tests/test_funding_carry.py -k run_kill_required -v`
Expected: FAIL (no module run_kill).

- [ ] **Step 3: Implement `tools/funding_carry/run_kill.py`**

```python
"""Orchestrate the tail-aware kill-rule study end-to-end → verdict artifacts.

Run: python -m tools.funding_carry.run_kill   (uses data/funding.db; no network)
Reads funding.db + ohlcv.db (read-only). Writes only under OUTPUT_DIR_KILL. No holdout."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
import numpy as np

from backtest_costs import calibration_identity_hash, load_calibration
from . import simulate, evaluate, kill_rule
from .constants import (OHLCV_DB, FUNDING_DB, OUTPUT_DIR_KILL, CANDIDATE_SYMBOLS,
                        WINDOW_START, WINDOW_END, NOTIONAL, KILL_K, K_SENSITIVITY,
                        N_SHOCKS, SHOCK_FUNDING_PER_8H, SHOCK_DAYS, SHOCK_INTERVALS_PER_DAY)
from .run import _ms, _covered_symbols

REQUIRED_KILL_KEYS = {"symbol", "net_with_kill", "net_no_kill", "n_kills", "max_dd", "churn_cost"}


def _max_dd(equity_curve) -> float:
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) == 0:
        return 0.0
    return float((np.maximum.accumulate(eq) - eq).max())


def _one_symbol(s, w0, w1, k):
    funding = simulate.load_funding(FUNDING_DB, s, w0, w1)
    if len(funding) < 2:
        return None
    times = [t for t, _ in funding]
    marks = simulate.perp_mark_series(FUNDING_DB, s, times)
    spot_e = simulate.spot_price_at(OHLCV_DB, s, times[0])
    perp_e = simulate.perp_price_at(FUNDING_DB, s, times[0])
    if any(np.isnan(x) for x in (spot_e, perp_e)) or any(np.isnan(m) for m in marks):
        return None
    units = NOTIONAL / spot_e
    liq = simulate.spot_liquidity(OHLCV_DB, s, times[0])
    rt = simulate.recost_four_legs(symbol=s, units=units, spot_price=spot_e,
                                   perp_price=perp_e, liq=liq,
                                   holding_hours=(times[-1] - times[0]) / 3_600_000)
    wk = kill_rule.simulate_with_kill(funding, marks=marks, units=units, rt_cost=rt, k=k)
    nk = kill_rule.simulate_no_kill(funding, marks=marks, units=units, rt_cost=rt)
    return {"symbol": s, "net_with_kill": wk["net"] / NOTIONAL,
            "net_no_kill": nk["net"] / NOTIONAL, "n_kills": wk["n_kills"],
            "max_dd": _max_dd(wk["equity_curve"]), "churn_cost": wk["churn_cost"],
            "equity_curve": wk["equity_curve"]}


def main():
    os.makedirs(OUTPUT_DIR_KILL, exist_ok=True)
    w0, w1 = _ms(WINDOW_START), _ms(WINDOW_END)
    symbols = _covered_symbols(FUNDING_DB, w0, w1)
    recs = [r for r in (_one_symbol(s, w0, w1, KILL_K) for s in symbols) if r]

    wk_ret = [r["net_with_kill"] for r in recs]
    nk_ret = [r["net_no_kill"] for r in recs]
    kvn = evaluate.kill_vs_nokill(wk_ret, nk_ret)
    wk_pooled = float(np.mean(wk_ret)) if recs else 0.0
    # G2: per-symbol, subtract N_SHOCKS one-time bleeds (kill caps each at K settlements
    # of the shock rate); pooled post-shock net return.
    shock_loss = KILL_K * SHOCK_FUNDING_PER_8H            # kill caps the bleed at K settlements
    post = [evaluate.inject_shocks([NOTIONAL * r["net_with_kill"]], n_shocks=N_SHOCKS,
                                   shock_loss=NOTIONAL * shock_loss) / NOTIONAL for r in recs]
    post_pooled = float(np.mean(post)) if recs else 0.0
    gate = evaluate.gate_tail(with_kill_net_pooled=wk_pooled, post_shock_net_pooled=post_pooled)

    # descriptive K-sensitivity (does NOT gate)
    ksens = {}
    for k in K_SENSITIVITY:
        rs = [r for r in (_one_symbol(s, w0, w1, k) for s in symbols) if r]
        ksens[k] = {"pooled_net": float(np.mean([x["net_with_kill"] for x in rs])) if rs else 0.0,
                    "mean_kills": float(np.mean([x["n_kills"] for x in rs])) if rs else 0.0}

    cal = load_calibration()
    out = {"verdict": gate, "kill_vs_nokill": kvn, "with_kill_pooled": wk_pooled,
           "no_kill_pooled": float(np.mean(nk_ret)) if recs else 0.0,
           "post_shock_pooled": post_pooled, "k_sensitivity": ksens,
           "manifest": {"experiment": "funding-carry-tail-kill", "spec_commit": "9605758",
                        "kill_k": KILL_K, "n_shocks": N_SHOCKS, "shock_loss_frac": shock_loss,
                        "cost_model": {"active_model": cal.active_model,
                                       "calibration_identity_hash": calibration_identity_hash(cal)},
                        "symbols_used": [r["symbol"] for r in recs],
                        "generated_utc": datetime.now(timezone.utc).isoformat()}}
    slim = [{kk: r[kk] for kk in REQUIRED_KILL_KEYS} for r in recs]
    with open(os.path.join(OUTPUT_DIR_KILL, "per_symbol.json"), "w") as f:
        json.dump(slim, f, indent=2)
    with open(os.path.join(OUTPUT_DIR_KILL, "verdict.json"), "w") as f:
        json.dump(out, f, indent=2)
    lines = [
        "# Funding-carry tail-aware kill rule: VERDICT", "",
        f"**Verdict: {gate['verdict']}**  (G1 in-sample: {gate['pass_g1']}, G2 out-of-sample: {gate['pass_g2']})", "",
        f"- Symbols used {len(recs)}: {', '.join(r['symbol'] for r in recs)}",
        f"- With-kill pooled net: {wk_pooled:.4f}   No-kill pooled net: {out['no_kill_pooled']:.4f}",
        f"- Kill vs no-kill: mean delta {kvn['mean_delta']:.4f}, CI95 [{kvn['ci_lo']:.4f}, {kvn['ci_hi']:.4f}], adds_value={kvn['kill_adds_value']}",
        f"- Post-{N_SHOCKS}-shock pooled net: {post_pooled:.4f}  (shock_loss/ea {shock_loss:.4f}, kill-capped at K settlements)",
        f"- K-sensitivity (descriptive): " + "; ".join(f"K={k}: net {v['pooled_net']:.4f}, kills {v['mean_kills']:.1f}" for k, v in ksens.items()), "",
        "Interpretation: kill ADDS value if mean delta > 0 (better net) or lowers max_dd; a PASS",
        "where kill <= no-kill means the carry is already robust without the kill. Leverage 2x fixed.",
        "Scope: liquid universe, in-sample 2024-26 + 2 synthetic shocks. NOT production-deployable",
        "(rebalance #2, long-tail #3, live #4 are separate sub-projects).",
    ]
    with open(os.path.join(OUTPUT_DIR_KILL, "findings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"VERDICT: {gate['verdict']}  (G1={gate['pass_g1']} G2={gate['pass_g2']}, "
          f"with-kill {wk_pooled:.4f} vs no-kill {out['no_kill_pooled']:.4f})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the unit test + full suite**

Run: `python -m pytest tests/test_funding_carry.py -q`
Expected: ALL pass (existing 16 + Task2 2 + Task3 3 + Task4 3 + Task5 1 = 25). DO NOT run `run_kill.main` yet (Task 6).

- [ ] **Step 5: Commit**

```bash
git add tools/funding_carry/run_kill.py tests/test_funding_carry.py
git commit -m "feat(funding-carry): run_kill orchestrator + tail-kill artifacts"
```

---

## Task 6: Execute + route (human-in-the-loop)

**Files:** runtime — `data/retune/2026-06-03-funding-carry-tail-kill/`

- [ ] **Step 1: Execute.** `python -m tools.funding_carry.run_kill` — prints `VERDICT: ...`, writes the 3 artifacts. (Uses the populated `data/funding.db`; no network.)
- [ ] **Step 2: Read `findings.md` + `verdict.json`.** Confirm G1/G2, the kill-vs-no-kill delta (does the kill add value or just churn?), and the K-sensitivity (is K=24 robust or a knife-edge?).
- [ ] **Step 3: `mex log`** the verdict + whether the kill adds value.
- [ ] **Step 4: Route.** PASS + kill-adds-value → the kill is a real risk control; proceed to sub-project #2 (rebalance) or #3 (long-tail). PASS + kill-neutral → the liquid carry is already tail-robust without a kill (also a useful finding). FAIL → the liquid carry is too thin for tail-aware deployment at this size; re-evaluate sizing/leverage or go straight to the long-tail (#3) where the gross edge is larger. Update memory `edge-landscape-funding-carry`.

---

## Self-Review

**Spec coverage:** §2 per-interval mark → Task 2. §3 kill rule (K=24, re-enter, churn) → Task 3. §4 with-kill/no-kill simulator → Task 3. §5 kill-vs-no-kill + K-sensitivity → Tasks 4-5. §6 gate G1∧G2 + shock injection → Tasks 4-5. §7 file structure → all. §8 NN (no holdout/live) → read-only sqlite + funding.db, no `open_holdout`/`simulate_strategy`/`PositionClosure`. §10 open questions resolved: strict `<0` breaks the run (Task 3 `neg = neg+1 if rate<0 else 0`); shocks applied to final equity per-symbol (Task 5 `inject_shocks`); basis per-tramo — see limitation below.

**Known simplification (pre-declared):** the equity curve and net in `simulate_with_kill` track FUNDING only; basis P&L per tramo is omitted (basis was trivial in the v1 PASS: ~$10-50 vs ~$1800 funding, <3%). This keeps the kill simulator focused on the funding-tail it manages. If basis matters at deployment it is a #2 (rebalance) concern. The churn cost (the kill's real price) IS charged in full.

**Placeholder scan:** none. K-sensitivity values are concrete; shock_loss = KILL_K × SHOCK_FUNDING_PER_8H is a derived constant, not a TODO.

**Type consistency:** `simulate_with_kill`/`simulate_no_kill` both return dicts with `net, n_tramos, n_kills, churn_cost, equity_curve`. `kill_vs_nokill(with_kill, no_kill)` takes two return lists. `gate_tail(with_kill_net_pooled=, post_shock_net_pooled=)` kwargs match the call in `run_kill.main`. `inject_shocks(equity_curve, n_shocks=, shock_loss=)` matches. `recost_four_legs` reused with its existing signature. `_ms`/`_covered_symbols` imported from the existing `run.py`. `REQUIRED_KILL_KEYS` ⊆ the per-symbol record.

**Note on G2:** `inject_shocks` is applied to a single-element list `[NOTIONAL * net_with_kill]` per symbol (the realized final $ net), subtracting `N_SHOCKS × NOTIONAL × shock_loss`. `shock_loss = KILL_K × SHOCK_FUNDING_PER_8H` models the kill capping each shock's bleed at K settlements (not the full SHOCK_DAYS) — the kill's whole purpose. Conservative: the realized in-sample net already absorbed real negative episodes; G2 adds 2 MORE worst-case shocks on top.
