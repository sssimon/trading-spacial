# Telegram Multi-Tenant Phase A — Backend Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Plan has no code changes — solo operational steps con checkpoints de verificación. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Samuel recibe señales del scanner en su chat personal de Telegram, vía la ruta per-user (`dispatch_signal_to_users`), no por broadcast global. Funciona hoy con `tenant_id=1`; deja la arquitectura lista para usuarios futuros (cada uno con su propio chat_id en `user_preferences`).

**Architecture:** Pure config + DB row update — **sin code changes**. Token globalmente vía `_env_map` (`api/config.py:105-112`), chat_id per-tenant vía `PUT /api/preferences` que persiste a `user_preferences.notify_channels_json`. Cada signal con `score ≥ 4` (default) dispara `push_telegram_direct` → `dispatch_signal_to_users` → `TelegramChannel.send`.

**Tech Stack:** Bash/SSH al server EC2 (`aws-server`), JS fetch desde browser Playwright (cookie JWT activa de Samuel), curl, journalctl.

**Spec:** [`docs/superpowers/specs/es/2026-05-21-telegram-multitenant-phase-a-pre-reg.md`](../specs/es/2026-05-21-telegram-multitenant-phase-a-pre-reg.md) (commit `f0dc654`).

---

## File / state changes summary

- **Files created:** ninguno.
- **Files modified in repo:** ninguno (Phase A es zero-code).
- **Server state modified:**
  - `/var/www/trading/.env`: una línea nueva `TRADING_TELEGRAM_BOT_TOKEN=<token>`. El archivo no está en repo (gitignored + excluido del rsync deploy).
  - Proceso `trading-spacial` (systemd): restart para releer `.env`.
- **DB row modified:** `signals.db` tabla `user_preferences`, UPSERT row con `tenant_id=1`. Columnas tocadas: `notify_channels_json` ← `{"telegram_chat_id": "<chat_id>"}`, `min_score` ← `4` (explícito).

---

## Pre-conditions (verificar antes de empezar)

- [ ] **PC1: Branch en main + sync con remote**

```bash
git checkout main && git pull --ff-only origin main
```

Expected: branch `main`, fast-forward sin conflictos. (Solo housekeeping — Phase A no toca repo, pero queremos partir de main limpio.)

- [ ] **PC2: Service en prod healthy**

```bash
ssh aws-server "systemctl is-active trading-spacial && curl -fsS http://localhost:8100/health"
```

Expected: `active` + `{"healthy":true,...}`. Si no, NO seguir — investigar antes.

- [ ] **PC3: Sesión Playwright activa con cookie JWT de Samuel**

Verificar con `browser_evaluate`:

```js
async () => {
  const r = await fetch('/api/auth/me', { credentials: 'include' });
  return { status: r.status, body: await r.json() };
}
```

Expected: 200 + `body.email === 'sssamuelll@gmail.com'`. Si 401: Samuel se loguea en el browser antes de seguir.

---

### Task 1: Pre-check de `user_preferences` para `tenant_id=1` (Owner: Claude)

**Files / state read:**
- DB: `user_preferences` table, row con `tenant_id=1`.

**Why this matters:** §5 del spec exige `min_score: 4` explícito en el PUT para evitar F8 (min_score residual de tests previos bloqueando signals). Este task captura el estado actual para saber si hay row existente.

- [ ] **Step 1.1: GET preferences actual via Playwright**

```js
async () => {
  const r = await fetch('/api/preferences', { credentials: 'include' });
  return { status: r.status, body: await r.json() };
}
```

Expected: 200 + body con shape `{tenant_id: 1, symbol_filter: ..., min_score: ..., notify_channels: ...}`.

- [ ] **Step 1.2: Capturar `min_score` y `notify_channels` actuales para registro**

Anotar en este chat los valores observados:
- `current.min_score`: ___ (esperado: probably `4`, pero podría ser 8 o algo residual).
- `current.notify_channels`: ___ (esperado: `null`, no había prefs).

Si `min_score !== 4` o `notify_channels` ya tiene `telegram_chat_id`, **flag para Samuel antes del PUT en Task 4**.

---

### Task 2: Bot creation + chat_id discovery (Owner: Samuel)

**Files / state created:**
- Telegram side: un nuevo bot bajo la cuenta de @BotFather.
- Información: token + chat_id de Samuel para usar en Task 3 y 4.

- [ ] **Step 2.1: Crear bot vía @BotFather**

