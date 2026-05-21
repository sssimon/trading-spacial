# Telegram per-user config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) o superpowers:executing-plans para ejecutar este plan task-por-task. Steps usan checkbox (`- [ ]`) para tracking.

**Goal:** Cada operador del sistema configura su propio bot de Telegram (token + chat_id) desde el dashboard, sin SSH ni admin work. Después de save+test exitoso, recibe automáticamente cada signal con `score ≥ min_score` en SU bot.

**Architecture:** El backend bypassa `notify()` para `/preferences/test` y llama `TelegramChannel.send()` directo (evita dedup edge-cases + side-effect en NotificationBell). El frontend reusa el item placeholder "Conexiones" en `UserMenu.tsx:31` que ya existe wirearlo + abre nuevo slide-out `ConnectionsPanel`. Tokens enmascarados en GET response (XSS blast-radius mitigation).

**Tech Stack:** Python 3.11 (FastAPI, pydantic, pytest), TypeScript/React (vitest), Playwright (e2e), httpx para Telegram Bot API.

**Spec:** [`docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md`](../specs/es/2026-05-21-telegram-per-user-config-pre-reg.md) (commit `142bbf5`).

---

## File structure

### Archivos creados (NEW)

| Path | Responsabilidad |
|---|---|
| `frontend/src/components/ConnectionsPanel.tsx` | Slide-out panel desde user dropdown; sección "Telegram" con form + 3 botones (Guardar, Probar, Eliminar). |
| `frontend/src/components/ConnectionsPanel.module.css` | Estilos (mismo patrón slide-out from right que `ConfigPanel.module.css`). |
| `frontend/src/components/ConnectionsPanel.test.tsx` | 8 vitest tests del componente. |
| `frontend/src/components/UserMenu.test.tsx` | 2 vitest tests del badge condicional. |
| `tests/test_api_user_preferences.py` | 9 pytest tests del backend (masking + endpoint /test). |

### Archivos modificados

| Path | Cambio |
|---|---|
| `api/user_preferences.py` | + `_mask_token()` helper; modificar GET para masking; modificar PUT para detect-masked-and-skip; agregar `POST /preferences/test`. |
| `db/user_preferences.py` | Posiblemente cero cambios — el masking vive en API layer, el DB layer sigue devolviendo plain. (Verificar en Task 3.) |
| `frontend/src/types.ts` | + interface `TestDeliveryResponse` + interface `NotifyChannels`. |
| `frontend/src/api.ts` | + wrapper `testPreferencesDelivery()`. |
| `frontend/src/components/UserMenu.tsx` | Wirear `onClick` al item "Conexiones"; badge `'1'` condicional según `notify_channels.telegram_bot_token`. |
| `frontend/src/App.tsx` | Extender `OverlayKind` con `'connections'`; render del `<ConnectionsPanel>`; cerrar UserMenu al click. |

### Branch

`feat/telegram-per-user-config` — un solo branch + un solo PR cubriendo backend + frontend + tests. Justification: el frontend depende del backend, pero el feature es shippeable de una pieza. Si el reviewer pide split, hacemos split en review.

---

## Pre-conditions

- [ ] **PC1: Branch nueva desde main sync**

```bash
git checkout main && git pull --ff-only origin main && git checkout -b feat/telegram-per-user-config
```

Expected: branch `feat/telegram-per-user-config`, parte de main al día.

- [ ] **PC2: Test baseline passing**

```bash
python -m pytest tests/test_provider_registry.py tests/test_api_user_preferences.py -k "not trio" 2>&1 | tail -3
```

Expected: tests/test_api_user_preferences.py no existe todavía → pytest errores "no tests collected" pero `test_provider_registry.py` 13/13 pass. OK arrancar.

- [ ] **PC3: Frontend test baseline passing**

```bash
cd frontend && npm test -- --run --reporter=verbose 2>&1 | tail -10
```

Expected: la suite existente verde. Si algo rojo, abort + investigar antes de agregar más tests.

---

### Task 1: Backend — `_mask_token` helper + tests

**Files:**
- Modify: `api/user_preferences.py` (agregar helper al final del module-level scope).
- Create: `tests/test_api_user_preferences.py`.

- [ ] **Step 1.1: Crear test file con failing tests para `_mask_token`**

Create `tests/test_api_user_preferences.py`:

```python
"""Tests for api/user_preferences.py — masking + /preferences/test endpoint.

Covers:
- _mask_token helper invariants.
- GET /api/preferences masks telegram_bot_token in response.
- PUT /api/preferences preserves masked token, replaces unmasked.
- POST /api/preferences/test: no-creds early-return, happy path, dedup-bypass,
  no-write-to-notifications_sent, isolated per tenant.
"""
from __future__ import annotations

import pytest


# ── _mask_token helper ──────────────────────────────────────────────


def test_mask_token_real_telegram_token():
    """Real Telegram tokens are ~46 chars (<bot_id>:<35-char-secret>).
    Mask should preserve first 10 + last 4 chars with **** between."""
    from api.user_preferences import _mask_token
    token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12"
    assert _mask_token(token) == "123456789:****gH12"


def test_mask_token_empty_returns_empty():
    """Empty input → empty output (no masking artifacts on empty)."""
    from api.user_preferences import _mask_token
    assert _mask_token("") == ""


def test_mask_token_short_input_returns_empty():
    """Inputs shorter than 10 chars are treated as garbage and return ""
    (defensive: real tokens are always ≥10 chars; this guard prevents
    leaking partial credentials via mask='****X' for X<10 chars)."""
    from api.user_preferences import _mask_token
    assert _mask_token("123") == ""
    assert _mask_token("123456789") == ""  # exactly 9, still under threshold
```

- [ ] **Step 1.2: Run tests — verify FAIL**

```bash
python -m pytest tests/test_api_user_preferences.py -v
```

Expected: 3 tests FAIL con `ImportError: cannot import name '_mask_token'` (la función no existe todavía).

- [ ] **Step 1.3: Implementar `_mask_token` en `api/user_preferences.py`**

Agregar al final del módulo (después de `put_preferences`):

```python
def _mask_token(token: str) -> str:
    """Mask a Telegram bot token, preserving first 10 + last 4 chars.

    Real Telegram tokens have shape `<bot_id>:<35-char-secret>` (~46 chars
    total). Output: first 10 chars + "****" + last 4 chars.

    Defensive: `len < 10` returns "" instead of partial mask. Real tokens
    are never shorter than 10 chars; this guard only fires for garbage
    (empty, corrupt, manually-truncated). Returning "" makes the masked
    field indistinguishable from "not configured" in those cases — accept-
    able because the path is not reachable in prod with valid creds.

    Spec ref: docs/superpowers/specs/es/2026-05-21-telegram-per-user-
    config-pre-reg.md §Security note.
    """
    if not token or len(token) < 10:
        return ""
    return f"{token[:10]}****{token[-4:]}"
```

