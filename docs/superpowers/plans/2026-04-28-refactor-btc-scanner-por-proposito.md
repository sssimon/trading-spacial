# Refactor btc_scanner.py por propósito — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break `btc_scanner.py` (1485 LOC) into per-purpose modules (`strategy/{regime,patterns,direction,tune,vol}`, `infra/http`, `cli/scanner_report`) preserving full backward compatibility via re-exports. Final `btc_scanner.py` ≈ 510-540 LOC (only `scan()` + setup + re-exports).

**Architecture:** Mirror the api+db refactor pattern from PR #226: each piece moves with re-exports preserved through PR8 cleanup. End-to-end `scan("BTCUSDT")` JSON snapshot + per-piece `is`-identity tests as the regression net. Pre/post-verify per task (every task in every PR runs the snapshot + suite gates before and after).

**Tech Stack:** Python 3, pytest, pandas/numpy, FastAPI (only for smoke tests).

**Spec:** `docs/superpowers/specs/es/2026-04-28-refactor-btc-scanner-por-proposito-design.md`

**Total PRs:** 9 (PR0 foundation + PR1-PR7 moves + PR8 cleanup).

---

## Operating principles (apply to every task)

1. **Pre-verify before every task:** run snapshot test + full suite. Don't start work on a red baseline.
2. **Post-verify before every commit:** snapshot still byte-equal + identity test green + full suite green.
3. **Re-export-first move:** add the import line to `btc_scanner.py` *before* deleting the original definition. Ensures no transient state where the function is undefined.
4. **One conceptual change per commit.** Mechanical moves only — no incidental cleanup.
5. **If snapshot drifts, STOP.** Investigate. Never silently regenerate.

---

## File Structure (target end state)

```
btc_scanner.py                         ~510-540 LOC: scan() + setup + re-exports
strategy/regime.py                     NEW — detect_regime, get_cached_regime, ...
strategy/patterns.py                   NEW — engulfings, divergences, triggers, score_label
strategy/direction.py                  NEW — resolve_direction_params, metrics_inc
strategy/tune.py                       NEW — _classify_tune_result
strategy/vol.py                        NEW — annualized_vol_yang_zhang
infra/__init__.py                      NEW (empty)
infra/http.py                          NEW — _load_proxy, _rate_limit
cli/__init__.py                        NEW (empty)
cli/scanner_report.py                  NEW — fmt, save_log, main, get_top_symbols
tests/_fixtures/scanner_frozen.py      NEW — pytest fixture
tests/_fixtures/btcusdt_*.csv          NEW — frozen klines
tests/_fixtures/scanner_frozen_responses.json  NEW — frozen HTTP JSON
tests/_fixtures/capture_baseline.py    NEW — one-shot baseline regenerator
tests/_baselines/scan_btcusdt.json     NEW — snapshot baseline
tests/_baselines/README.md             NEW — regen warning
tests/test_scanner_snapshot.py         NEW — snapshot assertion
tests/test_<piece>_reexport.py         NEW per PR — identity tests
```

---

# PR0 — Foundation

**Branch:** `refactor/scanner-pr0-foundation`

Sets up snapshot baseline + scaffolding. No code moves.

## Task 0.1: Create scaffolding directories

**Files:**
- Create: `infra/__init__.py`
- Create: `cli/__init__.py`
- Create: `tests/_fixtures/__init__.py`
- Create: `tests/_baselines/README.md`

- [ ] **Step 1: Create empty `infra/__init__.py`**

```python
"""Infra layer — low-level utilities (HTTP helpers, etc.)."""
```

- [ ] **Step 2: Create empty `cli/__init__.py`**

```python
"""CLI layer — text-mode entrypoints (scanner_report, etc.)."""
```

- [ ] **Step 3: Create `tests/_fixtures/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Create `tests/_baselines/README.md`**

```markdown
# Baselines