En Telegram, abrir chat con [@BotFather](https://t.me/BotFather):

1. `/newbot`
2. Responder con nombre amigable (ej: "Crypto Scanner – Samuel").
3. Responder con username terminado en `bot` (ej: `samuel_crypto_scanner_bot`).
4. BotFather responde con token tipo `123456789:ABCdefGHIjkl-MNOpqrs_TUVwxyz`.

Guardar token en buffer local (no pegarlo en este chat).

- [ ] **Step 2.2: Iniciar conversación con el bot**

En Telegram, buscar el username del bot (`@samuel_crypto_scanner_bot` o el que hayas elegido) y mandar `/start`. Telegram requiere que el usuario inicie la conversación primero — el bot NO puede DM-ear a alguien que no le mandó algo antes.

- [ ] **Step 2.3: Obtener chat_id vía getUpdates**

En el browser, abrir (reemplazando `<TOKEN>`):

```
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Expected: JSON con shape:

```json
{
  "ok": true,
  "result": [
    {
      "update_id": ...,
      "message": {
        "message_id": ...,
        "from": {...},
        "chat": {
          "id": 123456789,
          ...
        },
        "text": "/start",
        ...
      }
    }
  ]
}
```

Copiar `result[0].message.chat.id` (entero positivo para chats 1-a-1, ej `123456789`). **Importante:** se va a persistir como **string** en `notify_channels_json` — el `TelegramChannel.send` (`notifier/channels/telegram.py:26`) hace `.strip()` que crashea sobre int.

Guardar chat_id en buffer local (no pegarlo en este chat).

---

### Task 3: Token al `.env` del server + restart (Owner: Samuel)

**Files / state modified:**
- `/var/www/trading/.env`: append `TRADING_TELEGRAM_BOT_TOKEN=<token>`.
- Proceso `trading-spacial`: restart para releer.

- [ ] **Step 3.1: Append robusto del token al `.env`**

Desde tu shell local (Samuel reemplaza `<token>` por el real, NO copiar acá):

```bash
ssh aws-server "sudo sh -c 'printf \"\\nTRADING_TELEGRAM_BOT_TOKEN=%s\\n\" \"<token>\" >> /var/www/trading/.env'"
```

El `\n` líder garantiza separación aunque el último byte del `.env` previo no fuera newline (mitiga F6 del spec).

- [ ] **Step 3.2: Verificar que la línea existe (key-only check, sin valor)**

```bash
ssh aws-server "sudo grep -c '^TRADING_TELEGRAM_BOT_TOKEN=' /var/www/trading/.env"
```

Expected: `1`. Si `0` o `≥2`, F6 — investigar antes de seguir.

- [ ] **Step 3.3: Restart del service**

```bash
ssh aws-server "sudo systemctl restart trading-spacial"
```

Expected: comando vuelve sin error (exit 0). 2-3s downtime nominal.

- [ ] **Step 3.4: Wait + verificar service healthy post-restart**

```bash
sleep 15 && ssh aws-server "systemctl is-active trading-spacial && curl -fsS http://localhost:8100/health"
```

Expected: `active` + `{"healthy":true,...}`. Cold start con DB warmup puede tardar ~10s, por eso el `sleep 15`. Si `is-active` ≠ `active` tras los 15s, ir a F6 del spec (logs + retry).

---

### Task 4: PUT chat_id a preferences via Playwright (Owner: Claude)

**Files / state modified:**
- DB: `user_preferences` UPSERT row para `tenant_id=1`. `notify_channels_json` ← `{"telegram_chat_id": "<chat_id>"}`. `min_score` ← `4`.

- [ ] **Step 4.1: Capturar CSRF token desde document.cookie**

```js
async () => {
  const csrf = document.cookie
    .split('; ')
    .find(c => c.startsWith('csrf_token='))
    ?.split('=')[1];
  return { csrf_present: Boolean(csrf), csrf_len: csrf?.length };
}
```

Expected: `csrf_present: true`, `csrf_len: ~43` (Fernet token length). Si false, refrescar dashboard en Playwright.

- [ ] **Step 4.2: PUT con min_score: 4 explícito y notify_channels seteado**

Samuel **pasa el chat_id por chat** acá (es seguro: no es secreto — es un identificador de su cuenta de Telegram que igual aparece en getUpdates si alguien tiene el token).

Reemplazar `<CHAT_ID>` por el valor real (string, e.g. `"123456789"`):

```js
async () => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('csrf_token='))?.split('=')[1];
  const r = await fetch('/api/preferences', {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify({
      min_score: 4,
      notify_channels: { telegram_chat_id: '<CHAT_ID>' }
    })
  });
  return { status: r.status, body: await r.json() };
}
```

Expected:
- `status: 200`
- `body.ok: true`
- `body.preferences.tenant_id: 1`
- `body.preferences.min_score: 4`
- `body.preferences.notify_channels.telegram_chat_id: "<CHAT_ID>"` (string)

- [ ] **Step 4.3: Re-GET para confirmar persistencia (defensa contra Pydantic-passthrough sin DB commit)**

```js
async () => {
  const r = await fetch('/api/preferences', { credentials: 'include' });
  return await r.json();
}
```

Expected: misma shape que en Step 4.2 — `notify_channels.telegram_chat_id: "<CHAT_ID>"` + `min_score: 4`. Si difiere, hay bug en el UPSERT — abort + investigar.

---

### Task 5: Force scan + verify dispatch (Owner: Claude + Samuel)

**Why force vs wait:** Force evita 5 min de espera por el próximo ciclo. Pero hay caveat: si el último signal del símbolo fue hace <30 min, F7 (dedup) lo bloquea. Solución: probar con varios símbolos hasta encontrar uno fresh.

- [ ] **Step 5.1: Trigger force scan vía Playwright**

```js
async () => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('csrf_token='))?.split('=')[1];
  const t0 = performance.now();
  const r = await fetch('/api/scan', {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRF-Token': csrf }
  });
  const dt = Math.round(performance.now() - t0);
  const body = await r.json();
  return {
    status: r.status,
    elapsed_ms: dt,
    scanned: body.scanned,
    high_score_signals: body.results?.filter(x => (x.score || 0) >= 4)
      .map(x => ({ symbol: x.symbol, score: x.score, señal: x.señal }))
  };
}
```

Expected: `status: 200`, `scanned: 10`, y `high_score_signals` con al menos 1 entry con `señal: true`. Si vacío, esperar 5 min al próximo ciclo natural (paso 5.1.b abajo).

- [ ] **Step 5.1.b (fallback): Si force scan no produjo signals, esperar al próximo ciclo natural**

Setup background monitor en `journalctl` esperando el próximo `dispatch:` line:

```bash
ssh aws-server "sudo journalctl -u trading-spacial -f --since 'now'" 2>&1 | grep --line-buffered -E 'dispatch: user_id=1|senal duplicada|senal'
```

Dejar correr 5-10 min. Salir con Ctrl+C cuando aparezca un evento.

- [ ] **Step 5.2: Verificar dispatch en journalctl (acceptance gate 1)**

```bash
ssh aws-server "sudo journalctl -u trading-spacial --since '5 minutes ago' --no-pager | grep -E 'dispatch: user_id=1.*receipts=[1-9]'"
```

Expected: al menos 1 línea con shape:
```
dispatch: user_id=1 symbol=<X> score=<Y> receipts=1
```

El pattern `receipts=[1-9]` excluye:
- Skip lines (que también empiezan con `dispatch: user_id=1` pero terminan en `skipped — symbol=...`).
- `receipts=0` (indicaría canal mal configurado — F3 del spec).

Si solo aparecen skip lines: F8 (min_score residual no aplicó) o F7 (dedup activo). Diagnóstico: re-leer line completa.

Si solo aparecen `receipts=0`: F3 (token no llegó al proceso post-restart) o F4 (chat_id mal en prefs). Ir al journalctl del TelegramChannel:

```bash
ssh aws-server "sudo journalctl -u trading-spacial --since '5 minutes ago' --no-pager | grep -iE 'telegram|chat not found|unauthorized'"
```

- [ ] **Step 5.3: Samuel confirma recepción en Telegram (acceptance gate 2)**

Samuel chequea su chat con el bot. Expected: al menos 1 mensaje con shape de signal (símbolo + score + precio + dirección LONG/SHORT + niveles SL/TP). Si no llegó:
- Si journalctl mostró `receipts=1` pero no llegó a Telegram → race condition rara, esperar 30s.
- Si journalctl mostró 401/400 → F4/F5 del spec.

- [ ] **Step 5.4: Verificar persistencia de la row en DB (acceptance gate 3)**

```bash
ssh aws-server "cd /var/www/trading && .venv/bin/python -c '
import sqlite3, json
con = sqlite3.connect(\"signals.db\")
con.row_factory = sqlite3.Row
row = con.execute(\"SELECT tenant_id, min_score, notify_channels_json, updated_at FROM user_preferences WHERE tenant_id=1\").fetchone()
if row:
    d = dict(row)
    d[\"notify_channels_parsed\"] = json.loads(d[\"notify_channels_json\"])
    print(d)
else:
    print(\"NO ROW — PUT did not persist\")
'"
```

Expected:
```python
{
  'tenant_id': 1,
  'min_score': 4,
  'notify_channels_json': '{"telegram_chat_id": "<CHAT_ID>"}',
  'notify_channels_parsed': {'telegram_chat_id': '<CHAT_ID>'},
  'updated_at': '<ISO ts cercano al PUT>',
}
```

Si falta o difiere: bug grave en UPSERT — abort + investigar en `db/user_preferences.py:db_upsert_user_preferences`.

---

### Task 6: Cierre — task list + memory update (Owner: Claude)

- [ ] **Step 6.1: Marcar task #19 (Phase A execución) como completada**

Vía `TaskUpdate` tool.

- [ ] **Step 6.2: Guardar memoria de continuidad para sesiones futuras**

Crear `memory/project_telegram_phase_a_done.md` con resumen de qué quedó:

```markdown
---
name: telegram-phase-a-done
description: Phase A del epic Telegram multi-tenant shippeado YYYY-MM-DD — Samuel recibe señales vía dispatch_per_user
metadata:
  type: project
---

Phase A del epic Telegram multi-tenant completado YYYY-MM-DD-HH:MM UTC.

Estado:
- `TRADING_TELEGRAM_BOT_TOKEN` en `/var/www/trading/.env` (no en repo, no en transcript).
- `user_preferences` row para `tenant_id=1` (Simon): `min_score=4`, `notify_channels.telegram_chat_id=<set>`.
- Dispatch verificado end-to-end: journalctl receipt=1 + Telegram recibido + DB row persistido.

Out-of-scope que sigue pendiente (sub-proyectos B y C del mismo epic):
- Phase B: frontend UI self-service para gestionar prefs (sin spec todavía).
- Phase C: bot webhook + deep-link invites (deferido hasta Epic B #253 onboarding).

Spec/plan refs:
- Spec: [[telegram-multitenant-phase-a-pre-reg]]
- Plan: docs/superpowers/plans/2026-05-21-telegram-multitenant-phase-a.md
```

Update `MEMORY.md` con la línea nueva.

- [ ] **Step 6.3: Commit del plan a main**

El plan en sí ya está committed cuando se escribió. No hay code change.

---

## Acceptance gates (re-stated)

Phase A se considera completado cuando los **3 son verdaderos simultáneamente**:

| Gate | Comando / verificación | Pasa cuando |
|---|---|---|
| Backend dispatch | `journalctl ... \| grep -E 'dispatch: user_id=1.*receipts=[1-9]'` | Al menos 1 línea para signal real (no /webhook/test) |
| Telegram recepción | Samuel chequea en su chat del bot | Al menos 1 mensaje con shape de signal |
| DB persistencia | Python query a `user_preferences` | Row con `tenant_id=1`, `min_score=4`, `notify_channels.telegram_chat_id` seteado |

---

## Rollback (si necesario)

Reversible 100%:

```bash
# 1. Borrar token del .env
ssh aws-server "sudo sed -i '/^TRADING_TELEGRAM_BOT_TOKEN=/d' /var/www/trading/.env && sudo systemctl restart trading-spacial"

# 2. Borrar chat_id del prefs (set notify_channels a null)
# Vía browser_evaluate:
async () => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('csrf_token='))?.split('=')[1];
  return await fetch('/api/preferences', {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify({ notify_channels: null })
  }).then(r => r.json());
}
```

Estado pre-Phase-A queda restaurado: sin token global, sin row de prefs (efectivamente).

---

## Failure mode quick reference (full table en spec §7)

| Símptoma | Probable causa | Task affected | Fix |
|---|---|---|---|
| `receipts=0` en journalctl | Token no leyó del .env | 3.4 → 5.2 | Re-restart |
| `chat not found` 400 | chat_id mal | 4.2 → 5.2 | Re-PUT |
| `Unauthorized` 401 | Token mal copiado | 3.1 → 5.2 | Re-issue en BotFather |
| Service no levanta | .env syntax | 3.3 → 3.4 | Editar manual, retry |
| Solo skip lines en journalctl | min_score residual O dedup | 5.2 | F7/F8 del spec |
| Service no healthy en 15s | Cold start lento O algo serio | 3.4 | Logs + retry |

---

## Out of scope (recordatorio)

- Code changes — NONE en Phase A (delegado a Phase B y C).
- Frontend UI — Phase B.
- Bot webhook + auto-discovery — Phase C.
- Multi-user testing — single user (Simon) hasta Epic B.
