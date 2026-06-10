# Conexión Binance por-tenant (SOLO LECTURA, SPOT) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el sistema lea la cuenta SPOT real de Binance del papá (tenant 2) con una API key read-only cifrada, y reconcilie automáticamente la `qty` de sus posiciones EXTERNAL registradas — reemplazando el tecleo manual.

**Architecture:** Una tabla nueva `binance_credentials` per-tenant guarda la secret cifrada con Fernet (master key en env, fuera de backup/DB). Un cliente firmado HMAC-SHA256 aislado (separado del adapter de klines público) lee `/api/v3/account`. Un módulo de sync actualiza la `qty` de las filas `control_domain='EXTERNAL'` spot desde los balances reales. Una columna nueva `positions.market` + un trigger BEFORE INSERT/UPDATE hacen estructuralmente imposible que una fila con `market` seteado sea INTERNAL (cierra la landmine del REALIZED-falso). Todo read-only: cero colocación de órdenes.

**Tech Stack:** Python 3.11 (CI) / 3.14 (local), SQLite, `cryptography` (Fernet, dep nueva), `requests`, `hmac`/`hashlib` (stdlib), pytest.

**Spec:** `docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md` (REV 3). Este plan implementa **v0.1 (spot)**. Futuros = v0.2.

---

## Notas de realización (refinan REV 3, con justificación)

- **§4.3 / BNC-4 (garantía de tipo):** se implementa como **TRIGGER** `BEFORE INSERT/UPDATE`, NO como CHECK. SQLite no permite `ALTER TABLE ADD CONSTRAINT CHECK`; añadir un CHECK exige recrear `positions` (el canon documenta que el `CREATE TABLE positions_new` está duplicado en 4 migraciones y unificarlo "es un proyecto, no un PR"). El trigger da el mismo invariante sin recrear la tabla y además cubre UPDATE. (El canon `positions_schema.py` no rastrea triggers; se verifica con un test dedicado, Task 3.)
- **§4.7 (`observed_closed_pending`):** se realiza como flag **DERIVADO de `qty` ≈ 0**, NO como columna persistida. El sync actualiza `qty` desde Binance (autoridad §4.1); cuando el papá cierra, el balance → 0, `qty` → 0, y `compute_real_equity` (que hace `qty×precio`) da 0 — el equity-fantasma del BLOCKER-5 se cierra en la raíz por reconciliación de qty, sin columna nueva ni churn de canon. La señal "Binance muestra esto vacío, confirma el cierre" se deriva en el reporte del sync (qty < dust con credencial ACTIVE).
- Única columna nueva en `positions`: **`market`** (no `observed_closed_pending`).
- **El sync v0.1 es UPDATE-only + reporte** (no INSERT): reconcilia las filas EXTERNAL spot ya registradas y reporta holds no-registrados para registro manual (el `entry_price` es `NOT NULL` y no hay cost-basis en el endpoint de cuenta; auto-creación = v0.2).

---

## File Structure

| Archivo | Responsabilidad | Acción |
|---|---|---|
| `requirements.txt` | añadir `cryptography` | Modify |
| `db/secret_box.py` | cifrado/descifrado Fernet con master key de env (fail-closed) | Create |
| `db/binance_credentials.py` | capa DB per-tenant (upsert/get/delete; secret siempre cifrada) | Create |
| `db/schema.py` | migraciones: tabla `binance_credentials`, columna `positions.market`, índice de idempotencia, trigger de tipo | Modify |
| `db/positions_schema.py` | añadir `market` al canon + el índice de idempotencia | Modify |
| `tests/test_canonical_positions_schema.py` | actualizar conteos del canon | Modify |
| `data/providers/binance_account.py` | cliente firmado read-only spot (HMAC, clock-sync, sonda `order/test`) | Create |
| `binance_sync.py` | lógica de reconciliación (read account → map símbolo→asset → UPDATE qty + reporte) | Create |
| `tools/sync_binance_spot.py` | CLI one-shot que corre el sync para un tenant | Create |
| `tools/set_binance_key.py` | CLI one-shot: cifra+guarda la key del papá tras pasar la sonda BNC-9 | Create |
| `api/binance_credentials_api.py` | endpoint GET de metadatos (nunca la secret) | Create |
| `tests/test_*.py` | un test file por módulo nuevo | Create |

---

## Task 1: Dependencia `cryptography` + `secret_box` (cifrado Fernet)

**Files:**
- Modify: `requirements.txt`
- Create: `db/secret_box.py`
- Test: `tests/test_secret_box.py`

- [ ] **Step 1: Añadir la dependencia**

En `requirements.txt`, después de la línea `anthropic>=0.40,<1.0` (línea 27), añadir:

```
# Cifrado-at-rest de secretos de broker por-tenant (Fernet AES-128-CBC + HMAC).
# Añadido 2026-06-10 para binance_credentials (spec conexion-binance-solo-lectura).
cryptography>=42.0
```

- [ ] **Step 2: Instalar**

Run: `python -m pip install "cryptography>=42.0"`
Expected: `Successfully installed cryptography-...`

- [ ] **Step 3: Escribir el test que falla**

```python
# tests/test_secret_box.py
import os
import pytest


def test_roundtrip_encrypt_decrypt(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    from db.secret_box import encrypt_secret, decrypt_secret
    token = encrypt_secret("my-binance-secret")
    assert token != b"my-binance-secret"
    assert b"my-binance-secret" not in token  # ciphertext, not plaintext
    assert decrypt_secret(token) == "my-binance-secret"


def test_fail_closed_when_master_key_missing(monkeypatch):
    monkeypatch.delenv("TRADING_BINANCE_MASTER_KEY", raising=False)
    from db.secret_box import encrypt_secret
    with pytest.raises(RuntimeError, match="TRADING_BINANCE_MASTER_KEY"):
        encrypt_secret("x")
```

