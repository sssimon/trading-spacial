# Refactor btc_api.py por dominio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Descomponer `btc_api.py` (2618 LOC) en 10 módulos `api/`, 4 módulos `db/`, y `scanner/runtime.py`, dejando `btc_api.py` ≤ 200 LOC como bootstrap FastAPI. Eliminar la triplicación de constantes de indicadores creando `strategy/constants.py`.

**Architecture:** 8 PRs secuenciales. PR0 sienta la base (constants compartidas, scaffolding `api/`+`db/`+`scanner/`, capa DB extraída, helpers de testing). PR1-PR6 mueven un dominio por PR usando snapshots JSON como tests de paridad: capturar baseline → escribir test fallando → mover código → re-export en `btc_api.py` → verificar paridad. PR7 finaliza migrando `scanner_loop` a `scanner/runtime.py`, eliminando re-exports temporales y dejando `btc_api.py` como bootstrap puro.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, FastAPI TestClient. Mismas dependencias que el resto del proyecto.

**Spec:** `docs/superpowers/specs/es/2026-04-27-refactor-btc-api-por-dominio-design.md`

---

## File structure

### Created

```
strategy/constants.py                   (~30 LOC)
api/__init__.py                         (empty)
api/deps.py                             (~40 LOC)
api/ohlcv.py                            (~80 LOC)
api/config.py                           (~180 LOC)
api/telegram.py                         (~200 LOC)
api/notifications.py                    (~80 LOC)
api/positions.py                        (~350 LOC)
api/signals.py                          (~500 LOC)
api/kill_switch.py                      (~150 LOC)
api/health.py                           (~80 LOC)
api/tune.py                             (~120 LOC)
db/__init__.py                          (empty)
db/connection.py                        (~100 LOC)
db/schema.py                            (~250 LOC)
db/positions.py                         (~150 LOC)
db/signals.py                           (~200 LOC)
scanner/__init__.py                     (empty)
scanner/runtime.py                      (~250 LOC)
tests/_baselines/<domain>.json          (one per PR1-PR6)
tests/_baseline_capture.py              (~80 LOC)
tests/test_import_boundaries.py         (~60 LOC)
tests/test_scanner_smoke.py             (~50 LOC)
tests/test_api_<domain>_parity.py       (one per PR1-PR6)
```

### Modified

```
btc_api.py                              (2618 → ≤200 LOC final)
btc_scanner.py                          (only constants imports, lines 67-73, 412-422)
strategy/core.py                        (only constants imports, lines 39-56)
strategy/sizing.py                      (only constants imports, lines 8-9)
```

---

# PHASE PR0 — Foundation

Goal: crear `strategy/constants.py`, scaffolding de `api/`+`db/`+`scanner/`, extraer capa de conexión DB, y helpers de testing. Sin mover endpoints todavía.

---

## Task 1: Create strategy/constants.py

**Files:**
- Create: `strategy/constants.py`
- Test: `tests/test_strategy_constants.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_strategy_constants.py`:

```python
"""Verify strategy.constants is the single source of truth for indicator
periods, score tiers, and LRC zone thresholds."""
from strategy import constants as K


def test_indicator_periods():
    assert K.LRC_PERIOD == 100
    assert K.LRC_STDEV == 2.0
    assert K.RSI_PERIOD == 14
    assert K.BB_PERIOD == 20
    assert K.BB_STDEV == 2.0
    assert K.VOL_PERIOD == 20
    assert K.ATR_PERIOD == 14


def test_atr_defaults():
    assert K.ATR_SL_MULT_DEFAULT == 1.0
    assert K.ATR_TP_MULT_DEFAULT == 4.0
    assert K.ATR_BE_MULT_DEFAULT == 1.5


def test_lrc_zone_thresholds():
    assert K.LRC_LONG_MAX == 25.0
    assert K.LRC_SHORT_MIN == 75.0


def test_score_tiers():
    assert K.SCORE_MIN_HALF == 0
    assert K.SCORE_STANDARD == 2
    assert K.SCORE_PREMIUM == 4
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_strategy_constants.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'strategy.constants'`.

- [ ] **Step 3: Create strategy/constants.py**

```python
"""Shared trading constants — single source of truth for indicator periods,
score tiers, and LRC zone thresholds. Importable by btc_scanner, strategy/core,
strategy/sizing without circular dependencies (this module imports nothing).

Created in PR0 to eliminate the triplication that existed pre-2026-04-27 in
btc_scanner.py:67-73,412-422 / strategy/core.py:39-56 / strategy/sizing.py:8-9.
"""
from __future__ import annotations

# Indicator periods
LRC_PERIOD = 100
LRC_STDEV = 2.0
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STDEV = 2.0
VOL_PERIOD = 20
ATR_PERIOD = 14

# ATR multiplier defaults (used when symbol_overrides has no per-symbol value)
ATR_SL_MULT_DEFAULT = 1.0
ATR_TP_MULT_DEFAULT = 4.0
ATR_BE_MULT_DEFAULT = 1.5

# LRC zone thresholds (entry windows)
LRC_LONG_MAX = 25.0   # LRC% ≤ 25 → LONG entry zone
LRC_SHORT_MIN = 75.0  # LRC% ≥ 75 → SHORT entry zone (gated by regime=BEAR)

# Score tier thresholds (Spot V6, 0-9 scale)
SCORE_MIN_HALF = 0    # below this → don't enter
SCORE_STANDARD = 2    # 0-1 = 0.5x size, 2-3 = 1.0x, ≥4 = 1.5x
SCORE_PREMIUM = 4
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_strategy_constants.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Run full test suite to verify no regression**

```bash
python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: same number of passing tests as baseline (628+) plus 4 new = 632+ passing.

---

## Task 2: Migrate btc_scanner.py constants to strategy.constants

**Files:**
- Modify: `btc_scanner.py:67-73,412-422`

- [ ] **Step 1: Add import at top of btc_scanner.py**

Add after the existing imports (find the import block near line 30-50, add this line at the end of it):

```python
from strategy.constants import (
    LRC_PERIOD, LRC_STDEV, RSI_PERIOD, BB_PERIOD, BB_STDEV, VOL_PERIOD,
    ATR_PERIOD, ATR_SL_MULT_DEFAULT, ATR_TP_MULT_DEFAULT, ATR_BE_MULT_DEFAULT,
    LRC_LONG_MAX, LRC_SHORT_MIN, SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
)
```

- [ ] **Step 2: Delete the local definitions at lines 67-73**

Find and delete:
```python
LRC_PERIOD     = 100
LRC_STDEV      = 2.0
RSI_PERIOD     = 14
BB_PERIOD      = 20
BB_STDEV       = 2.0
VOL_PERIOD     = 20
ATR_PERIOD     = 14
```

- [ ] **Step 3: Delete the local definitions around lines 412-422**

Find and delete:
```python
LRC_LONG_MAX   = 25.0     # LRC% ≤ 25  →  zona de entrada
LRC_SHORT_MIN  = 75.0     # LRC% >= 75  →  zona de entrada SHORT
SCORE_MIN_HALF  = 0       # Mínimo para entrar (sizing reducido)
SCORE_STANDARD  = 2
SCORE_PREMIUM   = 4
```

(Also delete any standalone constants in that block; preserve any non-constant code if interleaved.)

- [ ] **Step 4: Run scanner tests**

```bash
python -m pytest tests/test_scanner.py -v 2>&1 | tail -5
```

Expected: PASS (existing scanner tests should keep passing — same numeric values).

- [ ] **Step 5: Verify identity (object equality)**

```bash
python -c "import btc_scanner, strategy.constants; assert btc_scanner.LRC_PERIOD is strategy.constants.LRC_PERIOD"
```

Expected: no output, exit 0.

---

## Task 3: Migrate strategy/core.py constants to strategy.constants

**Files:**
- Modify: `strategy/core.py:30-56`

- [ ] **Step 1: Read current strategy/core.py:30-56 to confirm shape**

```bash
sed -n '30,56p' strategy/core.py
```

- [ ] **Step 2: Add import to strategy/core.py**

Find the existing imports block (around line 20-28). After it, add:

```python
from strategy.constants import (
    LRC_PERIOD, LRC_STDEV, RSI_PERIOD, BB_PERIOD, BB_STDEV, VOL_PERIOD,
    ATR_PERIOD, ATR_SL_MULT_DEFAULT, ATR_TP_MULT_DEFAULT, ATR_BE_MULT_DEFAULT,
    LRC_LONG_MAX, LRC_SHORT_MIN, SCORE_MIN_HALF, SCORE_STANDARD, SCORE_PREMIUM,
)
```

- [ ] **Step 3: Delete the local block at lines 30-56**

Delete the entire block from the `# Strategy parameters — kept in sync...` comment (line 35) through `SCORE_PREMIUM = 4` (line 56), inclusive. Replace the deleted block with a 2-line comment:

```python
# Strategy parameters now live in strategy/constants.py (single source of truth).
# Imported above. The duplication that lived here pre-2026-04-27 was eliminated.
```

- [ ] **Step 4: Run strategy/core tests**

```bash
python -m pytest tests/test_strategy_core.py -v 2>&1 | tail -5
```

Expected: PASS.

---

## Task 4: Migrate strategy/sizing.py constants to strategy.constants

**Files:**
- Modify: `strategy/sizing.py:1-15`

- [ ] **Step 1: Read current strategy/sizing.py:1-15**

```bash
sed -n '1,15p' strategy/sizing.py
```

- [ ] **Step 2: Replace local constants with import**

Find:
```python
SCORE_PREMIUM = 4  # threshold for 1.5x
SCORE_STANDARD = 2  # threshold for 1.0x (else 0.5x)
```

Replace with:
```python
from strategy.constants import SCORE_PREMIUM, SCORE_STANDARD
```

- [ ] **Step 3: Run sizing tests**

```bash
python -m pytest tests/test_strategy_sizing.py -v 2>&1 | tail -5
```

Expected: PASS.

- [ ] **Step 4: Run full suite to confirm no regression**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 632+ passing.

---

## Task 5: Scaffold api/, db/, scanner/ packages

**Files:**
- Create: `api/__init__.py`
- Create: `api/deps.py`
- Create: `db/__init__.py`
- Create: `scanner/__init__.py`
- Create: `tests/_baselines/.gitkeep`

- [ ] **Step 1: Create empty package init files**

```bash
mkdir -p api db scanner tests/_baselines
echo '"""API layer — FastAPI routers and per-domain services."""' > api/__init__.py
echo '"""DB layer — SQLite connection, schema, and per-domain queries."""' > db/__init__.py
echo '"""Scanner runtime — background scan loop and threading."""' > scanner/__init__.py
touch tests/_baselines/.gitkeep
```

- [ ] **Step 2: Create api/deps.py with verify_api_key**

