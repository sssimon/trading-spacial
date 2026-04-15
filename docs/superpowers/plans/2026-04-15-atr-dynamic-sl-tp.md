# ATR Dynamic SL/TP + Trailing Ratchet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed 2%/4% SL/TP with ATR(14)-based dynamic levels and add trailing ratchet stop to breakeven.

**Architecture:** Add `calc_atr()` to scanner, use ATR multipliers for SL/TP in `scan()`, store ATR at entry in positions table, implement trailing ratchet in `check_position_stops()`, update frontend to display dynamic values and breakeven badge.

**Tech Stack:** Python (pandas/numpy), SQLite, React/TypeScript, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `btc_scanner.py` | Add `calc_atr()`, new constants, ATR-based sizing in `scan()` |
| Modify | `btc_api.py` | DB migration, trailing ratchet in `check_position_stops()`, config params |
| Modify | `tests/test_scanner.py` | Tests for `calc_atr()` |
| Modify | `tests/test_api.py` | Tests for trailing ratchet and ATR config |
| Modify | `frontend/src/types.ts` | Add `atr_entry` to Position type |
| Modify | `frontend/src/components/PositionsPanel.tsx` | Breakeven badge |
| Modify | `frontend/src/components/ChartModal.tsx` | ATR/SL/TP chips |
| Modify | `backtest.py` | ATR mode with `--sl-mode` flag |

---

### Task 1: Add `calc_atr()` to scanner with tests (TDD)

**Files:**
- Modify: `btc_scanner.py:63-88` (constants section + new function after `calc_sma`)
- Modify: `tests/test_scanner.py`

- [ ] **Step 1: Write failing tests for `calc_atr()`**

Add to `tests/test_scanner.py`:

```python
class TestCalcATR:
    def _make_df(self, n=30):
        """Create a DataFrame with known high/low/close for ATR testing."""
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        high = close + np.abs(np.random.randn(n) * 0.3)
        low = close - np.abs(np.random.randn(n) * 0.3)
        return pd.DataFrame({"high": high, "low": low, "close": close})

    def test_retorna_series(self):
        from btc_scanner import calc_atr
        df = self._make_df()
        atr = calc_atr(df, period=14)
        assert isinstance(atr, pd.Series)
        assert len(atr) == len(df)

    def test_valores_positivos(self):
        from btc_scanner import calc_atr
        df = self._make_df()
        atr = calc_atr(df, period=14)
        valid = atr.dropna()
        assert (valid > 0).all()

    def test_primeros_nan(self):
        from btc_scanner import calc_atr
        df = self._make_df()
        atr = calc_atr(df, period=14)
        assert pd.isna(atr.iloc[0])

    def test_periodo_custom(self):
        from btc_scanner import calc_atr
        df = self._make_df(50)
        atr7 = calc_atr(df, period=7)
        atr21 = calc_atr(df, period=21)
        # Shorter period should have fewer leading NaNs
        assert atr7.dropna().iloc[0] != atr21.dropna().iloc[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scanner.py::TestCalcATR -v`
Expected: FAIL with "cannot import name 'calc_atr'"

- [ ] **Step 3: Implement `calc_atr()` and add constants**

In `btc_scanner.py`, add after `VOL_PERIOD = 20` (line 69):

```python
ATR_PERIOD     = 14
ATR_SL_MULT    = 1.5    # SL = entry - 1.5x ATR
ATR_TP_MULT    = 3.0    # TP = entry + 3.0x ATR (mantiene ratio 2:1)
ATR_BE_MULT    = 1.5    # Mover SL a breakeven cuando profit >= 1.5x ATR
```

Add after `calc_sma()` function (after line 369):

```python
def calc_atr(df: pd.DataFrame, period=14) -> pd.Series:
    """Average True Range — mide la volatilidad real del mercado."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scanner.py::TestCalcATR -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add btc_scanner.py tests/test_scanner.py
git commit -m "feat: add calc_atr() indicator function with tests"
```

---