- [ ] **Step 1.4: Run tests — verify PASS**

```bash
python -m pytest tests/test_api_user_preferences.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add api/user_preferences.py tests/test_api_user_preferences.py
git commit -m "feat(prefs): _mask_token helper for telegram_bot_token

Preserves first 10 + last 4 chars (e.g. '123456789:****gH12'). Returns
empty string for inputs <10 chars (defensive, real tokens are ~46 chars).

Pre-reg: docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md"
```

---

### Task 2: Backend — GET masks token + PUT preserves masked

**Files:**
- Modify: `api/user_preferences.py` (modify `get_preferences` + `put_preferences`).
- Modify: `tests/test_api_user_preferences.py` (add 3 tests).

- [ ] **Step 2.1: Agregar failing tests al test file**

Append to `tests/test_api_user_preferences.py`:

```python
# ── GET /api/preferences masking ────────────────────────────────────


@pytest.fixture
def seeded_user_with_telegram(tmp_path, monkeypatch):
    """Fresh DB con user_id=1 + notify_channels con telegram creds populados.
    Devuelve el TestClient configurado para auth bypass (admin role)."""
    import btc_api
    from fastapi.testclient import TestClient
    from db.auth_schema import init_auth_db
    from db.schema import init_db
    from db.connection import get_db
    from db.user_preferences import db_upsert_user_preferences

    db_path = str(tmp_path / "test_prefs.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    init_db()
    init_auth_db()

    con = get_db()
    con.execute(
        "INSERT INTO users (id, email, password_hash, role, is_active, "
        "created_at, password_changed_at) VALUES "
        "(1, 'a@example.com', 'h', 'admin', 1, "
        "'2026-05-21T00:00:00+00:00', '2026-05-21T00:00:00+00:00')"
    )
    con.commit()
    con.close()

    db_upsert_user_preferences(
        1,
        notify_channels={
            "telegram_bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12",
            "telegram_chat_id":   "987654321",
        },
    )

    return TestClient(btc_api.app)


def test_get_preferences_masks_telegram_bot_token(seeded_user_with_telegram):
    """GET response should return masked token, not plain — defense against
    XSS exfiltration of credentials."""
    client = seeded_user_with_telegram
    r = client.get("/preferences")
    assert r.status_code == 200
    data = r.json()
    masked = data["notify_channels"]["telegram_bot_token"]
    assert masked == "123456789:****gH12"
    assert "ABCdefGHI" not in masked, "secret portion should not leak"


def test_get_preferences_chat_id_not_masked(seeded_user_with_telegram):
    """chat_id is not secret (visible in any getUpdates response); pass through."""
    client = seeded_user_with_telegram
    r = client.get("/preferences")
    assert r.json()["notify_channels"]["telegram_chat_id"] == "987654321"


# ── PUT /api/preferences preserves masked token ──────────────────────


def test_put_preserves_token_when_value_contains_mask_marker(seeded_user_with_telegram):
    """If user submits the masked value (didn't retype), DB token stays unchanged."""
    client = seeded_user_with_telegram
    masked = "123456789:****gH12"
    r = client.put("/preferences", json={
        "notify_channels": {
            "telegram_bot_token": masked,
            "telegram_chat_id":   "111222333",  # changed
        },
    })
    assert r.status_code == 200

    # Re-fetch raw from DB (bypass masking) to verify token preserved
    from db.user_preferences import db_get_user_preferences
    row = db_get_user_preferences(1)
    nc = row["notify_channels"]
    assert nc["telegram_bot_token"] == "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12"
    assert nc["telegram_chat_id"] == "111222333"


def test_put_replaces_token_when_value_unmasked(seeded_user_with_telegram):
    """If user submits a plain token (no ****), DB updates to the new value."""
    client = seeded_user_with_telegram
    r = client.put("/preferences", json={
        "notify_channels": {
            "telegram_bot_token": "111111111:NEWXYZabcDEFghiJKLmnoPQRstuVWXyzABCDE",
            "telegram_chat_id":   "987654321",
        },
    })
    assert r.status_code == 200

    from db.user_preferences import db_get_user_preferences
    row = db_get_user_preferences(1)
    assert row["notify_channels"]["telegram_bot_token"] == "111111111:NEWXYZabcDEFghiJKLmnoPQRstuVWXyzABCDE"
```

- [ ] **Step 2.2: Run tests — verify FAIL**

```bash
python -m pytest tests/test_api_user_preferences.py::test_get_preferences_masks_telegram_bot_token tests/test_api_user_preferences.py::test_put_preserves_token_when_value_contains_mask_marker -v
```

Expected: ambos FAIL (GET devuelve token plain; PUT sobreescribe con masked).

- [ ] **Step 2.3: Modificar `get_preferences` en `api/user_preferences.py`**

Encontrar:

```python
@router.get("", summary="Get preferences for current tenant (defaults if unset)")
def get_preferences(tenant_id: int = Depends(get_current_tenant_id)):
    row = db_get_user_preferences(tenant_id)
    if row is None:
        return {
            "tenant_id": tenant_id,
            "symbol_filter": None,
            "min_score": _DEFAULT_MIN_SCORE,
            "notify_channels": None,
        }
    return row
```

Reemplazar con:

```python
@router.get("", summary="Get preferences for current tenant (defaults if unset)")
def get_preferences(tenant_id: int = Depends(get_current_tenant_id)):
    row = db_get_user_preferences(tenant_id)
    if row is None:
        return {
            "tenant_id": tenant_id,
            "symbol_filter": None,
            "min_score": _DEFAULT_MIN_SCORE,
            "notify_channels": None,
        }
    # Mask telegram_bot_token in the response to reduce XSS blast radius.
    # See spec §Security note.
    nc = row.get("notify_channels") or None
    if nc and nc.get("telegram_bot_token"):
        nc = {**nc, "telegram_bot_token": _mask_token(nc["telegram_bot_token"])}
        row = {**row, "notify_channels": nc}
    return row
```

- [ ] **Step 2.4: Modificar `put_preferences` en `api/user_preferences.py`**

Encontrar:

```python
@router.put(
    "",
    summary="Upsert preferences for current tenant",
    dependencies=[Depends(verify_api_key)],
)
def put_preferences(
    body: PreferencesPutBody,
    tenant_id: int = Depends(get_current_tenant_id),
):
    row = db_upsert_user_preferences(
        tenant_id,
        symbol_filter=body.symbol_filter,
        min_score=body.min_score,
        notify_channels=body.notify_channels,
    )
    return {"ok": True, "preferences": row}
```

Reemplazar con:

```python
@router.put(
    "",
    summary="Upsert preferences for current tenant",
    dependencies=[Depends(verify_api_key)],
)
def put_preferences(
    body: PreferencesPutBody,
    tenant_id: int = Depends(get_current_tenant_id),
):
    # If the submitted telegram_bot_token contains the mask marker '****',
    # the user did NOT retype it — preserve the existing DB value. This
    # supports the UX pattern "pre-fill masked value, only update if user
    # types something new". See spec §Security note.
    notify_channels = body.notify_channels
    if notify_channels and "****" in (notify_channels.get("telegram_bot_token") or ""):
        existing = db_get_user_preferences(tenant_id)
        existing_token = (
            (existing or {}).get("notify_channels") or {}
        ).get("telegram_bot_token", "")
        notify_channels = {**notify_channels, "telegram_bot_token": existing_token}

    row = db_upsert_user_preferences(
        tenant_id,
        symbol_filter=body.symbol_filter,
        min_score=body.min_score,
        notify_channels=notify_channels,
    )
    # Re-fetch + mask before returning (consistency with GET).
    fresh = db_get_user_preferences(tenant_id)
    if fresh and fresh.get("notify_channels", {}).get("telegram_bot_token"):
        nc = fresh["notify_channels"]
        fresh = {**fresh, "notify_channels": {**nc, "telegram_bot_token": _mask_token(nc["telegram_bot_token"])}}
    return {"ok": True, "preferences": fresh}
```

- [ ] **Step 2.5: Run tests — verify PASS**

```bash
python -m pytest tests/test_api_user_preferences.py -v
```

Expected: 7/7 PASS (3 originales + 4 nuevos).

- [ ] **Step 2.6: Commit**

```bash
git add api/user_preferences.py tests/test_api_user_preferences.py
git commit -m "feat(prefs): mask telegram_bot_token in GET, preserve on PUT

Reduces XSS exfiltration blast radius (~90%) without schema change. GET
returns '<10chars>****<4chars>'; PUT detects masked value and preserves
existing DB token instead of overwriting with the placeholder.

Pre-reg: docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md §Security note"
```

---

### Task 3: Backend — `POST /preferences/test` endpoint

**Files:**
- Modify: `api/user_preferences.py` (agregar endpoint).
- Modify: `tests/test_api_user_preferences.py` (agregar 5 tests).

- [ ] **Step 3.1: Agregar failing tests al test file**

Append to `tests/test_api_user_preferences.py`:

```python
# ── POST /api/preferences/test ──────────────────────────────────────


def test_test_endpoint_no_channels_returns_no_telegram_configured(tmp_path, monkeypatch):
    """User without notify_channels → {ok: false, reason: 'no_telegram_configured'}."""
    import btc_api
    from fastapi.testclient import TestClient
    from db.auth_schema import init_auth_db
    from db.schema import init_db
    from db.connection import get_db

    db_path = str(tmp_path / "test_prefs_no_channels.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    init_db()
    init_auth_db()
    con = get_db()
    con.execute(
        "INSERT INTO users (id, email, password_hash, role, is_active, "
        "created_at, password_changed_at) VALUES "
        "(1, 'a@example.com', 'h', 'admin', 1, "
        "'2026-05-21T00:00:00+00:00', '2026-05-21T00:00:00+00:00')"
    )
    con.commit()
    con.close()

    client = TestClient(btc_api.app)
    r = client.post("/preferences/test")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": False, "receipts": [], "reason": "no_telegram_configured"}


def test_test_endpoint_only_token_returns_no_telegram_configured(seeded_user_with_telegram, monkeypatch):
    """Token set but chat_id missing → still no_telegram_configured."""
    from db.user_preferences import db_upsert_user_preferences
    db_upsert_user_preferences(1, notify_channels={"telegram_bot_token": "xxx:yyy"})

    client = seeded_user_with_telegram
    r = client.post("/preferences/test")
    assert r.json()["reason"] == "no_telegram_configured"


def test_test_endpoint_with_telegram_routes_correctly(seeded_user_with_telegram, monkeypatch):
    """Happy path: token + chat_id set → TelegramChannel.send called with user_cfg.
    Mock requests.post to avoid hitting real Telegram API."""
    sent_payloads = []
    class _FakeResp:
        ok = True
        status_code = 200
        text = "ok"
    def _fake_post(url, json=None, **kw):
        sent_payloads.append({"url": url, "body": json})
        return _FakeResp()

    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    client = seeded_user_with_telegram
    r = client.post("/preferences/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["receipts"] == [{"channel": "telegram", "status": "ok", "error": None}]
    assert body["reason"] is None
    # Sent payload uses the USER's bot token + chat_id from notify_channels overlay
    assert len(sent_payloads) == 1
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz_aBcDeFgH12" in sent_payloads[0]["url"]
    assert sent_payloads[0]["body"]["chat_id"] == "987654321"


def test_test_endpoint_two_calls_within_window_both_succeed(seeded_user_with_telegram, monkeypatch):
    """Defense against future dedup window changes: 2 calls in quick succession
    BOTH return ok=true. Bypass of notify() guarantees this regardless of any
    future tightening of signal-type dedup defaults."""
    class _FakeResp:
        ok = True
        status_code = 200
        text = "ok"
    def _fake_post(url, **kw):
        return _FakeResp()
    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    client = seeded_user_with_telegram
    r1 = client.post("/preferences/test")
    r2 = client.post("/preferences/test")
    assert r1.json()["ok"] is True
    assert r2.json()["ok"] is True


def test_test_endpoint_does_not_write_to_notifications_sent(seeded_user_with_telegram, monkeypatch):
    """Bypass of notify() means no row written to notifications_sent
    (avoids polluting NotificationBell with test pings)."""
    class _FakeResp:
        ok = True
        status_code = 200
        text = "ok"
    def _fake_post(url, **kw):
        return _FakeResp()
    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    from db.connection import get_db
    con = get_db()
    before = con.execute("SELECT COUNT(*) FROM notifications_sent").fetchone()[0]
    con.close()

    client = seeded_user_with_telegram
    client.post("/preferences/test")

    con = get_db()
    after = con.execute("SELECT COUNT(*) FROM notifications_sent").fetchone()[0]
    con.close()
    assert before == after, "POST /test should NOT write to notifications_sent"
```

- [ ] **Step 3.2: Run tests — verify FAIL**

```bash
python -m pytest tests/test_api_user_preferences.py -v -k "test_test_endpoint"
```

Expected: 5 tests FAIL — endpoint no existe, devuelve 404.

- [ ] **Step 3.3: Implementar `POST /preferences/test` en `api/user_preferences.py`**

Agregar después de `put_preferences`:

```python
@router.post(
    "/test",
    summary="Send a test message to current tenant's Telegram",
    dependencies=[Depends(verify_api_key)],
)
def post_preferences_test(tenant_id: int = Depends(get_current_tenant_id)):
    """Verifica end-to-end que las credenciales de Telegram del usuario
    funcionan, mandando un mensaje 'ping' a su bot.

    Bypassa notify() y dispatch_signal_to_users a propósito (spec §Backend
    Option 2):
      - Evita dedup edge-cases (signal event_type tiene dedup_window=0 hoy,
        pero el bypass blinda contra cambios futuros del default).
      - Evita side-effect en NotificationBell: cada test press NO crea una
        row en notifications_sent.
      - No aplica filtros del usuario (symbol_filter / min_score) — esos
        aplican a signals reales, no a "verificá tu config".

    Trade-off: NO ejercita el dispatcher per-user. Aceptable porque el
    dispatcher tiene cobertura propia (tests/test_multi_tenant_b4_signal_
    routing.py).
    """
    from api.config import load_config
    from db.user_preferences import db_get_user_preferences as _db_get
    from notifier.channels.telegram import TelegramChannel

    prefs = _db_get(tenant_id) or {}
    notify_channels = prefs.get("notify_channels") or {}
    base_cfg = load_config()
    user_cfg = {**base_cfg, **notify_channels}

    token = (user_cfg.get("telegram_bot_token") or "").strip()
    chat_id = (user_cfg.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        return {"ok": False, "receipts": [], "reason": "no_telegram_configured"}

    channel = TelegramChannel(user_cfg)
    receipt = channel.send(
        "*Crypto Scanner — prueba de conexión*\n"
        "Si ves este mensaje, tu bot y chat están bien configurados. ✅"
    )
    return {
        "ok": receipt.status == "ok",
        "receipts": [{
            "channel": receipt.channel,
            "status": receipt.status,
            "error": receipt.error,
        }],
        "reason": None,
    }
```

- [ ] **Step 3.4: Run tests — verify PASS**

```bash
python -m pytest tests/test_api_user_preferences.py -v
```

Expected: 12/12 PASS (3 mask + 4 GET/PUT + 5 endpoint).

- [ ] **Step 3.5: Commit**

```bash
git add api/user_preferences.py tests/test_api_user_preferences.py
git commit -m "feat(prefs): POST /preferences/test verifies user telegram config

Bypasses notify() and dispatch_signal_to_users to avoid:
- Dedup collisions on rapid presses (defensive against future changes
  to signal event_type dedup window default, currently 0).
- Side-effect in NotificationBell (test pings would otherwise appear
  as 'TEST' rows in notifications_sent).
- Filter false-negatives (user's symbol_filter / min_score should not
  block 'verify my config' flow).

Returns {ok, receipts, reason} where reason is 'no_telegram_configured'
when token or chat_id missing.

Pre-reg: docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md §Backend"
```

---

### Task 4: Frontend — types + api wrapper

**Files:**
- Modify: `frontend/src/types.ts` (agregar 2 interfaces).
- Modify: `frontend/src/api.ts` (agregar 1 wrapper).

- [ ] **Step 4.1: Agregar tipo `TestDeliveryResponse` + `NotifyChannels` a `frontend/src/types.ts`**

Append:

```typescript
// ---- Telegram per-user config (spec 2026-05-21) ----

export interface NotifyChannels {
  telegram_bot_token?: string;   // masked from server: '<10chars>****<4chars>'
  telegram_chat_id?:   string;
}

export interface TestDeliveryReceipt {
  channel: string;
  status:  'ok' | 'failed' | 'rate_limited';
  error:   string | null;
}

export type TestDeliveryReason = 'no_telegram_configured' | null;

export interface TestDeliveryResponse {
  ok:       boolean;
  receipts: TestDeliveryReceipt[];
  reason:   TestDeliveryReason;
}
```

- [ ] **Step 4.2: Agregar wrapper `testPreferencesDelivery` a `frontend/src/api.ts`**

Encontrar las funciones existentes de preferences (alrededor línea 413-419). Después de `updateUserPreferences`, agregar:

```typescript
export async function testPreferencesDelivery(): Promise<TestDeliveryResponse> {
  return request<TestDeliveryResponse>('/preferences/test', { method: 'POST' });
}
```

Y al import del archivo, agregar `TestDeliveryResponse` a la lista importada de `types.ts`.

- [ ] **Step 4.3: Verificar typecheck**

```bash
cd frontend && npx tsc --noEmit 2>&1 | tail -5
```

Expected: cero errors.

- [ ] **Step 4.4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(prefs/frontend): types + wrapper for testPreferencesDelivery"
```

---

### Task 5: Frontend — `ConnectionsPanel.tsx` skeleton + load prefs

**Files:**
- Create: `frontend/src/components/ConnectionsPanel.tsx`.
- Create: `frontend/src/components/ConnectionsPanel.module.css`.
- Create: `frontend/src/components/ConnectionsPanel.test.tsx`.

- [ ] **Step 5.1: Crear failing test — renders form with masked token from GET**

Create `frontend/src/components/ConnectionsPanel.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConnectionsPanel from './ConnectionsPanel';
import * as api from '../api';