```python
"""Shared FastAPI dependencies (auth, etc.).

verify_api_key replicates the guard that lived in btc_api.py pre-refactor.
The API key is read from config.json["api_key"] at request time (not at
startup) so config reloads pick up new keys without restart.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status


def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests whose X-API-Key header doesn't match config.api_key.

    No-op if config has no api_key set (dev mode). Uses constant-time
    comparison via hmac.compare_digest to avoid timing oracles.
    """
    # Lazy import to avoid circular dep with api/config.py once that lands.
    from api.config import load_config  # noqa: PLC0415

    cfg = load_config()
    expected = cfg.get("api_key") or os.environ.get("BTC_API_KEY")
    if not expected:
        return  # dev mode — no auth configured

    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
        )
```

Note: `api.config.load_config` doesn't exist yet — it lands in PR2. Until then, `api/deps.py` is unused; PR0 just creates the file. We'll wire `Depends(verify_api_key)` into routers as each domain moves.

- [ ] **Step 3: Verify Python can import the empty packages**

```bash
python -c "import api, db, scanner; print('packages importable')"
```

Expected: `packages importable`.

---

## Task 6: Extract DB connection layer to db/connection.py

**Files:**
- Create: `db/connection.py`
- Modify: `btc_api.py` (re-export for compatibility)

- [ ] **Step 1: Read current btc_api.py:804-857 (backup_db, _DictRow, get_db)**

```bash
sed -n '804,857p' btc_api.py
```

- [ ] **Step 2: Create db/connection.py**

```python
"""DB connection layer — SQLite handle factory + row factory + backup.

Extracted from btc_api.py (PR0 of the api+db domain refactor, 2026-04-27).

Design:
- get_db() returns a fresh sqlite3.Connection per call (no singleton).
  This is critical for thread safety: scanner_loop and FastAPI request
  handlers share the same DB file but each opens its own connection.
- _DictRow is a tuple subclass that supports both indexed access (row[0])
  AND dict-style access (row["column"]). It exists because health
  persistence tests rely on tuple equality while route code wants
  dict-style. sqlite3.Row doesn't support equality the way we need.
- backup_db copies signals.db to a timestamped file in data/db_backups/
  and prunes to keep only the most recent _BACKUP_MAX_FILES.
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("db.connection")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(_SCRIPT_DIR, "signals.db")
_BACKUP_DIR = os.path.join(_SCRIPT_DIR, "data", "db_backups")
_BACKUP_MAX_FILES = 14


class _DictRow(tuple):
    """Row factory that behaves as a plain tuple (supports == comparison) while
    also supporting dict-style access via row["column"] and row.get("column")."""

    def __new__(cls, cursor, row):
        instance = super().__new__(cls, row)
        instance._mapping = {
            desc[0]: val for desc, val in zip(cursor.description, row)
        }
        return instance

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._mapping[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def keys(self):
        return self._mapping.keys()


def get_db() -> sqlite3.Connection:
    """Open a fresh DB connection with the dict-row factory."""
    con = sqlite3.connect(DB_FILE)
    con.row_factory = _DictRow
    return con


def backup_db() -> None:
    """Copy signals.db to data/db_backups/signals_<UTC>.db, prune oldest beyond _BACKUP_MAX_FILES."""
    try:
        os.makedirs(_BACKUP_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = os.path.join(_BACKUP_DIR, f"signals_{ts}.db")
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, dest)
            log.info(f"DB backup created: {dest}")
        backups = sorted(glob.glob(os.path.join(_BACKUP_DIR, "signals_*.db")))
        for old in backups[:-_BACKUP_MAX_FILES]:
            os.remove(old)
            log.info(f"DB backup removed: {old}")
    except Exception as e:
        log.warning(f"DB backup failed: {e}")
```

- [ ] **Step 3: Modify btc_api.py to re-export from db.connection**

Find the existing `_DictRow` class definition (line 829) and `get_db` function (line 853) and `backup_db` function (line ~804). Replace all three (and their preceding comments) with:

```python
# DB connection layer moved to db/connection.py in PR0 of the api+db domain
# refactor (2026-04-27). Re-exports preserved for compatibility until PR7.
from db.connection import get_db, backup_db, _DictRow, DB_FILE  # noqa: F401
```

Keep the existing `DB_FILE = os.path.join(SCRIPT_DIR, "signals.db")` constant if other parts of btc_api.py still reference the local one — the re-export above shadows it. Verify by grepping.

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 632+ passing (no regression).

- [ ] **Step 5: Smoke-test that btc_api.py still boots**

```bash
timeout 5 python -c "
import sys; sys.path.insert(0, '.')
from btc_api import get_db, backup_db, _DictRow
con = get_db()
con.close()
print('OK')
"
```

Expected: `OK`.

---

## Task 7: Extract DB schema to db/schema.py

**Files:**
- Create: `db/schema.py`
- Modify: `btc_api.py` (re-export init_db)

- [ ] **Step 1: Locate init_db in btc_api.py**

```bash
grep -n "^def init_db" btc_api.py
```

Expected: a single line number (around 859).

- [ ] **Step 2: Read the full init_db function**

```bash
sed -n '859,1108p' btc_api.py
```

- [ ] **Step 3: Create db/schema.py**

Copy the entire `init_db()` function body (from the function signature through the final `con.close()` and any closing logging) into `db/schema.py`. Add module docstring and update the `from db.connection import get_db` line at the top:

```python
"""DB schema — table definitions and migrations.

Extracted from btc_api.py:859-1108 (PR0 of the api+db domain refactor).

init_db() is idempotent: CREATE TABLE IF NOT EXISTS for all tables, plus
ALTER TABLE statements wrapped in try/except to handle the case where
the column already exists (sqlite3 has no IF NOT EXISTS for ALTER).

Tables:
- scans (one row per scan; signal=1 if score reached threshold)
- webhooks_sent (audit trail of webhook deliveries)
- positions (open/closed positions; CRUD via db/positions.py)
- signal_outcomes (1h/4h/24h price tracking for back-validation)
- ... (see CREATE TABLE statements below)
"""
from __future__ import annotations

import logging
import sqlite3

from db.connection import get_db

log = logging.getLogger("db.schema")


def init_db() -> None:
    """Create or migrate all tables. Idempotent."""
    con = get_db()
    con.execute("PRAGMA journal_mode=WAL")
    # ... paste the rest of the init_db body here, preserving every CREATE TABLE
    # and migration block exactly. Do NOT change SQL.
    # ... at the end:
    con.commit()
    con.close()
```

The plan does not reproduce the full SQL here — it's ~200 lines. Copy verbatim from `btc_api.py:859-1108`. The only changes:
- Replace top-of-function `con = get_db()` if missing.
- Remove any references to module-level globals that lived in `btc_api.py` (e.g., logger references — use `log = logging.getLogger("db.schema")` at module level).

- [ ] **Step 4: Replace init_db in btc_api.py with re-export**

Replace the entire `def init_db(): ...` definition in `btc_api.py` with:

```python
# DB schema moved to db/schema.py in PR0 of the api+db domain refactor.
from db.schema import init_db  # noqa: F401
```

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 632+ passing.

- [ ] **Step 6: Smoke-test init_db on fresh DB**

```bash
rm -f /tmp/test_init.db
DB_FILE=/tmp/test_init.db python -c "
from db.schema import init_db
import db.connection
db.connection.DB_FILE = '/tmp/test_init.db'
init_db()
import sqlite3
con = sqlite3.connect('/tmp/test_init.db')
tables = [r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
print('tables:', sorted(tables))
"
```

Expected: list including `scans`, `positions`, `webhooks_sent`, `signal_outcomes`, etc.

---

## Task 8: Create tests/_baseline_capture.py helper

**Files:**
- Create: `tests/_baseline_capture.py`
- Create: `tests/_baselines/README.md`

- [ ] **Step 1: Create tests/_baselines/README.md**

```markdown
# API parity baselines

Each domain has a `<domain>.json` file capturing the HTTP responses produced
by `btc_api.py` against a deterministically seeded DB. Tests at
`tests/test_api_<domain>_parity.py` assert the post-refactor response matches
the baseline byte-for-byte.

## Regenerating a baseline

ONLY do this if the response format intentionally changed. Otherwise, a
mismatch is a real bug.

    python -m tests._baseline_capture <domain> > tests/_baselines/<domain>.json
    git add tests/_baselines/<domain>.json
    git commit -m "test(parity): regenerate <domain> baseline (<reason>)"
```

- [ ] **Step 2: Create tests/_baseline_capture.py**