### Task 2: Replace fixed SL/TP with ATR in `scan()`

**Files:**
- Modify: `btc_scanner.py:612-678` (sizing block)

- [ ] **Step 1: Write failing test for ATR-based SL/TP in scan output**

Add to `tests/test_scanner.py` in `TestScan` class:

```python
def test_scan_sizing_uses_atr(self):
    rep = scan("BTCUSDT")
    sz = rep["sizing_1h"]
    # Should have ATR fields
    assert "atr_1h" in sz
    assert "sl_mode" in sz
    assert sz["atr_1h"] > 0
    assert sz["sl_mode"] == "atr"
    # SL/TP should be calculated from ATR, not fixed percentage
    assert "sl_precio" in sz
    assert "tp_precio" in sz
    # SL distance should be approximately 1.5x ATR
    sl_dist = rep["price"] - sz["sl_precio"]
    assert abs(sl_dist - sz["atr_1h"] * 1.5) < 1.0  # within $1 rounding
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scanner.py::TestScan::test_scan_sizing_uses_atr -v`
Expected: FAIL with KeyError 'atr_1h'

- [ ] **Step 3: Modify sizing block in `scan()`**

Replace lines 612-624 in `btc_scanner.py`:

```python
    # ── Sizing informativo (1H Spot) ──────────────────────────────────────────
    atr_val    = float(calc_atr(df1h, ATR_PERIOD).iloc[-1])
    capital    = 1000.0
    risk_usd   = capital * 0.01

    # ATR-based SL/TP (adaptativo a volatilidad)
    sl_dist    = atr_val * ATR_SL_MULT
    tp_dist    = atr_val * ATR_TP_MULT
    sl_price   = round(price - sl_dist, 2)
    tp_price   = round(price + tp_dist, 2)
    sl_pct_val = round(sl_dist / price * 100, 2)
    tp_pct_val = round(tp_dist / price * 100, 2)

    qty_btc    = risk_usd / sl_dist
    val_pos    = qty_btc * price
    # Spot: valor posición no puede superar 98% del capital
    if val_pos > capital * 0.98:
        qty_btc = (capital * 0.98) / price
        val_pos  = qty_btc * price
```

Update the `sizing_1h` dict in the report (lines 668-678):

```python
        "sizing_1h": {
            "capital_usd": capital,
            "riesgo_usd":  round(risk_usd, 2),
            "atr_1h":      round(atr_val, 2),
            "sl_mode":     "atr",
            "sl_pct":      f"{sl_pct_val}%",
            "tp_pct":      f"{tp_pct_val}%",
            "sl_precio":   sl_price,
            "tp_precio":   tp_price,
            "qty_btc":     round(qty_btc, 6),
            "valor_pos":   round(val_pos, 2),
            "pct_capital": round(val_pos / capital * 100, 1),
        },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scanner.py::TestScan -v`
Expected: ALL TestScan tests PASS

- [ ] **Step 5: Run full scanner test suite**

Run: `python -m pytest tests/test_scanner.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add btc_scanner.py tests/test_scanner.py
git commit -m "feat: replace fixed SL/TP with ATR-based dynamic levels in scan()"
```

---

### Task 3: DB migration + trailing ratchet in `check_position_stops()`

**Files:**
- Modify: `btc_api.py:772-791` (positions table schema)
- Modify: `btc_api.py:535-584` (`check_position_stops`)
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests for trailing ratchet**

Add to `tests/test_api.py` in `TestPositionsCRUD` class:

```python
def test_trailing_ratchet_moves_sl_to_breakeven(self):
    """When price rises >= 1.5x ATR above entry, SL moves to entry (breakeven)."""
    import btc_api
    pos = btc_api.db_create_position({
        "symbol": "BTCUSDT",
        "entry_price": 60000.0,
        "sl_price": 59000.0,
        "tp_price": 63000.0,
        "direction": "LONG",
        "atr_entry": 666.67,  # 1.5x ATR = 1000
    })
    # Price rises to entry + 1.5*ATR = 60000 + 1000 = 61000
    btc_api.check_position_stops("BTCUSDT", 61000.0)
    # Position should still be open, but SL moved to breakeven
    updated = btc_api.db_get_positions(status="open")
    assert len(updated) == 1
    assert updated[0]["sl_price"] == 60000.0  # moved to entry price

def test_trailing_ratchet_never_lowers_sl(self):
    """SL should only go up (tighten), never down."""
    import btc_api
    pos = btc_api.db_create_position({
        "symbol": "BTCUSDT",
        "entry_price": 60000.0,
        "sl_price": 60000.0,  # already at breakeven
        "tp_price": 63000.0,
        "direction": "LONG",
        "atr_entry": 666.67,
    })
    # Price drops — SL should NOT move down
    btc_api.check_position_stops("BTCUSDT", 60500.0)
    updated = btc_api.db_get_positions(status="open")
    assert len(updated) == 1
    assert updated[0]["sl_price"] == 60000.0  # unchanged

def test_position_without_atr_skips_trailing(self):
    """Legacy positions without atr_entry skip trailing logic."""
    import btc_api
    pos = btc_api.db_create_position({
        "symbol": "BTCUSDT",
        "entry_price": 60000.0,
        "sl_price": 58800.0,
        "tp_price": 62400.0,
        "direction": "LONG",
    })
    btc_api.check_position_stops("BTCUSDT", 61500.0)
    updated = btc_api.db_get_positions(status="open")
    assert len(updated) == 1
    assert updated[0]["sl_price"] == 58800.0  # unchanged, no trailing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestPositionsCRUD::test_trailing_ratchet_moves_sl_to_breakeven -v`