describe('ConnectionsPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders form with masked token from current preferences', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id:       1,
      symbol_filter:   null,
      min_score:       4,
      notify_channels: {
        telegram_bot_token: '123456789:****gH12',
        telegram_chat_id:   '987654321',
      },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);

    await waitFor(() => {
      const tokenInput = screen.getByLabelText(/bot token/i) as HTMLInputElement;
      expect(tokenInput.value).toBe('123456789:****gH12');
    });
    const chatInput = screen.getByLabelText(/chat id/i) as HTMLInputElement;
    expect(chatInput.value).toBe('987654321');
    expect(screen.getByText(/token guardado/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 5.2: Run test — verify FAIL**

```bash
cd frontend && npm test -- --run ConnectionsPanel 2>&1 | tail -10
```

Expected: FAIL with "Cannot find module './ConnectionsPanel'".

- [ ] **Step 5.3: Crear `ConnectionsPanel.module.css`**

```css
/* ConnectionsPanel — slide-out from right, mirrors ConfigPanel.module.css. */

.backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  z-index: 100;
}

.panel {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  width: min(480px, 100vw);
  background: var(--bg-elevated, #0f1410);
  z-index: 101;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border, rgba(255, 255, 255, 0.08));
}

.header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title { font-size: 18px; font-weight: 500; }

.body { flex: 1; overflow-y: auto; padding: 24px; }

.section { margin-bottom: 32px; }

.sectionTitle {
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim, rgba(255, 255, 255, 0.6));
  margin-bottom: 12px;
}

.field { margin-bottom: 16px; }

.label {
  display: block;
  font-size: 12px;
  color: var(--text-dim, rgba(255, 255, 255, 0.6));
  margin-bottom: 6px;
}

.input {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg, #0a0d0b);
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  border-radius: 6px;
  color: var(--text, white);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 13px;
}

.hint {
  font-size: 11px;
  color: var(--text-dim, rgba(255, 255, 255, 0.4));
  margin-top: 4px;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}

.btn {
  padding: 8px 16px;
  border-radius: 6px;
  background: var(--bg-input, rgba(255, 255, 255, 0.08));
  color: var(--text, white);
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  cursor: pointer;
  font-size: 13px;
}

.btnPrimary {
  background: var(--accent, #4a9eff);
  color: white;
  border-color: var(--accent, #4a9eff);
}

.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.testResult { margin-top: 12px; font-size: 12px; }
.testOk { color: var(--success, #4ade80); }
.testErr { color: var(--danger, #f87171); }

.closeBtn {
  background: none;
  border: none;
  color: var(--text-dim, rgba(255, 255, 255, 0.6));
  font-size: 24px;
  cursor: pointer;
}
```

- [ ] **Step 5.4: Crear `ConnectionsPanel.tsx` con shape mínima para passing test**

```typescript
// ============================================================
// ConnectionsPanel.tsx — per-user Telegram bot config slide-out.
// Anchored to UserMenu → 'Conexiones' item.
// Spec: docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md
// ============================================================

import React, { useEffect, useState } from 'react';
import styles from './ConnectionsPanel.module.css';
import type { NotifyChannels } from '../types';
import { getUserPreferences } from '../api';

interface ConnectionsPanelProps {
  open:    boolean;
  onClose: () => void;
}

const ConnectionsPanel: React.FC<ConnectionsPanelProps> = ({ open, onClose }) => {
  const [botToken, setBotToken]   = useState('');
  const [chatId,   setChatId]     = useState('');
  const [tokenIsMasked, setTokenIsMasked] = useState(false);
  const [loading,  setLoading]    = useState(true);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getUserPreferences()
      .then((prefs) => {
        const nc = (prefs.notify_channels ?? {}) as NotifyChannels;
        setBotToken(nc.telegram_bot_token ?? '');
        setChatId(nc.telegram_chat_id ?? '');
        setTokenIsMasked((nc.telegram_bot_token ?? '').includes('****'));
      })
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <aside className={styles.panel} role="dialog" aria-labelledby="connections-title">
        <header className={styles.header}>
          <h2 id="connections-title" className={styles.title}>Conexiones</h2>
          <button className={styles.closeBtn} onClick={onClose} aria-label="Cerrar">×</button>
        </header>
        <div className={styles.body}>
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Telegram</h3>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="bot-token">Bot Token</label>
              <input
                id="bot-token"
                className={styles.input}
                type="password"
                value={botToken}
                onChange={(e) => { setBotToken(e.target.value); setTokenIsMasked(false); }}
                placeholder="123456789:ABCdef..."
                disabled={loading}
              />
              {tokenIsMasked && (
                <div className={styles.hint}>
                  Token guardado · pegá uno nuevo para reemplazar
                </div>
              )}
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="chat-id">Chat ID</label>
              <input
                id="chat-id"
                className={styles.input}
                type="text"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                placeholder="123456789"
                disabled={loading}
              />
            </div>
          </section>
        </div>
      </aside>
    </>
  );
};

export default ConnectionsPanel;
```

- [ ] **Step 5.5: Run test — verify PASS**

```bash
cd frontend && npm test -- --run ConnectionsPanel 2>&1 | tail -10
```

Expected: 1/1 PASS.

- [ ] **Step 5.6: Commit**

```bash
git add frontend/src/components/ConnectionsPanel.tsx \
        frontend/src/components/ConnectionsPanel.module.css \
        frontend/src/components/ConnectionsPanel.test.tsx
git commit -m "feat(prefs/frontend): ConnectionsPanel skeleton — loads + renders prefs"
```

---

### Task 6: Frontend — Save handler (preserve / replace)

**Files:**
- Modify: `frontend/src/components/ConnectionsPanel.tsx` (agregar save handler + button).
- Modify: `frontend/src/components/ConnectionsPanel.test.tsx` (agregar 2 tests).

- [ ] **Step 6.1: Agregar failing tests para save**

Append to `ConnectionsPanel.test.tsx`:

```typescript
  it('save sends notify_channels body when user changed chat_id, masked token preserved server-side', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: '123456789:****gH12', telegram_chat_id: '987' },
    });
    const updateSpy = vi.spyOn(api, 'updateUserPreferences').mockResolvedValue({
      ok: true, preferences: { tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    const chatInput = await screen.findByLabelText(/chat id/i);
    await userEvent.clear(chatInput);
    await userEvent.type(chatInput, '111');
    await userEvent.click(screen.getByRole('button', { name: /guardar/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({
        notify_channels: {
          telegram_bot_token: '123456789:****gH12',  // unchanged, server-side preserve
          telegram_chat_id:   '111',
        },
      });
    });
  });

  it('save sends plain token when user pastes a new one', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: '123456789:****gH12', telegram_chat_id: '987' },
    });
    const updateSpy = vi.spyOn(api, 'updateUserPreferences').mockResolvedValue({
      ok: true, preferences: { tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    const tokenInput = await screen.findByLabelText(/bot token/i);
    await userEvent.clear(tokenInput);
    await userEvent.type(tokenInput, '999:NEW_PLAIN_TOKEN_VALUE');
    await userEvent.click(screen.getByRole('button', { name: /guardar/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({
        notify_channels: {
          telegram_bot_token: '999:NEW_PLAIN_TOKEN_VALUE',
          telegram_chat_id:   '987',
        },
      });
    });
  });
```

- [ ] **Step 6.2: Run tests — verify FAIL**

```bash
cd frontend && npm test -- --run ConnectionsPanel 2>&1 | tail -10
```

Expected: 2 nuevos FAIL — no hay save button todavía.

- [ ] **Step 6.3: Agregar save logic a `ConnectionsPanel.tsx`**

Importar `updateUserPreferences` y agregar dentro del componente:

```typescript
import { getUserPreferences, updateUserPreferences } from '../api';
// ... within component:

const [saving, setSaving] = useState(false);

const handleSave = async () => {
  setSaving(true);
  try {
    await updateUserPreferences({
      notify_channels: {
        telegram_bot_token: botToken,
        telegram_chat_id:   chatId,
      },
    });
  } finally {
    setSaving(false);
  }
};
```

Y dentro del JSX, después del field de chat_id (antes de cerrar la section), agregar:

```typescript
<div className={styles.actions}>
  <button
    className={`${styles.btn} ${styles.btnPrimary}`}
    onClick={handleSave}
    disabled={saving || loading}
  >
    {saving ? 'Guardando...' : 'Guardar'}
  </button>
</div>
```

- [ ] **Step 6.4: Run tests — verify PASS**

```bash
cd frontend && npm test -- --run ConnectionsPanel 2>&1 | tail -10
```

Expected: 3/3 PASS.

- [ ] **Step 6.5: Commit**

```bash
git add frontend/src/components/ConnectionsPanel.tsx \
        frontend/src/components/ConnectionsPanel.test.tsx
git commit -m "feat(prefs/frontend): ConnectionsPanel save handler"
```

---

### Task 7: Frontend — "Probar envío" + "Eliminar credenciales" buttons

**Files:**
- Modify: `frontend/src/components/ConnectionsPanel.tsx`.
- Modify: `frontend/src/components/ConnectionsPanel.test.tsx`.

- [ ] **Step 7.1: Agregar failing tests para probar + eliminar**

Append to `ConnectionsPanel.test.tsx`:

```typescript
  it('"Probar envío" disabled when there are unsaved changes', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: '123:****abcd', telegram_chat_id: '987' },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);

    const probarBtn = screen.getByRole('button', { name: /probar env/i });
    expect(probarBtn).not.toBeDisabled();  // initially clean

    await userEvent.type(screen.getByLabelText(/chat id/i), '5');
    expect(probarBtn).toBeDisabled();  // dirty
  });

  it('"Probar envío" shows ok on success', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: 'x:y', telegram_chat_id: 'z' },
    });
    vi.spyOn(api, 'testPreferencesDelivery').mockResolvedValue({
      ok: true,
      receipts: [{ channel: 'telegram', status: 'ok', error: null }],
      reason: null,
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /probar env/i }));

    await waitFor(() => {
      expect(screen.getByText(/enviado/i)).toBeInTheDocument();
    });
  });

  it('"Probar envío" shows error on failure', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: 'x:y', telegram_chat_id: 'z' },
    });
    vi.spyOn(api, 'testPreferencesDelivery').mockResolvedValue({
      ok: false,
      receipts: [{ channel: 'telegram', status: 'failed', error: 'HTTP 401: Unauthorized' }],
      reason: null,
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /probar env/i }));

    await waitFor(() => {
      expect(screen.getByText(/Unauthorized/)).toBeInTheDocument();
    });
  });

  it('"Probar envío" shows no_telegram_configured hint', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null,
    });
    vi.spyOn(api, 'testPreferencesDelivery').mockResolvedValue({
      ok: false, receipts: [], reason: 'no_telegram_configured',
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /probar env/i }));

    await waitFor(() => {
      expect(screen.getByText(/configur.*token.*chat/i)).toBeInTheDocument();
    });
  });

  it('"Eliminar credenciales" sends notify_channels: null', async () => {
    vi.spyOn(api, 'getUserPreferences').mockResolvedValue({
      tenant_id: 1, symbol_filter: null, min_score: 4,
      notify_channels: { telegram_bot_token: 'x:y', telegram_chat_id: 'z' },
    });
    const updateSpy = vi.spyOn(api, 'updateUserPreferences').mockResolvedValue({
      ok: true, preferences: { tenant_id: 1, symbol_filter: null, min_score: 4, notify_channels: null },
    });

    render(<ConnectionsPanel open={true} onClose={() => {}} />);
    await screen.findByLabelText(/bot token/i);
    await userEvent.click(screen.getByRole('button', { name: /eliminar credenciales/i }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ notify_channels: null });
    });
  });
```

- [ ] **Step 7.2: Run tests — verify FAIL**

```bash
cd frontend && npm test -- --run ConnectionsPanel 2>&1 | tail -10
```

Expected: 5 nuevos FAIL.

- [ ] **Step 7.3: Agregar handlers + UI a `ConnectionsPanel.tsx`**

Importar `testPreferencesDelivery`:

```typescript
import { getUserPreferences, updateUserPreferences, testPreferencesDelivery } from '../api';
import type { NotifyChannels, TestDeliveryResponse } from '../types';
```

Agregar state + handlers dentro del component:

```typescript
const [dirty, setDirty] = useState(false);
const [testResult, setTestResult] = useState<TestDeliveryResponse | null>(null);
const [testing, setTesting] = useState(false);

// Update existing handleSave to clear dirty
const handleSave = async () => {
  setSaving(true);
  try {
    await updateUserPreferences({
      notify_channels: { telegram_bot_token: botToken, telegram_chat_id: chatId },
    });
    setDirty(false);
    setTestResult(null);
  } finally {
    setSaving(false);
  }
};

const handleTest = async () => {
  setTesting(true);
  setTestResult(null);
  try {
    const res = await testPreferencesDelivery();
    setTestResult(res);
  } finally {
    setTesting(false);
  }
};

const handleDelete = async () => {
  setSaving(true);
  try {
    await updateUserPreferences({ notify_channels: null });
    setBotToken('');
    setChatId('');
    setTokenIsMasked(false);
    setTestResult(null);
  } finally {
    setSaving(false);
  }
};
```

Y los input onChange deben marcar dirty:

```typescript
onChange={(e) => { setBotToken(e.target.value); setTokenIsMasked(false); setDirty(true); }}
// ...
onChange={(e) => { setChatId(e.target.value); setDirty(true); }}
```

Reemplazar el bloque `.actions` con:

```typescript
<div className={styles.actions}>
  <button
    className={`${styles.btn} ${styles.btnPrimary}`}
    onClick={handleSave}
    disabled={saving || loading || !dirty}
  >
    {saving ? 'Guardando...' : 'Guardar'}
  </button>
  <button
    className={styles.btn}
    onClick={handleTest}
    disabled={testing || saving || dirty}
  >
    {testing ? 'Enviando...' : 'Probar envío'}
  </button>
  <button
    className={styles.btn}
    onClick={handleDelete}
    disabled={saving}
  >
    Eliminar credenciales
  </button>
</div>
{testResult && (
  <div className={styles.testResult}>
    {testResult.ok && <span className={styles.testOk}>✓ Mensaje enviado a tu Telegram.</span>}
    {!testResult.ok && testResult.reason === 'no_telegram_configured' && (
      <span className={styles.testErr}>Configurá tu token y chat ID primero, después Guardar y volvé a probar.</span>
    )}
    {!testResult.ok && testResult.reason === null && testResult.receipts[0] && (
      <span className={styles.testErr}>✗ {testResult.receipts[0].error}</span>
    )}
  </div>
)}
```

Nota: el "Guardar" disabled-when-not-dirty + "Probar" disabled-when-dirty resuelve juntos el "type without save" → probar disabled.

- [ ] **Step 7.4: Run tests — verify PASS**

```bash
cd frontend && npm test -- --run ConnectionsPanel 2>&1 | tail -10
```

Expected: 8/8 PASS.

- [ ] **Step 7.5: Commit**

```bash
git add frontend/src/components/ConnectionsPanel.tsx \
        frontend/src/components/ConnectionsPanel.test.tsx
git commit -m "feat(prefs/frontend): ConnectionsPanel test + delete handlers"
```

---

### Task 8: Frontend — UserMenu wire-up (onClick + conditional badge)

**Files:**
- Modify: `frontend/src/components/UserMenu.tsx`.
- Create: `frontend/src/components/UserMenu.test.tsx`.

- [ ] **Step 8.1: Crear failing tests para UserMenu badge**

Create `frontend/src/components/UserMenu.test.tsx`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import UserMenu from './UserMenu';
import type { AuthUser } from '../auth/api';

const _fakeUser: AuthUser = { id: 1, email: 'a@example.com', role: 'admin' };

describe('UserMenu', () => {
  it('shows badge "1" on Conexiones when telegram is unconfigured', () => {
    render(
      <UserMenu
        open={true}
        user={_fakeUser}
        onClose={() => {}}
        onLogout={() => {}}
        onConnectionsOpen={() => {}}
        telegramConfigured={false}
      />
    );
    const conexionesBtn = screen.getByText('Conexiones').closest('button')!;
    expect(conexionesBtn.textContent).toContain('1');
  });

  it('hides badge on Conexiones when telegram is configured', () => {
    render(
      <UserMenu
        open={true}
        user={_fakeUser}
        onClose={() => {}}
        onLogout={() => {}}
        onConnectionsOpen={() => {}}
        telegramConfigured={true}
      />
    );
    const conexionesBtn = screen.getByText('Conexiones').closest('button')!;
    expect(conexionesBtn.textContent).not.toContain('1');
  });

  it('calls onConnectionsOpen when Conexiones is clicked', async () => {
    const onOpen = vi.fn();
    const { default: userEvent } = await import('@testing-library/user-event');
    render(
      <UserMenu
        open={true}
        user={_fakeUser}
        onClose={() => {}}
        onLogout={() => {}}
        onConnectionsOpen={onOpen}
        telegramConfigured={false}
      />
    );
    await userEvent.default.click(screen.getByText('Conexiones'));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 8.2: Run tests — verify FAIL**

```bash
cd frontend && npm test -- --run UserMenu 2>&1 | tail -10
```

Expected: 3 FAIL (props no aceptados, badge hardcoded).

- [ ] **Step 8.3: Modificar `UserMenu.tsx` para aceptar props + wire conditional**

Encontrar:

```typescript
interface UserMenuProps {
  open:    boolean;
  user:    AuthUser;
  onClose: () => void;
  onLogout: () => void;
}
```

Reemplazar con:

```typescript
interface UserMenuProps {
  open:    boolean;
  user:    AuthUser;
  onClose: () => void;
  onLogout: () => void;
  onConnectionsOpen?: () => void;
  telegramConfigured?: boolean;
}

const UserMenu: React.FC<UserMenuProps> = ({
  open, user, onClose, onLogout,
  onConnectionsOpen, telegramConfigured = false,
}) => {
```

Encontrar el array `items` (línea ~28) y reemplazar la entry de Conexiones:

```typescript
  const items: MenuItem[] = [
    { icon: '◧', label: 'Mi cuenta', hint: 'email · contraseña · 2FA' },
    { icon: '✦', label: 'Capital y riesgo', hint: 'gestión de balance' },
    {
      icon: '◐',
      label: 'Conexiones',
      hint: 'Telegram · Webhook',
      badge: telegramConfigured ? undefined : '1',
      onClick: () => { onConnectionsOpen?.(); onClose(); },
    },
    { icon: '⌨', label: 'Atajos de teclado', kbd: '?' },
    { icon: '❑', label: 'Documentación' },
  ];
```

- [ ] **Step 8.4: Run tests — verify PASS**

```bash
cd frontend && npm test -- --run UserMenu 2>&1 | tail -10
```

Expected: 3/3 PASS.

- [ ] **Step 8.5: Commit**

```bash
git add frontend/src/components/UserMenu.tsx \
        frontend/src/components/UserMenu.test.tsx
git commit -m "feat(prefs/frontend): UserMenu wire onClick + conditional badge

Item 'Conexiones' now opens ConnectionsPanel via onConnectionsOpen prop.
Badge '1' shows when notify_channels.telegram_bot_token is unconfigured;
hides once user has saved credentials."
```

---

### Task 9: Frontend — App.tsx wire-up (OverlayKind + render)

**Files:**
- Modify: `frontend/src/App.tsx`.

- [ ] **Step 9.1: Extender `OverlayKind` en App.tsx**

Encontrar línea 92:

```typescript
type OverlayKind = 'notifs' | 'settings' | 'user' | null;
```

Reemplazar con:

```typescript
type OverlayKind = 'notifs' | 'settings' | 'user' | 'connections' | null;
```

- [ ] **Step 9.2: Importar `ConnectionsPanel` + agregar state para telegramConfigured**

Después del import de `ConfigPanel` (línea ~67):

```typescript
import ConnectionsPanel from './components/ConnectionsPanel';
```

Dentro del componente, agregar state que se hidrata desde GET /preferences:

```typescript
const [telegramConfigured, setTelegramConfigured] = useState(false);

useEffect(() => {
  // Initial load + refresh after panel closes
  if (openOverlay !== 'connections') {
    getUserPreferences().then((p) => {
      const tok = (p.notify_channels as { telegram_bot_token?: string } | null)?.telegram_bot_token;
      setTelegramConfigured(Boolean(tok));
    }).catch(() => {});  // silent: badge stays in default state on error
  }
}, [openOverlay]);
```

(Asegurar que `getUserPreferences` está importado desde `./api`.)

- [ ] **Step 9.3: Pasar props al `<UserMenu>` (encontrar la invocation alrededor de línea 724)**

Encontrar:

```typescript
<UserMenu
  open={openOverlay === 'user'}
  // ...
/>
```

Agregar props:

```typescript
<UserMenu
  open={openOverlay === 'user'}
  user={...}
  onClose={() => setOpenOverlay(null)}
  onLogout={...}
  onConnectionsOpen={() => setOpenOverlay('connections')}
  telegramConfigured={telegramConfigured}
/>
```

- [ ] **Step 9.4: Renderizar `<ConnectionsPanel>` cerca del `<ConfigPanel>` existente**

Después del `<ConfigPanel>` (alrededor de línea 719-722):

```typescript
<ConnectionsPanel
  open={openOverlay === 'connections'}
  onClose={() => setOpenOverlay(null)}
/>
```

- [ ] **Step 9.5: Verificar typecheck + suite frontend completa**

```bash
cd frontend && npx tsc --noEmit && npm test -- --run 2>&1 | tail -15
```

Expected: cero tsc errors, toda la suite vitest passes.

- [ ] **Step 9.6: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(prefs/frontend): wire ConnectionsPanel into App + hydrate badge state"
```

---

### Task 10: Backend regression check + frontend build verify

**Files:** none modified.

- [ ] **Step 10.1: Backend regression — toda la suite agent + prefs**

```bash
python -m pytest tests/test_api_user_preferences.py tests/test_provider_registry.py tests/test_multi_tenant_b4_signal_routing.py tests/test_multi_tenant_b7_idor.py -k "not trio" 2>&1 | tail -5
```

Expected: all pass. Si rojo, abort + fix antes de seguir.

- [ ] **Step 10.2: Frontend build verify**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: build exitoso sin errors. Bundle size sigue razonable (<5% growth).

---

### Task 11: Playwright e2e (Claude vía Playwright MCP)

**Owner:** Claude using `mcp__plugin_playwright_playwright__*` tools. Samuel watches.

- [ ] **Step 11.1: Restart dev server / verificar prod accesible**

Usar el browser de Playwright que ya está logueado en `https://trading.sdar.dev/` (sesión activa). Si el feature está merged a main + deploy completado, el panel ya está disponible en prod.

Alternativa local (si querés testear antes del deploy): start frontend dev server y backend local, login. **No recomendado** para esta iteración — preferible deploy + test en prod.

- [ ] **Step 11.2: E2E happy path con creds fake (DB write check)**

Browser_evaluate sequence:

```js
// 1. Click avatar dropdown
async () => {
  document.querySelector('button[aria-label*="operador" i], button[class*="userBtn" i]')?.click();
  // wait for menu to render
  await new Promise(r => setTimeout(r, 200));
  return { menu_visible: !!document.querySelector('[role="menu"]') };
}
```

Después:

```js
// 2. Click "Conexiones"
async () => {
  const btn = [...document.querySelectorAll('button')].find(b => b.textContent?.includes('Conexiones'));
  btn?.click();
  await new Promise(r => setTimeout(r, 200));
  return { panel_visible: !!document.querySelector('[role="dialog"][aria-labelledby="connections-title"]') };
}
```

Después:

```js
// 3. Llenar creds FAKE + save (los reales son Task 12)
async () => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('csrf_token='))?.split('=')[1];
  const tokenInput = document.querySelector('#bot-token');
  const chatInput  = document.querySelector('#chat-id');
  tokenInput.value = 'FAKE_TOKEN_FOR_E2E:abcdefghi';
  tokenInput.dispatchEvent(new Event('input', { bubbles: true }));
  chatInput.value = 'FAKE_CHAT_ID_E2E';
  chatInput.dispatchEvent(new Event('input', { bubbles: true }));
  await new Promise(r => setTimeout(r, 100));
  document.querySelector('button.btnPrimary')?.click();
  await new Promise(r => setTimeout(r, 500));
  // Verify saved by re-fetching prefs
  const r = await fetch('/api/preferences', { credentials: 'include' });
  return await r.json();
}
```

Expected: response shows masked token con `****` y chat_id `FAKE_CHAT_ID_E2E`.

- [ ] **Step 11.3: Click "Eliminar credenciales"**

```js
async () => {
  const btn = [...document.querySelectorAll('button')].find(b => b.textContent?.includes('Eliminar credenciales'));
  btn?.click();
  await new Promise(r => setTimeout(r, 500));
  const r = await fetch('/api/preferences', { credentials: 'include' });
  return await r.json();
}
```

Expected: `notify_channels: null`.

- [ ] **Step 11.4: Cleanup — limpiar la fila de prefs**

```js
async () => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('csrf_token='))?.split('=')[1];
  await fetch('/api/preferences', {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify({ notify_channels: null }),
  });
}
```

E2E completo. **No commit necesario** (no hay code change).

---

### Task 12: Manual gate — Samuel hace el BotFather flow real

**Owner:** Samuel.

- [ ] **Step 12.1: Crear bot en BotFather**

En Telegram, abrir [@BotFather](https://t.me/BotFather):
1. `/newbot`
2. Nombre amigable (ej "Crypto Scanner - Samuel").
3. Username terminado en `bot`.
4. Guardar el token devuelto (formato `123456789:ABC...`).

- [ ] **Step 12.2: Iniciar conversación con el bot**

Buscar el username en Telegram, mandar `/start`.

- [ ] **Step 12.3: Conseguir chat_id**

Browser: `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `result[0].message.chat.id` (entero positivo).

- [ ] **Step 12.4: Configurar via dashboard**

1. Abrir https://trading.sdar.dev → avatar ▾ → Conexiones.
2. Pegar token + chat_id en los inputs.
3. Click Guardar.
4. Click Probar envío.
5. Confirmar que llega mensaje "*Crypto Scanner — prueba de conexión*..." en Telegram.

- [ ] **Step 12.5: Sub-gate — NotificationBell no se llena con TEST entries**

Click en el bell del header. Expected: 0 entries nuevas a pesar del "Probar envío" reciente. Confirma que el bypass de `notify()` funciona.

- [ ] **Step 12.6: Sub-gate — rapid-fire 5 presses**

Click "Probar envío" 5 veces seguidas. Todos deben mostrar ✓ enviado. Confirma que no hay dedup collision.

- [ ] **Step 12.7: Acceptance gate principal — recibir signal real**

Esperar (o forzar con POST /api/scan) un signal real con score ≥ 4. Verificar:
- journalctl muestra `dispatch: user_id=1 symbol=X score=Y receipts=1`.
- Telegram recibe mensaje con shape de signal real (símbolo + dirección + SL/TP).

- [ ] **Step 12.8: Decisión final + cierre**

Si todos los gates verdes:
- Mergear el PR.
- Actualizar memoria con [[telegram-per-user-config-done]] en MEMORY.md.
- Cerrar tasks #21 + #22.

Si algún gate rojo: abort, investigar, fix, retry.

---

## Acceptance gates (resumen)

Phase A v2 completo cuando los **3 son verdaderos**:

| Gate | Verificación | Owner |
|---|---|---|
| Tests | Backend + frontend suites verdes (Task 10) | Claude |
| E2E DB write | Playwright verifica save + masked GET + delete (Task 11) | Claude |
| Manual real-world | Samuel recibe signal real en su bot post-config (Task 12.7) | Samuel |

---

## Rollback

Si el feature rompe algo grave en prod:

```bash
# Local revert
git checkout main
git revert <merge_sha>
git push origin main
```

CI redeploy con código pre-feature. Schema sin cambios → no migration rollback necesaria. Si hay rows con notify_channels populadas, ignored gracefully por el código pre-feature (no rompe, simplemente no rutea).