```python
"""Capture HTTP response baselines from btc_api.py for parity testing.

Usage:
    python -m tests._baseline_capture <domain> > tests/_baselines/<domain>.json

Where <domain> is one of: ohlcv, config, telegram, positions, signals,
kill_switch, health, tune, notifications.

The script:
1. Spins up a TestClient against the current btc_api.app.
2. Seeds an in-memory or temp DB with deterministic fixtures.
3. Issues a fixed set of HTTP requests per domain.
4. Dumps {request_label: {status: int, body: <json>}} to stdout.

Determinism requirements: fixtures use fixed timestamps, fixed scan IDs,
and seed=42 for any randomness. The baseline is committed to git.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Callable

from fastapi.testclient import TestClient


def _seed_minimal(con) -> None:
    """Insert minimal fixtures shared across domains: 2 scans, 1 position."""
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (1, '2026-01-15T10:00:00Z', 'BTCUSDT', 'NEUTRAL', 0, 0, 50000.0, 30.0, 45.0, 2, 'standard', 1, 0, '{}')"
    )
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (2, '2026-01-15T10:05:00Z', 'BTCUSDT', 'LONG', 1, 0, 50000.0, 20.0, 40.0, 5, 'premium', 1, 1, '{\"sl\": 49000.0, \"tp\": 54000.0}')"
    )
    con.execute(
        "INSERT INTO positions (id, scan_id, symbol, direction, status, entry_price, entry_ts, sl_price, tp_price, size_usd, qty) "
        "VALUES (1, 2, 'BTCUSDT', 'LONG', 'open', 50000.0, '2026-01-15T10:05:00Z', 49000.0, 54000.0, 100.0, 0.002)"
    )
    con.commit()


def _capture_ohlcv(client: TestClient) -> dict[str, Any]:
    """Capture /ohlcv responses. Note: this hits md.get_klines_live which is
    network — for parity we mock it via a fixture, but the simplest baseline
    is the empty case (returns {symbol, interval, candles: [], volumes: []})."""
    resp = client.get("/ohlcv?symbol=BTCUSDT&interval=1h&limit=10")
    return {
        "GET /ohlcv?symbol=BTCUSDT&interval=1h&limit=10": {
            "status": resp.status_code,
            "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        }
    }


def _capture_config(client: TestClient) -> dict[str, Any]:
    resp = client.get("/config")
    return {
        "GET /config": {
            "status": resp.status_code,
            "body": resp.json(),
        }
    }


# ... add similar _capture_<domain> functions per PR.

CAPTURERS: dict[str, Callable[[TestClient], dict[str, Any]]] = {
    "ohlcv":         _capture_ohlcv,
    "config":        _capture_config,
    # registered as each PR adds its capturer
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in CAPTURERS:
        print(f"Usage: python -m tests._baseline_capture <{ '|'.join(CAPTURERS.keys()) }>", file=sys.stderr)
        sys.exit(1)

    domain = sys.argv[1]

    # Use a temp DB file to avoid touching production signals.db
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        # Override DB_FILE before importing btc_api
        os.environ["DB_FILE"] = db_path
        # Lazy imports — must happen AFTER env var is set
        import db.connection as dbconn
        dbconn.DB_FILE = db_path

        from db.schema import init_db
        init_db()

        from db.connection import get_db
        con = get_db()
        _seed_minimal(con)
        con.close()

        from btc_api import app
        client = TestClient(app)

        result = CAPTURERS[domain](client)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the helper boots without error**

```bash
python -m tests._baseline_capture ohlcv > /tmp/_ohlcv_smoke.json && head -3 /tmp/_ohlcv_smoke.json
```

Expected: a JSON file starts with `{` and includes a `GET /ohlcv...` key.

(The actual baseline content will be saved when each PR captures its domain.)

---

## Task 9: Create tests/test_import_boundaries.py + tests/test_scanner_smoke.py

**Files:**
- Create: `tests/test_import_boundaries.py`
- Create: `tests/test_scanner_smoke.py`

- [ ] **Step 1: Create tests/test_import_boundaries.py**

```python
"""Verify import boundaries between layers (anti-cycle, anti-drift).

Rules (per spec §3.2):
- api/* may import: db/*, strategy/*, scanner/*, health, notifications
- api/* must NOT import: btc_api
- db/* must NOT import: api/*, scanner/*
- scanner/* must NOT import: api/* (routers); api/telegram is OK as service
- strategy/* must NOT import anything outside strategy/

Implementation: walk the AST of each module file and check imports
against an allowlist + denylist.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest


PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# (folder, denylist) pairs — modules in <folder> must not import any name from <denylist>
DENYLIST_RULES = [
    ("api",     ["btc_api"]),
    ("db",      ["api", "scanner", "btc_api"]),
    ("scanner", ["api.ohlcv", "api.config", "api.positions", "api.signals",
                 "api.kill_switch", "api.health", "api.tune", "api.notifications",
                 "btc_api"]),  # api.telegram is allowed (service, not router)
    ("strategy", ["api", "db", "scanner", "btc_api", "btc_scanner"]),
]


def _imports_in_file(path: pathlib.Path) -> list[str]:
    """Return list of imported module names in a Python file."""
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


@pytest.mark.parametrize("folder,denylist", DENYLIST_RULES)
def test_import_boundaries(folder: str, denylist: list[str]) -> None:
    folder_path = PROJECT_ROOT / folder
    if not folder_path.exists():
        pytest.skip(f"{folder}/ does not exist yet")

    violations = []
    for py_file in folder_path.rglob("*.py"):
        if py_file.name.startswith("_"):
            continue
        imports = _imports_in_file(py_file)
        for imp in imports:
            for denied in denylist:
                if imp == denied or imp.startswith(denied + "."):
                    violations.append(f"{py_file.relative_to(PROJECT_ROOT)} imports {imp!r} (denied: {denied})")

    assert not violations, "Import boundary violations:\n  " + "\n  ".join(violations)
```

- [ ] **Step 2: Run import boundary test**

```bash
python -m pytest tests/test_import_boundaries.py -v
```

Expected: PASS (folders exist but are empty/scaffold-only — no imports to violate).

- [ ] **Step 3: Create tests/test_scanner_smoke.py**

```python
"""Smoke test: the scanner thread can boot and execute one cycle without
crashing, given a seeded DB. This catches re-export regressions (PR7-style
issues where a function moved to api/* but scanner_loop still imports the
old btc_api name).

Runs in <2 seconds. Mocked HTTP/Telegram.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def isolated_db(monkeypatch):
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", db_path)
    from db.schema import init_db
    init_db()
    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)


def test_scanner_executes_single_cycle(isolated_db, monkeypatch):
    """A single scan cycle on a seeded DB must complete without exceptions."""
    # Mock outbound HTTP / Telegram to avoid network
    monkeypatch.setattr("requests.post", MagicMock(return_value=MagicMock(status_code=200, ok=True)))
    monkeypatch.setattr("requests.get", MagicMock(return_value=MagicMock(status_code=200, ok=True, json=lambda: [])))

    # Import here so monkeypatches are in place
    from btc_scanner import scan
    # scan(symbol=...) returns a report dict; should not raise
    try:
        report = scan(symbol="BTCUSDT")
    except Exception as e:
        pytest.fail(f"scan() raised: {e}")
    # Minimal sanity: report has a symbol
    if report:
        assert "symbol" in report or "estado" in report, f"Unexpected report shape: {report}"
```

- [ ] **Step 4: Run smoke test**

```bash
python -m pytest tests/test_scanner_smoke.py -v
```

Expected: PASS (or SKIP gracefully if scan() requires live data — adjust the fixture accordingly if it fails on data fetching by mocking `data.market_data.get_klines`).

- [ ] **Step 5: Commit PR0**

```bash
git add strategy/constants.py \
        api/__init__.py api/deps.py \
        db/__init__.py db/connection.py db/schema.py \
        scanner/__init__.py \
        tests/_baselines/.gitkeep tests/_baselines/README.md \
        tests/_baseline_capture.py \
        tests/test_strategy_constants.py \
        tests/test_import_boundaries.py \
        tests/test_scanner_smoke.py \
        btc_scanner.py strategy/core.py strategy/sizing.py btc_api.py

git commit -m "$(cat <<'EOF'
refactor(api): PR0 — foundation for domain decomposition

- Create strategy/constants.py as single source of truth for indicator
  periods, score tiers, and LRC zone thresholds (eliminates triplication
  in btc_scanner.py:67-73,412-422 / strategy/core.py:39-56 / strategy/sizing.py:8-9).
- Scaffold api/, db/, scanner/ packages with __init__.py + api/deps.py.
- Extract DB connection layer (get_db, _DictRow, backup_db) → db/connection.py.
- Extract DB schema (init_db) → db/schema.py.
- Add tests/_baseline_capture.py helper for parity-test baselines.
- Add tests/test_import_boundaries.py (anti-drift, anti-cycle).
- Add tests/test_scanner_smoke.py (catches re-export regressions).

btc_api.py keeps re-exports for compatibility until PR7. No endpoints moved
yet. Spec: docs/superpowers/specs/es/2026-04-27-refactor-btc-api-por-dominio-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR1 — ohlcv

Goal: move `/ohlcv` route to `api/ohlcv.py`. Smallest, lowest-risk domain (read-only, no DB).

---

## Task 10: Capture ohlcv baseline + write parity test

**Files:**
- Create: `tests/_baselines/ohlcv.json`
- Create: `tests/test_api_ohlcv_parity.py`
- Modify: `tests/_baseline_capture.py` (add ohlcv-specific captures with mocked fetcher)

- [ ] **Step 1: Update `_capture_ohlcv` in `tests/_baseline_capture.py`**

Replace the placeholder with a version that mocks `data.market_data.get_klines_live` to return a deterministic DataFrame:

```python
def _capture_ohlcv(client: TestClient) -> dict[str, Any]:
    """Capture /ohlcv with mocked fetcher returning a fixed DataFrame."""
    import pandas as pd
    from unittest.mock import patch

    fixed_df = pd.DataFrame({
        "open_time": [1736899200000 + i * 3_600_000 for i in range(5)],
        "open":      [50000.0, 50100.0, 50050.0, 50200.0, 50300.0],
        "high":      [50500.0, 50400.0, 50300.0, 50500.0, 50600.0],
        "low":       [49800.0, 49900.0, 49850.0, 50000.0, 50100.0],
        "close":     [50100.0, 50050.0, 50200.0, 50300.0, 50400.0],
        "volume":    [10.0, 12.0, 8.0, 15.0, 11.0],
    })

    out: dict[str, Any] = {}
    with patch("data.market_data.get_klines_live", return_value=fixed_df):
        for url in [
            "/ohlcv?symbol=BTCUSDT&interval=1h&limit=5",
            "/ohlcv?symbol=ETHUSDT&interval=4h&limit=5",
            "/ohlcv?symbol=BTCUSDT&interval=invalid&limit=5",
        ]:
            resp = client.get(url)
            out[f"GET {url}"] = {
                "status": resp.status_code,
                "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
            }
    return out
```

- [ ] **Step 2: Capture the baseline**

```bash
python -m tests._baseline_capture ohlcv > tests/_baselines/ohlcv.json
cat tests/_baselines/ohlcv.json | head -20
```

Expected: a JSON file with three top-level keys (one per URL), each having `status` and `body`.

- [ ] **Step 3: Create tests/test_api_ohlcv_parity.py**

```python
"""Parity test for /ohlcv: response after refactor must match baseline."""
from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "ohlcv.json"


@pytest.fixture
def fixed_klines_df() -> pd.DataFrame:
    return pd.DataFrame({
        "open_time": [1736899200000 + i * 3_600_000 for i in range(5)],
        "open":      [50000.0, 50100.0, 50050.0, 50200.0, 50300.0],
        "high":      [50500.0, 50400.0, 50300.0, 50500.0, 50600.0],
        "low":       [49800.0, 49900.0, 49850.0, 50000.0, 50100.0],
        "close":     [50100.0, 50050.0, 50200.0, 50300.0, 50400.0],
        "volume":    [10.0, 12.0, 8.0, 15.0, 11.0],
    })


@pytest.fixture
def client(fixed_klines_df, monkeypatch):
    monkeypatch.setattr("data.market_data.get_klines_live", lambda *a, **kw: fixed_klines_df)
    from btc_api import app
    return TestClient(app)


def test_ohlcv_responses_match_baseline(client):
    expected = json.loads(BASELINE_PATH.read_text())
    for url_label, expected_resp in expected.items():
        method, url = url_label.split(" ", 1)
        assert method == "GET"
        actual = client.get(url)
        assert actual.status_code == expected_resp["status"], f"status mismatch for {url}"
        actual_body = actual.json() if actual.headers.get("content-type", "").startswith("application/json") else actual.text
        assert actual_body == expected_resp["body"], f"body mismatch for {url}"
```

- [ ] **Step 4: Run the test against current (un-moved) btc_api.py**

```bash
python -m pytest tests/test_api_ohlcv_parity.py -v
```

Expected: PASS (baseline was just captured against the same code, so it must match itself).

---

## Task 11: Create api/ohlcv.py and wire into btc_api.py

**Files:**
- Create: `api/ohlcv.py`
- Modify: `btc_api.py` (delete inline route, mount router)

- [ ] **Step 1: Create api/ohlcv.py**

```python
"""OHLCV route — returns candle data for the frontend chart.

Extracted from btc_api.py:2164-2195 in PR1 of the api+db refactor.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from data import market_data as md

router = APIRouter(tags=["ohlcv"])

_VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}


@router.get("/ohlcv", summary="Velas OHLCV para graficar")
def get_ohlcv(
    symbol:   str = Query("BTCUSDT", description="Par de trading (ej: ETHUSDT)"),
    interval: str = Query("1h",      description="Intervalo: 5m,15m,1h,4h,1d"),
    limit:    int = Query(300,       ge=1, le=1000, description="Número de velas"),
):
    """Retorna datos OHLCV listos para lightweight-charts (timestamps en segundos UTC).
    Usa md.get_klines_live() — incluye la barra en curso para el gráfico animado."""
    if interval not in _VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Intervalo invalido: {interval}")
    try:
        df = md.get_klines_live(symbol.upper(), interval, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error obteniendo OHLCV: {e}")

    if df.empty:
        return {"symbol": symbol.upper(), "interval": interval, "candles": [], "volumes": []}

    candles, volumes = [], []
    for _, row in df.iterrows():
        ts = int(row["open_time"]) // 1000  # ms → seconds for lightweight-charts
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        v = float(row["volume"])
        candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
        volumes.append({
            "time":  ts,
            "value": v,
            "color": "rgba(34,197,94,0.35)" if c >= o else "rgba(239,68,68,0.35)",
        })

    return {"symbol": symbol.upper(), "interval": interval, "candles": candles, "volumes": volumes}
```

- [ ] **Step 2: Modify btc_api.py — delete inline /ohlcv route, mount router**

Find the existing `@app.get("/ohlcv", ...)` block (around line 2164) and the `def get_ohlcv(...)` function. Delete both (the decorator + the function definition).

After the line `app.add_middleware(...)` block (around line 1611-1616), add:

```python
from api.ohlcv import router as ohlcv_router
app.include_router(ohlcv_router)
```

- [ ] **Step 3: Run parity test**

```bash
python -m pytest tests/test_api_ohlcv_parity.py -v
```

Expected: PASS (response shape unchanged).

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 633+ passing (existing tests + 1 new parity test).

- [ ] **Step 5: Run import boundaries check**

```bash
python -m pytest tests/test_import_boundaries.py -v
```

Expected: PASS.

- [ ] **Step 6: Smoke test btc_api.py**

```bash
python btc_api.py &
APP_PID=$!
sleep 2
curl -s "http://localhost:8000/ohlcv?symbol=BTCUSDT&interval=1h&limit=5" | head -c 200
kill $APP_PID
```

Expected: a JSON response starting with `{"symbol":"BTCUSDT"...}`.

- [ ] **Step 7: Commit PR1**

```bash
git add api/ohlcv.py tests/_baselines/ohlcv.json tests/test_api_ohlcv_parity.py \
        tests/_baseline_capture.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR1 — extract /ohlcv to api/ohlcv.py

Move the /ohlcv route to a dedicated APIRouter in api/ohlcv.py. Adds
parity test with snapshot baseline (tests/_baselines/ohlcv.json) verifying
the response shape is byte-for-byte identical pre/post-move.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR2 — config

Goal: move `/config` GET+POST routes and `load_config`, `save_config`, `_deep_merge`, `_load_json_file`, `_strip_secrets`, `ConfigUpdate`, `SignalFiltersUpdate` to `api/config.py`. No `db/` (config is file-based).

---

## Task 12: Capture config baseline + write parity test

**Files:**
- Modify: `tests/_baseline_capture.py` (add `_capture_config` proper + register)
- Create: `tests/_baselines/config.json`
- Create: `tests/test_api_config_parity.py`

- [ ] **Step 1: Update `_capture_config` in `tests/_baseline_capture.py`**

Replace the placeholder with:

```python
def _capture_config(client: TestClient) -> dict[str, Any]:
    """Capture /config GET (with secrets stripped) and POST scenarios."""
    out: dict[str, Any] = {}

    # GET /config — secrets must be stripped
    r = client.get("/config")
    out["GET /config"] = {"status": r.status_code, "body": r.json()}

    # POST /config (no auth header → 401 if api_key set, 200 if dev mode)
    r = client.post("/config", json={"signal_filters": {"min_score": 5}})
    out["POST /config (no auth)"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}

    # POST /config (with auth header) — assumes test config has api_key="test-key"
    r = client.post("/config",
                    json={"signal_filters": {"min_score": 5}},
                    headers={"X-API-Key": "test-key"})
    out["POST /config (with auth)"] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}

    return out
```

Also: ensure the seed step in `main()` writes a test config.json so this domain has predictable state. Add to `main()` before `from btc_api import app`:

```python
# Write a test config.json
test_cfg = {
    "api_key": "test-key",
    "webhook_url": "http://test.local/hook",
    "telegram_bot_token": "test-token",
    "telegram_chat_id": "test-chat",
    "signal_filters": {"min_score": 4, "require_macro_ok": False, "notify_setup": False},
    "scan_interval_sec": 300,
    "num_symbols": 20,
    "proxy": "",
}
import json as _json
test_cfg_path = os.path.join(os.path.dirname(db_path), "config.json")
with open(test_cfg_path, "w") as f:
    _json.dump(test_cfg, f)
os.environ["CONFIG_FILE"] = test_cfg_path
```

(If `btc_api.py` reads CONFIG_FILE from a constant, this needs to monkeypatch the constant instead. Adjust based on the actual code.)

- [ ] **Step 2: Capture the baseline**

```bash
python -m tests._baseline_capture config > tests/_baselines/config.json
head -30 tests/_baselines/config.json
```

Expected: JSON with three keys (GET, POST no auth, POST with auth).

- [ ] **Step 3: Create tests/test_api_config_parity.py**

```python
"""Parity test for /config endpoints."""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "config.json"


@pytest.fixture
def client(monkeypatch, tmp_path):
    test_cfg = {
        "api_key": "test-key",
        "webhook_url": "http://test.local/hook",
        "telegram_bot_token": "test-token",
        "telegram_chat_id": "test-chat",
        "signal_filters": {"min_score": 4, "require_macro_ok": False, "notify_setup": False},
        "scan_interval_sec": 300,
        "num_symbols": 20,
        "proxy": "",
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(test_cfg))

    monkeypatch.setenv("CONFIG_FILE", str(cfg_path))
    # If btc_api.py uses a module-level constant CONFIG_FILE, monkeypatch it:
    import btc_api
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)

    from btc_api import app
    return TestClient(app)


def test_config_responses_match_baseline(client):
    expected = json.loads(BASELINE_PATH.read_text())
    for url_label, expected_resp in expected.items():
        method, url = url_label.split(" ", 1)[0], url_label.split(" ", 1)[1].split(" ")[0]
        # url_label like "GET /config" or "POST /config (no auth)"
        # Parse: extract method and URL
        parts = url_label.split(" ")
        method = parts[0]
        url = parts[1]
        is_auth = "(with auth)" in url_label

        if method == "GET":
            r = client.get(url)
        elif method == "POST":
            headers = {"X-API-Key": "test-key"} if is_auth else {}
            r = client.post(url, json={"signal_filters": {"min_score": 5}}, headers=headers)
        else:
            pytest.fail(f"Unexpected method {method}")

        assert r.status_code == expected_resp["status"], f"status mismatch for {url_label}"
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        assert body == expected_resp["body"], f"body mismatch for {url_label}"
```

- [ ] **Step 4: Run parity test against current btc_api.py**

```bash
python -m pytest tests/test_api_config_parity.py -v
```

Expected: PASS.

---

## Task 13: Create api/config.py and wire into btc_api.py

**Files:**
- Create: `api/config.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Read current functions in btc_api.py**

```bash
sed -n '164,326p' btc_api.py     # _deep_merge, load_config, save_config, models
sed -n '2139,2163p' btc_api.py   # /config GET + POST routes
```

- [ ] **Step 2: Create api/config.py**

```python
"""Config domain — load/save/validate config.json + endpoints.

Extracted from btc_api.py in PR2 of the api+db refactor.

Config is layered from three files:
- config.json (legacy + Simon's prod overrides)
- config.defaults.json (committed defaults + symbol_overrides)
- config.secrets.json (gitignored: telegram/webhook creds)

load_config() does deep_merge(defaults, secrets, legacy). save_config()
writes only to config.json (legacy file) for backwards compatibility.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_api_key

log = logging.getLogger("api.config")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("CONFIG_FILE", os.path.join(_SCRIPT_DIR, "config.json"))
DEFAULTS_FILE = os.path.join(_SCRIPT_DIR, "config.defaults.json")
SECRETS_FILE = os.path.join(_SCRIPT_DIR, "config.secrets.json")

# Sensitive keys are stripped from GET /config responses.
_SECRET_KEYS = {"telegram_bot_token", "telegram_chat_id", "webhook_url", "api_key"}

router = APIRouter(tags=["config"])


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive merge: dicts merge, other types replace. Used to layer config files."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_json_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    """Layered config: defaults < secrets < legacy. Idempotent."""
    cfg = _load_json_file(DEFAULTS_FILE)
    cfg = _deep_merge(cfg, _load_json_file(SECRETS_FILE))
    cfg = _deep_merge(cfg, _load_json_file(CONFIG_FILE))
    return cfg


def _strip_secrets(cfg: dict) -> dict:
    """Return a copy with sensitive keys removed/redacted (recursive on nested dicts)."""
    out = {}
    for k, v in cfg.items():
        if k in _SECRET_KEYS:
            out[k] = "***" if v else ""
        elif isinstance(v, dict):
            out[k] = _strip_secrets(v)
        else:
            out[k] = v
    return out


class SignalFiltersUpdate(BaseModel):
    min_score:        Optional[int]  = Field(default=None, ge=0, le=9)
    require_macro_ok: Optional[bool] = None
    notify_setup:     Optional[bool] = None


class ConfigUpdate(BaseModel):
    signal_filters:   Optional[SignalFiltersUpdate] = None
    scan_interval_sec: Optional[int]                = Field(default=None, ge=10)
    num_symbols:       Optional[int]                = Field(default=None, ge=1, le=100)
    proxy:             Optional[str]                = None


def save_config(updates: dict) -> dict:
    """Merge `updates` into config.json (legacy file) and persist. Returns the new full layered config."""
    current = _load_json_file(CONFIG_FILE)
    merged = _deep_merge(current, updates)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    return load_config()


@router.get("/config", summary="Leer configuracion actual")
def get_config():
    return _strip_secrets(load_config())


@router.post("/config", summary="Actualizar configuracion", dependencies=[Depends(verify_api_key)])
def update_config(body: ConfigUpdate):
    try:
        updates = body.model_dump(exclude_none=True)
        new_cfg = save_config(updates)
        return {"ok": True, "config": _strip_secrets(new_cfg)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 3: Modify btc_api.py — delete moved code, mount router, keep re-exports**

Find and delete:
- The `_deep_merge` function (line 164)
- The `_load_json_file` function (line 175)
- The `load_config` function (line 182)
- The `_strip_secrets` function (line 280)
- `class SignalFiltersUpdate` (line 289)
- `class ConfigUpdate` (line 295)
- The `save_config` function (line 307)
- The `@app.get("/config", ...)` route + `def get_config()` (lines 2139-2147)
- The `@app.post("/config", ...)` route + `def update_config()` (lines 2148-2163)

After the existing `app.include_router(ohlcv_router)` line, add:

```python
from api.config import router as config_router, load_config  # noqa: F401  (load_config re-exported for legacy)
app.include_router(config_router)
```

- [ ] **Step 4: Run parity test**

```bash
python -m pytest tests/test_api_config_parity.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 634+ passing.

- [ ] **Step 6: Smoke test**

```bash
python btc_api.py &
APP_PID=$!
sleep 2
curl -s http://localhost:8000/config | head -c 200
kill $APP_PID
```

Expected: JSON config without secrets.

- [ ] **Step 7: Commit PR2**

```bash
git add api/config.py tests/_baselines/config.json tests/test_api_config_parity.py \
        tests/_baseline_capture.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR2 — extract config domain to api/config.py

Move load_config, save_config, _deep_merge, _strip_secrets, ConfigUpdate,
SignalFiltersUpdate, and /config GET+POST routes to api/config.py.
Parity test with baseline snapshot ensures the response (with secrets
stripped) is identical pre/post-move.

btc_api.py re-exports load_config for legacy callers (scanner_loop, etc.)
until PR7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR3 — telegram

Goal: move telegram/webhook outbound publishing helpers (no signal-filter logic — that's PR5) to `api/telegram.py`. Service module, no router exposed (telegram is outbound-only; in-app notifications go in PR6).

---

## Task 14: Move telegram service to api/telegram.py

**Files:**
- Create: `api/telegram.py`
- Create: `tests/test_api_telegram_unit.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Read current functions in btc_api.py**

```bash
sed -n '1226,1404p' btc_api.py   # build_telegram_message, push_telegram_direct, _send_telegram_raw, push_webhook
```

- [ ] **Step 2: Create api/telegram.py**

Copy the four functions verbatim into `api/telegram.py` with this header (replace internal references to `load_config` with `from api.config import load_config`, and references to logger with `log = logging.getLogger("api.telegram")`):

```python
"""Telegram + webhook outbound delivery.

Extracted from btc_api.py:1226-1436 in PR3 of the api+db refactor.

This is a service module (no APIRouter exposed) — telegram is outbound-only.
In-app /notifications endpoints live in api/notifications.py.

Functions:
- build_telegram_message(rep) — format a signal report as Telegram-ready text
- push_telegram_direct(rep, cfg) — send via Telegram Bot API (with rate limiting)
- _send_telegram_raw(message, cfg) — low-level HTTP POST to Telegram
- push_webhook(rep, scan_id, cfg) — send to configured webhook URL (n8n etc.)

All HTTP calls use requests with a configurable proxy from cfg["proxy"].
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests as req_lib

from api.config import load_config

log = logging.getLogger("api.telegram")


def build_telegram_message(rep: dict) -> str:
    """[paste the body of build_telegram_message from btc_api.py:1226 verbatim]"""
    # COPY verbatim from btc_api.py
    ...


def push_telegram_direct(rep: dict, cfg: dict) -> bool:
    """[paste verbatim from btc_api.py:1308]"""
    ...


def _send_telegram_raw(message: str, cfg: dict) -> bool:
    """[paste verbatim from btc_api.py:1344]"""
    ...


def push_webhook(rep: dict, scan_id: int, cfg: dict) -> bool:
    """[paste verbatim from btc_api.py:1365]"""
    ...
```

(The plan does NOT reproduce the bodies — copy verbatim. ~200 LOC total.)

- [ ] **Step 3: Create tests/test_api_telegram_unit.py**

```python
"""Unit tests for api/telegram.py — verify message format unchanged."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def cfg():
    return {
        "telegram_bot_token": "test-token",
        "telegram_chat_id": "test-chat",
        "webhook_url": "http://test.local/hook",
        "proxy": "",
    }


@pytest.fixture
def signal_rep():
    return {
        "symbol": "BTCUSDT",
        "estado": "LONG",
        "score": 5,
        "score_label": "premium",
        "lrc_pct": 20.0,
        "rsi_1h": 40.0,
        "macro_ok": True,
        "gatillo": True,
        "price": 50000.0,
        "sl": 49000.0,
        "tp": 54000.0,
    }


def test_build_message_contains_symbol_and_score(signal_rep):
    from api.telegram import build_telegram_message
    msg = build_telegram_message(signal_rep)
    assert "BTCUSDT" in msg
    assert "5" in msg or "premium" in msg


def test_send_telegram_raw_uses_bot_api(cfg):
    from api.telegram import _send_telegram_raw
    with patch("api.telegram.req_lib.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, ok=True)
        ok = _send_telegram_raw("hello", cfg)
        assert ok is True
        url = mock_post.call_args[0][0]
        assert "api.telegram.org" in url
        assert "test-token" in url


def test_push_webhook_handles_failure(signal_rep, cfg):
    from api.telegram import push_webhook
    with patch("api.telegram.req_lib.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, ok=False)
        ok = push_webhook(signal_rep, scan_id=1, cfg=cfg)
        assert ok is False
```

- [ ] **Step 4: Modify btc_api.py — delete moved functions, add re-exports**

Delete the four functions (`build_telegram_message`, `push_telegram_direct`, `_send_telegram_raw`, `push_webhook`) from `btc_api.py`. Replace with:

```python
# Telegram service moved to api/telegram.py in PR3 of the api+db refactor.
# Re-exports preserved until PR7 (scanner_loop still imports these names).
from api.telegram import (  # noqa: F401
    build_telegram_message,
    push_telegram_direct,
    _send_telegram_raw,
    push_webhook,
)
```

- [ ] **Step 5: Run unit tests**

```bash
python -m pytest tests/test_api_telegram_unit.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 637+ passing.

- [ ] **Step 7: Run import boundaries check**

```bash
python -m pytest tests/test_import_boundaries.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit PR3**

```bash
git add api/telegram.py tests/test_api_telegram_unit.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR3 — extract telegram service to api/telegram.py

Move build_telegram_message, push_telegram_direct, _send_telegram_raw,
push_webhook from btc_api.py to api/telegram.py. No APIRouter — service
module only (telegram is outbound). Signal-filter logic (should_notify_signal,
_is_duplicate_signal, _mark_notified) stays in btc_api.py until PR5 since
it's signal-domain.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR4 — positions

Goal: split positions into `db/positions.py` (CRUD queries) and `api/positions.py` (router + check_position_stops + _calc_pnl + helpers).

---

## Task 15: Capture positions baseline + write parity test

**Files:**
- Modify: `tests/_baseline_capture.py`
- Create: `tests/_baselines/positions.json`
- Create: `tests/test_api_positions_parity.py`

- [ ] **Step 1: Add `_capture_positions` to `tests/_baseline_capture.py`**

Add a function that exercises the position CRUD endpoints with the seeded position from `_seed_minimal`:

```python
def _capture_positions(client: TestClient) -> dict[str, Any]:
    out: dict[str, Any] = {}

    r = client.get("/positions?status=all")
    out["GET /positions?status=all"] = {"status": r.status_code, "body": r.json()}

    r = client.get("/positions?status=open")
    out["GET /positions?status=open"] = {"status": r.status_code, "body": r.json()}

    r = client.get("/positions?status=closed")
    out["GET /positions?status=closed"] = {"status": r.status_code, "body": r.json()}

    # POST without auth → 401
    r = client.post("/positions", json={"symbol": "ETHUSDT", "entry_price": 3000.0})
    out["POST /positions (no auth)"] = {"status": r.status_code, "body": r.json()}

    # POST with auth → 200 + new position
    r = client.post("/positions",
                    json={"symbol": "ETHUSDT", "entry_price": 3000.0,
                          "sl_price": 2900.0, "tp_price": 3300.0,
                          "size_usd": 100.0, "qty": 0.033, "direction": "LONG"},
                    headers={"X-API-Key": "test-key"})
    out["POST /positions (auth)"] = {"status": r.status_code, "body": r.json()}

    # Edit position 1 (set notes)
    r = client.put("/positions/1",
                   json={"notes": "test note"},
                   headers={"X-API-Key": "test-key"})
    out["PUT /positions/1"] = {"status": r.status_code, "body": r.json()}

    return out
```

Register in CAPTURERS dict:
```python
"positions": _capture_positions,
```

- [ ] **Step 2: Capture baseline**

```bash
python -m tests._baseline_capture positions > tests/_baselines/positions.json
head -40 tests/_baselines/positions.json
```

Expected: JSON with 6 keys.

- [ ] **Step 3: Create tests/test_api_positions_parity.py**

```python
"""Parity test for /positions endpoints."""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient


BASELINE_PATH = pathlib.Path(__file__).parent / "_baselines" / "positions.json"


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"

    import db.connection as dbconn
    monkeypatch.setattr(dbconn, "DB_FILE", str(db_path))

    from db.schema import init_db
    init_db()

    from db.connection import get_db
    con = get_db()
    con.execute(
        "INSERT INTO scans (id, ts, symbol, estado, señal, setup, price, lrc_pct, rsi_1h, score, score_label, macro_ok, gatillo, payload) "
        "VALUES (2, '2026-01-15T10:05:00Z', 'BTCUSDT', 'LONG', 1, 0, 50000.0, 20.0, 40.0, 5, 'premium', 1, 1, '{}')"
    )
    con.execute(
        "INSERT INTO positions (id, scan_id, symbol, direction, status, entry_price, entry_ts, sl_price, tp_price, size_usd, qty) "
        "VALUES (1, 2, 'BTCUSDT', 'LONG', 'open', 50000.0, '2026-01-15T10:05:00Z', 49000.0, 54000.0, 100.0, 0.002)"
    )
    con.commit()
    con.close()

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setenv("CONFIG_FILE", str(cfg_path))
    import btc_api
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)

    from btc_api import app
    return TestClient(app)


def test_positions_match_baseline(client):
    expected = json.loads(BASELINE_PATH.read_text())
    for label, expected_resp in expected.items():
        parts = label.split(" ")
        method, url = parts[0], parts[1]
        is_auth = "(auth)" in label
        body_data = None
        if "POST /positions (auth)" in label:
            body_data = {"symbol": "ETHUSDT", "entry_price": 3000.0,
                         "sl_price": 2900.0, "tp_price": 3300.0,
                         "size_usd": 100.0, "qty": 0.033, "direction": "LONG"}
        elif "POST /positions (no auth)" in label:
            body_data = {"symbol": "ETHUSDT", "entry_price": 3000.0}
        elif "PUT /positions/1" in label:
            body_data = {"notes": "test note"}

        headers = {"X-API-Key": "test-key"} if is_auth else {}
        if method == "GET":
            r = client.get(url)
        elif method == "POST":
            r = client.post(url, json=body_data, headers=headers)
        elif method == "PUT":
            r = client.put(url, json=body_data, headers=headers)
        else:
            pytest.fail(f"Unexpected method {method}")

        assert r.status_code == expected_resp["status"], f"status mismatch for {label}"
        assert r.json() == expected_resp["body"], f"body mismatch for {label}"
```

- [ ] **Step 4: Run parity test against current btc_api.py**

```bash
python -m pytest tests/test_api_positions_parity.py -v
```

Expected: PASS.

---

## Task 16: Create db/positions.py + api/positions.py

**Files:**
- Create: `db/positions.py`
- Create: `api/positions.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Read current functions in btc_api.py**

```bash
sed -n '496,740p' btc_api.py     # _calc_pnl, db_create_position, db_get_positions, db_close_position, db_update_position, check_position_stops, _write_position_event_log, update_positions_json
sed -n '2200,2257p' btc_api.py   # /positions routes
```

- [ ] **Step 2: Create db/positions.py**

```python
"""Positions DB layer — CRUD queries.

Extracted from btc_api.py in PR4 of the api+db refactor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_db

log = logging.getLogger("db.positions")


def db_create_position(data: dict) -> dict:
    """[paste verbatim from btc_api.py:506]"""
    ...


def db_get_positions(status: Optional[str] = None) -> list:
    """[paste verbatim from btc_api.py:538]"""
    ...


def db_close_position(pos_id: int, exit_price: float, exit_reason: str) -> Optional[dict]:
    """[paste verbatim from btc_api.py:552]"""
    ...


def db_update_position(pos_id: int, data: dict) -> Optional[dict]:
    """[paste verbatim from btc_api.py:580]"""
    ...
```

(Copy bodies verbatim. Replace any references to globals like `_BACKUP_DIR` or `log` with module-local equivalents.)

- [ ] **Step 3: Create api/positions.py**

```python
"""Positions API — router + side-effects (stops checking, event logging, JSON snapshot).

Extracted from btc_api.py in PR4. Uses db/positions.py for queries.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from typing import Optional

from api.deps import verify_api_key
from db.positions import (
    db_create_position, db_get_positions, db_close_position, db_update_position,
)

log = logging.getLogger("api.positions")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_SCRIPT_DIR, "data")
LOGS_DIR = os.path.join(_SCRIPT_DIR, "logs")
POSITIONS_JSON_FILE = os.path.join(DATA_DIR, "positions_summary.json")
POSITIONS_LOG_FILE = os.path.join(LOGS_DIR, "positions.log")

router = APIRouter(prefix="/positions", tags=["positions"])


def _calc_pnl(direction: str, entry: float, exit_p: float, qty: float):
    """[paste verbatim from btc_api.py:496]"""
    ...


def _write_position_event_log(pos: dict, reason: str, exit_price: float):
    """[paste verbatim from btc_api.py:682]"""
    ...


def update_positions_json():
    """[paste verbatim from btc_api.py:704]"""
    ...


def check_position_stops(symbol: str, price: float):
    """[paste verbatim from btc_api.py:595 — ~85 LOC]

    Note: this function calls db_close_position and update_positions_json
    when a stop is hit. It also calls api.telegram.push_telegram_direct on
    BE/SL/TP transitions. Update internal imports accordingly.
    """
    ...


@router.get("", summary="Listar posiciones")
def list_positions(status: Optional[str] = Query("all", description="open | closed | all")):
    positions = db_get_positions(status)
    return {"total": len(positions), "positions": positions}


@router.post("", summary="Abrir nueva posicion", dependencies=[Depends(verify_api_key)])
def open_position(body: dict = Body(...)):
    required = {"symbol", "entry_price"}
    missing  = required - body.keys()
    if missing:
        raise HTTPException(status_code=422, detail=f"Faltan campos: {missing}")
    try:
        pos = db_create_position(body)
        update_positions_json()
        return {"ok": True, "position": pos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{pos_id}", summary="Editar posicion (SL/TP/notas)", dependencies=[Depends(verify_api_key)])
def edit_position(pos_id: int, body: dict = Body(...)):
    """[paste verbatim from btc_api.py:2222 — adapt @app.put to @router.put]"""
    ...


@router.post("/{pos_id}/close", summary="Cerrar posicion manualmente", dependencies=[Depends(verify_api_key)])
def close_position(pos_id: int, body: dict = Body(...)):
    """[paste verbatim from btc_api.py:2231 — adapt to @router]"""
    ...


@router.delete("/{pos_id}", summary="Cancelar/eliminar posicion", dependencies=[Depends(verify_api_key)])
def delete_position(pos_id: int):
    """[paste verbatim from btc_api.py:2245 — adapt to @router]"""
    ...
```

- [ ] **Step 4: Modify btc_api.py — delete moved code, mount router, keep re-exports**

Delete:
- `_calc_pnl`, `db_create_position`, `db_get_positions`, `db_close_position`, `db_update_position`, `check_position_stops`, `_write_position_event_log`, `update_positions_json`
- All 5 `/positions*` route definitions

Add after `app.include_router(config_router)`:

```python
from api.positions import router as positions_router  # noqa: F401
from db.positions import (  # noqa: F401  (re-exports for scanner_loop until PR7)
    db_create_position, db_get_positions, db_close_position, db_update_position,
)
from api.positions import check_position_stops, update_positions_json  # noqa: F401
app.include_router(positions_router)
```

- [ ] **Step 5: Run parity test**

```bash
python -m pytest tests/test_api_positions_parity.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 638+ passing.

- [ ] **Step 7: Commit PR4**

```bash
git add api/positions.py db/positions.py tests/_baselines/positions.json \
        tests/test_api_positions_parity.py tests/_baseline_capture.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR4 — split positions into api/positions.py + db/positions.py

Move position CRUD queries to db/positions.py. Move position routes,
check_position_stops, _calc_pnl, _write_position_event_log,
update_positions_json to api/positions.py. Parity test with snapshot
baseline ensures CRUD endpoints unchanged.

btc_api.py re-exports CRUD names for scanner_loop until PR7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR5 — signals (heaviest)

Goal: split signals into `db/signals.py` (queries) and `api/signals.py` (router + filters + dedup + CSV/log appenders + read-only outcomes tracking).

---

## Task 17: Capture signals baseline + write parity test

**Files:**
- Modify: `tests/_baseline_capture.py`
- Create: `tests/_baselines/signals.json`
- Create: `tests/test_api_signals_parity.py`

- [ ] **Step 1: Add `_capture_signals` to baseline_capture**

```python
def _capture_signals(client: TestClient) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for url in [
        "/signals",
        "/signals?limit=10",
        "/signals?only_signals=true",
        "/signals?min_score=4",
        "/signals/latest",
        "/signals/latest?symbol=BTCUSDT",
        "/signals/latest/message",
        "/signals/latest/message?symbol=BTCUSDT",
        "/signals/performance",
        "/signals/2",     # by ID — uses seeded scan_id=2
    ]:
        r = client.get(url)
        out[f"GET {url}"] = {
            "status": r.status_code,
            "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text,
        }
    return out
```

Register `"signals": _capture_signals` in CAPTURERS.

- [ ] **Step 2: Capture baseline**

```bash
python -m tests._baseline_capture signals > tests/_baselines/signals.json
```

- [ ] **Step 3: Create tests/test_api_signals_parity.py**

(Same structure as positions parity: load baseline, iterate URL labels, assert match.)

- [ ] **Step 4: Run parity test against current btc_api.py**

```bash
python -m pytest tests/test_api_signals_parity.py -v
```

Expected: PASS.

---

## Task 18: Create db/signals.py + api/signals.py

**Files:**
- Create: `db/signals.py`
- Create: `api/signals.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Identify functions to move**

Functions in btc_api.py to move to `db/signals.py` (queries only):
- `save_scan` (line 1109)
- `get_scans` (line 1151)
- `get_latest_signal` (line 1177)
- `get_latest_scan` (line 1192)
- `get_signals_summary` (line 1205)

Functions in btc_api.py to move to `api/signals.py`:
- `should_notify_signal` (line 327)
- `_is_duplicate_signal` (line 363)
- `_mark_notified` (line 375)
- `_ensure_dirs` (line 384)
- `update_symbols_json` (line 389)
- `_csv_escape` (line 405)
- `append_signal_csv` (line 413)
- `append_signal_log` (line 452)
- `check_pending_signal_outcomes` — only the read-only version (the writing version goes to scanner/runtime.py in PR7)
- All `/signals*` routes (lines 1980-2138)

- [ ] **Step 2: Create db/signals.py**

```python
"""Signals DB layer — query functions.

Extracted from btc_api.py:1109-1224 in PR5.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_db

log = logging.getLogger("db.signals")


def save_scan(rep: dict) -> int:
    """[paste verbatim from btc_api.py:1109]"""
    ...


def get_scans(limit=50, only_signals=False, only_setups=False, since=None, symbol=None) -> list:
    """[paste verbatim from btc_api.py:1151]"""
    ...


def get_latest_signal(symbol: Optional[str] = None) -> Optional[dict]:
    """[paste verbatim from btc_api.py:1177]"""
    ...


def get_latest_scan(symbol: Optional[str] = None) -> Optional[dict]:
    """[paste verbatim from btc_api.py:1192]"""
    ...


def get_signals_summary() -> list:
    """[paste verbatim from btc_api.py:1205]"""
    ...
```

- [ ] **Step 3: Create api/signals.py**

```python
"""Signals API — router + filters + dedup + CSV/log appenders + outcomes tracker (read-only).

Extracted from btc_api.py in PR5. Uses db/signals.py for queries.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.config import load_config
from data import market_data as md
from db.signals import (
    save_scan, get_scans, get_latest_signal, get_latest_scan, get_signals_summary,
)
from db.connection import get_db

log = logging.getLogger("api.signals")

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_SCRIPT_DIR, "data")
LOGS_DIR = os.path.join(_SCRIPT_DIR, "logs")
SYMBOLS_JSON_FILE = os.path.join(DATA_DIR, "symbols_status.json")
SIGNALS_CSV_FILE = os.path.join(DATA_DIR, "signals_history.csv")
SIGNALS_LOG_FILE = os.path.join(LOGS_DIR, "signals.log")

router = APIRouter(prefix="/signals", tags=["signals"])

# In-memory dedup state (replicates btc_api.py behavior)
_NOTIFIED_AT: dict[str, datetime] = {}
_DEDUP_WINDOW_MIN = 30


def should_notify_signal(rep: dict, cfg: dict) -> bool:
    """[paste verbatim from btc_api.py:327]"""
    ...


def _is_duplicate_signal(symbol: str, cfg: dict) -> bool:
    """[paste verbatim from btc_api.py:363]"""
    ...


def _mark_notified(symbol: str):
    """[paste verbatim from btc_api.py:375]"""
    ...


def _ensure_dirs():
    """[paste verbatim from btc_api.py:384]"""
    ...


def update_symbols_json(symbols_rows: list):
    """[paste verbatim from btc_api.py:389]"""
    ...


def _csv_escape(val) -> str:
    """[paste verbatim from btc_api.py:405]"""
    ...


def append_signal_csv(rep: dict, scan_id: int):
    """[paste verbatim from btc_api.py:413]"""
    ...


def append_signal_log(rep: dict, scan_id: int):
    """[paste verbatim from btc_api.py:452]"""
    ...


def check_pending_signal_outcomes_readonly(current_prices: dict[str, float]):
    """Read-only version of check_pending_signal_outcomes for API use.

    [paste read portions from btc_api.py:51 — the version called from
    scanner_loop that ALSO writes goes to scanner/runtime.py in PR7.]

    For PR5: this just queries pending outcomes; writing is deferred.
    """
    ...


@router.get("", summary="Historial de escaneos / señales")
def list_signals(
    limit: int = Query(50, ge=1, le=500),
    only_signals: bool = Query(False),
    only_setups: bool = Query(False),
    since: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
):
    """[paste verbatim from btc_api.py:1981]"""
    ...


@router.get("/performance", summary="Métricas de éxito de las señales históricas")
def get_signals_performance():
    """[paste verbatim from btc_api.py:2015]"""
    ...


@router.get("/latest", summary="Ultima señal completa (con gatillo)")
def latest_signal(symbol: Optional[str] = Query(None)):
    """[paste verbatim from btc_api.py:2073]"""
    ...


@router.get("/latest/message", summary="Mensaje Telegram de la ultima señal")
def latest_message(symbol: Optional[str] = Query(None)):
    """[paste verbatim from btc_api.py:2105]"""
    ...


@router.get("/{scan_id}", summary="Detalle de un escaneo por ID")
def signal_by_id(scan_id: int):
    """[paste verbatim from btc_api.py:2124]"""
    ...
```

- [ ] **Step 4: Modify btc_api.py — delete moved code, mount router, re-exports**

Delete all functions and routes listed in Task 18 Step 1. Add after `app.include_router(positions_router)`:

```python
from api.signals import router as signals_router  # noqa: F401
from db.signals import (  # noqa: F401  (re-exports for scanner_loop until PR7)
    save_scan, get_scans, get_latest_signal, get_latest_scan, get_signals_summary,
)
from api.signals import (  # noqa: F401
    should_notify_signal, _is_duplicate_signal, _mark_notified,
    update_symbols_json, append_signal_csv, append_signal_log,
)
app.include_router(signals_router)
```

- [ ] **Step 5: Run parity test**

```bash
python -m pytest tests/test_api_signals_parity.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 639+ passing.

- [ ] **Step 7: Commit PR5**

```bash
git add api/signals.py db/signals.py tests/_baselines/signals.json \
        tests/test_api_signals_parity.py tests/_baseline_capture.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR5 — split signals into api/signals.py + db/signals.py

Move signal queries (save_scan, get_scans, get_latest_*, get_signals_summary)
to db/signals.py. Move signal routes, filters (should_notify_signal,
_is_duplicate_signal, _mark_notified), CSV/log appenders, and read-only
outcomes tracking to api/signals.py.

The writing version of check_pending_signal_outcomes (used by scanner_loop)
stays in btc_api.py until PR7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR6 — thin wrappers (kill_switch + health + tune + notifications)

Goal: move four "thin" route groups that mostly delegate to existing modules. Each new file is < 200 LOC.

---

## Task 19: Move kill_switch routes to api/kill_switch.py

**Files:**
- Create: `api/kill_switch.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Read kill_switch routes**

```bash
sed -n '1690,1980p' btc_api.py
sed -n '2523,2566p' btc_api.py
```

- [ ] **Step 2: Create api/kill_switch.py**

```python
"""Kill switch API — thin wrapper over strategy/kill_switch_v2*.

Extracted from btc_api.py in PR6.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import verify_api_key

log = logging.getLogger("api.kill_switch")

router = APIRouter(prefix="/kill_switch", tags=["kill_switch"])


@router.post("/recalibrate", summary="Recalibrate kill switch v2 baselines",
             dependencies=[Depends(verify_api_key)])
def kill_switch_recalibrate():
    """[paste verbatim from btc_api.py:1690-1737]"""
    ...


@router.get("/recommendations", dependencies=[Depends(verify_api_key)])
def kill_switch_list_recommendations(...):
    """[paste verbatim from btc_api.py:1738-1809]"""
    ...


@router.post("/recommendations/{rec_id}/apply", dependencies=[Depends(verify_api_key)])
def kill_switch_apply_recommendation(rec_id: int):
    """[paste verbatim from btc_api.py:1810-1907]"""
    ...


@router.post("/recommendations/{rec_id}/ignore", dependencies=[Depends(verify_api_key)])
def kill_switch_ignore_recommendation(rec_id: int):
    """[paste verbatim from btc_api.py:1908-1979]"""
    ...


@router.get("/decisions", dependencies=[Depends(verify_api_key)])
def get_kill_switch_decisions(...):
    """[paste verbatim from btc_api.py:2523-2537]"""
    ...


@router.get("/current_state", dependencies=[Depends(verify_api_key)])
def get_kill_switch_current_state(engine: str = Query("v1")):
    """[paste verbatim from btc_api.py:2538-2544]"""
    ...
```

Note: the original `@app.post("/kill_switch_recalibrate")` has the slash spelling `kill_switch_recalibrate` (one path segment). To preserve exact URL parity, the router prefix `/kill_switch` plus path `/recalibrate` produces `/kill_switch/recalibrate` — this is **different from the original** `/kill_switch_recalibrate`.

**Decision:** preserve original URL by using `prefix=""` (no router prefix) and explicit paths:

```python
router = APIRouter(tags=["kill_switch"])

@router.post("/kill_switch_recalibrate", ...)
def kill_switch_recalibrate(): ...

@router.get("/kill_switch/recommendations", ...)
...
```

- [ ] **Step 3: Modify btc_api.py — delete kill_switch routes, mount router**

Delete the kill_switch routes (lines 1690-1979 and 2523-2566) from btc_api.py. Add:

```python
from api.kill_switch import router as kill_switch_router
app.include_router(kill_switch_router)
```

- [ ] **Step 4: Smoke test endpoints still work**

```bash
python btc_api.py &
APP_PID=$!
sleep 2
curl -s -H "X-API-Key: $BTC_API_KEY" http://localhost:8000/kill_switch/current_state | head -c 100
kill $APP_PID
```

Expected: a JSON response.

---

## Task 20: Move health routes to api/health.py

**Files:**
- Create: `api/health.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Read health routes**

```bash
sed -n '2417,2566p' btc_api.py
sed -n '2545,2570p' btc_api.py
```

- [ ] **Step 2: Create api/health.py**

```python
"""Health API — symbol health, dashboard, reactivation. Thin wrapper over health.py.

Extracted from btc_api.py in PR6.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.deps import verify_api_key

log = logging.getLogger("api.health")

router = APIRouter(tags=["health"])


class ReactivateRequest(BaseModel):
    """[paste verbatim from btc_api.py:2417]"""
    ...


@router.get("/health/symbols", dependencies=[Depends(verify_api_key)])
def get_health_symbols():
    """[paste verbatim from btc_api.py:2421]"""
    ...


@router.get("/health/events", dependencies=[Depends(verify_api_key)])
def get_health_events(...):
    """[paste verbatim from btc_api.py:2440]"""
    ...


@router.get("/health/dashboard", dependencies=[Depends(verify_api_key)])
def get_health_dashboard():
    """[paste verbatim from btc_api.py:2546]"""
    ...


@router.post("/health/reactivate/{symbol}", dependencies=[Depends(verify_api_key)])
def post_health_reactivate(symbol: str, body: ReactivateRequest):
    """[paste verbatim from btc_api.py:2559]"""
    ...


@router.get("/health", summary="Health check for monitoring and Docker")
def health_check():
    """[paste verbatim from btc_api.py:2568]"""
    ...
```

- [ ] **Step 3: Modify btc_api.py**

Delete the health routes from btc_api.py. Add:

```python
from api.health import router as health_router
app.include_router(health_router)
```

---

## Task 21: Move tune routes to api/tune.py

**Files:**
- Create: `api/tune.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Create api/tune.py**

```python
"""Tune API — auto-tune proposal lifecycle (latest, apply, reject).

Extracted from btc_api.py in PR6.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import verify_api_key

log = logging.getLogger("api.tune")

router = APIRouter(prefix="/tune", tags=["tune"])


@router.get("/latest", summary="Latest tune result")
def tune_latest():
    """[paste verbatim from btc_api.py:2311]"""
    ...


@router.post("/apply", summary="Apply pending tune proposal", dependencies=[Depends(verify_api_key)])
def tune_apply():
    """[paste verbatim from btc_api.py:2332]"""
    ...


@router.post("/reject", summary="Reject pending tune proposal", dependencies=[Depends(verify_api_key)])
def tune_reject():
    """[paste verbatim from btc_api.py:2395]"""
    ...
```

- [ ] **Step 2: Modify btc_api.py**

Delete the tune routes from btc_api.py. Add:

```python
from api.tune import router as tune_router
app.include_router(tune_router)
```

---

## Task 22: Move notifications routes to api/notifications.py

**Files:**
- Create: `api/notifications.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Create api/notifications.py**

```python
"""Notifications API — in-app notification list/read endpoints.

Extracted from btc_api.py in PR6. Distinct from api/telegram (outbound).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from api.deps import verify_api_key

log = logging.getLogger("api.notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", dependencies=[Depends(verify_api_key)])
def get_notifications(...):
    """[paste verbatim from btc_api.py:2471]"""
    ...


@router.post("/{notif_id}/read", dependencies=[Depends(verify_api_key)])
def post_notification_read(notif_id: int):
    """[paste verbatim from btc_api.py:2504]"""
    ...


@router.post("/read-all", dependencies=[Depends(verify_api_key)])
def post_notifications_read_all():
    """[paste verbatim from btc_api.py:2512]"""
    ...
```

- [ ] **Step 2: Modify btc_api.py**

Delete the notifications routes. Add:

```python
from api.notifications import router as notifications_router
app.include_router(notifications_router)
```

---

## Task 23: Verify PR6 parity + commit

- [ ] **Step 1: Capture light parity baselines for kill_switch, health, tune, notifications**

For each, add a `_capture_<domain>` to baseline_capture covering 1-2 GET endpoints + auth-failure case. Capture and commit.

```bash
for d in kill_switch health tune notifications; do
  python -m tests._baseline_capture $d > tests/_baselines/$d.json
done
```

- [ ] **Step 2: Create one parity test per domain**

Following the same template as positions/signals. Light coverage: 1 GET + 1 auth case per domain.

- [ ] **Step 3: Run all parity tests**

```bash
python -m pytest tests/test_api_kill_switch_parity.py \
                 tests/test_api_health_parity.py \
                 tests/test_api_tune_parity.py \
                 tests/test_api_notifications_parity.py -v
```

Expected: PASS.

- [ ] **Step 4: Run full suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 643+ passing.

- [ ] **Step 5: Commit PR6**

```bash
git add api/kill_switch.py api/health.py api/tune.py api/notifications.py \
        tests/_baselines/{kill_switch,health,tune,notifications}.json \
        tests/test_api_{kill_switch,health,tune,notifications}_parity.py \
        tests/_baseline_capture.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR6 — extract thin wrappers (kill_switch, health, tune, notifications)

Move four route groups that delegate to strategy/, health.py, tune system,
and the notifications table. Each new file < 200 LOC. Light parity tests
per domain.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# PHASE PR7 — scanner runtime + bootstrap

Goal: extract scanner thread to `scanner/runtime.py`; remove **all** re-exports from `btc_api.py`; trim `btc_api.py` to ≤ 200 LOC.

---

## Task 24: Create scanner/runtime.py

**Files:**
- Create: `scanner/runtime.py`
- Modify: `btc_api.py`

- [ ] **Step 1: Read scanner thread code**

```bash
sed -n '1437,1620p' btc_api.py    # execute_scan_for_symbol, scanner_loop, start_scanner_thread
sed -n '51,128p' btc_api.py       # check_pending_signal_outcomes (writing version)
```

- [ ] **Step 2: Create scanner/runtime.py**

```python
"""Scanner runtime — background scan loop + threading + outcomes tracker.

Extracted from btc_api.py in PR7 (final phase of api+db refactor).

Imports from db/* and api/telegram (service); does NOT import api/* routers.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from api.config import load_config
from api.signals import (
    should_notify_signal, _is_duplicate_signal, _mark_notified,
    update_symbols_json, append_signal_csv, append_signal_log,
)
from api.telegram import push_telegram_direct, push_webhook
from db.connection import get_db, backup_db
from db.signals import save_scan
from data import market_data as md

log = logging.getLogger("scanner.runtime")

_SCANNER_LOCK = threading.Lock()
_scanner_state: dict = {
    "running": False,
    "last_scan_ts": None,
    "last_scan_duration_s": None,
    "symbols_active": [],
    "errors_consecutive": 0,
}


def execute_scan_for_symbol(sym: str, cfg: dict) -> dict:
    """[paste verbatim from btc_api.py:1437]"""
    ...


def check_pending_signal_outcomes(current_prices: dict[str, float]):
    """Writing version — updates signal_outcomes table.

    [paste verbatim from btc_api.py:51-128]
    """
    ...


def scanner_loop():
    """[paste verbatim from btc_api.py:1501]"""
    ...


def start_scanner_thread():
    """[paste verbatim from btc_api.py:1562]"""
    ...
```

- [ ] **Step 3: Run import boundaries check**

```bash
python -m pytest tests/test_import_boundaries.py -v
```

Expected: PASS — `scanner/runtime.py` only imports `api.config`, `api.signals`, `api.telegram` (services + filters, not routers; `api.signals.router` is the router but `from api.signals import some_function` is fine — adjust the boundary test if needed).

---

## Task 25: Remove all re-exports + trim btc_api.py

**Files:**
- Modify: `btc_api.py`

- [ ] **Step 1: Remove all re-exports added in PR0-PR6**

Find and delete every `from <module> import ... # noqa: F401` line that's a re-export only. Examples:
```python
from db.connection import get_db, backup_db, _DictRow, DB_FILE  # noqa: F401
from db.schema import init_db  # noqa: F401
from api.config import load_config  # noqa: F401
from api.telegram import build_telegram_message, ...  # noqa: F401
from db.positions import db_create_position, ...  # noqa: F401
from db.signals import save_scan, ...  # noqa: F401
from api.signals import should_notify_signal, ...  # noqa: F401
```

These were temporary; PR7 is when they go.

- [ ] **Step 2: Update btc_api.py to use scanner.runtime**

Replace the inline `start_scanner_thread()` call (and any `_scanner_state` references) with:

```python
from scanner.runtime import start_scanner_thread, _scanner_state
```

- [ ] **Step 3: Trim btc_api.py to bootstrap-only**

The final `btc_api.py` should look approximately like:

```python
#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   CRYPTO SCANNER API  —  Ultimate Macro & Order Flow V6.0        ║
║   FastAPI bootstrap — routers in api/*, scanner in scanner/      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Make project imports available
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from api.config import load_config
from api.config import router as config_router
from api.health import router as health_router
from api.kill_switch import router as kill_switch_router
from api.notifications import router as notifications_router
from api.ohlcv import router as ohlcv_router
from api.positions import router as positions_router
from api.signals import router as signals_router
from api.telegram import push_telegram_direct  # for legacy import compat
from api.tune import router as tune_router
from db.schema import init_db
from scanner.runtime import start_scanner_thread, _scanner_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("btc_api")

API_HOST = "0.0.0.0"
API_PORT = 8000


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Initializing DB schema…")
    init_db()
    log.info("Starting scanner thread…")
    start_scanner_thread()
    yield
    log.info("Shutdown.")


app = FastAPI(
    title="Crypto Scanner API",
    description="Ultimate Macro & Order Flow V6.0",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.get("/", summary="Bienvenida y estado general")
def root():
    cfg = load_config()
    return {
        "service":             "Crypto Scanner API — Ultimate Macro V6.0",
        "version":             "2.0.0",
        "symbols":             _scanner_state.get("symbols_active", []),
        "num_symbols":         cfg.get("num_symbols", 20),
        "docs":                f"http://localhost:{API_PORT}/docs",
        "scanner":             _scanner_state,
        "webhook_configurado": bool(cfg.get("webhook_url")),
    }


@app.get("/symbols", summary="Estado actual de cada par monitoreado")
def list_symbols():
    """[paste verbatim from btc_api.py:1636 — uses _scanner_state]"""
    ...


@app.get("/status", summary="Estado detallado del scanner")
def status():
    """[paste verbatim from btc_api.py:1659 — uses _scanner_state, db_create_position]"""
    ...


@app.post("/scan", summary="Forzar escaneo manual")
def force_scan(...):
    """[paste verbatim from btc_api.py:1677 — calls scanner.runtime.execute_scan_for_symbol]"""
    ...


@app.get("/webhook/test", summary="Probar webhook y Telegram directo")
def test_webhook():
    """[paste verbatim from btc_api.py:2260]"""
    ...


# Mount all domain routers
app.include_router(config_router)
app.include_router(health_router)
app.include_router(kill_switch_router)
app.include_router(notifications_router)
app.include_router(ohlcv_router)
app.include_router(positions_router)
app.include_router(signals_router)
app.include_router(tune_router)


if __name__ == "__main__":
    uvicorn.run(app, host=API_HOST, port=API_PORT)
```

(`/`, `/symbols`, `/status`, `/scan`, `/webhook/test` are kept inline as bootstrap-level endpoints since they're cross-cutting / use scanner state. Total LOC ~180.)

- [ ] **Step 4: Verify line count**

```bash
wc -l btc_api.py
```

Expected: ≤ 200.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -3
```

Expected: 643+ passing.

- [ ] **Step 6: Run import boundaries**

```bash
python -m pytest tests/test_import_boundaries.py -v
```

Expected: PASS — no `api/*` should import `btc_api`; no `db/*` should import `api/*`; etc.

- [ ] **Step 7: Smoke test full app**

```bash
python btc_api.py &
APP_PID=$!
sleep 3
echo "=== / ==="
curl -s http://localhost:8000/ | head -c 200
echo ""
echo "=== /signals ==="
curl -s http://localhost:8000/signals?limit=3 | head -c 300
echo ""
echo "=== /positions ==="
curl -s http://localhost:8000/positions | head -c 200
echo ""
echo "=== /ohlcv ==="
curl -s "http://localhost:8000/ohlcv?symbol=BTCUSDT&interval=1h&limit=2" | head -c 200
echo ""
echo "=== /health ==="
curl -s http://localhost:8000/health | head -c 100
kill $APP_PID
```

Expected: every endpoint returns valid JSON.

- [ ] **Step 8: Smoke test frontend**

```bash
cd frontend
npm run dev &
DEV_PID=$!
sleep 5
echo "Open http://localhost:5173 in browser, verify dashboard renders for 30s"
sleep 30
kill $DEV_PID
cd ..
```

Expected: dashboard renders, no console errors related to API contract changes.

- [ ] **Step 9: Commit PR7 (final)**

```bash
git add scanner/runtime.py btc_api.py
git commit -m "$(cat <<'EOF'
refactor(api): PR7 — scanner runtime extracted, btc_api.py becomes bootstrap

Move execute_scan_for_symbol, scanner_loop, start_scanner_thread, and the
writing version of check_pending_signal_outcomes to scanner/runtime.py.
Remove all temporary re-exports from btc_api.py. Trim btc_api.py to
≤200 LOC: FastAPI app, lifespan (init_db + start_scanner_thread), CORS,
mount of 8 domain routers, and 5 bootstrap-level endpoints (/, /symbols,
/status, /scan, /webhook/test).

Closes the api+db domain refactor. Definition of done in spec §8 satisfied.
btc_scanner.py post-#186 cleanup tracked in follow-up issue.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 26: Create follow-up issue for btc_scanner.py

- [ ] **Step 1: Create issue via gh CLI**

```bash
gh issue create --title "refactor(scanner): break btc_scanner.py post-#186 leftovers by purpose" --body "$(cat <<'EOF'
## Context

Following the btc_api.py domain refactor (spec: docs/superpowers/specs/es/2026-04-27-refactor-btc-api-por-dominio-design.md, completed in PR0-PR7), btc_scanner.py is the next monolith to address.

btc_scanner.py (1494 LOC post-#186) mixes pieces with naturally distinct destinations:

- `detect_regime` + cache → `strategy/regime.py` (decision logic, fits strategy/)
- `detect_bull_engulfing`, `detect_bear_engulfing`, `detect_rsi_divergence`, `check_trigger_5m`, `check_trigger_5m_short` → `strategy/patterns.py`
- `resolve_direction_params` → `strategy/direction.py`
- `get_top_symbols`, `_get_binance_usdt_symbols`, `get_active_symbols` → `markets/symbols.py`
- `_load_proxy`, `_rate_limit` → `infra/http.py`
- `fmt`, `save_log`, `main` → `cli/scanner_report.py` (or merged with btc_report.py)

## Goal

btc_scanner.py < 200 LOC (just the `scan()` function + minimal glue), following the pattern validated in epic #186 and the api+db refactor.

## Approach

Per-piece PRs with parity tests, mirroring the api refactor cadence.

## Spec

Full spec to be written via `superpowers:brainstorming` once the api+db refactor lands.
EOF
)" --label refactor
```

Expected: prints the new issue URL.

---

## Self-Review Notes

After this plan was drafted, the following inline fixes were applied:

- Task 19 (kill_switch routes): noted that the original `/kill_switch_recalibrate` URL is one segment, not under `/kill_switch/` — adjusted router to use no prefix and explicit path strings to preserve URL parity.
- All "thin wrapper" PR6 sub-tasks (kill_switch, health, tune, notifications) noted that parity tests should cover at least 1 GET + 1 auth-failure case per domain.
- Task 7 (db/schema.py): explicit warning that the verbatim copy must NOT change SQL.
- All tasks: emphasized "paste verbatim from btc_api.py:<line>" rather than reproducing the bodies, both to keep the plan readable and to make it crystal clear that the engineer should not "improve" the moved code in this refactor.

---

## Definition of done (cross-checked against spec §8)

- [ ] `wc -l btc_api.py` ≤ 200 (verified in Task 25 Step 4)
- [ ] `api/` has 10 modules (verified by listing)
- [ ] `db/` has 4 modules (connection, schema, positions, signals)
- [ ] `scanner/runtime.py` exists
- [ ] `strategy/constants.py` is the single source for indicator constants (Tasks 1-4)
- [ ] 628+ tests pass (verified after each PR)
- [ ] `tests/test_import_boundaries.py` enforces anti-cycle/anti-drift rules (Task 9)
- [ ] `python btc_api.py` boots and `/health` responds 200 (Task 25 Step 7)
- [ ] Frontend renders without changes (Task 25 Step 8)
- [ ] Follow-up issue for btc_scanner.py created (Task 26)
