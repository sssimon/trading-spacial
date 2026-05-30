# #397 — Residual DD inflation in `emit_shadow_decision` (ledger-based DD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every live portfolio-drawdown computation use the capital ledger as the single source of truth (`current_equity = balance + open_mtm`), eliminating the double-counting of closed trades that under-reports the shadow DD and blocks the kill-switch shadow→active promotion (#397).

**Architecture:** Extract the correct DD formula — which currently lives **inline only** inside `health.get_dashboard_state` (health.py:1155-1207) and is pinned by `test_get_health_dashboard_no_double_count_on_realized_pnl_history` — into one pure, DB-free helper `compute_portfolio_dd_from_ledger()` in `strategy/kill_switch_v2.py`. Route the three live consumers through it: (1) `emit_shadow_decision`, (2) the sibling `_compute_current_portfolio_dd` (which has the identical double-count bug and feeds `get_health_dashboard` + the degradation trigger), and (3) the `get_dashboard_state` inline block (replace with a call to the new helper to dedupe). New shadow decision-log rows are tagged with `dd_formula_version` in `reasons_json`; historical rows are left untouched (no 199k-row migration).

**Tech Stack:** Python 3.11, sqlite3 (WAL), pytest. No new dependencies.

---

## Background: why this is a 3-site fix, not 1

The issue asks to fix `emit_shadow_decision`. But the root cause is that the correct DD formula was never extracted into a shared helper, so it was reimplemented **incorrectly** in two other places. The buggy pattern is always:

```python
equity_curve = compute_portfolio_equity_curve(
    closed_trades=closed,        # <-- closed PnL already folded into `balance`
    open_positions=opens,
    capital_base=balance,        # <-- ...so this double-counts closed PnL
    now_price_by_symbol=prices,
)
portfolio_dd = compute_portfolio_dd(equity_curve)
```

Sites with the bug:

| # | Location | Feeds | Status |
|---|----------|-------|--------|
| 1 | `strategy/kill_switch_v2_shadow.py:448` `emit_shadow_decision` | decision log (`v2_shadow` rows) | the one #397 names |
| 2 | `strategy/kill_switch_v2_calibrator.py:603` `_compute_current_portfolio_dd` | `health.get_health_dashboard` (health.py:432), `get_dashboard_state` no-capital fallback (health.py:1217), degradation trigger (calibrator.py:452) | **sibling, same bug** |
| 3 | n/a — `get_dashboard_state` main path (health.py:1198) | `/health/dashboard` | **already correct, inline** |

Site 3 is the reference implementation. We extract it and make sites 1 + 2 use it. Fixing only site 1 would leave `get_health_dashboard` and the degradation trigger reporting an inflated-low DD.