This directory holds frozen snapshots of `scan()` output used by `tests/test_scanner_snapshot.py` to detect regressions during the per-purpose refactor of `btc_scanner.py` (issue #225).

## When to regenerate

**Almost never.** The baseline is the ground truth that proves the refactor preserves `scan()` behavior byte-for-byte.

If you intentionally change `scan()` output (new field, fixed bug, new indicator):

1. Discuss the change with a reviewer first.
2. Run `pytest tests/_fixtures/capture_baseline.py::test_capture -s` to regenerate.
3. Diff the new vs old baseline and commit BOTH the baseline and the code change in the same PR.
4. PR description must explain the intentional drift.

If a refactor PR causes the snapshot to drift, **STOP** and investigate. It means the refactor introduced a behavior change. Don't regenerate to make the test pass.

## Files

- `scan_btcusdt.json` — full `scan("BTCUSDT")` return value with frozen clock + klines + network mocks.
```

- [ ] **Step 5: Commit scaffolding**

```bash
git add infra/__init__.py cli/__init__.py tests/_fixtures/__init__.py tests/_baselines/README.md
git commit -m "refactor(scanner): scaffold infra/, cli/, tests/_fixtures, tests/_baselines for #225 PR0"
```

## Task 0.2: Capture frozen klines CSVs

**Files:**
- Create: `tests/_fixtures/capture_klines.py`
- Create: `tests/_fixtures/btcusdt_5m.csv` (generated)
- Create: `tests/_fixtures/btcusdt_1h.csv` (generated)
- Create: `tests/_fixtures/btcusdt_4h.csv` (generated)
- Create: `tests/_fixtures/btcusdt_1d.csv` (generated)
- Create: `tests/_fixtures/scanner_frozen_responses.json` (generated)

- [ ] **Step 1: Pre-verify — full suite green on `main`**

```bash
git checkout main && git pull
pytest tests/ -q
```
Expected: green.

- [ ] **Step 2: Create capture script `tests/_fixtures/capture_klines.py`**

```python
"""One-shot script to generate frozen klines CSVs and frozen HTTP response JSON.

Run: python -m tests._fixtures.capture_klines

Output is committed to the repo and regenerated only on intentional behavior change.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pandas as pd
import requests

# Ensure repo root on path (for `data` imports)
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data import market_data as md  # noqa: E402

_OUT = Path(__file__).parent


def main() -> None:
    for tf in ("5m", "1h", "4h", "1d"):
        df = md.get_klines("BTCUSDT", tf, limit=210)
        if df.empty:
            raise RuntimeError(f"empty klines for BTCUSDT {tf}")
        out_path = _OUT / f"btcusdt_{tf}.csv"
        df.to_csv(out_path, index=False)
        print(f"saved {out_path.name} ({len(df)} rows)")

    fng = requests.get(
        "https://api.alternative.me/fng/?limit=1", timeout=10).json()
    funding = requests.get(
        "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
        timeout=10,
    ).json()
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "ADAUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "AVAXUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "DOGEUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "UNIUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "XLMUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "PENDLEUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "JUPUSDT", "status": "TRADING", "quoteAsset": "USDT"},
            {"symbol": "RUNEUSDT", "status": "TRADING", "quoteAsset": "USDT"},
        ],
    }

    payloads = {
        "fng": fng,
        "funding": funding,
        "exchangeInfo": exchange_info,
    }
    out = _OUT / "scanner_frozen_responses.json"
    out.write_text(json.dumps(payloads, indent=2))
    print(f"saved {out.name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run capture script**

```bash
python -m tests._fixtures.capture_klines
```
Expected output:
```
saved btcusdt_5m.csv (210 rows)
saved btcusdt_1h.csv (210 rows)
saved btcusdt_4h.csv (150 rows)
saved btcusdt_1d.csv (250 rows)
saved scanner_frozen_responses.json
```

- [ ] **Step 4: Verify CSV files exist and have content**

```bash
wc -l tests/_fixtures/btcusdt_*.csv
ls -la tests/_fixtures/scanner_frozen_responses.json
```
Expected: 4 CSVs each > 100 rows; JSON file > 200 bytes.

- [ ] **Step 5: Commit fixtures + capture script**

```bash
git add tests/_fixtures/capture_klines.py tests/_fixtures/btcusdt_*.csv tests/_fixtures/scanner_frozen_responses.json
git commit -m "test(scanner): capture frozen klines + HTTP fixtures for #225 PR0"
```

## Task 0.3: Create the `frozen_scan` fixture

**Files:**
- Create: `tests/_fixtures/scanner_frozen.py`

- [ ] **Step 1: Create `tests/_fixtures/scanner_frozen.py`**

```python
"""Frozen fixture for scan() snapshot tests.

Monkeypatches:
- datetime.now() → fixed UTC timestamp
- data.market_data.get_klines() → CSVs from tests/_fixtures/btcusdt_*.csv
- data.market_data.prefetch() → no-op
- requests.get() → fixed JSON for F&G, funding rate, exchange info
- _REGIME_CACHE_FILE / _REGIME_CACHE_PATH / _regime_cache → tmp_path isolation
- observability.record_decision → no-op
- strategy.kill_switch_v2_shadow.emit_shadow_decision → no-op

PR0 monkeypatches `btc_scanner.*` for regime cache vars. As pieces move out
(notably regime → strategy.regime in PR6), this fixture is updated per-PR.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import requests

from data import market_data as md

_FIXTURE_DIR = Path(__file__).resolve().parent
_RESPONSES_PATH = _FIXTURE_DIR / "scanner_frozen_responses.json"


def _frozen_get_klines(symbol, interval, limit=None, **kw):
    csv_path = _FIXTURE_DIR / f"{symbol.lower()}_{interval}.csv"
    if not csv_path.exists():
        return pd.DataFrame()
    return pd.read_csv(csv_path)


def _frozen_requests_get(url, **kw):
    payloads = json.loads(_RESPONSES_PATH.read_text())

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.ok = True

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    if "fng" in url:
        return _Resp(payloads["fng"])
    if "fundingRate" in url:
        return _Resp(payloads["funding"])
    if "exchangeInfo" in url:
        return _Resp(payloads["exchangeInfo"])
    raise RuntimeError(f"unexpected URL in frozen test: {url}")


_FIXED_NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """datetime subclass with frozen now()/utcnow()."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FIXED_NOW.replace(tzinfo=None)
        return _FIXED_NOW.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return _FIXED_NOW.replace(tzinfo=None)


@pytest.fixture
def frozen_scan(monkeypatch, tmp_path):
    """Apply all monkeypatches needed to get deterministic scan() output."""
    monkeypatch.setattr("btc_scanner.datetime", _FrozenDatetime)
    monkeypatch.setattr(md, "get_klines", _frozen_get_klines)
    monkeypatch.setattr(md, "prefetch", lambda *a, **kw: None)
    monkeypatch.setattr(
        "btc_scanner._REGIME_CACHE_FILE", str(tmp_path / "regime.json"))
    monkeypatch.setattr(
        "btc_scanner._REGIME_CACHE_PATH", str(tmp_path / "regime.json"))
    monkeypatch.setattr("btc_scanner._regime_cache", {})
    monkeypatch.setattr(requests, "get", _frozen_requests_get)
    monkeypatch.setattr("observability.record_decision", lambda **kw: None)
    monkeypatch.setattr(
        "strategy.kill_switch_v2_shadow.emit_shadow_decision", lambda **kw: None)
    yield
```

- [ ] **Step 2: Sanity check — fixture imports without error**

```bash
python -c "from tests._fixtures.scanner_frozen import frozen_scan; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit fixture module**

```bash
git add tests/_fixtures/scanner_frozen.py
git commit -m "test(scanner): add frozen_scan fixture for snapshot tests (#225 PR0)"
```

## Task 0.4: Capture the snapshot baseline

**Files:**
- Create: `tests/_fixtures/capture_baseline.py`
- Create: `tests/_baselines/scan_btcusdt.json` (generated)

- [ ] **Step 1: Create capture-baseline test**

```python
# tests/_fixtures/capture_baseline.py
"""One-shot to regenerate tests/_baselines/scan_btcusdt.json.

Run:
    pytest tests/_fixtures/capture_baseline.py::test_capture_btcusdt -s

DO NOT run unless you intentionally want to reset the baseline.
See tests/_baselines/README.md.
"""
from __future__ import annotations
import json
from pathlib import Path

from btc_scanner import scan
from tests._fixtures.scanner_frozen import frozen_scan  # noqa: F401

_BASELINE = Path(__file__).resolve().parent.parent / "_baselines" / "scan_btcusdt.json"


def _normalize(obj):
    """Convert any non-JSON-native types (e.g. numpy) to native Python."""
    import numpy as np

    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(x) for x in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def test_capture_btcusdt(frozen_scan):
    rep = scan("BTCUSDT")
    rep_norm = _normalize(rep)
    _BASELINE.parent.mkdir(parents=True, exist_ok=True)
    _BASELINE.write_text(json.dumps(rep_norm, indent=2, sort_keys=True))
    print(f"\nwrote {_BASELINE}")
```

- [ ] **Step 2: Run the capture**

```bash
pytest tests/_fixtures/capture_baseline.py::test_capture_btcusdt -s
```
Expected: PASS, prints `wrote .../tests/_baselines/scan_btcusdt.json`

- [ ] **Step 3: Inspect baseline — sanity check it looks right**

```bash
jq 'keys' tests/_baselines/scan_btcusdt.json
jq '.symbol, .estado, .price, .score' tests/_baselines/scan_btcusdt.json
```
Expected: keys include `symbol`, `estado`, `price`, `score`, `lrc_1h`, `confirmations`, `exclusions`, `sizing_1h`, etc. `symbol` = `"BTCUSDT"`. `estado` is a non-empty string.

- [ ] **Step 4: Commit baseline + capture script**

```bash
git add tests/_fixtures/capture_baseline.py tests/_baselines/scan_btcusdt.json
git commit -m "test(scanner): capture scan_btcusdt.json baseline for #225 PR0"
```

## Task 0.5: Create the snapshot assertion test

**Files:**
- Create: `tests/test_scanner_snapshot.py`

- [ ] **Step 1: Create snapshot assertion test**

```python
# tests/test_scanner_snapshot.py
"""End-to-end snapshot regression for scan() during the #225 refactor.

If this fails, STOP. Either the refactor introduced a behavior change (most
likely) or the snapshot needs an intentional regen (rare; see
tests/_baselines/README.md).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from btc_scanner import scan
from tests._fixtures.scanner_frozen import frozen_scan  # noqa: F401
from tests._fixtures.capture_baseline import _normalize

_BASELINE = Path(__file__).resolve().parent / "_baselines" / "scan_btcusdt.json"


def test_scan_btcusdt_matches_baseline(frozen_scan):
    rep = _normalize(scan("BTCUSDT"))
    expected = json.loads(_BASELINE.read_text())
    assert rep == expected, (
        "scan('BTCUSDT') drifted from tests/_baselines/scan_btcusdt.json. "
        "Investigate before regenerating."
    )
```

- [ ] **Step 2: Run snapshot test — must PASS on baseline commit**

```bash
pytest tests/test_scanner_snapshot.py -v
```
Expected: PASS.

- [ ] **Step 3: Run full suite — must still be green**

```bash
pytest tests/ -q
```
Expected: green-bar maintained.

- [ ] **Step 4: Commit snapshot assertion**

```bash
git add tests/test_scanner_snapshot.py
git commit -m "test(scanner): add snapshot assertion for scan() (#225 PR0)"
```

## Task 0.6: Open PR0

- [ ] **Step 1: Push branch**

```bash
git push -u origin refactor/scanner-pr0-foundation
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "refactor(scanner): PR0 foundation — snapshot baseline + scaffolding (#225)" --body "$(cat <<'EOF'
## Summary
- Scaffolds `infra/`, `cli/`, `tests/_fixtures/`, `tests/_baselines/` for the per-purpose refactor of `btc_scanner.py` (#225).
- Captures frozen klines (BTCUSDT 5m/1h/4h/1d) and HTTP response JSON.
- Captures `scan("BTCUSDT")` snapshot at `tests/_baselines/scan_btcusdt.json`.
- Adds `tests/test_scanner_snapshot.py` asserting bit-equality.
- No code moves yet — this is the regression net for PR1-PR8.

Spec: `docs/superpowers/specs/es/2026-04-28-refactor-btc-scanner-por-proposito-design.md`

## Risks-touched (from spec §8)
- [x] Snapshot regen sin review — README warns explicitly
- [ ] Re-export omission — N/A this PR
- [ ] Module-global identity drift — N/A this PR
- [ ] Monkeypatch namespace — N/A (PR0 patches btc_scanner.* directly)
- [ ] Kill switch v2 calibrator — N/A
- [ ] CLI behavior drift — N/A

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/ -q
<full suite green>
\`\`\`

## Test plan
- [x] Snapshot test passes on this branch
- [x] Full suite green
- [ ] Reviewer reviews the captured baseline JSON makes sense

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR1 — strategy/patterns.py

**Branch:** `refactor/scanner-pr1-patterns` (off `main` after PR0 merged)

Moves: `detect_bull_engulfing`, `detect_bear_engulfing`, `detect_rsi_divergence`, `check_trigger_5m`, `check_trigger_5m_short`, `score_label`.

## Task 1.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```
Expected: both green.

- [ ] **Step 2: Create branch**

```bash
git checkout -b refactor/scanner-pr1-patterns
```

## Task 1.1: Create `strategy/patterns.py` with all six functions

**Files:**
- Create: `strategy/patterns.py`

- [ ] **Step 1: Write `strategy/patterns.py`**

```python
"""Candle/indicator pattern detectors used by scan() (extracted from btc_scanner.py per #225).

Pure functions, no I/O. Imports only from strategy.constants and strategy.indicators.
"""
from __future__ import annotations

import pandas as pd

from strategy.constants import (
    RSI_PERIOD, SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
)
from strategy.indicators import calc_rsi


def detect_bull_engulfing(df: pd.DataFrame):
    """BullEngulfing: vela anterior bajista completamente engullida por vela alcista.

    Si está activo → NO entrar (E1).
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return (p["close"] < p["open"]          # anterior bajista
            and c["close"] > c["open"]      # actual alcista
            and c["open"]  <= p["close"]    # abre ≤ cierre anterior
            and c["close"] >= p["open"])    # cierra ≥ open anterior


def detect_bear_engulfing(df: pd.DataFrame):
    """BearEngulfing: vela anterior alcista completamente engullida por vela bajista.

    Si está activo → NO entrar SHORT (exclusion para shorts).
    """
    if len(df) < 2:
        return False
    p, c = df.iloc[-2], df.iloc[-1]
    return bool(p["close"] > p["open"]          # anterior alcista
               and c["close"] < c["open"]       # actual bajista
               and c["open"]  >= p["close"]     # abre >= cierre anterior
               and c["close"] <= p["open"])     # cierra <= open anterior


def detect_rsi_divergence(close: pd.Series, rsi: pd.Series, window=72):
    """Detecta divergencias entre precio y RSI.

    - Alcista (Bullish): Precio hace mínimo más bajo, RSI hace mínimo más alto.
    - Bajista (Bearish): Precio hace máximo más alto, RSI hace máximo más bajo.
    Ventana default: 72 barras (3 días en 1H).
    Usa extremos locales de 5 puntos para filtrar ruido.
    """
    if len(close) < window:
        return {"bull": False, "bear": False}

    p = close.iloc[-window:].values
    r = rsi.iloc[-window:].values

    mins = [i for i in range(2, window - 2)
            if p[i] < p[i-1] and p[i] < p[i-2] and p[i] < p[i+1] and p[i] < p[i+2]]

    bull_div = False
    if len(mins) >= 2:
        a, b = mins[-2], mins[-1]
        bull_div = bool(p[b] < p[a] and r[b] > r[a])

    maxs = [i for i in range(2, window - 2)
            if p[i] > p[i-1] and p[i] > p[i-2] and p[i] > p[i+1] and p[i] > p[i+2]]

    bear_div = False
    if len(maxs) >= 2:
        a, b = maxs[-2], maxs[-1]
        bear_div = bool(p[b] > p[a] and r[b] < r[a])

    return {"bull": bull_div, "bear": bear_div}


def score_label(score):
    """Etiqueta de calidad según puntuación Spot V6."""
    if score >= SCORE_PREMIUM:
        return "PREMIUM ⭐⭐⭐ (sizing 150%)"
    elif score >= SCORE_STANDARD:
        return "ESTÁNDAR ⭐⭐ (sizing 100%)"
    elif score >= SCORE_MIN_HALF:
        return "MÍNIMA ⭐ (sizing 50%)"
    return "INSUFICIENTE"


def check_trigger_5m(df5: pd.DataFrame):
    """Evalúa si la última vela de 5M activa el gatillo de entrada (LONG)."""
    if len(df5) < 3:
        return False, {}

    rsi5        = calc_rsi(df5["close"], RSI_PERIOD)
    cur         = df5.iloc[-1]

    bullish_candle  = bool(cur["close"] > cur["open"])
    rsi_recovering  = bool(rsi5.iloc[-1] > rsi5.iloc[-2])

    trigger_active = bullish_candle and rsi_recovering

    details = {
        "vela_5m_alcista":    bullish_candle,
        "rsi_5m_recuperando": rsi_recovering,
        "rsi_5m_actual":      round(rsi5.iloc[-1], 2),
        "rsi_5m_anterior":    round(rsi5.iloc[-2], 2),
        "close_5m":           round(cur["close"], 2),
        "open_5m":            round(cur["open"], 2),
    }
    return trigger_active, details


def check_trigger_5m_short(df5: pd.DataFrame):
    """Evalúa si la última vela de 5M activa el gatillo de entrada SHORT."""
    if len(df5) < 3:
        return False, {}

    rsi5        = calc_rsi(df5["close"], RSI_PERIOD)
    cur         = df5.iloc[-1]

    bearish_candle  = bool(cur["close"] < cur["open"])
    rsi_falling     = bool(rsi5.iloc[-1] < rsi5.iloc[-2])

    trigger_active = bearish_candle and rsi_falling

    details = {
        "vela_5m_bajista":    bearish_candle,
        "rsi_5m_cayendo":     rsi_falling,
        "rsi_5m_actual":      round(rsi5.iloc[-1], 2),
        "rsi_5m_anterior":    round(rsi5.iloc[-2], 2),
        "close_5m":           round(cur["close"], 2),
        "open_5m":            round(cur["open"], 2),
    }
    return trigger_active, details
```

- [ ] **Step 2: Sanity check — file imports cleanly**

```bash
python -c "from strategy import patterns; print(patterns.score_label(4))"
```
Expected: `PREMIUM ⭐⭐⭐ (sizing 150%)`

## Task 1.2: Add re-exports to `btc_scanner.py` and remove originals

**Files:**
- Modify: `btc_scanner.py` (lines 523-548, 554-590, 593-601, 608-668)

- [ ] **Step 1: Add re-export imports near the top of `btc_scanner.py`** (after the existing strategy imports, around line 38)

```python
# Re-exports for backward compatibility — moved to strategy/patterns.py per #225 PR1
from strategy.patterns import (  # noqa: F401
    detect_bull_engulfing, detect_bear_engulfing, detect_rsi_divergence,
    score_label, check_trigger_5m, check_trigger_5m_short,
)
```

- [ ] **Step 2: Delete originals from `btc_scanner.py`**

Remove these line ranges (verify line numbers — they will shift after the edit):
- `def detect_bull_engulfing` (was lines 523-534)
- `def detect_bear_engulfing` (was lines 537-548)
- `def detect_rsi_divergence` (was lines 554-590)
- `def score_label` (was lines 593-601)
- `def check_trigger_5m` (was lines 608-638)
- `def check_trigger_5m_short` (was lines 641-668)

Use Grep to confirm none remain in `btc_scanner.py`:
```bash
grep -n "^def detect_\(bull\|bear\)_engulfing\|^def detect_rsi_divergence\|^def score_label\|^def check_trigger_5m" btc_scanner.py
```
Expected: no output.

- [ ] **Step 3: Quick LOC delta check**

```bash
wc -l btc_scanner.py
```
Expected: ~1370 (was 1485, removed ~115 LOC).

## Task 1.3: Add identity test

**Files:**
- Create: `tests/test_patterns_reexport.py`

- [ ] **Step 1: Write identity test**

```python
# tests/test_patterns_reexport.py
"""Identity tests: btc_scanner re-exports must point to the same objects as
their new home in strategy.patterns. Prevents silent drift if a re-export
is accidentally rebound or shadowed.
"""


def test_patterns_reexport_identity():
    import btc_scanner
    from strategy import patterns

    assert btc_scanner.detect_bull_engulfing is patterns.detect_bull_engulfing
    assert btc_scanner.detect_bear_engulfing is patterns.detect_bear_engulfing
    assert btc_scanner.detect_rsi_divergence is patterns.detect_rsi_divergence
    assert btc_scanner.score_label is patterns.score_label
    assert btc_scanner.check_trigger_5m is patterns.check_trigger_5m
    assert btc_scanner.check_trigger_5m_short is patterns.check_trigger_5m_short
```

- [ ] **Step 2: Run identity test**

```bash
pytest tests/test_patterns_reexport.py -v
```
Expected: PASS.

## Task 1.4: Post-verify + commit + PR

- [ ] **Step 1: Post-verify — snapshot still green**

```bash
pytest tests/test_scanner_snapshot.py -v
```
Expected: PASS (byte-equal).

- [ ] **Step 2: Post-verify — full suite green**

```bash
pytest tests/ -q
```
Expected: green-bar.

- [ ] **Step 3: Commit**

```bash
git add btc_scanner.py strategy/patterns.py tests/test_patterns_reexport.py
git commit -m "refactor(scanner): extract strategy/patterns.py — engulfings, divergences, triggers, score_label (#225 PR1)"
```

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin refactor/scanner-pr1-patterns
gh pr create --title "refactor(scanner): PR1 strategy/patterns.py (#225)" --body "$(cat <<'EOF'
## Summary
Moves to `strategy/patterns.py`:
- `detect_bull_engulfing`, `detect_bear_engulfing`
- `detect_rsi_divergence`
- `score_label`
- `check_trigger_5m`, `check_trigger_5m_short`

Re-exported from `btc_scanner` for backward compat. ~115 LOC moved.

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_patterns_reexport.py`
- [ ] Module-global identity drift — N/A (no module globals moved this PR)
- [ ] Monkeypatch namespace — N/A
- [x] Snapshot regen sin review — snapshot still byte-equal
- [ ] Kill switch v2 calibrator — N/A
- [ ] CLI behavior drift — N/A

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_patterns_reexport.py -v
PASSED
$ pytest tests/ -q
<full suite green>
$ wc -l btc_scanner.py
~1370
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR2 — strategy/direction.py

**Branch:** `refactor/scanner-pr2-direction`

Moves: `resolve_direction_params`, `metrics_inc_direction_disabled`, `ATR_SL_MULT/TP/BE` aliases.

## Task 2.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```
Expected: both green.

- [ ] **Step 2: Branch**

```bash
git checkout -b refactor/scanner-pr2-direction
```

## Task 2.1: Create `strategy/direction.py`

**Files:**
- Create: `strategy/direction.py`

- [ ] **Step 1: Write `strategy/direction.py`**

```python
"""Per-direction parameter resolution for scan() (extracted from btc_scanner.py per #225).

resolve_direction_params: read symbol_overrides config and return per-direction
ATR multipliers, or None if the direction is disabled for that symbol.
"""
from __future__ import annotations

from strategy.constants import (
    ATR_SL_MULT_DEFAULT, ATR_TP_MULT_DEFAULT, ATR_BE_MULT_DEFAULT,
)

# Module-level aliases (preserved from btc_scanner.py for backward compat).
ATR_SL_MULT = ATR_SL_MULT_DEFAULT
ATR_TP_MULT = ATR_TP_MULT_DEFAULT
ATR_BE_MULT = ATR_BE_MULT_DEFAULT


def resolve_direction_params(
    overrides: dict | None,
    symbol: str,
    direction: str,
) -> dict | None:
    """Resolve {atr_sl_mult, atr_tp_mult, atr_be_mult} for (symbol, direction).

    Returns None if the direction is disabled for that symbol (via `"short": null`).
    Precedence: direction block (long/short) > flat dict > global defaults.
    Case insensitive on direction.

    Spec: docs/superpowers/specs/es/2026-04-20-per-symbol-regime-design.md §6
    """
    defaults = {
        "atr_sl_mult": ATR_SL_MULT,
        "atr_tp_mult": ATR_TP_MULT,
        "atr_be_mult": ATR_BE_MULT,
    }

    if direction is None:
        return defaults

    if not isinstance(overrides, dict):
        return defaults

    entry = overrides.get(symbol, {})
    if not isinstance(entry, dict):
        return defaults

    sentinel = object()
    dir_key = direction.lower()
    dir_block = entry.get(dir_key, sentinel)

    if dir_block is None:
        return None  # direction disabled

    if isinstance(dir_block, dict):
        return {
            "atr_sl_mult": dir_block.get("atr_sl_mult",
                              entry.get("atr_sl_mult", defaults["atr_sl_mult"])),
            "atr_tp_mult": dir_block.get("atr_tp_mult",
                              entry.get("atr_tp_mult", defaults["atr_tp_mult"])),
            "atr_be_mult": dir_block.get("atr_be_mult",
                              entry.get("atr_be_mult", defaults["atr_be_mult"])),
        }

    return {
        "atr_sl_mult": entry.get("atr_sl_mult", defaults["atr_sl_mult"]),
        "atr_tp_mult": entry.get("atr_tp_mult", defaults["atr_tp_mult"]),
        "atr_be_mult": entry.get("atr_be_mult", defaults["atr_be_mult"]),
    }


def metrics_inc_direction_disabled(symbol: str, direction: str) -> None:
    """Increment the direction_disabled_skips_total metric (no-op on failure)."""
    try:
        from data import metrics
        metrics.inc("direction_disabled_skips_total",
                    labels={"symbol": symbol, "direction": direction})
    except Exception:
        pass  # metrics optional — don't crash scan on metric failure
```

- [ ] **Step 2: Sanity check**

```bash
python -c "from strategy.direction import resolve_direction_params; print(resolve_direction_params(None, 'BTCUSDT', 'LONG'))"
```
Expected: `{'atr_sl_mult': ..., 'atr_tp_mult': ..., 'atr_be_mult': ...}`

## Task 2.2: Update `btc_scanner.py` — re-export + remove originals

**Files:**
- Modify: `btc_scanner.py`

- [ ] **Step 1: Replace existing local aliases (lines 42-44) with re-export from strategy/direction**

Find:
```python
# strategy.constants exports ATR_*_MULT_DEFAULT (rename in #186 to disambiguate
# from per-symbol overrides). This module's existing call sites still use the
# shorter ATR_SL_MULT/TP/BE names; aliases preserve those without renaming.
ATR_SL_MULT = ATR_SL_MULT_DEFAULT
ATR_TP_MULT = ATR_TP_MULT_DEFAULT
ATR_BE_MULT = ATR_BE_MULT_DEFAULT
```

Replace with:
```python
# Re-exports for backward compatibility — moved to strategy/direction.py per #225 PR2
from strategy.direction import (  # noqa: F401
    ATR_SL_MULT, ATR_TP_MULT, ATR_BE_MULT,
    resolve_direction_params, metrics_inc_direction_disabled,
)
```

- [ ] **Step 2: Delete the original definitions**

Remove from `btc_scanner.py`:
- `def resolve_direction_params(...)` (was lines 356-407)
- `def metrics_inc_direction_disabled(...)` (was lines 857-864)

- [ ] **Step 3: Confirm removal**

```bash
grep -n "^def resolve_direction_params\|^def metrics_inc_direction_disabled" btc_scanner.py
```
Expected: no output.

## Task 2.3: Identity test

**Files:**
- Create: `tests/test_direction_reexport.py`

- [ ] **Step 1: Write identity test**

```python
# tests/test_direction_reexport.py
"""Identity tests: strategy.direction re-exports preserved on btc_scanner."""


def test_direction_reexport_identity():
    import btc_scanner
    from strategy import direction

    assert btc_scanner.resolve_direction_params is direction.resolve_direction_params
    assert btc_scanner.metrics_inc_direction_disabled is direction.metrics_inc_direction_disabled
    assert btc_scanner.ATR_SL_MULT is direction.ATR_SL_MULT
    assert btc_scanner.ATR_TP_MULT is direction.ATR_TP_MULT
    assert btc_scanner.ATR_BE_MULT is direction.ATR_BE_MULT
```

- [ ] **Step 2: Run identity test**

```bash
pytest tests/test_direction_reexport.py -v
```
Expected: PASS.

## Task 2.4: Post-verify + commit + PR

- [ ] **Step 1: Post-verify**

```bash
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
wc -l btc_scanner.py
```
Expected: snapshot green; suite green; LOC ~1310.

- [ ] **Step 2: Commit**

```bash
git add btc_scanner.py strategy/direction.py tests/test_direction_reexport.py
git commit -m "refactor(scanner): extract strategy/direction.py — resolve_direction_params + metrics + ATR aliases (#225 PR2)"
```

- [ ] **Step 3: Push + open PR (use the same body template as PR1, adapt summary + verification log)**

```bash
git push -u origin refactor/scanner-pr2-direction
gh pr create --title "refactor(scanner): PR2 strategy/direction.py (#225)" --body "$(cat <<'EOF'
## Summary
Moves to `strategy/direction.py`:
- `resolve_direction_params`
- `metrics_inc_direction_disabled`
- `ATR_SL_MULT`, `ATR_TP_MULT`, `ATR_BE_MULT` aliases (now sourced from `strategy.constants`)

Re-exported from `btc_scanner` for backward compat. ~60 LOC moved.

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_direction_reexport.py`
- [ ] Module-global identity drift — N/A (no module globals moved this PR)
- [ ] Monkeypatch namespace — N/A
- [x] Snapshot regen sin review — snapshot still byte-equal
- [ ] Kill switch v2 calibrator — N/A
- [ ] CLI behavior drift — N/A

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_direction_reexport.py -v
PASSED
$ pytest tests/ -q
<full suite green>
$ wc -l btc_scanner.py
~1310
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR3 — strategy/tune.py

**Branch:** `refactor/scanner-pr3-tune`

Moves: `_classify_tune_result`. Migrates `scripts/apply_tune_to_config.py` and `tests/test_tier_classification.py`. Re-export retained for any other consumer.

## Task 3.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Branch**

```bash
git checkout -b refactor/scanner-pr3-tune
```

## Task 3.1: Create `strategy/tune.py`

**Files:**
- Create: `strategy/tune.py`

- [ ] **Step 1: Write `strategy/tune.py`**

```python
"""Tune-result classification for the auto-tune pipeline (extracted from btc_scanner.py per #225).

Used by scripts/apply_tune_to_config.py to decide whether a (symbol, direction)
tuning result yields a dedicated triplet, fallback to per-symbol, or disabled.
"""
from __future__ import annotations

import numpy as np


def _classify_tune_result(count: int, profit_factor: float | None) -> str:
    """Classify a (symbol, direction) tuning result into one of three tiers.

    Returns one of: "dedicated", "fallback", "disabled".

    Rules:
        N ≥ 30 AND PF ≥ 1.3   → "dedicated"
        N ≥ 30 AND 1.0 ≤ PF < 1.3 → "fallback"
        N < 30 OR PF < 1.0    → "disabled"
        PF = inf (no losses)  → "dedicated" if N ≥ 30
        PF is None or NaN     → "disabled" (insufficient info)
    """
    if count == 0 or profit_factor is None:
        return "disabled"
    try:
        pf = float(profit_factor)
    except (TypeError, ValueError):
        return "disabled"
    if np.isnan(pf):
        return "disabled"
    if count < 30:
        return "disabled"
    if pf < 1.0:
        return "disabled"
    if pf < 1.3:
        return "fallback"
    return "dedicated"  # pf ≥ 1.3 (including inf)
```

## Task 3.2: Update `btc_scanner.py` — re-export + remove

- [ ] **Step 1: Add re-export to `btc_scanner.py` imports**

```python
# Re-export for backward compatibility — moved to strategy/tune.py per #225 PR3
from strategy.tune import _classify_tune_result  # noqa: F401
```

- [ ] **Step 2: Delete original `_classify_tune_result` (was lines 323-353)**

```bash
grep -n "^def _classify_tune_result" btc_scanner.py
```
Expected: no output after edit.

## Task 3.3: Migrate the script's import

**Files:**
- Modify: `scripts/apply_tune_to_config.py:14`

- [ ] **Step 1: Update import in script**

Find:
```python
from btc_scanner import _classify_tune_result  # noqa: E402
```

Replace with:
```python
from strategy.tune import _classify_tune_result  # noqa: E402
```

- [ ] **Step 2: Migrate test import**

In `tests/test_tier_classification.py:3`:

Find:
```python
from btc_scanner import _classify_tune_result
```

Replace with:
```python
from strategy.tune import _classify_tune_result
```

## Task 3.4: Identity test

**Files:**
- Create: `tests/test_tune_reexport.py`

- [ ] **Step 1: Write identity test**

```python
# tests/test_tune_reexport.py
def test_tune_reexport_identity():
    import btc_scanner
    from strategy import tune
    assert btc_scanner._classify_tune_result is tune._classify_tune_result
```

- [ ] **Step 2: Run identity test**

```bash
pytest tests/test_tune_reexport.py -v
pytest tests/test_tier_classification.py -v
```
Expected: both PASS.

## Task 3.5: Post-verify + commit + PR

- [ ] **Step 1: Post-verify**

```bash
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Smoke the script (if available)**

```bash
python -c "from scripts.apply_tune_to_config import _classify_tune_result; print(_classify_tune_result(30, 1.5))"
```
Expected: `dedicated`.

- [ ] **Step 3: Commit + PR**

```bash
git add btc_scanner.py strategy/tune.py scripts/apply_tune_to_config.py tests/test_tier_classification.py tests/test_tune_reexport.py
git commit -m "refactor(scanner): extract strategy/tune.py — _classify_tune_result + migrate script + test (#225 PR3)"
git push -u origin refactor/scanner-pr3-tune
gh pr create --title "refactor(scanner): PR3 strategy/tune.py (#225)" --body "$(cat <<'EOF'
## Summary
Moves to `strategy/tune.py`:
- `_classify_tune_result`

Migrated callers in the same commit:
- `scripts/apply_tune_to_config.py` — now imports from `strategy.tune`
- `tests/test_tier_classification.py` — now imports from `strategy.tune`

Re-export retained on `btc_scanner` for any other consumer (audited in PR8). ~30 LOC moved.

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_tune_reexport.py`
- [ ] Module-global identity drift — N/A
- [ ] Monkeypatch namespace — N/A
- [x] \`scripts/apply_tune_to_config.py\` cron breaks — mitigated: re-export retained AND script migrated atomically
- [x] Snapshot regen sin review — snapshot still byte-equal
- [ ] Kill switch v2 calibrator — N/A
- [ ] CLI behavior drift — N/A

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_tune_reexport.py tests/test_tier_classification.py -v
PASSED
$ pytest tests/ -q
<full suite green>
$ python -c "from scripts.apply_tune_to_config import _classify_tune_result; print(_classify_tune_result(30, 1.5))"
dedicated
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR4 — strategy/vol.py

**Branch:** `refactor/scanner-pr4-vol`

Moves: `annualized_vol_yang_zhang`, `TARGET_VOL_ANNUAL`, `VOL_LOOKBACK_DAYS`. Migrates `tests/test_vol_calc.py`.

## Task 4.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Branch**

```bash
git checkout -b refactor/scanner-pr4-vol
```

## Task 4.1: Create `strategy/vol.py`

**Files:**
- Create: `strategy/vol.py`

- [ ] **Step 1: Write `strategy/vol.py`**

```python
"""Yang-Zhang annualized volatility — diagnostic utility (extracted from btc_scanner.py per #225).

NOT applied to position sizing. The vol-normalized sizing idea of #125 was
found to regress P&L in comparative backtest: the per-symbol atr_sl_mult/tp
tuning from epic #121 (735 sims) already adapts to volatility structurally.
Function kept available for telemetry / future dashboards.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


TARGET_VOL_ANNUAL = 0.15   # reference target (not currently applied)
VOL_LOOKBACK_DAYS = 30


def annualized_vol_yang_zhang(df_daily: pd.DataFrame) -> float:
    """Yang-Zhang annualized vol over daily bars (diagnostic utility).

    Not wired into position sizing. Returns TARGET_VOL_ANNUAL when fewer
    than 5 bars are available.
    """
    if len(df_daily) < 5:
        return TARGET_VOL_ANNUAL
    o = df_daily["open"].astype(float)
    h = df_daily["high"].astype(float)
    l = df_daily["low"].astype(float)
    c = df_daily["close"].astype(float)
    log_ho = np.log(h / o)
    log_lo = np.log(l / o)
    log_co = np.log(c / o)
    log_oc_prev = np.log(o / c.shift(1)).dropna()
    n = len(df_daily) - 1
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    sigma_on = log_oc_prev.var(ddof=1) if len(log_oc_prev) >= 2 else 0.0
    sigma_oc = log_co.var(ddof=1)
    sigma_rs = (log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)).mean()
    var_daily = max(sigma_on + k * sigma_oc + (1 - k) * sigma_rs, 1e-10)
    return float(np.sqrt(var_daily * 365))
```

## Task 4.2: Update `btc_scanner.py` — re-export + remove

- [ ] **Step 1: Add re-export to `btc_scanner.py` imports**

```python
# Re-export for backward compatibility — moved to strategy/vol.py per #225 PR4
from strategy.vol import (  # noqa: F401
    annualized_vol_yang_zhang, TARGET_VOL_ANNUAL, VOL_LOOKBACK_DAYS,
)
```

- [ ] **Step 2: Delete originals**

Remove from `btc_scanner.py`:
- Constants `TARGET_VOL_ANNUAL`, `VOL_LOOKBACK_DAYS` (was lines 83-84)
- `def annualized_vol_yang_zhang` (was lines 87-109)

```bash
grep -n "^def annualized_vol_yang_zhang\|^TARGET_VOL_ANNUAL\|^VOL_LOOKBACK_DAYS" btc_scanner.py
```
Expected: only the re-export `from strategy.vol import` line shows.

## Task 4.3: Migrate test imports

**Files:**
- Modify: `tests/test_vol_calc.py`

- [ ] **Step 1: Update imports**

Find each instance (lines 14, 21, 27 per the grep earlier):
```python
from btc_scanner import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL
# or
from btc_scanner import annualized_vol_yang_zhang
```

Replace with:
```python
from strategy.vol import annualized_vol_yang_zhang, TARGET_VOL_ANNUAL
# or
from strategy.vol import annualized_vol_yang_zhang
```

## Task 4.4: Identity test

**Files:**
- Create: `tests/test_vol_reexport.py`

- [ ] **Step 1: Write identity test**

```python
# tests/test_vol_reexport.py
def test_vol_reexport_identity():
    import btc_scanner
    from strategy import vol
    assert btc_scanner.annualized_vol_yang_zhang is vol.annualized_vol_yang_zhang
    assert btc_scanner.TARGET_VOL_ANNUAL is vol.TARGET_VOL_ANNUAL
    assert btc_scanner.VOL_LOOKBACK_DAYS is vol.VOL_LOOKBACK_DAYS
```

- [ ] **Step 2: Run identity test + migrated test**

```bash
pytest tests/test_vol_reexport.py tests/test_vol_calc.py -v
```
Expected: PASS.

## Task 4.5: Post-verify + commit + PR

- [ ] **Step 1: Post-verify**

```bash
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Commit + PR**

```bash
git add btc_scanner.py strategy/vol.py tests/test_vol_calc.py tests/test_vol_reexport.py
git commit -m "refactor(scanner): extract strategy/vol.py — annualized_vol_yang_zhang (#225 PR4)"
git push -u origin refactor/scanner-pr4-vol
gh pr create --title "refactor(scanner): PR4 strategy/vol.py (#225)" --body "$(cat <<'EOF'
## Summary
Moves to `strategy/vol.py`:
- `annualized_vol_yang_zhang`
- `TARGET_VOL_ANNUAL`, `VOL_LOOKBACK_DAYS` constants

Migrated `tests/test_vol_calc.py` to import from `strategy.vol`. Re-export retained on `btc_scanner`. Diagnostic-only utility (not wired into sizing). ~25 LOC moved.

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_vol_reexport.py`
- [ ] Module-global identity drift — N/A
- [ ] Monkeypatch namespace — N/A
- [x] Snapshot regen sin review — snapshot still byte-equal
- [ ] Kill switch v2 calibrator — N/A
- [ ] CLI behavior drift — N/A

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_vol_reexport.py tests/test_vol_calc.py -v
PASSED
$ pytest tests/ -q
<full suite green>
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR5 — infra/http.py

**Branch:** `refactor/scanner-pr5-infra-http`

Moves: `_load_proxy`, `_rate_limit`, `_last_api_call`, `_API_MIN_INTERVAL`, `_api_lock`.

## Task 5.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Branch**

```bash
git checkout -b refactor/scanner-pr5-infra-http
```

## Task 5.1: Create `infra/http.py`

**Files:**
- Create: `infra/http.py`

- [ ] **Step 1: Write `infra/http.py`**

```python
"""HTTP infra — proxy loader + rate limiter (extracted from btc_scanner.py per #225).

Used by:
- strategy.regime (PR6) — _rate_limit before F&G + funding-rate calls
- cli.scanner_report (PR7) — _load_proxy in get_top_symbols (CoinGecko)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_proxy() -> dict:
    """Lee proxy de config.json o de variables de entorno."""
    cfg_path = REPO_ROOT / "config.json"
    proxy_str = ""
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            proxy_str = cfg.get("proxy", "").strip()
        except Exception:
            pass
    proxy_str = os.environ.get(
        "HTTPS_PROXY", os.environ.get("HTTP_PROXY", proxy_str)).strip()
    if proxy_str:
        return {"http": proxy_str, "https": proxy_str}
    return {}


_last_api_call: float = 0.0
_API_MIN_INTERVAL = 0.1   # 100ms between API calls
_api_lock = threading.Lock()


def _rate_limit() -> None:
    """Enforce minimum interval between API calls to avoid rate-limit bans."""
    global _last_api_call
    with _api_lock:
        now = time.time()
        elapsed = now - _last_api_call
        if elapsed < _API_MIN_INTERVAL:
            time.sleep(_API_MIN_INTERVAL - elapsed)
        _last_api_call = time.time()
```

## Task 5.2: Update `btc_scanner.py` — re-export + remove

- [ ] **Step 1: Add re-export to `btc_scanner.py` imports**

```python
# Re-export for backward compatibility — moved to infra/http.py per #225 PR5
from infra.http import (  # noqa: F401
    _load_proxy, _rate_limit, _last_api_call, _API_MIN_INTERVAL, _api_lock,
)
```

- [ ] **Step 2: Delete originals**

Remove from `btc_scanner.py`:
- `def _load_proxy` (was lines 481-497)
- `_last_api_call`, `_API_MIN_INTERVAL`, `_api_lock` constants (was lines 500-502)
- `def _rate_limit` (was lines 505-513)

```bash
grep -n "^def _load_proxy\|^def _rate_limit\|^_last_api_call\|^_API_MIN_INTERVAL\|^_api_lock" btc_scanner.py
```
Expected: only the re-export shows.

## Task 5.3: Add minimal `_rate_limit` test

**Files:**
- Create: `tests/test_http_reexport.py`

- [ ] **Step 1: Write identity + behavior test**

```python
# tests/test_http_reexport.py
"""Identity + minimal behavior tests for infra.http (PR5)."""
import time


def test_http_reexport_identity():
    import btc_scanner
    from infra import http
    assert btc_scanner._load_proxy is http._load_proxy
    assert btc_scanner._rate_limit is http._rate_limit
    assert btc_scanner._API_MIN_INTERVAL is http._API_MIN_INTERVAL
    assert btc_scanner._api_lock is http._api_lock


def test_rate_limit_enforces_min_interval():
    """Two consecutive calls must be at least _API_MIN_INTERVAL seconds apart."""
    from infra.http import _rate_limit, _API_MIN_INTERVAL

    _rate_limit()
    t0 = time.time()
    _rate_limit()
    elapsed = time.time() - t0

    # Allow some slack — must be at least the min interval (with epsilon for clock jitter).
    assert elapsed >= _API_MIN_INTERVAL * 0.95


def test_load_proxy_from_env(monkeypatch):
    """HTTPS_PROXY env var takes precedence."""
    from infra import http

    monkeypatch.setenv("HTTPS_PROXY", "socks5://test:1080")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)

    proxy = http._load_proxy()
    assert proxy == {"http": "socks5://test:1080", "https": "socks5://test:1080"}
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_http_reexport.py -v
```
Expected: 3 PASS.

## Task 5.4: Post-verify + commit + PR

- [ ] **Step 1: Post-verify**

```bash
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Commit + PR**

```bash
git add btc_scanner.py infra/http.py tests/test_http_reexport.py
git commit -m "refactor(scanner): extract infra/http.py — _load_proxy + _rate_limit (#225 PR5)"
git push -u origin refactor/scanner-pr5-infra-http
gh pr create --title "refactor(scanner): PR5 infra/http.py (#225)" --body "$(cat <<'EOF'
## Summary
Moves to `infra/http.py`:
- `_load_proxy` (CoinGecko + scanner proxy support)
- `_rate_limit` (100ms minimum interval between API calls)
- `_last_api_call`, `_API_MIN_INTERVAL`, `_api_lock` module globals

Re-exported from `btc_scanner` with object identity preserved (the `_api_lock` and `_last_api_call` globals must remain the same Python objects so re-export and home reference the same lock/counter). ~30 LOC moved.

**Blocks PR6 + PR7** (both consume `_rate_limit` / `_load_proxy`).

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_http_reexport.py` identity assertions
- [x] Module-global identity drift — `_api_lock`, `_last_api_call` re-exported via `from infra.http import …` so the same objects are bound on both modules
- [ ] Monkeypatch namespace — no fixture changes; existing tests don't monkeypatch these globals
- [x] Snapshot regen sin review — snapshot still byte-equal
- [ ] Kill switch v2 calibrator — N/A (PR6 will need this)
- [ ] CLI behavior drift — N/A (PR7 will need this)

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_http_reexport.py -v
PASSED (3 tests: identity + rate_limit interval + proxy env)
$ pytest tests/ -q
<full suite green>
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR6 — strategy/regime.py

**Branch:** `refactor/scanner-pr6-regime` (off `main` after PR5 merged)

**Largest PR.** Moves all of regime: `detect_regime`, `get_cached_regime`, `detect_regime_for_symbol`, 5× `_compute_*_score`, `_compute_local_regime`, `_regime_cache_key`, `_load_regime_cache`, `_save_regime_cache`, `_REGIME_CACHE_FILE/PATH`, `_REGIME_TTL_SEC`, `_regime_cache` global. **Critical: updates the snapshot fixture to monkeypatch the new home.**

## Task 6.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Branch (after PR5 merged)**

```bash
git checkout -b refactor/scanner-pr6-regime
```

## Task 6.1: Create `strategy/regime.py`

**Files:**
- Create: `strategy/regime.py`

- [ ] **Step 1: Write `strategy/regime.py` (full file with all 12 functions + 4 globals)**

```python
"""Market regime detector — composite score (price + sentiment + funding + optional momentum).

Extracted from btc_scanner.py per #225 PR6. Two entry points:
- detect_regime() / get_cached_regime() — global, BTCUSDT-anchored, 24h-TTL cache
- detect_regime_for_symbol(symbol, mode) — per-symbol; modes: global, hybrid, hybrid_momentum

Cache file: data/regime_cache.json. Cache shape: {key: regime_dict} where key is
either "global" or "{mode}:{symbol}" for per-symbol modes.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import requests

from data import market_data as md
from infra.http import _rate_limit
from strategy.indicators import calc_adx, calc_rsi, calc_sma

log = logging.getLogger("strategy.regime")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REGIME_CACHE_FILE = os.path.join(_SCRIPT_DIR, "data", "regime_cache.json")
_REGIME_CACHE_PATH = _REGIME_CACHE_FILE  # canonical alias
_REGIME_TTL_SEC = 86400  # 24 hours


def _load_regime_cache() -> dict:
    """Load regime cache from JSON with soft migration of legacy single-regime shape."""
    if not os.path.exists(_REGIME_CACHE_PATH):
        return {}
    try:
        with open(_REGIME_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if isinstance(data, dict) and "ts" in data and "regime" in data:
        return {"global": data}
    return data if isinstance(data, dict) else {}


def _save_regime_cache(data: dict) -> None:
    """Persist regime cache to disk."""
    try:
        os.makedirs(os.path.dirname(_REGIME_CACHE_FILE), exist_ok=True)
        with open(_REGIME_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to save regime cache: {e}")


_regime_cache = _load_regime_cache()


def _compute_price_score(df_daily: pd.DataFrame) -> int:
    """Score 0-100 bearish-to-bullish over daily bars. Pure."""
    if df_daily is None or df_daily.empty or len(df_daily) < 200:
        return 100
    try:
        sma50 = df_daily["close"].rolling(50).mean().iloc[-1]
        sma200 = df_daily["close"].rolling(200).mean().iloc[-1]
        if pd.isna(sma50) or pd.isna(sma200):
            return 100
        price = float(df_daily["close"].iloc[-1])
        score = 100
        if sma50 < sma200:
            score -= 40
        if price < sma200:
            score -= 30
        if len(df_daily) >= 30:
            ret30 = df_daily["close"].iloc[-1] / df_daily["close"].iloc[-30] - 1
            if ret30 < -0.10:
                score -= 20
            elif ret30 < 0:
                score -= 10
        return max(0, min(100, int(score)))
    except Exception:
        return 100


def _compute_fng_score(fng_value: int) -> int:
    """F&G already 0-100. Pass-through with clamp."""
    return max(0, min(100, int(fng_value)))


def _compute_funding_score(rate: float) -> int:
    """Map: -0.01 → 0, 0 → 50, +0.01 → 100. Clamp [0,100]."""
    return max(0, min(100, int(50 + rate * 5000)))


def _compute_rsi_score(rsi_1d_last: float) -> int:
    """Inverted from momentum (mean-reversion strategy): low RSI → bullish."""
    return max(0, min(100, int(100 - rsi_1d_last)))


def _compute_adx_score(adx_1d_last: float) -> int:
    """ADX < 20 → 75 (ranging); 20-30 → 50; ≥30 → 25 (strong trend)."""
    if adx_1d_last < 20:
        return 75
    if adx_1d_last < 30:
        return 50
    return 25


def _regime_cache_key(symbol: str | None, mode: str) -> str:
    """Return cache key: 'global' for legacy mode, '{mode}:{symbol}' otherwise."""
    if mode == "global":
        return "global"
    return f"{mode}:{symbol}"


def _compute_local_regime(
    symbol: str | None,
    mode: str,
    df_daily_sym: pd.DataFrame,
    fng_score: int,
    funding_score: int,
    rsi_score: int = 50,
    adx_score: int = 50,
) -> dict:
    """Compose final regime score per mode."""
    price_score = _compute_price_score(df_daily_sym)

    if mode == "global":
        composite = price_score * 0.40 + fng_score * 0.30 + funding_score * 0.30
        components = {"price": price_score, "fng": fng_score, "funding": funding_score}
    elif mode == "hybrid":
        composite = price_score * 0.50 + fng_score * 0.25 + funding_score * 0.25
        components = {"price": price_score, "fng": fng_score, "funding": funding_score}
    elif mode == "hybrid_momentum":
        composite = (price_score * 0.30 + rsi_score * 0.15 + adx_score * 0.20
                     + fng_score * 0.20 + funding_score * 0.15)
        components = {
            "price": price_score, "rsi": rsi_score, "adx": adx_score,
            "fng": fng_score, "funding": funding_score,
        }
    else:
        raise ValueError(f"Unknown regime mode: {mode}")

    if composite > 60:
        regime = "BULL"
    elif composite < 40:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "score": round(composite, 2),
        "mode": mode,
        "symbol": symbol,
        "components": components,
    }


def detect_regime_for_symbol(symbol: str | None, mode: str = "global") -> dict:
    """Public entry. Dispatches by mode; 24h TTL cache."""
    VALID_MODES = {"global", "hybrid", "hybrid_momentum"}
    if mode not in VALID_MODES:
        log.warning(f"Invalid regime mode '{mode}'; falling back to 'global'")
        mode = "global"

    if mode == "global":
        return get_cached_regime()

    key = _regime_cache_key(symbol, mode)
    global _regime_cache
    cached = _regime_cache.get(key)
    if cached and cached.get("ts"):
        try:
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(cached["ts"])).total_seconds()
            if age < 86400:
                return cached
        except Exception:
            pass

    df_daily = None
    try:
        df_daily = md.get_klines(symbol, "1d", limit=250) if symbol else None
    except Exception as e:
        log.warning(f"detect_regime_for_symbol: md.get_klines failed for {symbol}: {e}")

    fng_score = 50
    funding_score = 50
    rsi_score = 50
    adx_score = 50

    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            fng_value = int(r.json()["data"][0]["value"])
            fng_score = _compute_fng_score(fng_value)
    except Exception:
        pass

    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
            timeout=10,
        )
        if r.ok and r.json():
            rate = float(r.json()[0]["fundingRate"])
            funding_score = _compute_funding_score(rate)
    except Exception:
        pass

    if mode == "hybrid_momentum" and df_daily is not None and len(df_daily) >= 20:
        try:
            rsi_val = calc_rsi(df_daily["close"], 14).iloc[-1]
            if not pd.isna(rsi_val):
                rsi_score = _compute_rsi_score(rsi_val)
        except Exception:
            pass
        try:
            adx_val = calc_adx(df_daily, 14).iloc[-1]
            if not pd.isna(adx_val):
                adx_score = _compute_adx_score(adx_val)
        except Exception:
            pass

    result = _compute_local_regime(
        symbol, mode, df_daily,
        fng_score, funding_score, rsi_score, adx_score,
    )

    _regime_cache[key] = result
    _save_regime_cache(_regime_cache)
    return result


def detect_regime() -> dict:
    """Composite market regime detection — price (40%) + sentiment (30%) + funding (30%).

    Returns dict with regime ("BULL"/"BEAR"/"NEUTRAL"), score (0-100), details.
    """
    details = {}
    score_components = []

    price_score = 100
    try:
        df1d = md.get_klines("BTCUSDT", "1d", limit=250)
        if len(df1d) >= 200:
            sma50  = calc_sma(df1d["close"], 50).iloc[-1]
            sma200 = calc_sma(df1d["close"], 200).iloc[-1]
            price  = float(df1d["close"].iloc[-1])
            ret30d = (price / float(df1d["close"].iloc[-30]) - 1) * 100 if len(df1d) >= 30 else 0

            death_cross = bool(sma50 < sma200)
            price_below_sma200 = bool(price < sma200)

            price_score = 100
            if death_cross:
                price_score -= 40
            if price_below_sma200:
                price_score -= 30
            if ret30d < -10:
                price_score -= 20
            elif ret30d < 0:
                price_score -= 10
            price_score = max(0, min(100, price_score))

            details["price"] = {
                "sma50": round(float(sma50), 2),
                "sma200": round(float(sma200), 2),
                "price": round(price, 2),
                "death_cross": death_cross,
                "price_below_sma200": price_below_sma200,
                "ret_30d_pct": round(ret30d, 1),
                "score": price_score,
            }
    except Exception as e:
        log.warning(f"Regime: price structure error: {e}")
        details["price"] = {"error": str(e), "score": price_score}
    score_components.append(("price", price_score, 0.4))

    fng_score = 50
    try:
        _rate_limit()
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            data = r.json()
            fng_value = int(data["data"][0]["value"])
            fng_label = data["data"][0]["value_classification"]
            fng_score = fng_value
            details["sentiment"] = {
                "fear_greed_index": fng_value,
                "classification": fng_label,
                "score": fng_score,
            }
    except Exception as e:
        log.warning(f"Regime: Fear & Greed error: {e}")
        details["sentiment"] = {"error": str(e), "score": fng_score}
    score_components.append(("sentiment", fng_score, 0.3))

    funding_score = 50
    try:
        _rate_limit()
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
            timeout=10,
        )
        if r.ok:
            data = r.json()
            rate = float(data[0]["fundingRate"])
            funding_score = max(0, min(100, int(50 + rate * 5000)))
            details["funding"] = {
                "rate": rate,
                "rate_pct": round(rate * 100, 4),
                "score": funding_score,
            }
    except Exception as e:
        log.warning(f"Regime: funding rate error: {e}")
        details["funding"] = {"error": str(e), "score": funding_score}
    score_components.append(("funding", funding_score, 0.3))

    composite = sum(s * w for _, s, w in score_components)
    composite = round(composite, 1)

    if composite > 60:
        regime = "BULL"
    elif composite < 40:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"

    result = {
        "regime": regime,
        "score": composite,
        "details": details,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    global _regime_cache
    _regime_cache["global"] = result
    _save_regime_cache(_regime_cache)
    log.info(f"Regime Detection: {regime} (score={composite}) "
             f"[price={price_score} fng={fng_score} funding={funding_score}]")

    return result


def get_cached_regime() -> dict:
    """Return cached regime, refreshing if older than TTL."""
    global_entry = _regime_cache.get("global", {})
    if not global_entry or global_entry.get("ts") is None:
        return detect_regime()
    cache_age = (datetime.now(timezone.utc) -
                 datetime.fromisoformat(global_entry["ts"])).total_seconds()
    if cache_age > _REGIME_TTL_SEC:
        return detect_regime()
    return global_entry
```

## Task 6.2: Update `btc_scanner.py` — re-export + remove all regime code

- [ ] **Step 1: Add re-export to imports**

```python
# Re-export for backward compatibility — moved to strategy/regime.py per #225 PR6
from strategy.regime import (  # noqa: F401
    detect_regime, get_cached_regime, detect_regime_for_symbol,
    _compute_price_score, _compute_fng_score, _compute_funding_score,
    _compute_rsi_score, _compute_adx_score,
    _regime_cache_key, _compute_local_regime,
    _load_regime_cache, _save_regime_cache,
    _REGIME_CACHE_FILE, _REGIME_CACHE_PATH, _REGIME_TTL_SEC,
    _regime_cache,
)
```

- [ ] **Step 2: Delete originals from `btc_scanner.py`**

Remove these line ranges (verify after edits):
- `_compute_price_score`, `_compute_fng_score`, `_compute_funding_score`, `_compute_rsi_score`, `_compute_adx_score` (was lines 112-175)
- `_regime_cache_key`, `_compute_local_regime`, `detect_regime_for_symbol` (was lines 178-320)
- `_REGIME_CACHE_FILE`, `_REGIME_CACHE_PATH`, `_REGIME_TTL_SEC` constants (was lines 675-677)
- `_load_regime_cache`, `_save_regime_cache`, `_regime_cache` global, `detect_regime`, `get_cached_regime` (was lines 680-850)

```bash
grep -n "^def detect_regime\|^def get_cached_regime\|^def _compute_\|^def _load_regime_cache\|^def _save_regime_cache\|^def _regime_cache_key\|^_REGIME_CACHE_\|^_REGIME_TTL_SEC\|^_regime_cache" btc_scanner.py
```
Expected: only the re-export `from strategy.regime import` line shows.

## Task 6.3: Update fixture monkeypatch targets (CRITICAL)

**Files:**
- Modify: `tests/_fixtures/scanner_frozen.py`

The fixture currently patches `btc_scanner._REGIME_CACHE_FILE` etc. — these are now re-exports, not the home. Patches must target `strategy.regime.*` so production code (which lives in `strategy.regime`) sees the patch.

- [ ] **Step 1: Update fixture's monkeypatch targets**

In `tests/_fixtures/scanner_frozen.py`, replace:
```python
    monkeypatch.setattr(
        "btc_scanner._REGIME_CACHE_FILE", str(tmp_path / "regime.json"))
    monkeypatch.setattr(
        "btc_scanner._REGIME_CACHE_PATH", str(tmp_path / "regime.json"))
    monkeypatch.setattr("btc_scanner._regime_cache", {})
```

With:
```python
    # PR6: regime moved to strategy.regime — patch the home module so the
    # production read of _REGIME_CACHE_FILE inside _save_regime_cache picks
    # up the tmp_path. Patching btc_scanner.* would only rebind the
    # re-export name, not the home-module name that production code reads.
    monkeypatch.setattr(
        "strategy.regime._REGIME_CACHE_FILE", str(tmp_path / "regime.json"))
    monkeypatch.setattr(
        "strategy.regime._REGIME_CACHE_PATH", str(tmp_path / "regime.json"))
    monkeypatch.setattr("strategy.regime._regime_cache", {})
```

- [ ] **Step 2: Run snapshot test — must still pass byte-equal**

```bash
pytest tests/test_scanner_snapshot.py -v
```
Expected: PASS. **If it fails, the fixture update was wrong — investigate before continuing.**

## Task 6.4: Identity test

**Files:**
- Create: `tests/test_regime_reexport.py`

- [ ] **Step 1: Write identity test (covers all 14 names)**

```python
# tests/test_regime_reexport.py
"""Identity tests: strategy.regime re-exports preserved on btc_scanner."""


def test_regime_reexport_identity():
    import btc_scanner
    from strategy import regime

    # Functions
    assert btc_scanner.detect_regime is regime.detect_regime
    assert btc_scanner.get_cached_regime is regime.get_cached_regime
    assert btc_scanner.detect_regime_for_symbol is regime.detect_regime_for_symbol
    assert btc_scanner._compute_price_score is regime._compute_price_score
    assert btc_scanner._compute_fng_score is regime._compute_fng_score
    assert btc_scanner._compute_funding_score is regime._compute_funding_score
    assert btc_scanner._compute_rsi_score is regime._compute_rsi_score
    assert btc_scanner._compute_adx_score is regime._compute_adx_score
    assert btc_scanner._regime_cache_key is regime._regime_cache_key
    assert btc_scanner._compute_local_regime is regime._compute_local_regime
    assert btc_scanner._load_regime_cache is regime._load_regime_cache
    assert btc_scanner._save_regime_cache is regime._save_regime_cache

    # Constants
    assert btc_scanner._REGIME_CACHE_FILE is regime._REGIME_CACHE_FILE
    assert btc_scanner._REGIME_CACHE_PATH is regime._REGIME_CACHE_PATH
    assert btc_scanner._REGIME_TTL_SEC is regime._REGIME_TTL_SEC

    # Module-global dict — same object so mutations from either name propagate
    assert btc_scanner._regime_cache is regime._regime_cache
```

- [ ] **Step 2: Run identity test**

```bash
pytest tests/test_regime_reexport.py -v
```
Expected: PASS.

## Task 6.5: Run regime-specific test files

- [ ] **Step 1: Run regime tests**

```bash
pytest tests/test_regime_per_symbol.py tests/test_regime_modes_e2e.py -v
```
Expected: PASS (all use `from btc_scanner import …` which now resolves via re-export).

- [ ] **Step 2: Run kill-switch v2 calibrator tests (uses `from btc_scanner import get_cached_regime`)**

```bash
pytest tests/test_strategy_kill_switch_v2_calibrator.py -v
```
Expected: PASS.

## Task 6.6: Post-verify + commit + PR

- [ ] **Step 1: Post-verify all gates**

```bash
pytest tests/test_scanner_snapshot.py -v
pytest tests/test_regime_reexport.py -v
pytest tests/ -q
wc -l btc_scanner.py
```
Expected: snapshot green; identity green; suite green; LOC ~1000.

- [ ] **Step 2: CLI smoke**

```bash
timeout 30 python btc_scanner.py --once BTCUSDT 2>&1 | tee /tmp/pr6_smoke.log
grep -E "(ERROR|Traceback)" /tmp/pr6_smoke.log && echo FAIL || echo OK
```
Expected: `OK`. Scanner runs one cycle and exits cleanly.

- [ ] **Step 3: Commit**

```bash
git add btc_scanner.py strategy/regime.py tests/_fixtures/scanner_frozen.py tests/test_regime_reexport.py
git commit -m "refactor(scanner): extract strategy/regime.py — detect_regime + per-symbol + helpers (#225 PR6)"
```

- [ ] **Step 4: Push + PR**

```bash
git push -u origin refactor/scanner-pr6-regime
gh pr create --title "refactor(scanner): PR6 strategy/regime.py (#225)" --body "$(cat <<'EOF'
## Summary
**Largest PR.** Moves to `strategy/regime.py`:
- `detect_regime`, `get_cached_regime`, `detect_regime_for_symbol`
- 5× `_compute_*_score` helpers (price, fng, funding, rsi, adx)
- `_regime_cache_key`, `_compute_local_regime`
- `_load_regime_cache`, `_save_regime_cache`
- `_REGIME_CACHE_FILE/PATH`, `_REGIME_TTL_SEC` constants
- `_regime_cache` module-global dict (identity preserved via `from strategy.regime import _regime_cache`)

**Critical fixture update:** `tests/_fixtures/scanner_frozen.py` monkeypatch targets switched from `btc_scanner._REGIME_CACHE_*` to `strategy.regime._REGIME_CACHE_*` — production code reads the home-module names, not the re-exports.

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_regime_reexport.py` covering all 14 names
- [x] Module-global identity drift — mitigated by `is` test on `_regime_cache`
- [x] Monkeypatch namespace — fixture updated to target home module
- [x] Snapshot regen sin review — snapshot still byte-equal (verified after fixture update)
- [x] Kill switch v2 calibrator — `tests/test_strategy_kill_switch_v2_calibrator.py` runs green
- [ ] CLI behavior drift — N/A this PR

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_regime_reexport.py -v
PASSED (16 assertions)
$ pytest tests/ -q
<full suite green>
$ python btc_scanner.py --once BTCUSDT
<one cycle, no errors>
$ wc -l btc_scanner.py
~1000
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR7 — cli/scanner_report.py

**Branch:** `refactor/scanner-pr7-cli` (off `main` after PR1 + PR5 merged)

Moves: `fmt`, `save_log`, `main`, `get_top_symbols`, `LOG_FILE`, `SCAN_INTERVAL`, `STABLECOINS`. Rewires `btc_scanner.py:__main__` to delegate.

## Task 7.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
```

- [ ] **Step 2: Branch (after PR1 + PR5 merged)**

```bash
git checkout -b refactor/scanner-pr7-cli
```

## Task 7.1: Create `cli/scanner_report.py`

**Files:**
- Create: `cli/scanner_report.py`

- [ ] **Step 1: Write `cli/scanner_report.py`**

```python
"""CLI scanner — formatter + log writer + main loop + symbol fetcher.

Extracted from btc_scanner.py per #225 PR7. The scanner CLI runs:
- python btc_scanner.py [--once] [SYMBOL]   (entrypoint preserved via delegation)

Or directly:
- python -m cli.scanner_report
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from infra.http import _load_proxy
from strategy.constants import SCORE_PREMIUM, SCORE_STANDARD
from strategy.patterns import score_label

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = str(REPO_ROOT / "logs" / "signals_log.txt")
os.makedirs(REPO_ROOT / "logs", exist_ok=True)

SCAN_INTERVAL = 300

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "GUSD", "FRAX",
    "LUSD", "FDUSD", "PYUSD", "SUSD", "CRVUSD", "USDE", "USDS",
}

log = logging.getLogger("cli.scanner_report")


def get_top_symbols(n: int = 20, quote: str = "USDT") -> list:
    """Obtiene los N primeros criptos por capitalización desde CoinGecko.

    Excluye stablecoins y retorna pares USDT. Fallback a btc_scanner.DEFAULT_SYMBOLS
    si CoinGecko no responde.
    """
    import requests as _req
    try:
        proxies = _load_proxy()
        r = _req.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": n * 2,
                "page": 1,
                "sparkline": "false",
            },
            proxies=proxies or None,
            timeout=15,
            headers={"User-Agent": "btc-scanner/1.0"},
        )
        r.raise_for_status()
        symbols = []
        for coin in r.json():
            ticker = coin["symbol"].upper()
            if ticker in STABLECOINS:
                continue
            pair = f"{ticker}{quote}"
            symbols.append(pair)
            if len(symbols) >= n:
                break
        if symbols:
            log.info(f"CoinGecko: top {len(symbols)} símbolos → {symbols[:5]}…")
            return symbols
    except Exception as e:
        log.warning(f"CoinGecko no disponible ({e}). Usando lista por defecto.")
    from btc_scanner import DEFAULT_SYMBOLS
    return DEFAULT_SYMBOLS[:n]


def fmt(rep: dict) -> str:
    """Format a scan report dict into a human-readable text block."""
    SEP = "=" * 65
    DIV = "─" * 65

    def ok(b):
        return "✅" if b is True else ("❌" if b is False else "❓")

    lines = [
        SEP,
        f"  CRYPTO SCANNER  1H+5M  |  {rep.get('symbol','?')}  |  {rep['timestamp']}",
        SEP,
        f"  💰 PRECIO (cierre 1H) : ${rep['price']:,.2f}",
        f"  📡 ESTADO             : {rep['estado']}",
        f"  📐 DIRECCION          : {rep.get('direction') or 'N/A'}",
        DIV,
        "  ── SETUP 1H  (señal principal) ──────────────────────────",
        f"  LRC 1H : {rep['lrc_1h']['pct']}%   "
        f"{'✅ ZONA LONG (≤ 25%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] <= 25 else '🔴 ZONA SHORT (≥ 75%)' if rep['lrc_1h']['pct'] and rep['lrc_1h']['pct'] >= 75 else '⏳ Fuera de zona'}",
        f"  Upper  : ${rep['lrc_1h']['upper']}   |   Mid : ${rep['lrc_1h']['mid']}   |   Lower : ${rep['lrc_1h']['lower']}",
        f"  RSI 1H : {rep['rsi_1h']}  {'✅ Sobreventa' if rep['rsi_1h'] < 40 else ''}",
        DIV,
        "  ── CONTEXTO MACRO 4H ────────────────────────────────────",
        f"  SMA100 4H        : ${rep['macro_4h']['sma100']}",
        f"  Precio > SMA100  : {ok(rep['macro_4h']['price_above'])}  "
        f"({'alcista ✅' if rep['macro_4h']['price_above'] else 'bajista ⚠️ — solo operar si hay confluencia fuerte'})",
        DIV,
        f"  ── SCORE 1H : {rep['score']}/9  ({rep['score_label']}) ──────────────────",
    ]

    for k, v in rep.get("confirmations", {}).items():
        passed = v.get("pass")
        sym = ok(passed) if isinstance(passed, bool) else "❓"
        pts = v.get("pts", 0)
        extras = {ek: ev for ek, ev in v.items()
                  if ek not in ("pass", "pts", "max_pts", "nota")}
        nota = f"\n      → {v['nota']}" if "nota" in v else ""
        xs = ("  " + str(extras)) if extras else ""
        lines.append(f"    {sym} {k:<30} {pts}pts{xs}{nota}")

    lines += [DIV, "  ── GATILLO 5M  (precisión de entrada) ───────────────────"]
    gat = rep.get("gatillo_5m", {})

    def g_ok(b):
        return "✅" if b else "❌"

    lines += [
        f"    {g_ok(gat.get('vela_5m_alcista'))}  Vela 5M alcista (close > open)"
        f"  →  open ${gat.get('open_5m')} / close ${gat.get('close_5m')}",
        f"    {g_ok(gat.get('rsi_5m_recuperando'))}  RSI 5M recuperando"
        f"  →  {gat.get('rsi_5m_anterior')} → {gat.get('rsi_5m_actual')}",
        f"    {'✅ GATILLO ACTIVO' if rep.get('gatillo_activo') else '🕐 Gatillo inactivo — esperar próxima vela 5M'}",
    ]

    lines += [DIV, "  ── BLOQUEOS AUTOMÁTICOS ─────────────────────────────────"]
    if rep["blocks_auto"]:
        for b in rep["blocks_auto"]:
            lines.append(f"    🚫 {b}")
    else:
        lines.append("    ✅ Ningún bloqueo automático activo")

    lines += [DIV, "  ── VERIFICAR MANUALMENTE ANTES DE ENTRAR ─────────────────"]
    for k, v in rep.get("exclusions", {}).items():
        if isinstance(v, dict) and v.get("activo") == "VERIFICAR_MANUAL":
            lines.append(f"    📋 {k}: {v.get('nota','')}")

    lines += [DIV, "  ── SIZING  (ejemplo $1,000 capital) ──────────────────────"]
    sz = rep["sizing_1h"]
    lines += [
        f"    Riesgo 1%        : ${sz['riesgo_usd']}",
        f"    SL / TP          : {sz['sl_pct']} / {sz['tp_pct']}   →   R:R 2:1",
        f"    Precio SL        : ${sz['sl_precio']}",
        f"    Precio TP        : ${sz['tp_precio']}",
        f"    Cantidad BTC     : {sz['qty_btc']} BTC",
        f"    Valor posición   : ${sz['valor_pos']}  ({sz['pct_capital']}% del capital)",
    ]

    score = rep['score']
    if score >= SCORE_PREMIUM:
        lines.append(f"    💡 Score ≥ 4 → Puedes usar sizing +50% (riesgo hasta 1.5%)")
    elif score < SCORE_STANDARD:
        lines.append(f"    ⚠️  Score < 2 → Usar sizing 50% (riesgo 0.5%)")

    if rep.get("errors"):
        lines += [DIV, "  ADVERTENCIAS"]
        for e in rep["errors"]:
            lines.append(f"    ⚠️  {e}")

    lines.append(SEP)
    return "\n".join(lines)


def save_log(rep: dict, full_text: str) -> None:
    """Append scan output to logs/signals_log.txt with a per-state format."""
    SCRIPT_DIR_REPO = str(REPO_ROOT)
    estado = rep.get("estado", "")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if rep.get("señal_activa"):
            f.write(full_text + "\n\n")
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            score = rep.get("score", 0)
            sig_path = os.path.join(SCRIPT_DIR_REPO,
                                    f"SIGNAL_LONG_SCORE{score}_{ts_str}.txt")
            with open(sig_path, "w", encoding="utf-8") as sf:
                sf.write(full_text)
            print(f"\n  ⚡ ¡SEÑAL GUARDADA! → {sig_path}")
        elif "SETUP VÁLIDO" in estado:
            f.write(f"[{rep['timestamp']}] 🕐 SETUP VÁLIDO SIN GATILLO | "
                    f"${rep.get('price','?')} | LRC%: {rep.get('lrc_1h',{}).get('pct','?')} | "
                    f"Score: {rep.get('score', 0)}\n")
        else:
            f.write(f"[{rep['timestamp']}] {estado[:50]} | "
                    f"${rep.get('price','?')} | "
                    f"LRC%: {rep.get('lrc_1h',{}).get('pct','?')}\n")


def main() -> None:
    """Scanner CLI loop. Usage: python -m cli.scanner_report [--once] [SYMBOL]"""
    from btc_scanner import scan
    from data import market_data as md

    once = "--once" in sys.argv
    sym_arg = next((a for a in sys.argv[1:] if a != "--once"), None)

    print(f"\n{'='*65}")
    print(f"  CRYPTO SCANNER  |  Señal 1H + Gatillo 5M  |  Top 20 pares")
    print(f"  Log: {LOG_FILE}")
    if not once:
        print(f"  Revisa cada {SCAN_INTERVAL}s  |  Ctrl+C para detener")
    print(f"{'='*65}\n")

    while True:
        symbols = [sym_arg] if sym_arg else get_top_symbols(20)
        try:
            md.prefetch(symbols, ["5m", "1h", "4h"], limit=210)
        except Exception as e:
            log.warning("prefetch batch failed: %s", e)
        try:
            for sym in symbols:
                try:
                    rep = scan(sym)
                    text = fmt(rep)
                    print(text)
                    save_log(rep, text)
                except Exception as e:
                    print(f"\n  ❌ Error en {sym}: {e}\n")
                    with open(LOG_FILE, "a") as f:
                        f.write(f"[{datetime.now(timezone.utc)}] ERROR {sym}: {e}\n")
        except KeyboardInterrupt:
            print("\n\n  ⛔ Scanner detenido.\n")
            break

        if once:
            break

        print(f"\n  ⏳ Próximo ciclo en {SCAN_INTERVAL}s (Ctrl+C para detener)...\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
```

## Task 7.2: Update `btc_scanner.py` — re-export + remove + delegate `__main__`

- [ ] **Step 1: Add re-exports to imports**

```python
# Re-exports for backward compatibility — moved to cli/scanner_report.py per #225 PR7
from cli.scanner_report import (  # noqa: F401
    fmt, save_log, main, get_top_symbols,
    LOG_FILE, SCAN_INTERVAL, STABLECOINS,
)
```

- [ ] **Step 2: Delete originals from `btc_scanner.py`**

Remove:
- `STABLECOINS` constant (was lines 61-64)
- `LOG_FILE` + `os.makedirs(...)` (was lines 72-73)
- `SCAN_INTERVAL` (was line 75)
- `def get_top_symbols` (was lines 429-466)
- `def fmt` (was lines 1322-1406)
- `def save_log` (was lines 1413-1436)
- `def main` (was lines 1443-1481)

- [ ] **Step 3: Replace the `if __name__ == "__main__":` block**

Find:
```python
if __name__ == "__main__":
    main()
```

(The `main` resolved via re-export already works, but the explicit delegation makes the intent obvious.)

Replace with:
```python
if __name__ == "__main__":
    from cli.scanner_report import main as _cli_main
    _cli_main()
```

- [ ] **Step 4: Confirm**

```bash
grep -n "^def fmt\|^def save_log\|^def main\|^def get_top_symbols\|^STABLECOINS\|^LOG_FILE\|^SCAN_INTERVAL" btc_scanner.py
```
Expected: only the re-export `from cli.scanner_report import` line shows.

## Task 7.3: Identity test

**Files:**
- Create: `tests/test_cli_reexport.py`

- [ ] **Step 1: Write identity test**

```python
# tests/test_cli_reexport.py
def test_cli_reexport_identity():
    import btc_scanner
    from cli import scanner_report

    assert btc_scanner.fmt is scanner_report.fmt
    assert btc_scanner.save_log is scanner_report.save_log
    assert btc_scanner.main is scanner_report.main
    assert btc_scanner.get_top_symbols is scanner_report.get_top_symbols
    assert btc_scanner.LOG_FILE is scanner_report.LOG_FILE
    assert btc_scanner.SCAN_INTERVAL is scanner_report.SCAN_INTERVAL
    assert btc_scanner.STABLECOINS is scanner_report.STABLECOINS
```

- [ ] **Step 2: Run identity test**

```bash
pytest tests/test_cli_reexport.py -v
```
Expected: PASS.

## Task 7.4: CLI smoke (critical for PR7)

- [ ] **Step 1: Verify `python btc_scanner.py --once BTCUSDT` works**

```bash
timeout 60 python btc_scanner.py --once BTCUSDT 2>&1 | tee /tmp/pr7_smoke.log
grep -E "(ERROR|Traceback)" /tmp/pr7_smoke.log && echo FAIL || echo OK
```
Expected: `OK`. Output contains the formatted scan report.

- [ ] **Step 2: Verify `python -m cli.scanner_report --once BTCUSDT` works**

```bash
timeout 60 python -m cli.scanner_report --once BTCUSDT 2>&1 | tee /tmp/pr7_module_smoke.log
grep -E "(ERROR|Traceback)" /tmp/pr7_module_smoke.log && echo FAIL || echo OK
```
Expected: `OK`. Same output as above.

- [ ] **Step 3: Verify log file path unchanged**

```bash
tail -5 logs/signals_log.txt
```
Expected: recent entry from the smoke runs (timestamp matches).

## Task 7.5: Post-verify + commit + PR

- [ ] **Step 1: Post-verify**

```bash
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
wc -l btc_scanner.py
```
Expected: snapshot green; suite green; LOC ~520-540.

- [ ] **Step 2: Commit + PR**

```bash
git add btc_scanner.py cli/scanner_report.py tests/test_cli_reexport.py
git commit -m "refactor(scanner): extract cli/scanner_report.py — fmt + save_log + main + get_top_symbols (#225 PR7)"
git push -u origin refactor/scanner-pr7-cli
gh pr create --title "refactor(scanner): PR7 cli/scanner_report.py (#225)" --body "$(cat <<'EOF'
## Summary
Moves to `cli/scanner_report.py`:
- `fmt`, `save_log`, `main`, `get_top_symbols`
- `LOG_FILE`, `SCAN_INTERVAL`, `STABLECOINS` constants

Re-exports retained on `btc_scanner`. \`btc_scanner.py:__main__\` rewritten to delegate via \`from cli.scanner_report import main as _cli_main; _cli_main()\` — \`python btc_scanner.py [--once] [SYMBOL]\` continues to work unchanged. \`LOG_FILE\` resolves to the same path string (\`<repo>/logs/signals_log.txt\`) via REPO_ROOT computation. ~250 LOC moved.

## Risks-touched (from spec §8)
- [x] Re-export omission — mitigated by `tests/test_cli_reexport.py`
- [ ] Module-global identity drift — N/A
- [ ] Monkeypatch namespace — N/A
- [x] Snapshot regen sin review — snapshot still byte-equal
- [ ] Kill switch v2 calibrator — N/A
- [x] CLI behavior drift — mitigated: smoke verifies \`python btc_scanner.py --once BTCUSDT\` writes to \`logs/signals_log.txt\` at the expected path

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_cli_reexport.py -v
PASSED
$ pytest tests/ -q
<full suite green>
$ python btc_scanner.py --once BTCUSDT
<one cycle, formatted output, log appended to logs/signals_log.txt>
$ python -m cli.scanner_report --once BTCUSDT
<same output via the module entrypoint>
$ wc -l btc_scanner.py
~520
\`\`\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR8 — Cleanup audit

**Branch:** `refactor/scanner-pr8-cleanup` (off `main` after all of PR1-PR7 merged)

Mirror of PR #227 role. Audit `btc_scanner.py` re-exports; remove those whose callers all migrated; document those retained.

## Task 8.0: Pre-verify + branch

- [ ] **Step 1: Pre-verify on main**

```bash
git checkout main && git pull
pytest tests/test_scanner_snapshot.py -v
pytest tests/ -q
wc -l btc_scanner.py
```
Expected: snapshot green; suite green; LOC ~520-540.

- [ ] **Step 2: Branch**

```bash
git checkout -b refactor/scanner-pr8-cleanup
```

## Task 8.1: Audit `btc_scanner.py` re-exports

- [ ] **Step 1: List all re-exports**

```bash
grep -n "^from \(strategy\|infra\|cli\)\." btc_scanner.py
```

Expected: 6-7 re-export lines (one per home module).

- [ ] **Step 2: For each re-exported name, count external callers via grep**

```bash
# For each of the moved names — grep across the repo (excluding btc_scanner.py itself, the home module, and docs/specs)
for name in detect_bull_engulfing detect_bear_engulfing detect_rsi_divergence score_label \
            check_trigger_5m check_trigger_5m_short \
            resolve_direction_params metrics_inc_direction_disabled ATR_SL_MULT ATR_TP_MULT ATR_BE_MULT \
            _classify_tune_result \
            annualized_vol_yang_zhang TARGET_VOL_ANNUAL VOL_LOOKBACK_DAYS \
            _load_proxy _rate_limit \
            detect_regime get_cached_regime detect_regime_for_symbol \
            fmt save_log main get_top_symbols LOG_FILE SCAN_INTERVAL STABLECOINS; do
  count=$(grep -rE "from btc_scanner import.*\\b${name}\\b" --include='*.py' \
            --exclude-dir=docs --exclude-dir=__pycache__ . | wc -l)
  echo "$name: $count callers"
done
```

- [ ] **Step 3: Categorize each re-export**

For each re-export, decide:
- **Keep** if `count > 0` (external callers still using `from btc_scanner import …`).
- **Remove** if `count == 0` (no callers — re-export is dead weight).

Document the decision in a comment block in `btc_scanner.py`:

```python
# ── BACKWARD-COMPAT RE-EXPORTS ────────────────────────────────────────────────
# These are intentional re-exports preserved for callers that still import
# `from btc_scanner import …`. Each has at least one external caller as of
# PR8 cleanup (#225). Removing one without migrating its callers will
# cause silent ImportErrors at module load time.
#
# To remove a re-export in the future:
#   1. Run `grep -r "from btc_scanner import .*<name>" --include='*.py'`.
#   2. If callers exist, migrate them to import from the home module first.
#   3. Then remove the re-export line.
```

## Task 8.2: Remove dead re-exports

- [ ] **Step 1: For each re-export with `count == 0`, remove it**

For example, if `_classify_tune_result` shows `0 callers` (because PR3 migrated `scripts/apply_tune_to_config.py`):

Remove from `btc_scanner.py`:
```python
from strategy.tune import _classify_tune_result  # noqa: F401
```

And from the corresponding identity test (it'll fail otherwise — just keep the test asserting via the home module).

- [ ] **Step 2: Run identity tests for the names you DID NOT remove**

```bash
pytest tests/test_patterns_reexport.py tests/test_direction_reexport.py \
       tests/test_tune_reexport.py tests/test_vol_reexport.py \
       tests/test_http_reexport.py tests/test_regime_reexport.py \
       tests/test_cli_reexport.py -v
```

- [ ] **Step 3: Remove identity tests for removed names**

If the corresponding identity test asserts identity for a name we just removed, delete that line. (Example: if we removed `_classify_tune_result` re-export, delete the corresponding `assert btc_scanner._classify_tune_result is tune._classify_tune_result` line.)

If the entire test file becomes empty/trivial, leave it — it's a useful breadcrumb for future maintenance.

## Task 8.3: Final post-verify

- [ ] **Step 1: Snapshot still green**

```bash
pytest tests/test_scanner_snapshot.py -v
```
Expected: PASS.

- [ ] **Step 2: Full suite green**

```bash
pytest tests/ -q
```
Expected: green-bar.

- [ ] **Step 3: API boot smoke**

```bash
python btc_api.py &
SCANNER_PID=$!
sleep 5
curl -s http://localhost:8000/health | jq .status
kill $SCANNER_PID 2>/dev/null
wait $SCANNER_PID 2>/dev/null
```
Expected: `"ok"`.

- [ ] **Step 4: Final LOC check**

```bash
wc -l btc_scanner.py strategy/regime.py strategy/patterns.py strategy/direction.py \
      strategy/tune.py strategy/vol.py infra/http.py cli/scanner_report.py
```
Expected: btc_scanner.py ≤ 540; each new module < 300.

## Task 8.4: Update import boundaries test

**Files:**
- Modify: `tests/test_import_boundaries.py`

- [ ] **Step 1: Read existing test for context**

```bash
cat tests/test_import_boundaries.py
```

- [ ] **Step 2: Add boundary rules for new modules**

Append to the test (adapt to existing test structure):

```python
def test_strategy_regime_no_circular_imports():
    """strategy.regime must not import api/, db/, scanner/, cli/, btc_scanner."""
    import ast
    src = open("strategy/regime.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("api.", "db.", "scanner.", "cli.")), \
                f"strategy/regime.py must not import {node.module}"
            assert node.module != "btc_scanner", \
                f"strategy/regime.py must not import btc_scanner"


def test_strategy_patterns_no_external_strategy_imports():
    """strategy.patterns may only import from strategy.{constants,indicators}."""
    import ast
    src = open("strategy/patterns.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("strategy."):
                assert mod in ("strategy.constants", "strategy.indicators"), \
                    f"strategy/patterns.py must not import {mod}"


def test_infra_http_pure():
    """infra.http imports only stdlib + requests."""
    import ast
    src = open("infra/http.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod in ("", "pathlib") or not mod.startswith(("strategy.", "btc_scanner", "api.", "db.")), \
                f"infra/http.py must not import {mod}"


def test_cli_scanner_report_no_api_db_imports():
    """cli.scanner_report must not import api/ or db/."""
    import ast
    src = open("cli/scanner_report.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith(("api.", "db.")), \
                f"cli/scanner_report.py must not import {mod}"
```

- [ ] **Step 3: Run boundary tests**

```bash
pytest tests/test_import_boundaries.py -v
```
Expected: all PASS.

## Task 8.5: Create follow-up issue

- [ ] **Step 1: Open follow-up issue for `scan()` carve-up**

```bash
gh issue create --title "refactor(scanner): evaluate scan() carve-up via scanner/report.py adapter (post-#225)" --body "$(cat <<'EOF'
## Context

After #225 lands, `btc_scanner.py` is ~520 LOC dominated by the `scan()` function (~449 LOC). The remaining size is mostly `scan()`'s "report-shape" code: engulfing recompute, LONG/SHORT score branches, exclusions dict, sizing dict, blocks_long/short, estado branches, clean_dict.

## Question

Should we extract a `scanner/report.py` adapter:

```python
def build_report(decision, df1h, df5, df4h, _cfg, _so, regime_data, _health_state) -> dict:
    ...
```

Then `scan()` becomes:
```python
def scan(symbol):
    df5, df1h, df4h = fetch_data(symbol)
    cfg = load_config()
    health_state = get_symbol_state(symbol)
    decision = evaluate_signal(...)
    return build_report(decision, df1h, df5, df4h, cfg, ..., health_state)
```

Reducing `btc_scanner.py` to ~80-100 LOC.

## Trade-off

- **Pro:** Clean boundary; testable adapter; meets the original `<200 LOC` aspiration of #225.
- **Con:** Widens the parity surface; introduces a function with 8+ args; scan()'s linear narrative becomes a call chain.

## Recommendation

Evaluate after living with the post-#225 layout for ~2 weeks. If `scan()` editing pain remains, do this. Otherwise leave it — the per-piece refactor already accomplished the legibility win.

## Spec reference

`docs/superpowers/specs/es/2026-04-28-refactor-btc-scanner-por-proposito-design.md` §10.1
EOF
)"
```

## Task 8.6: Commit + PR

- [ ] **Step 1: Commit**

```bash
git add btc_scanner.py tests/test_import_boundaries.py tests/test_*_reexport.py
git commit -m "refactor(scanner): cleanup audit — prune dead re-exports + boundary tests (#225 PR8)"
```

- [ ] **Step 2: Push + PR**

```bash
git push -u origin refactor/scanner-pr8-cleanup
gh pr create --title "refactor(scanner): PR8 cleanup audit + boundary tests (#225 closes #225)" --body "$(cat <<'EOF'
## Summary
Final cleanup of the per-purpose scanner refactor (#225):
- Audited every `btc_scanner.py` re-export. Removed those with no remaining callers.
- Documented retained re-exports with a comment block explaining why each persists.
- Extended `tests/test_import_boundaries.py` with rules for `strategy/regime`, `strategy/patterns`, `infra/http`, `cli/scanner_report`.
- Opened follow-up issue (URL emitted by `gh issue create` in Task 8.5) for evaluating `scan()` carve-up via `scanner/report.py`.

## Risks-touched (from spec §8)
- [x] Re-export omission — final boundary test catches any remaining holes
- [x] Module-global identity drift — N/A (no globals removed; all retained)
- [ ] Monkeypatch namespace — N/A (fixture stable since PR6)
- [x] Snapshot regen sin review — snapshot still byte-equal (8th consecutive PR)
- [ ] Kill switch v2 calibrator — N/A
- [ ] CLI behavior drift — verified preserved in PR7

## Verification log
\`\`\`
$ pytest tests/test_scanner_snapshot.py -v
PASSED
$ pytest tests/test_import_boundaries.py -v
PASSED (additional 4 tests)
$ pytest tests/ -q
<full suite green>
$ wc -l btc_scanner.py
~510-530
$ python btc_api.py & ; sleep 5; curl -s http://localhost:8000/health | jq .status
"ok"
\`\`\`

Closes #225.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# Final state — Definition of done

After PR8 merges:

- [ ] `wc -l btc_scanner.py` ≤ 540 (esperado ~510-530)
- [ ] 5 new modules in `strategy/`: `regime.py`, `patterns.py`, `direction.py`, `tune.py`, `vol.py`
- [ ] 1 new module in `infra/`: `http.py`
- [ ] 1 new module in `cli/`: `scanner_report.py`
- [ ] `tests/_baselines/scan_btcusdt.json` snapshot byte-equal across all 9 PRs
- [ ] 7 `tests/test_*_reexport.py` files (one per move PR), all green
- [ ] `tests/test_import_boundaries.py` extended, green
- [ ] `pytest tests/ -q` green-bar maintained throughout
- [ ] `python btc_scanner.py --once BTCUSDT` runs without errors
- [ ] `python btc_api.py` boots; `/health` returns `"ok"`
- [ ] PR8 cleanup auditing committed
- [ ] Follow-up issue created for `scan()` carve-up evaluation