- [ ] **Step 4: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_secret_box.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'db.secret_box'`

- [ ] **Step 5: Implementar `db/secret_box.py`**

```python
"""Cifrado-at-rest de secretos de broker (Fernet). Master key desde env.

Fernet = AES-128-CBC + HMAC-SHA256 (encrypt-then-MAC). NO es AEAD canónico y
NO soporta associated data. La master key (`TRADING_BINANCE_MASTER_KEY`,
url-safe base64 de 32 bytes) vive en el EnvironmentFile de systemd, fuera de
backup/repo/CI. Fail-closed: sin master key, cualquier operación lanza.

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.3.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet

_ENV_KEY = "TRADING_BINANCE_MASTER_KEY"


def _fernet() -> Fernet:
    raw = os.environ.get(_ENV_KEY)
    if not raw:
        raise RuntimeError(
            f"{_ENV_KEY} no está en el entorno; el cifrado-at-rest es fail-closed. "
            "Genera una con `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` y ponla en el EnvironmentFile."
        )
    return Fernet(raw.encode() if isinstance(raw, str) else raw)


def encrypt_secret(plaintext: str) -> bytes:
    """Cifra un secreto. Devuelve el token Fernet (bytes) para guardar en BLOB."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    """Descifra un token Fernet. Lanza si la master key no corresponde."""
    return _fernet().decrypt(token).decode("utf-8")
```

- [ ] **Step 6: Correr el test, verificar que pasa**

Run: `python -m pytest tests/test_secret_box.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt db/secret_box.py tests/test_secret_box.py
git commit -m "feat(binance): secret_box — cifrado Fernet at-rest fail-closed (dep cryptography)"
```

---

## Task 2: Tabla `binance_credentials` + capa DB

**Files:**
- Modify: `db/schema.py` (nueva migración + registro en `init_db`)
- Create: `db/binance_credentials.py`
- Test: `tests/test_binance_credentials.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_binance_credentials.py
import sqlite3
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from db.schema import _migrate_binance_credentials
    _migrate_binance_credentials(c)
    return c


def test_upsert_stores_secret_encrypted_not_plaintext(con):
    from db.binance_credentials import db_upsert_binance_credential, db_get_binance_credential_raw
    db_upsert_binance_credential(
        con, tenant_id=2, api_key_public="PUBKEY123",
        secret_plaintext="SUPERSECRET", ip_whitelisted=True, scope_detected="READ_ONLY_SPOT",
    )
    raw = con.execute("SELECT secret_enc FROM binance_credentials WHERE tenant_id=2").fetchone()
    assert b"SUPERSECRET" not in raw["secret_enc"]  # never plaintext at rest


def test_get_decrypts_secret(con):
    from db.binance_credentials import db_upsert_binance_credential, db_get_decrypted_secret
    db_upsert_binance_credential(
        con, tenant_id=2, api_key_public="PUBKEY123", secret_plaintext="SUPERSECRET",
    )
    assert db_get_decrypted_secret(con, tenant_id=2) == "SUPERSECRET"


def test_upsert_is_idempotent_one_row_per_tenant(con):
    from db.binance_credentials import db_upsert_binance_credential
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="A", secret_plaintext="s1")
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="B", secret_plaintext="s2")
    n = con.execute("SELECT COUNT(*) FROM binance_credentials WHERE tenant_id=2").fetchone()[0]
    assert n == 1
    assert con.execute("SELECT api_key_public FROM binance_credentials WHERE tenant_id=2").fetchone()[0] == "B"


def test_metadata_view_never_exposes_secret(con):
    from db.binance_credentials import db_upsert_binance_credential, get_credential_metadata
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUBKEY123456", secret_plaintext="SUPERSECRET")
    meta = get_credential_metadata(con, tenant_id=2)
    assert "secret" not in str(meta).lower()
    assert meta["api_key_last4"] == "3456"
    assert "secret_enc" not in meta
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_binance_credentials.py -v`
Expected: FAIL con `ImportError: cannot import name '_migrate_binance_credentials'`

- [ ] **Step 3: Añadir la migración a `db/schema.py`**

Añadir esta función junto a `_migrate_cash_balance` (después de la línea ~876):

```python
def _migrate_binance_credentials(con: sqlite3.Connection) -> None:
    """Crea la tabla binance_credentials (una fila por tenant; secret cifrada).

    NO en config.secrets.json (global + texto plano) ni en positions (credencial
    = acceso, no posición). Molde de capital/user_preferences: UNIQUE INDEX por
    tenant_id. Idempotente (CREATE TABLE/INDEX IF NOT EXISTS).

    Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.2.
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS binance_credentials (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id       INTEGER NOT NULL,
            api_key_public  TEXT NOT NULL,
            secret_enc      BLOB NOT NULL,
            key_version     INTEGER NOT NULL DEFAULT 1,
            scope_detected  TEXT,
            ip_whitelisted  INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_binance_cred_tenant "
        "ON binance_credentials(tenant_id)"
    )
```

Registrar en `init_db` (después del bloque `_migrate_cash_balance`, ~línea 441):

```python
    # binance_credentials: API key read-only spot por-tenant, cifrada at-rest
    # (spec 2026-06-10-conexion-binance-solo-lectura §2.2). Idempotente.
    with transaction() as con_bc:
        _migrate_binance_credentials(con_bc)