Expected: FAIL (atr_entry column doesn't exist or not handled)

- [ ] **Step 3: Add `atr_entry` column to positions table**

In `btc_api.py`, in `init_db()` positions schema (line 790), add before `notes`:

```python
            atr_entry   REAL,
            notes       TEXT
```

Also add migration for existing DBs — after `con.commit()` in `init_db()`:

```python
    # Migrate: add atr_entry column if missing
    try:
        con_mig = get_db()
        cols = [r[1] for r in con_mig.execute("PRAGMA table_info(positions)").fetchall()]
        if "atr_entry" not in cols:
            con_mig.execute("ALTER TABLE positions ADD COLUMN atr_entry REAL")
            con_mig.commit()
            log.info("DB migration: added atr_entry column to positions")
        con_mig.close()
    except Exception as e:
        log.warning(f"DB migration check: {e}")
```

- [ ] **Step 4: Update `db_create_position()` to accept `atr_entry`**

In the `db_create_position` function, add `atr_entry` to the INSERT statement. Find the INSERT and add the field:

```python
    atr_entry = data.get("atr_entry")
```

Add to the INSERT column list and values.

- [ ] **Step 5: Implement trailing ratchet in `check_position_stops()`**

In `btc_api.py`, in `check_position_stops()`, add BEFORE the existing SL/TP check logic (before line 549):

```python
        # Trailing ratchet: move SL to breakeven when profit >= ATR_BE_MULT * ATR
        atr_entry = pos.get("atr_entry")
        if atr_entry and pos["direction"] == "LONG" and pos["sl_price"]:
            be_threshold = pos["entry_price"] + atr_entry * 1.5  # ATR_BE_MULT
            if price >= be_threshold and pos["sl_price"] < pos["entry_price"]:
                new_sl = pos["entry_price"]
                con_trail = get_db()
                con_trail.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = ?",
                    (new_sl, pos["id"])
                )
                con_trail.commit()
                con_trail.close()
                pos["sl_price"] = new_sl
                log.info(f"Trailing: #{pos['id']} {symbol} SL → breakeven ${new_sl:.2f}")
        elif atr_entry and pos["direction"] == "SHORT" and pos["sl_price"]:
            be_threshold = pos["entry_price"] - atr_entry * 1.5
            if price <= be_threshold and pos["sl_price"] > pos["entry_price"]:
                new_sl = pos["entry_price"]
                con_trail = get_db()
                con_trail.execute(
                    "UPDATE positions SET sl_price = ? WHERE id = ?",
                    (new_sl, pos["id"])
                )
                con_trail.commit()
                con_trail.close()
                pos["sl_price"] = new_sl
                log.info(f"Trailing: #{pos['id']} {symbol} SL → breakeven ${new_sl:.2f}")
```

- [ ] **Step 6: Update `allowed` fields in `db_update_position()`**

In `btc_api.py` line 521, add `atr_entry` to the allowed set:

```python
    allowed = {"sl_price", "tp_price", "size_usd", "qty", "notes", "entry_price", "atr_entry"}
```

- [ ] **Step 7: Run trailing ratchet tests**

Run: `python -m pytest tests/test_api.py::TestPositionsCRUD::test_trailing_ratchet_moves_sl_to_breakeven tests/test_api.py::TestPositionsCRUD::test_trailing_ratchet_never_lowers_sl tests/test_api.py::TestPositionsCRUD::test_position_without_atr_skips_trailing -v`
Expected: 3 PASSED

- [ ] **Step 8: Run full API test suite**

Run: `python -m pytest tests/test_api.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add btc_api.py tests/test_api.py
git commit -m "feat: add trailing ratchet stop with ATR breakeven logic (fixes #114)"
```

---

### Task 4: Wire ATR from scanner to position creation

**Files:**
- Modify: `btc_api.py:830-850` (where scan results create positions)
- Modify: `btc_api.py:1080-1095` (webhook payload)

- [ ] **Step 1: Pass `atr_1h` through scan → notification → position**

In `btc_api.py`, find where `execute_scan_for_symbol` builds the webhook payload (around line 1089). Add:

```python
        "atr_1h":          rep.get("sizing_1h", {}).get("atr_1h"),
```

- [ ] **Step 2: In `execute_scan_for_symbol`, when auto-creating positions, pass `atr_entry`**

Find the section that calls `db_create_position` (if it exists) or where positions are created from scan results. Ensure `atr_entry` from `sizing_1h.atr_1h` is passed through.

- [ ] **Step 3: Update Telegram message to show ATR-based SL/TP**

In `build_telegram_message()`, the sizing section already uses `sz.get("sl_pct")` and `sz.get("tp_pct")` — these now contain ATR-derived percentages. Add ATR value:

Find the line with `SL / TP` in the Telegram message and append ATR info:

```python
f"ATR(14): ${sz.get('atr_1h', 'N/A')}"
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add btc_api.py
git commit -m "feat: wire ATR from scanner through to positions and Telegram messages"
```

---

### Task 5: Frontend — breakeven badge and ATR display

**Files:**
- Modify: `frontend/src/types.ts:134-152`
- Modify: `frontend/src/components/PositionsPanel.tsx`
- Modify: `frontend/src/components/ChartModal.tsx`

- [ ] **Step 1: Add `atr_entry` to Position type**

In `frontend/src/types.ts`, add to the `Position` interface after `notes`:

```typescript
  atr_entry:   number | null;
```

- [ ] **Step 2: Add breakeven badge to PositionsPanel**

In `frontend/src/components/PositionsPanel.tsx`, find where SL price is displayed in the position row. Add a "BE" badge when SL >= entry price:

```tsx
{pos.sl_price != null && pos.entry_price != null && pos.sl_price >= pos.entry_price && (
  <span style={{ 
    backgroundColor: '#22c55e', color: '#fff', 
    fontSize: '0.65rem', padding: '1px 4px', 
    borderRadius: '3px', marginLeft: '4px' 
  }}>BE</span>
)}
```

- [ ] **Step 3: Add ATR chip to ChartModal**

In `frontend/src/components/ChartModal.tsx`, find the score chip section (around line 304-307). Add ATR and SL/TP chips after the score:

```tsx
{symbol.sizing_1h?.atr_1h && (
  <>
    <div className="chart-chip">
      <span className="chart-chip-label">ATR</span>
      <span className="chart-chip-val">${symbol.sizing_1h.atr_1h.toLocaleString()}</span>
    </div>
    <div className="chart-chip">
      <span className="chart-chip-label">SL/TP</span>
      <span className="chart-chip-val">{symbol.sizing_1h.sl_pct} / {symbol.sizing_1h.tp_pct}</span>
    </div>
  </>
)}
```

- [ ] **Step 4: TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/PositionsPanel.tsx frontend/src/components/ChartModal.tsx
git commit -m "feat: add breakeven badge and ATR display to frontend"
```

---

### Task 6: Backtest with ATR mode

**Files:**
- Modify: `backtest.py`

- [ ] **Step 1: Add `--sl-mode` argument and ATR calculation**

In `backtest.py`, add to the argparse section:

```python
parser.add_argument("--sl-mode", default="atr", choices=["atr", "fixed"],
                    help="SL/TP mode: 'atr' (dynamic) or 'fixed' (2%%/4%%)")
```

- [ ] **Step 2: Modify `simulate_strategy()` to accept `sl_mode`**

Add `sl_mode: str = "atr"` parameter. In the entry logic, after computing score:

```python
if sl_mode == "atr":
    from btc_scanner import calc_atr, ATR_SL_MULT, ATR_TP_MULT, ATR_BE_MULT, ATR_PERIOD
    atr_series = calc_atr(window_1h, ATR_PERIOD)
    atr_val = float(atr_series.iloc[-1])
    if pd.isna(atr_val) or atr_val <= 0:
        continue
    sl_price = round(price - atr_val * ATR_SL_MULT, 2)
    tp_price = round(price + atr_val * ATR_TP_MULT, 2)
    be_threshold = price + atr_val * ATR_BE_MULT
else:
    sl_price = round(price * (1 - SL_PCT / 100), 2)
    tp_price = round(price * (1 + TP_PCT / 100), 2)
    be_threshold = None
```

- [ ] **Step 3: Add trailing ratchet to position exit logic**

In the SL/TP check section, before checking hits, add:

```python
if position.get("be_threshold") and bar["high"] >= position["be_threshold"]:
    if position["sl"] < position["entry_price"]:
        position["sl"] = position["entry_price"]  # breakeven
```

- [ ] **Step 4: Run backtest in both modes and compare**

```bash
python backtest.py --sl-mode atr
python backtest.py --sl-mode fixed
```

Compare results in the terminal output.

- [ ] **Step 5: Commit**

```bash
git add backtest.py
git commit -m "feat: add ATR mode to backtester with trailing ratchet comparison"
```

---

### Task 7: Config support and final integration

**Files:**
- Modify: `btc_scanner.py` (read config overrides)
- Modify: `btc_api.py` (pass config to scanner)

- [ ] **Step 1: Add config overrides for ATR multipliers**

In `btc_scanner.py`, in `scan()` function, before the sizing block, read config overrides:

```python
    # Config overrides para ATR (si existen)
    import json as _json
    _cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    _cfg = {}
    if os.path.exists(_cfg_path):
        try:
            with open(_cfg_path) as _f:
                _cfg = _json.load(_f)
        except Exception:
            pass
    _sl_mode = _cfg.get("sl_mode", "atr")
    _atr_sl = _cfg.get("atr_sl_mult", ATR_SL_MULT)
    _atr_tp = _cfg.get("atr_tp_mult", ATR_TP_MULT)
```

Use `_sl_mode`, `_atr_sl`, `_atr_tp` in the sizing block. If `_sl_mode == "fixed"`, fall back to `SL_PCT`/`TP_PCT`.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Run backtest to generate updated report**

```bash
python backtest.py --sl-mode atr
```

- [ ] **Step 4: Final commit**

```bash
git add btc_scanner.py btc_api.py backtest.py docs/
git commit -m "feat: ATR dynamic SL/TP fully integrated with config support (fixes #113)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```