The correct formula (ledger path) — verbatim from health.py:1160-1207:
- `open_mtm = Σ signed (price_now − entry) × qty` over open positions; skip `qty is None` (legacy_unmeasurable, #467); skip symbols with no price.
- `current_equity = balance + open_mtm`
- `peak_equity = max(ledger_peak, current_equity)` where `ledger_peak = peak_balance or balance`
- `dd = (peak_equity − current_equity) / peak_equity` if `peak_equity > 0` else `0.0`
- **Sign convention:** return `-dd` (negative in drawdown), matching `compute_portfolio_dd`.

## File Structure

- **Modify** `strategy/kill_switch_v2.py` — add pure helper `compute_portfolio_dd_from_ledger(...)` next to `compute_portfolio_equity_curve` / `compute_portfolio_dd`.
- **Modify** `strategy/kill_switch_v2_calibrator.py:559-615` — rewrite `_compute_current_portfolio_dd` internals to use the new helper (drop `closed_trades`). Signature unchanged.
- **Modify** `strategy/kill_switch_v2_shadow.py:425-454` — rewrite the capital-base + equity-curve block in `emit_shadow_decision` to use the new helper; add `dd_formula_version` to the `reasons` dict.
- **Modify** `health.py:1160-1207` — replace the inline ledger-DD block in `get_dashboard_state` with a call to the new helper (dedupe; behavior identical).
- **Test** `tests/test_strategy_kill_switch_v2.py` — unit tests for the pure helper.
- **Test** `tests/test_strategy_kill_switch_v2_calibrator.py` — `_compute_current_portfolio_dd` no longer double-counts.
- **Test** `tests/test_strategy_kill_switch_v2.py` (shadow section) — `emit_shadow_decision` persists the correct DD + version flag.
- **Test** `tests/test_health_dashboard.py` — existing `test_get_health_dashboard_no_double_count_on_realized_pnl_history` + `test_get_health_dashboard_isolates_tenants` must stay green after the dedupe.
- **Docs/GROW** `.mex/context/` + `.mex/patterns/` + the #397 issue + `mex log`.

## Branch setup

This plan runs on a fresh branch off `main` (current HEAD `dfc4caf`). Do NOT reuse `feat/278-deflated-metrics`.

```bash
git checkout main && git pull --ff-only
git checkout -b fix/397-shadow-dd-ledger
```

---

### Task 1: Pure helper `compute_portfolio_dd_from_ledger`

**Files:**
- Modify: `strategy/kill_switch_v2.py` (add after `compute_portfolio_dd`, ~line 187)
- Test: `tests/test_strategy_kill_switch_v2.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategy_kill_switch_v2.py`:

```python
def test_dd_from_ledger_flat_no_positions():
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=10_000.0, peak_balance=10_000.0,
        open_positions=[], now_price_by_symbol={},
    )
    assert out["current_equity"] == pytest.approx(10_000.0)
    assert out["peak_equity"] == pytest.approx(10_000.0)
    assert out["portfolio_dd"] == pytest.approx(0.0)


def test_dd_from_ledger_does_not_double_count_closed_pnl():
    # balance already includes realized PnL; with no open positions the DD
    # must be 0 relative to the ledger peak — NOT inflated by re-applying trades.
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=10_200.0, peak_balance=10_400.0,
        open_positions=[], now_price_by_symbol={},
    )
    assert out["current_equity"] == pytest.approx(10_200.0)
    assert out["peak_equity"] == pytest.approx(10_400.0)
    assert out["portfolio_dd"] == pytest.approx(-(10_400.0 - 10_200.0) / 10_400.0)


def test_dd_from_ledger_open_long_loss_drives_drawdown():
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=10_000.0, peak_balance=10_000.0,
        open_positions=[{"symbol": "BTC", "entry_price": 100.0, "qty": 10.0, "direction": "LONG"}],
        now_price_by_symbol={"BTC": 90.0},  # -100 MTM
    )
    assert out["current_equity"] == pytest.approx(9_900.0)
    assert out["peak_equity"] == pytest.approx(10_000.0)
    assert out["portfolio_dd"] == pytest.approx(-100.0 / 10_000.0)


def test_dd_from_ledger_open_short_gain_lifts_peak():
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=10_000.0, peak_balance=10_000.0,
        open_positions=[{"symbol": "ETH", "entry_price": 100.0, "qty": 5.0, "direction": "SHORT"}],
        now_price_by_symbol={"ETH": 80.0},  # +100 MTM
    )
    assert out["current_equity"] == pytest.approx(10_100.0)
    assert out["peak_equity"] == pytest.approx(10_100.0)  # unrealized gain lifts peak
    assert out["portfolio_dd"] == pytest.approx(0.0)


def test_dd_from_ledger_skips_legacy_unmeasurable_qty_none():
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=10_000.0, peak_balance=10_000.0,
        open_positions=[{"symbol": "BTC", "entry_price": 100.0, "qty": None, "direction": "LONG"}],
        now_price_by_symbol={"BTC": 50.0},
    )
    # qty=None row excluded from MTM (not coerced to 0 exposure silently).
    assert out["current_equity"] == pytest.approx(10_000.0)
    assert out["portfolio_dd"] == pytest.approx(0.0)


def test_dd_from_ledger_peak_balance_none_falls_back_to_balance():
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=9_000.0, peak_balance=None,
        open_positions=[], now_price_by_symbol={},
    )
    assert out["peak_equity"] == pytest.approx(9_000.0)
    assert out["portfolio_dd"] == pytest.approx(0.0)


def test_dd_from_ledger_skips_symbol_without_price():
    from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
    out = compute_portfolio_dd_from_ledger(
        balance=10_000.0, peak_balance=10_000.0,
        open_positions=[{"symbol": "DOGE", "entry_price": 1.0, "qty": 100.0, "direction": "LONG"}],
        now_price_by_symbol={},  # no price for DOGE
    )
    assert out["current_equity"] == pytest.approx(10_000.0)
    assert out["portfolio_dd"] == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_strategy_kill_switch_v2.py -k dd_from_ledger -v`
Expected: FAIL — `ImportError: cannot import name 'compute_portfolio_dd_from_ledger'`

- [ ] **Step 3: Write the implementation**

Add to `strategy/kill_switch_v2.py` immediately after `compute_portfolio_dd` (after line 186):

```python
def compute_portfolio_dd_from_ledger(
    *,
    balance: float,
    peak_balance: float | None,
    open_positions: list[dict[str, Any]],
    now_price_by_symbol: dict[str, float],
) -> dict[str, float]:
    """Portfolio drawdown from the capital ledger + open-position MTM.

    The capital ledger `balance` already folds every closed trade's realized
    PnL (via apply_pnl_to_capital). The correct live equity is therefore
    `balance + open_mtm` — NOT `balance + Σ_closed_trades + open_mtm`, which
    double-counts the closed PnL and under-reports drawdown (#397).

    This is the single source of truth extracted from get_dashboard_state's
    ledger path. Pure: no DB access, no price-cache reads — caller supplies
    balance, peak, positions, and prices.

    Args:
        balance: tenant's ledger balance (realized PnL already applied).
        peak_balance: tenant's monotonic ledger peak; falls back to `balance`
            when None (pre-onboarding tenants).
        open_positions: [{"symbol", "entry_price", "qty", "direction"}]. Rows
            with `qty is None` are quarantined legacy_unmeasurable (#467) and
            excluded from MTM rather than silently counted as zero exposure.
        now_price_by_symbol: current price per symbol; symbols without a price
            are skipped.

    Returns:
        {"portfolio_dd", "current_equity", "peak_equity"}.
        portfolio_dd is NEGATIVE when in drawdown, 0.0 otherwise — same sign
        convention as compute_portfolio_dd, so evaluate_portfolio_tier reads
        the threshold identically.
    """
    open_mtm = 0.0
    for pos in open_positions:
        sym = pos.get("symbol")
        if not sym or sym not in now_price_by_symbol:
            continue
        raw_qty = pos.get("qty")
        if raw_qty is None:
            log.warning(
                "compute_portfolio_dd_from_ledger: skipping legacy_unmeasurable "
                "position from open_mtm sym=%s", sym,
            )
            continue
        entry = float(pos.get("entry_price") or 0)
        qty = float(raw_qty)
        direction = pos.get("direction", "LONG")
        price_now = float(now_price_by_symbol[sym])
        if direction == "SHORT":
            open_mtm += (entry - price_now) * qty
        else:
            open_mtm += (price_now - entry) * qty

    current_equity = float(balance) + open_mtm
    ledger_peak = float(peak_balance) if peak_balance is not None else float(balance)
    peak_equity = max(ledger_peak, current_equity)
    dd = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
    return {
        "portfolio_dd": -dd,
        "current_equity": current_equity,
        "peak_equity": peak_equity,
    }
```

Confirm `log` and `Any` are already in scope in this module (they are: `log = logging.getLogger(...)` and `from typing import Any`). If not, add them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_strategy_kill_switch_v2.py -k dd_from_ledger -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add strategy/kill_switch_v2.py tests/test_strategy_kill_switch_v2.py
git commit -m "feat(ks-v2): pure compute_portfolio_dd_from_ledger helper (Advances #397)"
```

---

### Task 2: Fix the sibling `_compute_current_portfolio_dd` (no double-count)

This function feeds `get_health_dashboard` (health.py:432), the `get_dashboard_state` no-capital fallback (health.py:1217), and the degradation trigger (calibrator.py:452). Rewrite its internals to use the ledger helper. **Signature unchanged** so all three callers are fixed without edits.

**Files:**
- Modify: `strategy/kill_switch_v2_calibrator.py:559-615`
- Test: `tests/test_strategy_kill_switch_v2_calibrator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_strategy_kill_switch_v2_calibrator.py` (uses the project's standard fresh-DB monkeypatch pattern — mirror the existing fixtures in that file for DB setup; if the file already has a `fresh_db`/`client` style fixture, reuse it):

```python
def test_compute_current_portfolio_dd_does_not_double_count_closed_trades(fresh_db):
    """#397: closed trades are already folded into capital.balance. The live
    DD must be computed as balance + open_mtm, never balance + Σ_closed.

    Seed balance=$10,200, peak=$10,400 and ALSO insert the closed positions
    that produced that PnL. With no open positions the DD must reflect only
    the ledger peak-to-current gap (≈ -1.923%), not an inflated curve."""
    from db.capital import db_upsert_capital
    from db.transaction import transaction
    from strategy.kill_switch_v2_calibrator import _compute_current_portfolio_dd

    cfg = {"capital_usd": 1000.0}
    with transaction() as con:
        db_upsert_capital(con, 1, balance=10_200.0, peak_balance=10_400.0)
        for i, pnl in enumerate([200.0, 200.0, -300.0, 200.0, -100.0]):
            con.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts, qty,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, tenant_id)
                   VALUES ('BTC','LONG','closed',100.0,?,1.0,?,?,?,?,?,1)""",
                (f"2026-04-2{i+1}T12:00:00+00:00", 100.0 + pnl/10.0,
                 f"2026-04-2{i+1}T13:00:00+00:00", "TP" if pnl > 0 else "SL",
                 pnl, pnl/100.0),
            )

    dd = _compute_current_portfolio_dd(cfg, tenant_id=1)
    assert dd == pytest.approx(-(10_400.0 - 10_200.0) / 10_400.0, abs=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_kill_switch_v2_calibrator.py -k does_not_double_count -v`
Expected: FAIL — current code returns an inflated (closer-to-zero or wrong-magnitude) DD because it re-applies the 5 closed trades on top of balance.

- [ ] **Step 3: Rewrite the implementation**

Replace the body of `_compute_current_portfolio_dd` (calibrator.py:575-609, the `try:` block up to the `return`) with:

```python
    try:
        from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
        from strategy.kill_switch_v2_shadow import (
            _load_open_positions, _snapshot_prices,
        )
        from db.capital import db_get_capital
        # Task 5 (#446): `db_get_capital` requires `con` positional.
        # When the caller did not pass `conn`, open a short read-only tx.
        if conn is not None:
            cap_row = db_get_capital(conn, tenant_id)
            opens = _load_open_positions(conn, tenant_id=tenant_id)
        else:
            from db.transaction import transaction as _tx
            with _tx() as _c:
                cap_row = db_get_capital(_c, tenant_id)
                opens = _load_open_positions(_c, tenant_id=tenant_id)

        if cap_row and cap_row.get("balance") is not None:
            balance = float(cap_row["balance"])
            peak_balance = cap_row.get("peak_balance")
        else:
            # No ledger row (pre-onboarding): cfg default, no peak history.
            balance = float(cfg.get("capital_usd", 1000.0))
            peak_balance = None

        result = compute_portfolio_dd_from_ledger(
            balance=balance,
            peak_balance=(float(peak_balance) if peak_balance is not None else None),
            open_positions=opens,
            now_price_by_symbol=_snapshot_prices(),
        )
        return float(result["portfolio_dd"])
    except Exception as e:
        log.warning(
            "compute_current_portfolio_dd failed for tenant_id=%s: %s",
            tenant_id, e, exc_info=True,
        )
        return 0.0
```

Then update the docstring line that says `Reuses kill_switch_v2.compute_portfolio_dd + compute_portfolio_equity_curve.` to:
`Reuses kill_switch_v2.compute_portfolio_dd_from_ledger (ledger + open MTM; closed PnL already in balance — see #397).`

Remove the now-unused `_load_closed_trades` import if it appears in this function (it does at calibrator.py:580 — drop it from this function's import line; verify no other use in the function).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_kill_switch_v2_calibrator.py -k does_not_double_count -v`
Expected: PASS

- [ ] **Step 5: Run the calibrator + health suites to confirm no regression**

Run: `python -m pytest tests/test_strategy_kill_switch_v2_calibrator.py tests/test_health_dashboard.py -v`
Expected: PASS (all). The `test_get_health_dashboard_*` tests exercise the `get_health_dashboard` path that calls this helper.

- [ ] **Step 6: Commit**

```bash
git add strategy/kill_switch_v2_calibrator.py tests/test_strategy_kill_switch_v2_calibrator.py
git commit -m "fix(ks-v2): _compute_current_portfolio_dd uses ledger, no closed-trade double-count (Advances #397)"
```

---

### Task 3: Fix `emit_shadow_decision` + tag decision-log rows

**Files:**
- Modify: `strategy/kill_switch_v2_shadow.py:425-454` (capital base + equity-curve block) and the `reasons` dict at ~532
- Test: `tests/test_strategy_kill_switch_v2.py` (shadow section)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_strategy_kill_switch_v2.py` (reuse the file's existing DB fixture; if it monkeypatches `btc_api.DB_FILE` like the other suites, follow that):

```python
def test_emit_shadow_decision_persists_ledger_dd_not_inflated(fresh_db):
    """#397: the portfolio_dd written to the decision log must be the ledger
    DD (balance + open_mtm), not the inflated curve that re-applies closed
    trades. Seed balance=$10,200 / peak=$10,400 with matching closed trades
    and no open positions → persisted portfolio_dd ≈ -1.923%, and the row
    carries dd_formula_version."""
    import json
    from db.capital import db_upsert_capital
    from db.transaction import transaction, snapshot_connection
    from strategy.kill_switch_v2_shadow import emit_shadow_decision

    cfg = {"capital_usd": 1000.0, "kill_switch": {"v2": {"aggressiveness": 50.0}}}
    with transaction() as con:
        db_upsert_capital(con, 1, balance=10_200.0, peak_balance=10_400.0)
        for i, pnl in enumerate([200.0, 200.0, -300.0, 200.0, -100.0]):
            con.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts, qty,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, tenant_id)
                   VALUES ('BTC','LONG','closed',100.0,?,1.0,?,?,?,?,?,1)""",
                (f"2026-04-2{i+1}T12:00:00+00:00", 100.0 + pnl/10.0,
                 f"2026-04-2{i+1}T13:00:00+00:00", "TP" if pnl > 0 else "SL",
                 pnl, pnl/100.0),
            )

    emit_shadow_decision("BTC", cfg, tenant_id=1)

    with snapshot_connection() as con:
        row = con.execute(
            """SELECT reasons_json FROM kill_switch_decisions
               WHERE engine='v2_shadow' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    assert row is not None, "no v2_shadow decision row written"
    reasons = json.loads(row["reasons_json"])
    assert reasons["portfolio_dd"] == pytest.approx(
        -(10_400.0 - 10_200.0) / 10_400.0, abs=1e-6
    )
    assert reasons.get("dd_formula_version") == "ledger_v1"
```

NOTE for the implementer: confirm the decision-log table/column names by reading `observability.record_decision` and the `kill_switch_decisions` schema in `db/schema.py` before running — adjust the SELECT (`reasons_json`, `engine`, table name) to match exactly. The assertion logic stays the same.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy_kill_switch_v2.py -k persists_ledger_dd -v`
Expected: FAIL — persisted `portfolio_dd` is the inflated curve value and `dd_formula_version` is absent.

- [ ] **Step 3: Rewrite the implementation**

In `strategy/kill_switch_v2_shadow.py`, in `emit_shadow_decision`:

(a) Replace the capital-base + loads + equity-curve block (lines ~425-454, from the `# Capital base for this tenant:` comment through `portfolio_dd = compute_portfolio_dd(equity_curve)`) with:

```python
        # Ledger-based DD (#397): balance already folds realized PnL; the live
        # equity is balance + open MTM. Do NOT walk closed trades again (that
        # double-counts and under-reports DD). Single source of truth:
        # kill_switch_v2.compute_portfolio_dd_from_ledger (same as the dashboard).
        from db.capital import db_get_capital
        from db.transaction import transaction as _tx
        from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
        with _tx() as _con:
            _capital_row = db_get_capital(_con, tenant_id)
            opens = _load_open_positions(_con, tenant_id=tenant_id)
        if _capital_row and _capital_row.get("balance") is not None:
            _balance = float(_capital_row["balance"])
            _peak = _capital_row.get("peak_balance")
        else:
            _balance = float(cfg.get("capital_usd", _DEFAULT_CAPITAL_USD))
            _peak = None
        prices = _snapshot_prices()
        if now_price_by_symbol:
            prices.update(now_price_by_symbol)

        _dd_result = compute_portfolio_dd_from_ledger(
            balance=_balance,
            peak_balance=(float(_peak) if _peak is not None else None),
            open_positions=opens,
            now_price_by_symbol=prices,
        )
        portfolio_dd = _dd_result["portfolio_dd"]
        concurrent = _count_concurrent_failures()
```

This removes the `compute_portfolio_equity_curve` + `_load_closed_trades` usage from this function. Also remove `compute_portfolio_equity_curve` and `compute_portfolio_dd` from the function's top `from strategy.kill_switch_v2 import (...)` block (lines ~397-402) if they are no longer referenced anywhere else in the function — verify with a grep inside the function body first; keep `evaluate_portfolio_tier` and `classify_regime`.

(b) In the `reasons={...}` dict passed to `observability.record_decision` (~line 532), add the version flag as the first key:

```python
            reasons={
                "dd_formula_version": "ledger_v1",
                "portfolio_dd": portfolio_dd,
                "reduced_threshold": portfolio["reduced_threshold"],
                ...
```

(keep all existing keys unchanged).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_kill_switch_v2.py -k persists_ledger_dd -v`
Expected: PASS

- [ ] **Step 5: Run the full kill-switch v2 suite**

Run: `python -m pytest tests/test_strategy_kill_switch_v2.py -v`
Expected: PASS (all). Watch for any test asserting on the old curve shape from `emit_shadow_decision`; if one breaks because it asserted the inflated value, that test was pinning the bug — update it to the ledger value and note it in the commit body.

- [ ] **Step 6: Commit**

```bash
git add strategy/kill_switch_v2_shadow.py tests/test_strategy_kill_switch_v2.py
git commit -m "fix(ks-v2): emit_shadow_decision persists ledger DD + dd_formula_version (Closes #397 core)"
```

---

### Task 4: Dedupe — route `get_dashboard_state` inline block through the helper

The inline block at health.py:1160-1207 is the reference implementation. Replace it with a call to the new helper so there is exactly one copy of the formula. Behavior must be identical — the existing regression tests are the guard.

**Files:**
- Modify: `health.py:1155-1207`
- Test: `tests/test_health_dashboard.py` (existing tests, no new ones needed — they pin the behavior)

- [ ] **Step 1: Confirm the guard tests are green before refactor**

Run: `python -m pytest tests/test_health_dashboard.py -k "no_double_count or isolates_tenants" -v`
Expected: PASS (these must already pass on main; they protect the refactor).

- [ ] **Step 2: Refactor the inline block**

Replace health.py lines 1162-1207 (the `try:` open-MTM loop through `portfolio_dd = -portfolio_dd`) inside the `if capital is not None and capital.get("balance") is not None:` branch with:

```python
            try:
                from strategy.kill_switch_v2_shadow import (
                    _load_open_positions, _snapshot_prices,
                )
                from strategy.kill_switch_v2 import compute_portfolio_dd_from_ledger
                open_positions = _load_open_positions(conn, tenant_id=tenant_id)
                _dd = compute_portfolio_dd_from_ledger(
                    balance=realized_balance,
                    peak_balance=ledger_peak,
                    open_positions=open_positions,
                    now_price_by_symbol=_snapshot_prices(),
                )
                current_equity = _dd["current_equity"]
                peak_equity = _dd["peak_equity"]
                portfolio_dd = _dd["portfolio_dd"]
            except Exception:
                log.warning(
                    "get_dashboard_state ledger-DD computation failed; "
                    "treating as flat", exc_info=True,
                )
                current_equity = realized_balance
                peak_equity = ledger_peak
                portfolio_dd = 0.0
```

Keep the preceding lines that compute `realized_balance` and `ledger_peak` (1156-1158) — the helper consumes them. The big explanatory comment block at 1140-1154 stays (it documents the ledger-vs-curve decision); optionally trim it to one line pointing at the helper, but that is not required.

- [ ] **Step 3: Run the guard tests**

Run: `python -m pytest tests/test_health_dashboard.py -v`
Expected: PASS (all). `current_equity=10_200`, `peak_equity=10_400`, `dd_pct≈-0.01923` exactly as before.

- [ ] **Step 4: Commit**

```bash
git add health.py
git commit -m "refactor(health): get_dashboard_state ledger DD via shared helper, single source of truth (Advances #397)"
```

---

### Task 5: GROW — docs, pattern, issue, mex log

**Files:**
- Modify: `.mex/context/architecture.md` (Key Backend Logic / kill-switch section) — note that portfolio DD has ONE canonical computation: `compute_portfolio_dd_from_ledger`, ledger-based, used by shadow + calibrator + dashboard.
- Create or modify: `.mex/patterns/` — if no pattern covers portfolio-DD computation, add `computing-portfolio-dd.md` (rule: ledger balance + open MTM, never re-walk closed trades; cite #397) and add it to `.mex/patterns/INDEX.md`. If a kill-switch pattern exists, edit it surgically instead.
- Modify: the #397 issue — check off the completed boxes and note the decision-log decision (new rows tagged `dd_formula_version: ledger_v1`; historical rows untouched; consumers distinguish by flag presence).

- [ ] **Step 1: Update architecture context**

Add a short subsection to `.mex/context/architecture.md` under the kill-switch material:

```markdown
### Portfolio drawdown — single source of truth

`strategy.kill_switch_v2.compute_portfolio_dd_from_ledger(balance, peak_balance,
open_positions, now_price_by_symbol)` is the ONE canonical live-DD computation.
`current_equity = balance + open_mtm`; closed PnL is already folded into the
capital ledger `balance` (apply_pnl_to_capital), so it is NEVER re-applied.
Consumers: `emit_shadow_decision`, `_compute_current_portfolio_dd`,
`get_dashboard_state`. Re-walking closed trades double-counts and under-reports
DD — the #397 bug. Do not reintroduce `compute_portfolio_equity_curve(capital_base=
balance, closed_trades=...)` on a live path.
```

- [ ] **Step 2: Add/update the pattern + INDEX**

If creating `.mex/patterns/computing-portfolio-dd.md`, give it the standard pattern shape (Context / Rule / Steps / Anti-pattern) with the rule above, then add a one-line entry to `.mex/patterns/INDEX.md`.

- [ ] **Step 3: Update the issue**

```bash
gh issue comment 397 --body "Fixed in branch fix/397-shadow-dd-ledger: extracted compute_portfolio_dd_from_ledger as the single canonical live-DD computation; routed emit_shadow_decision, _compute_current_portfolio_dd (the sibling with the same double-count bug), and get_dashboard_state through it. Decision-log policy: new v2_shadow rows tagged dd_formula_version=ledger_v1; historical rows left untouched (no 199k-row migration) — consumers distinguish by flag presence. Shadow→active stays gated until this lands on main."
```

- [ ] **Step 4: mex log**

```bash
mex log "fix #397: ledger-based portfolio DD; one canonical helper, 3 sites deduped (shadow, calibrator, dashboard)"
```

- [ ] **Step 5: Commit**

```bash
git add .mex/
git commit -m "docs(mex): portfolio DD single-source-of-truth pattern + context (Advances #397)"
```

---

### Task 6: Full suite + adversarial audit gate

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. If any pre-existing failures appear, confirm they are on the orthogonal-flake list (`.mex/context/ci-discipline.md`) before proceeding — do not absorb unrelated red.

- [ ] **Step 2: Adversarial audit of the math (per the chosen process)**

Per `adversarial-audit-before-push` (this is a guardrail/risk computation that gates a safety organ): commit all work locally, then dispatch an **independent** adversarial audit (4 lenses, default-to-suspicion) focused on:
1. **Sign convention** — does `portfolio_dd` stay negative-in-drawdown across all 3 sites and feed `evaluate_portfolio_tier` correctly? (A flipped sign makes the kill-switch MORE permissive — the exact failure #397 warns about.)
2. **Double-count regression** — is there ANY remaining live path that calls `compute_portfolio_equity_curve` with `capital_base=balance` + `closed_trades`? Grep to prove zero.
3. **Tenant isolation** — do all three sites still pass `tenant_id` to `_load_open_positions` / `db_get_capital`? No cross-tenant leak.
4. **Fail-open behavior** — on exception, does each site degrade to a CONSERVATIVE value (0.0 DD = no false freeze, but also no false permissiveness)? Confirm the catch blocks match intent.

The auditor must be a separate subagent from the implementer. Apply fixes, re-run the suite, then push.

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin fix/397-shadow-dd-ledger
gh pr create --title "fix(ks-v2): ledger-based portfolio DD — close residual inflation (Closes #397)" \
  --body "<summary: 3-site dedupe, single canonical helper, decision-log version flag, audit notes>"
```

- [ ] **Step 4: Verify CI green, then report to the user for merge authorization**

Do NOT admin-merge. Wait for the clean gate or, if a failure appears, follow the CLAUDE.md §7 admin-merge ritual (full failure summary, orthogonal-flake confirmation, mex-log the bypass). Report status and ask before merging.

---

## Self-Review

**Spec coverage (issue #397 checkboxes):**
- [x] "Refactor emit_shadow_decision so persisted portfolio_dd reflects real DD" → Task 3.
- [x] "Decide decision-log migration: recompute vs new-only" → new-only + `dd_formula_version` flag (Tasks 3 & 5).
- [x] "Regression test equivalent to the dashboard no-double-count test, against the shadow output" → Task 3 Step 1.
- [x] "Block shadow→active promotion until closed" → documented in the issue comment (Task 5 Step 3) and the architecture note; the code fix itself removes the blocker once merged.
- [x] Sibling bug in `_compute_current_portfolio_dd` (not in the issue text but the same root cause feeding the live dashboard) → Task 2.

**Placeholder scan:** No TBD/TODO; every code step shows full code. The two NOTEs (calibrator fixture reuse in Task 2, decision-log column names in Task 3) instruct the implementer to verify exact names against source before running — these are verification steps, not missing content; the assertions and logic are complete.

**Type consistency:** `compute_portfolio_dd_from_ledger` returns a dict `{"portfolio_dd","current_equity","peak_equity"}` in Task 1 and is consumed with those exact keys in Tasks 2, 3, 4. `_compute_current_portfolio_dd` keeps its signature `(cfg, *, tenant_id, conn=None) -> float`. Sign convention (negative-in-drawdown) consistent across all sites.