```

- [ ] **Step 4: Implementar `db/binance_credentials.py`**

```python
"""Capa DB per-tenant para credenciales Binance (secret cifrada at-rest).

Una fila por tenant_id (UNIQUE INDEX idx_binance_cred_tenant). La secret SOLO
entra/sale cifrada vía db.secret_box; nunca se devuelve en claro salvo por
`db_get_decrypted_secret` (que descifra en memoria justo para firmar).

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.2, §2.4.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.secret_box import decrypt_secret, encrypt_secret

VALID_STATUSES = {"ACTIVE", "AUTH_FAILED", "REVOKED", "RATE_BANNED", "CLOCK_SKEW"}


def db_upsert_binance_credential(
    con: sqlite3.Connection,
    *,
    tenant_id: int,
    api_key_public: str,
    secret_plaintext: str,
    scope_detected: Optional[str] = None,
    ip_whitelisted: bool = False,
    key_version: int = 1,
) -> None:
    """Inserta o reemplaza la credencial del tenant. La secret se cifra ANTES
    del INSERT (nunca en claro en reposo). El caller posee la transacción."""
    now = datetime.now(timezone.utc).isoformat()
    secret_enc = encrypt_secret(secret_plaintext)
    existing = con.execute(
        "SELECT id FROM binance_credentials WHERE tenant_id=?", (tenant_id,),
    ).fetchone()
    if existing is None:
        con.execute(
            "INSERT INTO binance_credentials (tenant_id, api_key_public, secret_enc, "
            "key_version, scope_detected, ip_whitelisted, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (tenant_id, api_key_public, secret_enc, key_version, scope_detected,
             1 if ip_whitelisted else 0, now, now),
        )
    else:
        con.execute(
            "UPDATE binance_credentials SET api_key_public=?, secret_enc=?, key_version=?, "
            "scope_detected=?, ip_whitelisted=?, status='ACTIVE', updated_at=? WHERE tenant_id=?",
            (api_key_public, secret_enc, key_version, scope_detected,
             1 if ip_whitelisted else 0, now, tenant_id),
        )


def db_get_binance_credential_raw(con: sqlite3.Connection, tenant_id: int) -> Optional[dict]:
    """Fila cruda (incluye secret_enc cifrada). NO exponer al frontend."""
    row = con.execute(
        "SELECT * FROM binance_credentials WHERE tenant_id=?", (tenant_id,),
    ).fetchone()
    return dict(row) if row else None


def db_get_decrypted_secret(con: sqlite3.Connection, tenant_id: int) -> Optional[str]:
    """Descifra la secret en memoria (justo antes de firmar). None si no hay fila."""
    row = db_get_binance_credential_raw(con, tenant_id)
    if row is None:
        return None
    return decrypt_secret(row["secret_enc"])


def db_set_credential_status(con: sqlite3.Connection, tenant_id: int, status: str) -> None:
    """Fija el estado fail-closed (ACTIVE/AUTH_FAILED/REVOKED/RATE_BANNED/CLOCK_SKEW)."""
    assert status in VALID_STATUSES, f"status inválido: {status}"
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "UPDATE binance_credentials SET status=?, updated_at=? WHERE tenant_id=?",
        (status, now, tenant_id),
    )


def get_credential_metadata(con: sqlite3.Connection, tenant_id: int) -> Optional[dict]:
    """Metadatos seguros para el frontend: NUNCA la secret, ni cifrada."""
    row = db_get_binance_credential_raw(con, tenant_id)
    if row is None:
        return None
    pub = row["api_key_public"] or ""
    return {
        "tenant_id": row["tenant_id"],
        "api_key_last4": pub[-4:],
        "scope_detected": row["scope_detected"],
        "ip_whitelisted": bool(row["ip_whitelisted"]),
        "status": row["status"],
        "updated_at": row["updated_at"],
    }
```

- [ ] **Step 5: Correr el test, verificar que pasa**

Run: `python -m pytest tests/test_binance_credentials.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add db/schema.py db/binance_credentials.py tests/test_binance_credentials.py
git commit -m "feat(binance): tabla binance_credentials per-tenant + capa DB (secret cifrada)"
```

---

## Task 3: Columna `positions.market` + trigger de tipo + índice de idempotencia

**Files:**
- Modify: `db/schema.py` (migración `_migrate_positions_market`)
- Modify: `db/positions_schema.py` (canon: columna + índice)
- Modify: `tests/test_canonical_positions_schema.py` (conteos)
- Test: `tests/test_positions_market_trigger.py`

- [ ] **Step 1: Escribir el test que falla (trigger + idempotencia)**

```python
# tests/test_positions_market_trigger.py
import sqlite3
import pytest


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # Tabla positions mínima suficiente para el trigger + índice.
    c.execute(
        "CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "scan_id INTEGER, symbol TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'LONG', "
        "status TEXT NOT NULL DEFAULT 'open', entry_price REAL NOT NULL, entry_ts TEXT NOT NULL, "
        "qty REAL, tenant_id INTEGER, control_domain TEXT NOT NULL DEFAULT 'INTERNAL', market TEXT)"
    )
    from db.schema import _install_binance_external_guards
    _install_binance_external_guards(c)
    return c


def _ins(con, **kw):
    cols = ", ".join(kw); ph = ", ".join("?" for _ in kw)
    con.execute(f"INSERT INTO positions ({cols}) VALUES ({ph})", tuple(kw.values()))


def test_market_set_with_internal_is_rejected(con):
    with pytest.raises(sqlite3.IntegrityError):
        _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
             control_domain="INTERNAL", market="SPOT")


def test_market_set_with_external_is_allowed(con):
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
         control_domain="EXTERNAL", market="SPOT")
    assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1


def test_market_null_internal_is_allowed(con):
    # Las filas INTERNAL normales (market NULL) pasan sin problema.
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
         control_domain="INTERNAL")
    assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1


def test_update_to_internal_with_market_is_rejected(con):
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t", tenant_id=2,
         control_domain="EXTERNAL", market="SPOT")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE positions SET control_domain='INTERNAL' WHERE symbol='BTCUSDT'")


def test_idempotency_index_blocks_duplicate_external(con):
    _ins(con, symbol="BTCUSDT", entry_price=1, entry_ts="t1", tenant_id=2,
         direction="LONG", control_domain="EXTERNAL", market="SPOT")
    with pytest.raises(sqlite3.IntegrityError):
        _ins(con, symbol="BTCUSDT", entry_price=2, entry_ts="t2", tenant_id=2,
             direction="LONG", control_domain="EXTERNAL", market="SPOT")
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_positions_market_trigger.py -v`
Expected: FAIL con `ImportError: cannot import name '_install_binance_external_guards'`

- [ ] **Step 3: Implementar la migración + los guards en `db/schema.py`**

Añadir junto a `_migrate_control_domain` (~línea 860):

```python
def _migrate_positions_market(con: sqlite3.Connection) -> None:
    """Add `market` (SPOT/FUTURES) a positions. NULL para INTERNAL/manuales.

    Identidad de posición EXTERNAL = (tenant_id, symbol, market, direction). Se
    setea SOLO en filas nacidas del sync de Binance. Idempotente: PRAGMA-guarded
    ADD COLUMN (sin default → NULL para filas existentes).

    Spec: 2026-06-10-conexion-binance-solo-lectura §4.3b.
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()}
    if "market" not in cols:
        con.execute("ALTER TABLE positions ADD COLUMN market TEXT")
        log.info("DB migration: added market column to positions")


def _install_binance_external_guards(con: sqlite3.Connection) -> None:
    """Garantía de tipo ESTRUCTURAL + índice de idempotencia para EXTERNAL synced.

    SQLite no permite ALTER ADD CONSTRAINT CHECK sin recrear la tabla; un TRIGGER
    da el mismo invariante (BNC-4): una fila con `market` seteado DEBE ser
    EXTERNAL. Cierra la landmine del REALIZED-falso (una fila viva de Binance que
    entre como INTERNAL la auto-cerraría check_position_stops). Idempotente.

    Spec: 2026-06-10-conexion-binance-solo-lectura §4.3 (realizado como trigger).
    """
    con.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_market_implies_external_ins
        BEFORE INSERT ON positions
        WHEN NEW.market IS NOT NULL AND NEW.control_domain != 'EXTERNAL'
        BEGIN
            SELECT RAISE(ABORT, 'market set requires control_domain=EXTERNAL (BNC-4)');
        END
        """
    )
    con.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_market_implies_external_upd
        BEFORE UPDATE ON positions
        WHEN NEW.market IS NOT NULL AND NEW.control_domain != 'EXTERNAL'
        BEGIN
            SELECT RAISE(ABORT, 'market set requires control_domain=EXTERNAL (BNC-4)');
        END
        """
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_external_identity "
        "ON positions(tenant_id, symbol, market, direction) "
        "WHERE control_domain='EXTERNAL'"
    )
```

Registrar en `init_db`, en el mismo bloque que `_migrate_control_domain` (debe correr DESPUÉS de él, ~línea 436):

```python
    with transaction() as con_cd:
        _migrate_control_domain(con_cd)
        _migrate_positions_market(con_cd)
        _install_binance_external_guards(con_cd)
```

- [ ] **Step 4: Actualizar el canon `db/positions_schema.py`**

Añadir el `ColumnSpec` al final de `CANONICAL_POSITIONS_COLUMNS` (después de `control_domain`, línea 150):

```python
    # market: SPOT/FUTURES para filas EXTERNAL nacidas del sync de Binance; NULL
    # para INTERNAL/manuales. Parte de la identidad de idempotencia
    # (tenant_id, symbol, market, direction) y, vía el trigger
    # trg_market_implies_external_*, garantiza estructuralmente que una fila con
    # market seteado es EXTERNAL. Spec: 2026-06-10-conexion-binance-solo-lectura.
    ColumnSpec("market", "TEXT"),
```

Añadir el `IndexSpec` al final de `CANONICAL_POSITIONS_INDEXES` (después del partial unique, línea 219):

```python
    IndexSpec(
        name="idx_positions_external_identity",
        columns=("tenant_id", "symbol", "market", "direction"),
        unique=True,
        partial_where_fragment="control_domain='external'",
    ),
```

- [ ] **Step 5: Actualizar los conteos del canon test**

En `tests/test_canonical_positions_schema.py::test_no_unexpected_table_columns_or_constraints`, cambiar el assert de índices (línea 267) de `== 2` a `== 3`:

```python
    assert len(CANONICAL_POSITIONS_INDEXES) == 3, (
        "canonical index list should have 3 entries "
        "(tenant index + open-scan partial unique + external-identity partial unique)"
    )
```

- [ ] **Step 6: Correr los tests, verificar que pasan**

Run: `python -m pytest tests/test_positions_market_trigger.py tests/test_canonical_positions_schema.py -v`
Expected: PASS (todos). El canon test confirma que la columna `market` y el índice nuevo están declarados y vivos.

- [ ] **Step 7: Commit**

```bash
git add db/schema.py db/positions_schema.py tests/test_canonical_positions_schema.py tests/test_positions_market_trigger.py
git commit -m "feat(binance): columna positions.market + trigger de tipo EXTERNAL + indice idempotencia"
```

---

## Task 4: Cliente firmado read-only spot (`binance_account.py`)

**Files:**
- Create: `data/providers/binance_account.py`
- Test: `tests/test_binance_account_client.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_binance_account_client.py
import hashlib
import hmac
from unittest.mock import patch
import pytest


def test_signature_is_hmac_sha256_of_query():
    from data.providers.binance_account import _sign
    sig = _sign("secretkey", "symbol=BTCUSDT&timestamp=123")
    expected = hmac.new(b"secretkey", b"symbol=BTCUSDT&timestamp=123", hashlib.sha256).hexdigest()
    assert sig == expected


def test_get_spot_account_parses_free_plus_locked():
    from data.providers.binance_account import BinanceAccountClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {"balances": [
                {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                {"asset": "ETH", "free": "2.0", "locked": "0.0"},
                {"asset": "DUST", "free": "0.0", "locked": "0.0"},
            ]}

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        balances = client.get_spot_balances()
    assert balances == {"BTC": 0.6, "ETH": 2.0}  # free+locked; zero-balance dropped


def test_minus_2015_raises_auth_error():
    from data.providers.binance_account import BinanceAccountClient, BinanceAuthError

    class FakeResp:
        status_code = 401
        def json(self):
            return {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}
        text = "{}"

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        with pytest.raises(BinanceAuthError):
            client.get_spot_balances()


def test_probe_trading_disabled_when_order_test_returns_minus_2015():
    from data.providers.binance_account import BinanceAccountClient

    class FakeResp:
        status_code = 401
        def json(self):
            return {"code": -2015, "msg": "..."}
        text = "{}"

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        assert client.probe_trading_disabled() is True  # -2015 → no trading → OK


def test_probe_trading_enabled_when_order_test_succeeds():
    from data.providers.binance_account import BinanceAccountClient

    class FakeResp:
        status_code = 200
        def json(self):
            return {}  # order/test success ⇒ trading ENABLED ⇒ key debe rechazarse
        text = "{}"

    client = BinanceAccountClient(api_key="k", secret="s", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        assert client.probe_trading_disabled() is False
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_binance_account_client.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'data.providers.binance_account'`

- [ ] **Step 3: Implementar `data/providers/binance_account.py`**

```python
"""Cliente Binance AUTENTICADO read-only spot (firmado HMAC-SHA256).

SEPARADO del adapter público de klines (data/providers/binance.py): NO comparte
el failover a Bybit (un endpoint de cuenta no es fungible — Bybit no tiene la
cuenta del tenant). Solo lectura: get_spot_balances + la sonda probe_trading_disabled
(usa /api/v3/order/test, que valida SIN colocar). Cero métodos que coloquen órdenes.

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §3, §2.4.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse

import requests

BASE_URL = "https://api.binance.com"
RECV_WINDOW_MS = 5000


class BinanceAuthError(Exception):
    """-2015: API-key/IP/permiso inválido (Binance no los distingue)."""


class BinanceClockSkew(Exception):
    """-1021: timestamp fuera de recvWindow (reloj desfasado)."""


class BinanceRateBanned(Exception):
    """-1003 / 418 / 429: ban temporal por weight."""


def _http_get(url, params=None, headers=None, timeout=10):
    """Wrapper fino para que los tests mockeen solo esta llamada."""
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def _http_post(url, params=None, headers=None, timeout=10):
    return requests.post(url, params=params, headers=headers, timeout=timeout)


def _sign(secret: str, query_string: str) -> str:
    return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_get(api_key, secret, path, params, offset_ms, method="GET"):
    """Firma y envía un request a un endpoint USER_DATA/TRADE. NO loguea la secret."""
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000) + offset_ms
    p["recvWindow"] = RECV_WINDOW_MS
    qs = urllib.parse.urlencode(p)
    qs = f"{qs}&signature={_sign(secret, qs)}"
    url = f"{BASE_URL}{path}?{qs}"
    headers = {"X-MBX-APIKEY": api_key}
    if method == "POST":
        return _http_post(url, headers=headers, timeout=10)
    return _http_get(url, headers=headers, timeout=10)


def get_server_time_offset_ms() -> int:
    """offset = serverTime - localTime, para no fallar -1021 por clock skew."""
    r = _http_get(f"{BASE_URL}/api/v3/time", timeout=5)
    server_ms = int(r.json()["serverTime"])
    return server_ms - int(time.time() * 1000)


def _raise_for_error_code(resp):
    """Mapea códigos firmados de Binance a excepciones tipadas. La secret NUNCA
    entra al mensaje de la excepción (solo el code + msg de Binance)."""
    try:
        body = resp.json()
    except Exception:
        body = {}
    code = body.get("code")
    if code == -2015:
        raise BinanceAuthError(f"-2015: {body.get('msg', '')}")
    if code == -1021:
        raise BinanceClockSkew(f"-1021: {body.get('msg', '')}")
    if code == -1003 or resp.status_code in (418, 429):
        raise BinanceRateBanned(f"rate banned: HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"binance account HTTP {resp.status_code}: code={code}")


class BinanceAccountClient:
    def __init__(self, *, api_key: str, secret: str, server_time_offset_ms: int = 0):
        self._api_key = api_key
        self._secret = secret
        self._offset = server_time_offset_ms

    def get_spot_balances(self) -> dict[str, float]:
        """{asset: free+locked} para balances > 0. Lee /api/v3/account (USER_DATA)."""
        resp = _signed_get(self._api_key, self._secret, "/api/v3/account", {}, self._offset)
        _raise_for_error_code(resp)
        out: dict[str, float] = {}
        for b in resp.json().get("balances", []):
            total = float(b["free"]) + float(b["locked"])
            if total > 0:
                out[b["asset"]] = total
        return out

    def probe_trading_disabled(self) -> bool:
        """True si la key NO puede operar (lo que queremos para read-only).

        Usa /api/v3/order/test con una orden bien-formada: si Binance la rechaza
        por permiso (-2015) ⇒ trading deshabilitado ⇒ True. Si devuelve éxito ({})
        ⇒ trading HABILITADO ⇒ False (la key debe rechazarse). order/test NO
        coloca ninguna orden (valida y descarta)."""
        params = {
            "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
            "timeInForce": "GTC", "quantity": "0.00001", "price": "1",
        }
        resp = _signed_get(self._api_key, self._secret, "/api/v3/order/test",
                           params, self._offset, method="POST")
        try:
            code = resp.json().get("code")
        except Exception:
            code = None
        if code == -2015:
            return True   # sin permiso de trading → correcto
        if resp.status_code == 200:
            return False  # order/test aceptado → la key SÍ puede operar
        # Otro error (p.ej. parámetro) — no concluyente; fail-closed a "no validado".
        raise RuntimeError(f"order/test no concluyente: HTTP {resp.status_code} code={code}")
```

- [ ] **Step 4: Correr el test, verificar que pasa**

Run: `python -m pytest tests/test_binance_account_client.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add data/providers/binance_account.py tests/test_binance_account_client.py
git commit -m "feat(binance): cliente firmado read-only spot (HMAC, clock-sync, sonda order/test)"
```

---

## Task 5: Lógica de sync/reconciliación (`binance_sync.py`)

**Files:**
- Create: `binance_sync.py`
- Test: `tests/test_binance_sync.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_binance_sync.py
import sqlite3
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, "
        "symbol TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'LONG', status TEXT NOT NULL DEFAULT 'open', "
        "entry_price REAL NOT NULL, entry_ts TEXT NOT NULL, sl_price REAL, tp_price REAL, "
        "size_usd REAL, qty REAL, tenant_id INTEGER, control_domain TEXT NOT NULL DEFAULT 'INTERNAL', market TEXT)"
    )
    from db.schema import _install_binance_external_guards
    _install_binance_external_guards(c)
    # Fila EXTERNAL tecleada del papá (bootstrap, market NULL aún).
    c.execute(
        "INSERT INTO positions (symbol, direction, status, entry_price, entry_ts, qty, "
        "tenant_id, control_domain) VALUES ('BTCUSDT','LONG','open',64390,'t',0.01967,2,'EXTERNAL')"
    )
    return c


def test_base_asset_strips_quote():
    from binance_sync import base_asset
    assert base_asset("BTCUSDT") == "BTC"
    assert base_asset("ETHUSDC") == "ETH"


def test_reconcile_updates_qty_and_adopts_market(con):
    from binance_sync import reconcile_spot
    report = reconcile_spot(con, tenant_id=2, balances={"BTC": 0.02100}, dust=1e-6)
    row = con.execute("SELECT qty, market FROM positions WHERE symbol='BTCUSDT'").fetchone()
    assert abs(row["qty"] - 0.02100) < 1e-9   # qty autoridad-Binance
    assert row["market"] == "SPOT"            # bootstrap adoption
    assert report["reconciled"] == ["BTCUSDT"]


def test_closed_position_qty_goes_to_zero_and_is_flagged(con):
    from binance_sync import reconcile_spot
    report = reconcile_spot(con, tenant_id=2, balances={}, dust=1e-6)  # papá cerró en Binance
    row = con.execute("SELECT qty FROM positions WHERE symbol='BTCUSDT'").fetchone()
    assert row["qty"] == 0.0
    assert "BTCUSDT" in report["closed_pending"]


def test_untracked_hold_is_reported_not_inserted(con):
    from binance_sync import reconcile_spot
    report = reconcile_spot(con, tenant_id=2, balances={"BTC": 0.02, "SOL": 5.0}, dust=1e-6)
    n = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n == 1  # SOL NO se inserta (no hay cost-basis)
    assert "SOLUSDT" in report["untracked"]
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_binance_sync.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'binance_sync'`

- [ ] **Step 3: Implementar `binance_sync.py`**

```python
"""Reconciliación SPOT read-only: balances reales de Binance → filas EXTERNAL.

v0.1 es UPDATE-only + reporte (NO inserta): actualiza la `qty` de las filas
EXTERNAL spot registradas (autoridad = Binance, §4.1), adopta `market='SPOT'`
en el primer sync (bootstrap), y reporta holds no-registrados para registro
manual (no auto-crea: entry_price es NOT NULL, sin cost-basis = v0.2).

`closed_pending` (derivado de qty≈0 con credencial ACTIVE) = señal para que el
humano confirme el cierre (CD-5: el sistema no escribe `closed`). El equity se
auto-corrige porque qty→0 (compute_real_equity hace qty×precio).

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §4.
"""
from __future__ import annotations

import sqlite3

_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD")


def base_asset(symbol: str) -> str:
    """'BTCUSDT' → 'BTC'. Asume quote en _QUOTES (spot v0.1)."""
    s = symbol.upper()
    for q in _QUOTES:
        if s.endswith(q):
            return s[: -len(q)]
    return s


def reconcile_spot(
    con: sqlite3.Connection, *, tenant_id: int, balances: dict[str, float], dust: float = 1e-6,
) -> dict:
    """Reconcilia las filas EXTERNAL spot del tenant contra los balances reales.

    `balances`: {asset: free+locked} de get_spot_balances(). El caller posee la tx.
    """
    rows = con.execute(
        "SELECT id, symbol, qty FROM positions "
        "WHERE tenant_id=? AND status='open' AND control_domain='EXTERNAL' "
        "AND (market='SPOT' OR market IS NULL)",
        (tenant_id,),
    ).fetchall()

    reconciled: list[str] = []
    closed_pending: list[str] = []
    tracked_assets: set[str] = set()

    for r in rows:
        symbol = r["symbol"]
        asset = base_asset(symbol)
        tracked_assets.add(asset)
        real_qty = float(balances.get(asset, 0.0))
        # market='SPOT' adoptado en el mismo UPDATE (el trigger lo permite: EXTERNAL).
        con.execute(
            "UPDATE positions SET qty=?, market='SPOT' WHERE id=?",
            (real_qty, r["id"]),
        )
        if real_qty <= dust:
            closed_pending.append(symbol)   # señal de cierre observado (derivado)
        else:
            reconciled.append(symbol)

    # Holds reales no-registrados (asset con balance > dust sin fila): se REPORTAN.
    untracked: list[str] = []
    for asset, amount in balances.items():
        if amount > dust and asset not in tracked_assets and asset not in _QUOTES:
            untracked.append(f"{asset}USDT")

    return {
        "reconciled": reconciled,
        "closed_pending": closed_pending,
        "untracked": untracked,
    }
```

- [ ] **Step 4: Correr el test, verificar que pasa**

Run: `python -m pytest tests/test_binance_sync.py -v`
Expected: PASS (4 passed). Nota: `test_reconcile_updates_qty_and_adopts_market` confirma que el UPDATE que setea `market='SPOT'` sobre una fila EXTERNAL pasa el trigger (Task 3).

- [ ] **Step 5: Commit**

```bash
git add binance_sync.py tests/test_binance_sync.py
git commit -m "feat(binance): reconcile_spot — UPDATE qty autoridad-Binance + reporte (no auto-crea)"
```

---

## Task 6: Exclusión del equity de holds vacíos (cierra BLOCKER-5 en `compute_real_equity`)

**Files:**
- Modify: `api/equity.py`
- Test: `tests/test_real_equity.py` (añadir un test; no romper los existentes)

> Nota: `reconcile_spot` ya pone `qty=0` para un hold cerrado, así que `qty×precio=0`
> y el equity no se infla. Este task hace la exclusión EXPLÍCITA (defensa en
> profundidad): una fila EXTERNAL con `qty ≤ 0` no aparece en `holds` ni suma.

- [ ] **Step 1: Escribir el test que falla**

```python
# añadir a tests/test_real_equity.py
def test_zero_qty_external_excluded_from_equity(tmp_path):
    import sqlite3
    from api.equity import compute_real_equity
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE capital (id INTEGER PRIMARY KEY, tenant_id INTEGER, "
                "balance REAL, peak_balance REAL, max_drawdown_pct REAL, updated_at TEXT, "
                "cash_balance_usd REAL DEFAULT 0)")
    con.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, symbol TEXT, qty REAL, "
                "status TEXT, control_domain TEXT, tenant_id INTEGER)")
    con.execute("INSERT INTO capital (tenant_id, balance, peak_balance, cash_balance_usd, updated_at) "
                "VALUES (2, 0, 0, 100.0, 't')")
    con.execute("INSERT INTO positions (symbol, qty, status, control_domain, tenant_id) "
                "VALUES ('BTCUSDT', 0.0, 'open', 'EXTERNAL', 2)")
    out = compute_real_equity(con, tenant_id=2, price_lookup={"BTCUSDT": 64000.0})
    assert out["holds"] == []
    assert out["real_equity_usd"] == 100.0
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_real_equity.py::test_zero_qty_external_excluded_from_equity -v`
Expected: FAIL — la fila con qty=0 entra como hold con value 0 (`holds` no está vacío).

- [ ] **Step 3: Modificar `api/equity.py`**

En el SELECT (línea 36), filtrar qty > 0 directamente en SQL:

```python
    rows = con.execute(
        "SELECT symbol, qty FROM positions "
        "WHERE tenant_id = ? AND status = 'open' AND control_domain = 'EXTERNAL' "
        "AND qty IS NOT NULL AND qty > 0 "
        "ORDER BY symbol",
        (tenant_id,),
    ).fetchall()
```

- [ ] **Step 4: Correr los tests, verificar que pasan**

Run: `python -m pytest tests/test_real_equity.py -v`
Expected: PASS (el nuevo + los existentes; un hold con qty=0 ya no aparece).

- [ ] **Step 5: Commit**

```bash
git add api/equity.py tests/test_real_equity.py
git commit -m "fix(equity): excluir holds EXTERNAL con qty<=0 (cierre observado no infla equity)"
```

---

## Task 7: Candados de no-fuga + endpoint de metadatos

**Files:**
- Create: `api/binance_credentials_api.py`
- Test: `tests/test_binance_credentials_no_leak.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_binance_credentials_no_leak.py
import logging
import sqlite3
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    from db.schema import _migrate_binance_credentials
    _migrate_binance_credentials(c)
    return c


def test_metadata_endpoint_never_returns_secret(con):
    from db.binance_credentials import db_upsert_binance_credential
    from api.binance_credentials_api import credential_status_payload
    db_upsert_binance_credential(con, tenant_id=2, api_key_public="PUBKEY1234567890",
                                 secret_plaintext="SUPERSECRET")
    payload = credential_status_payload(con, tenant_id=2)
    blob = str(payload).lower()
    assert "supersecret" not in blob
    assert "secret_enc" not in payload
    assert payload["api_key_last4"] == "7890"


def test_secret_not_in_logs_on_upsert(con, caplog):
    from db.binance_credentials import db_upsert_binance_credential
    with caplog.at_level(logging.DEBUG):
        db_upsert_binance_credential(con, tenant_id=2, api_key_public="P", secret_plaintext="LEAKME")
    assert "LEAKME" not in caplog.text


def test_auth_error_message_excludes_secret():
    from data.providers.binance_account import BinanceAccountClient, BinanceAuthError
    from unittest.mock import patch

    class FakeResp:
        status_code = 401
        def json(self): return {"code": -2015, "msg": "Invalid API-key"}
        text = "{}"

    client = BinanceAccountClient(api_key="PUBKEY", secret="THE_SECRET", server_time_offset_ms=0)
    with patch("data.providers.binance_account._signed_get", return_value=FakeResp()):
        try:
            client.get_spot_balances()
            assert False
        except BinanceAuthError as e:
            assert "THE_SECRET" not in str(e)
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_binance_credentials_no_leak.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'api.binance_credentials_api'`

- [ ] **Step 3: Implementar `api/binance_credentials_api.py`**

```python
"""Endpoint read-only de ESTADO de la credencial Binance — NUNCA la secret.

Devuelve solo metadatos seguros (existe/no, últimos 4 de la api_key pública,
scope, ip_whitelisted, status). La secret no sale del backend ni cifrada.

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.4 (BNC-2).
"""
from __future__ import annotations

import sqlite3

from db.binance_credentials import get_credential_metadata


def credential_status_payload(con: sqlite3.Connection, tenant_id: int) -> dict:
    """Payload seguro para el frontend. {connected: False} si no hay credencial."""
    meta = get_credential_metadata(con, tenant_id)
    if meta is None:
        return {"connected": False}
    return {"connected": True, **meta}
```

> Nota de integración: el router FastAPI (GET `/binance/credential/status` con
> `Depends(get_current_tenant_id)`) se monta en `btc_api.py` junto a los demás
> routers; usa `tenant_id` del JWT, NUNCA de un param (precedente IDOR, Epic B).
> El wiring del router es parte del Task 8.

- [ ] **Step 4: Correr el test, verificar que pasa**

Run: `python -m pytest tests/test_binance_credentials_no_leak.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add api/binance_credentials_api.py tests/test_binance_credentials_no_leak.py
git commit -m "feat(binance): endpoint de metadatos de credencial + candados de no-fuga"
```

---

## Task 8: CLI de onboarding (sonda BNC-9) + CLI de sync

**Files:**
- Create: `tools/set_binance_key.py`
- Create: `tools/sync_binance_spot.py`
- Test: `tests/test_set_binance_key.py`

- [ ] **Step 1: Escribir el test que falla (gate de la sonda BNC-9)**

```python
# tests/test_set_binance_key.py
import sqlite3
import pytest
from unittest.mock import MagicMock
from cryptography.fernet import Fernet


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("TRADING_BINANCE_MASTER_KEY", Fernet.generate_key().decode())
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    from db.schema import _migrate_binance_credentials
    _migrate_binance_credentials(c)
    return c


def test_onboard_rejects_key_with_trading_enabled(con):
    from tools.set_binance_key import onboard_credential
    client = MagicMock()
    client.probe_trading_disabled.return_value = False  # trading ENABLED → rechazar
    client.get_spot_balances.return_value = {"BTC": 0.5}
    with pytest.raises(ValueError, match="trading"):
        onboard_credential(con, tenant_id=2, api_key="P", secret="S",
                           client=client, ip_whitelisted=True)
    assert con.execute("SELECT COUNT(*) FROM binance_credentials").fetchone()[0] == 0


def test_onboard_stores_when_read_only_and_ip_whitelisted(con):
    from tools.set_binance_key import onboard_credential
    from db.binance_credentials import db_get_decrypted_secret
    client = MagicMock()
    client.probe_trading_disabled.return_value = True   # read-only → OK
    client.get_spot_balances.return_value = {"BTC": 0.5}
    onboard_credential(con, tenant_id=2, api_key="PUB", secret="SECRET",
                       client=client, ip_whitelisted=True)
    assert db_get_decrypted_secret(con, tenant_id=2) == "SECRET"


def test_onboard_requires_ip_whitelist(con):
    from tools.set_binance_key import onboard_credential
    client = MagicMock()
    client.probe_trading_disabled.return_value = True
    client.get_spot_balances.return_value = {"BTC": 0.5}
    with pytest.raises(ValueError, match="IP"):
        onboard_credential(con, tenant_id=2, api_key="P", secret="S",
                           client=client, ip_whitelisted=False)
```

- [ ] **Step 2: Correr el test, verificar que falla**

Run: `python -m pytest tests/test_set_binance_key.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'tools.set_binance_key'`

- [ ] **Step 3: Implementar `tools/set_binance_key.py`**

```python
"""CLI one-shot: onboardea la API key read-only spot del tenant (gate BNC-9).

Antes de guardar UNA key real, exige: (a) lectura spot OK; (b) la sonda
order/test confirma trading DESHABILITADO; (c) IP-whitelist declarada. Sin los
tres, NO persiste. La secret se cifra antes del INSERT.

Usage (en el server, contra DB_FILE = prod):
    python -m tools.set_binance_key --tenant 2 --api-key <PUB> --secret <SECRET> --ip-whitelisted

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §2.4 (BNC-9).
"""
from __future__ import annotations

import argparse
import sqlite3

from db.binance_credentials import db_upsert_binance_credential


def onboard_credential(con, *, tenant_id, api_key, secret, client, ip_whitelisted):
    """Gate BNC-9 + persistencia. `client` = BinanceAccountClient (inyectable para test)."""
    if not ip_whitelisted:
        raise ValueError("IP-whitelist obligatorio (BNC-9): la IP estática del VPS "
                         "debe estar en la key de Binance antes de aceptarla.")
    client.get_spot_balances()                       # (a) lectura spot OK (lanza si -2015)
    if not client.probe_trading_disabled():          # (b) trading debe estar OFF
        raise ValueError("la key tiene trading habilitado; v0.1 exige read-only. "
                         "Crea una key con SOLO 'Enable Reading' y vuelve a intentar.")
    db_upsert_binance_credential(
        con, tenant_id=tenant_id, api_key_public=api_key, secret_plaintext=secret,
        scope_detected="READ_ONLY_SPOT", ip_whitelisted=True,
    )


def main() -> int:
    from db.transaction import transaction
    from data.providers.binance_account import BinanceAccountClient, get_server_time_offset_ms

    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--ip-whitelisted", action="store_true",
                    help="confirma que la IP del VPS está whitelisted en Binance")
    args = ap.parse_args()

    client = BinanceAccountClient(
        api_key=args.api_key, secret=args.secret,
        server_time_offset_ms=get_server_time_offset_ms(),
    )
    with transaction() as con:
        con.row_factory = sqlite3.Row
        onboard_credential(con, tenant_id=args.tenant, api_key=args.api_key,
                           secret=args.secret, client=client,
                           ip_whitelisted=args.ip_whitelisted)
    print(f"Credencial Binance read-only spot guardada para tenant {args.tenant}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Implementar `tools/sync_binance_spot.py`**

```python
"""CLI one-shot: corre la reconciliación spot para un tenant (Cassian cut-line).

Lee la credencial cifrada, descifra en memoria, consulta balances, reconcilia.
Maneja el estado de credencial fail-closed (AUTH_FAILED/RATE_BANNED/CLOCK_SKEW).
Correrlo a mano una vez al día ya entrega el valor central (dejar de teclear qty).
El auto-loop en el ciclo de scan = v0.1.1.

Usage: python -m tools.sync_binance_spot --tenant 2

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §4, §7.
"""
from __future__ import annotations

import argparse
import sqlite3

from binance_sync import reconcile_spot
from data.providers.binance_account import (
    BinanceAccountClient, BinanceAuthError, BinanceClockSkew, BinanceRateBanned,
    get_server_time_offset_ms,
)
from db.binance_credentials import (
    db_get_binance_credential_raw, db_get_decrypted_secret, db_set_credential_status,
)


def sync_tenant(con: sqlite3.Connection, tenant_id: int) -> dict:
    """Reconcilia spot para un tenant. Devuelve el reporte o {status: ...} si falla.
    Una credencial no-ACTIVE no se sincroniza (fail-closed)."""
    cred = db_get_binance_credential_raw(con, tenant_id)
    if cred is None:
        return {"status": "NO_CREDENTIAL"}
    if cred["status"] != "ACTIVE":
        return {"status": cred["status"], "skipped": True}

    secret = db_get_decrypted_secret(con, tenant_id)
    client = BinanceAccountClient(
        api_key=cred["api_key_public"], secret=secret,
        server_time_offset_ms=get_server_time_offset_ms(),
    )
    try:
        balances = client.get_spot_balances()
    except BinanceAuthError:
        db_set_credential_status(con, tenant_id, "AUTH_FAILED")
        return {"status": "AUTH_FAILED"}
    except BinanceClockSkew:
        db_set_credential_status(con, tenant_id, "CLOCK_SKEW")
        return {"status": "CLOCK_SKEW"}
    except BinanceRateBanned:
        db_set_credential_status(con, tenant_id, "RATE_BANNED")
        return {"status": "RATE_BANNED"}

    report = reconcile_spot(con, tenant_id=tenant_id, balances=balances)
    report["status"] = "ACTIVE"
    return report


def main() -> int:
    from db.transaction import transaction
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    args = ap.parse_args()
    with transaction() as con:
        con.row_factory = sqlite3.Row
        report = sync_tenant(con, args.tenant)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Correr el test, verificar que pasa**

Run: `python -m pytest tests/test_set_binance_key.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Correr el gate completo (selección de CI)**

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: PASS (toda la suite, incluido el canon test y los nuevos). Si algo rojo, DEBUG antes de seguir.

- [ ] **Step 7: Commit**

```bash
git add tools/set_binance_key.py tools/sync_binance_spot.py tests/test_set_binance_key.py
git commit -m "feat(binance): CLI onboarding (gate BNC-9) + CLI sync spot one-shot"
```

---

## Despliegue (NO parte del merge — pasos de operador, post-PR)

Estos pasos NO van en el PR; son la activación contra prod, y solo tras CI verde + revisión. Recordar: **merge a main = deploy automático** (el código viaja; la key NO).

1. Generar la master key y ponerla en el `EnvironmentFile` de systemd del server (`chmod 600`, fuera de backup): `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` → `TRADING_BINANCE_MASTER_KEY=...`. `systemctl restart` el servicio.
2. El papá crea en Binance una API key con **SOLO "Enable Reading"** + **IP-whitelist a la IP del VPS** + **Withdrawals OFF**.
3. Correr el onboarding (pasa el gate BNC-9): `python -m tools.set_binance_key --tenant 2 --api-key <PUB> --secret <SECRET> --ip-whitelisted`.
4. Correr el sync una vez y verificar el reporte: `python -m tools.sync_binance_spot --tenant 2`.
5. Confirmar contra prod que la `qty` de las EXTERNAL del papá refleja Binance y el equity es coherente.

---

## Self-Review (hecha por el autor del plan)

**Spec coverage (REV 3):** §2.1 permisos→Task 8 (gate BNC-9 rechaza trading + exige IP). §2.2 tabla→Task 2. §2.3 Fernet/master-key/pérdida→Task 1 (+ despliegue paso 1). §2.4 no-fuga + sonda→Tasks 7, 8. §3 cliente firmado/clock-sync→Task 4. §4.1 autoridad/no-auto-crea→Task 5. §4.2 idempotencia→Task 3 (índice) + Task 5. §4.3 garantía estructural→Task 3 (trigger). §4.3b schema→Task 3. §4.4 estados credencial→Task 5/8 (sync_tenant mapea -2015/-1021/-1003). §4.5 spot→todo. §4.6 staleness=v0.1.5 (BNC-11: sin cambio de firma — respetado, Task 6 no toca `price_lookup`). §4.7 cierre observado→Tasks 5+6 (qty→0 + exclusión + closed_pending derivado). §4.8 bootstrap→Task 5 (adopción por identidad + market='SPOT'). §5 ontología: el sync no acuña conducta (no toca exit_reason); CD-2-EXT (no co-render) = concern del frontend, fuera de v0.1 backend. §8 invariantes BNC-1..11 cubiertos. §10.1 (spot/futuros de las 2 filas) = pregunta abierta a Samuel, no bloquea el código (Task 5 adopta si están como spot).

**Placeholder scan:** sin TODO/TBD en pasos de código; cada paso tiene código o comando real.

**Type consistency:** `BinanceAccountClient(api_key=, secret=, server_time_offset_ms=)`, `get_spot_balances()→dict[str,float]`, `probe_trading_disabled()→bool`, `reconcile_spot(con,*,tenant_id,balances,dust)→{reconciled,closed_pending,untracked}`, `onboard_credential(con,*,tenant_id,api_key,secret,client,ip_whitelisted)`, `db_upsert_binance_credential(con,*,tenant_id,api_key_public,secret_plaintext,...)` — consistentes entre tasks.

**Gap conocido (no bloquea v0.1):** el wiring del router FastAPI GET `/binance/credential/status` en `btc_api.py` se describe en Task 7 nota pero no tiene paso TDD propio (es montaje de router, verificable a mano); si se quiere candado, añadir un test de integración del router en un follow-up. El auto-loop del sync en el ciclo de scan = v0.1.1 (línea de corte de Cassian).
