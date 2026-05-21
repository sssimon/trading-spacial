# Telegram per-user config — pre-reg

**Fecha:** 2026-05-21
**Operator:** Samuel (en nombre de Simon)
**Estado:** pre-reg pre-implementación
**Estimación:** ~½ – 1 día
**Supersedes:** [`2026-05-21-telegram-multitenant-phase-a-pre-reg.md`](2026-05-21-telegram-multitenant-phase-a-pre-reg.md) (modelo admin-side abandonado)

## Contexto

Sesión 2026-05-21: investigación con `systematic-debugging` descartó la hipótesis "deploy borró credenciales" — Telegram nunca había sido configurado en este deploy. Primera iteración de diseño asumía el modelo standard "un bot global compartido por todos los usuarios, admin lo configura via SSH + .env" (ver spec superseded). Durante la ejecución, el operator redirigió:

> "yo quiero que sea un canal al que varios usuarios puedan subscribirse" → (después del intercambio sobre el modelo de canal) → "en openclaw el bot es personal de cada usuario pero la configuracion la hace cada usuario tambien pero es muy facil de integrar"

El modelo correcto es **per-user end-to-end**: cada operador crea su propio bot en BotFather y administra su propio token + chat_id desde el dashboard. Zero admin work, zero SSH, zero global infra.

La plomería del backend **ya soporta esto sin cambios**: `notifier/channels/telegram.py:24-26` lee `telegram_bot_token` y `telegram_chat_id` del `cfg`; `notifier/dispatch_per_user.py:107` hace `user_cfg = {**base_cfg, **notify_channels}` que aplica overlay per-user sobre el base. Si el usuario guarda **ambos** valores en su `notify_channels`, el TelegramChannel los recibe y postea a SU bot.

Este spec cubre lo que falta:
1. Frontend self-service para que el usuario gestione `telegram_bot_token` + `telegram_chat_id`.
2. Un endpoint `POST /api/preferences/test` que dispare un signal sintético contra el path per-user del dispatcher, para que el usuario verifique su setup sin esperar al próximo signal real.

## Goal

Después de este spec implementado:

- Cada usuario logueado puede abrir su panel "Mi configuración" desde el dropdown del avatar (top right del header).
- En el panel ve una sección "Notificaciones por Telegram" con dos campos (`bot_token` masked + `chat_id` text), un botón "Guardar" y un botón "Probar envío".
- Después de guardar y testear con éxito, el usuario recibe automáticamente cada signal con `score ≥ min_score` (default 4) en SU bot.
- Cero pasos manuales del lado del admin/servidor.

## Non-goals (Out of scope)

- **Admin-side global bot token**: descartado por el operator. No habrá `TRADING_TELEGRAM_BOT_TOKEN` en .env, ni columna global en `config.json`, ni endpoint admin para setear el token de otros usuarios.
- **Bot webhook + deep-link invites** (`POST /telegram/webhook` que captura `/start` automático): queda para una Phase B futura. Hoy el usuario discover-ea su chat_id manualmente via `getUpdates`.
- **Per-user min_score / symbol_filter UI**: el schema lo soporta pero la UI queda fuera de este spec. Se agregan en una iteración futura cuando se justifique.
- **Encryption-at-rest de bot tokens**: ver §7 (security note). Decisión actual: plain-text en JSON column es aceptable para el modelo de threat actual (single-server EC2, único operador con sudo). Documentado, no implementado.
- **Multi-user testing real**: hoy hay un solo usuario (`tenant_id=1`, sssamuelll@gmail.com). El path per-user se ejercita con él. Multi-user real espera a Epic B (#253) onboarding.

## Arquitectura

### Lo que ya existe (sin cambios)

- `notifier/channels/telegram.py:TelegramChannel.__init__` lee `cfg["telegram_bot_token"]` + `cfg["telegram_chat_id"]`. Per-call retries con backoff exponencial.
- `notifier/dispatch_per_user.py:dispatch_signal_to_users` itera usuarios activos, hace `user_cfg = {**base_cfg, **notify_channels}` y llama `notify(event, user_cfg, tenant_id=user["id"])`. Tenant-prefixed dedup keys (`notifier/__init__.py:88-91`).
- `api/user_preferences.py:get_preferences` (GET) + `put_preferences` (PUT, JWT-gated). `PreferencesPutBody.notify_channels: Optional[dict[str, Any]]` — acepta cualquier shape, no valida keys.
- `db/user_preferences.py:db_upsert_user_preferences` persiste `notify_channels_json` como TEXT JSON.
- `frontend/src/api.ts`: ya tiene `getUserPreferences()` + `updateUserPreferences()` wrappers (líneas ~398-419).
- `frontend/src/types.ts`: `UserPreferences.notify_channels: Record<string, unknown> | null` — type-safe para cualquier shape.

### Lo que cambia

#### Backend (nuevo, ~1 file)

**`api/user_preferences.py`** — agregar endpoint `POST /preferences/test`:

```python
@router.post("/test", summary="Send a synthetic SignalEvent to current tenant's channels")
def post_preferences_test(tenant_id: int = Depends(get_current_tenant_id)):
    """Dispara un SignalEvent placeholder por el path per-user del dispatcher
    para que el usuario verifique end-to-end que sus credenciales funcionan.

    Reuses dispatch_signal_to_users so la verificación cubre exactamente la
    misma ruta que un signal real (notify_channels overlay → TelegramChannel
    → Telegram API).
    """
    from api.config import load_config
    from notifier.events import SignalEvent
    from notifier.dispatch_per_user import dispatch_signal_to_users

    event = SignalEvent(
        symbol="TEST", score=9, direction="LONG",
        entry=0.0, sl=0.0, tp=0.0,
        lrc_pct=None, health_state="NORMAL",
    )
    base_cfg = load_config()
    receipts = dispatch_signal_to_users(event, base_cfg)
    user_receipts = receipts.get(tenant_id, [])
    return {
        "ok": any(r.status == "ok" for r in user_receipts),
        "receipts": [
            {"channel": r.channel, "status": r.status, "error": r.error}
            for r in user_receipts
        ],
    }
```

Tres notas sobre este endpoint:
- **Filtros aplican**: `dispatch_signal_to_users` aplica `symbol_filter` + `min_score` del usuario. Usamos `symbol="TEST"` + `score=9` para que pasen filtros razonables (score=9 ≥ cualquier min_score; symbol_filter=null acepta todo, lista explícita debería incluir "TEST" o el usuario verá receipts=[] aunque sus creds estén bien). Esto es un trade-off: forzar `score=9` bypassa el filtro real del usuario, pero verifica el path técnico. Documentado en el response para que el usuario sepa qué se ejercitó.
- **Dedup**: usar `symbol="TEST"` evita colisionar con dedup keys de signals reales. Cada test es independiente.
- **No-op si el usuario no tiene notify_channels**: receipts será `[]` (no canales configurados). Response: `{"ok": false, "receipts": []}`. UI muestra "Configurá token + chat_id antes de probar".

#### Frontend (2 archivos nuevos + 4 modificaciones)

**Discovery clave**: `UserMenu.tsx:31` ya tiene un item placeholder `{ icon: '◐', label: 'Conexiones', hint: 'Telegram · Webhook', badge: '1' }` con `onClick` sin wirear. La UI scaffold ya estaba diseñada para este feature. Solo hay que poner el click + crear el panel que abre.

**Crear: `frontend/src/components/ConnectionsPanel.tsx`** — slide-out panel inspirado en `ConfigPanel.tsx` (mismo patrón: slide-out from right + backdrop blur). Una sola sección activa por ahora: "Telegram". Estructura preparada para futuras (Webhook, Discord, etc).

Sección "Telegram":
- Input `telegram_bot_token`: type=password, placeholder hint "123456789:ABCdef...", botón "ojo" para revelar/ocultar.
- Input `telegram_chat_id`: type=text, placeholder "123456789".
- Botón "Guardar" → llama `updateUserPreferences({notify_channels: {telegram_bot_token, telegram_chat_id}})`.
- Botón "Probar envío" (deshabilitado mientras dirty unsaved): llama nuevo wrapper `testPreferencesDelivery()` → POST /preferences/test. Muestra resultado inline (✓ enviado / ✗ error con detail).
- Instrucciones colapsables: "¿Cómo creo mi bot?" con steps de BotFather + getUpdates.

**Crear: `frontend/src/components/ConnectionsPanel.module.css`** — estilos consistentes con `ConfigPanel.module.css`.

**Modificar: `frontend/src/components/UserMenu.tsx`** — agregar `onClick` al item "Conexiones" existente. Aceptar nueva prop `onConnectionsOpen: () => void`.

**Modificar: `frontend/src/App.tsx`** — extender `OverlayKind = 'notifs' | 'settings' | 'user' | 'connections' | null`. Pasar `onConnectionsOpen` al UserMenu. Render del `<ConnectionsPanel>` cuando `openOverlay === 'connections'`. El click en "Conexiones" cierra el UserMenu (`setOpenOverlay('connections')` reemplaza el `'user'` actual).

**Modificar: `frontend/src/api.ts`** — agregar wrapper:
```typescript
export async function testPreferencesDelivery(): Promise<TestDeliveryResponse> {
  return request<TestDeliveryResponse>('/preferences/test', { method: 'POST' });
}
```
+ type `TestDeliveryResponse` en `types.ts`.

**Bonus opcional (no blocker)**: el badge "1" del menú item actualmente es hardcoded en `UserMenu.tsx`. Podría wire-arse a "número de conexiones no configuradas" (1 si telegram_bot_token vacío, 0 si seteado). Out of scope para esta iteración — leave hardcoded.

### Data flow end-to-end

1. Usuario click en avatar ▾ del header → UserMenu abre.
2. Click en item "Conexiones".
3. App.tsx: `setOpenOverlay('connections')` (cierra el UserMenu, abre el panel).
4. `ConnectionsPanel` monta, llama `getUserPreferences()` → recibe `{notify_channels: {telegram_bot_token, telegram_chat_id} | null, ...}`.
5. Form renderiza valores actuales (o vacíos si null).
6. Usuario tipea credenciales → click Guardar.
7. `updateUserPreferences({notify_channels: {...}})` → `PUT /api/preferences` con JWT cookie + CSRF.
8. Backend `db_upsert_user_preferences` persiste a `user_preferences.notify_channels_json`.
9. Usuario click "Probar envío".
10. `testPreferencesDelivery()` → `POST /api/preferences/test`.
11. Backend: `dispatch_signal_to_users(SignalEvent("TEST", score=9, ...), base_cfg)` → para `tenant_id=current`, overlay carga `telegram_bot_token` + `telegram_chat_id` del JSON, `TelegramChannel.send()` postea a la Bot API.
12. Response: `{ok: true/false, receipts: [{channel, status, error}]}`.
13. UI muestra resultado.

### Storage — extensión del shape de notify_channels

Antes:
```json
{ "telegram_chat_id": "123456789" }
```

Después:
```json
{
  "telegram_bot_token": "123456789:ABCdef...",
  "telegram_chat_id": "123456789"
}
```

Ambos como **string** (TelegramChannel hace `.strip()` — int causaría crash, documented en spec superseded). `dict[str, Any]` del Pydantic acepta el shape sin schema change.

## Security note

**Bot tokens en plain text dentro de `notify_channels_json` (TEXT column de SQLite).** Threat model actual:
- Single-server EC2 con UNICO operador admin (Samuel/Simon).
- Acceso al servidor implica acceso a `.env` (donde viven JWT secrets, DEEPSEEK API key) y a `signals.db`.
- Robar un bot token NO es más grave que cualquier otra credencial — todas viven al mismo nivel de protección.

**Aceptable hoy. Futuro:**
- Si se invitan operadores no-trusted (post-Epic B #253), revisitar: encrypt-at-rest con clave en .env, o stash en separate `secrets` table con column-level encryption.
- Documentado como **deferred** en este spec. NO se implementa en esta iteración.

UI-side: el campo `telegram_bot_token` es `<input type="password">`, no se loggea en el browser, ni se envía al `/agent/conversations` audit. El response del GET /preferences sí lo devuelve plain (necesario para pre-fill al editar). Acceptable por threat model.

## Failure modes

| # | Síntoma observable | Causa probable | Recovery |
|---|---|---|---|
| F1 | "Probar envío" responde `{ok: false, receipts: []}` | Usuario no tiene `notify_channels` seteado (null) | Llenar campos + Guardar primero |
| F2 | "Probar envío" responde con receipt status `failed`, error `"telegram not configured (missing token or chat_id)"` | Uno de los 2 campos vacío después del save | Re-verificar ambos campos, save again |
| F3 | Receipt error `"HTTP 401: Unauthorized"` | Token mal copiado (typo, espacio extra) o bot deleted en BotFather | Re-issue token vía `/token` en BotFather, paste de nuevo |
| F4 | Receipt error `"HTTP 400: Bad Request: chat not found"` | chat_id mal escrito O usuario nunca hizo `/start` del bot (Telegram requiere "permiso explícito") | Mandar `/start` en Telegram, re-verificar chat_id en getUpdates, paste de nuevo |
| F5 | Receipt error `"HTTP 403: Forbidden: bot was blocked by the user"` | Usuario bloqueó el bot en Telegram | Desbloquear el bot, re-test |
| F6 | Signals reales (no el test) no llegan aunque "Probar envío" ✓ | min_score del usuario o symbol_filter excluyen los signals que aparecen | Verificar min_score vía GET /preferences; si demasiado alto, bajarlo |
| F7 | Después de save, "Probar envío" sigue fallando con creds que parecen correctas | Race: el PUT response volvió 200 pero load_config cachea (en realidad no — re-lee cada request) | Recargar la página, GET /preferences debería mostrar valores correctos |

## Rollback

Reversible 100%:
- Si el feature rompe algo en prod: revert los commits de backend + frontend. Schema sin cambios (sigue siendo `notify_channels: dict[str, Any]`), el shape extendido se ignora gracefully por TelegramChannel cuando ambos campos no están.
- Si el operator decide volver al modelo admin-side global: descartar este spec y volver al [`2026-05-21-telegram-multitenant-phase-a-pre-reg.md`](2026-05-21-telegram-multitenant-phase-a-pre-reg.md) original.
- Si solo se quiere desactivar feature flag (en caso de bug grave): no hay feature flag explícito porque el shape extendido es backward-compatible. Hard-rollback es revert.

## Test plan / acceptance gates

### Unit tests (backend)

- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_no_channels_returns_empty_receipts` — usuario sin `notify_channels` → `{ok: false, receipts: []}`.
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_with_telegram_routes_correctly` — usuario con `{telegram_bot_token, telegram_chat_id}` → llama `dispatch_signal_to_users` con `SignalEvent("TEST", score=9, ...)`, receipts incluye un row con `channel="telegram"`. (Telegram API mockeada para evitar I/O real).
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_only_returns_current_tenant_receipts` — con 2 usuarios activos en DB, el response solo incluye receipts del JWT-actual tenant_id (no del otro).

### Unit tests (frontend, vitest)

- [ ] `ConnectionsPanel.test.tsx::renders_form_with_current_values` — mock GET /preferences con notify_channels populado, verifica que los inputs muestran los valores.
- [ ] `ConnectionsPanel.test.tsx::save_calls_put_with_correct_body` — type en inputs + click Save, verifica que `updateUserPreferences` recibe el body esperado.
- [ ] `ConnectionsPanel.test.tsx::test_button_disabled_when_dirty` — type sin save → "Probar envío" debe estar disabled.
- [ ] `ConnectionsPanel.test.tsx::test_button_shows_ok_on_success` — mock POST /preferences/test con `{ok: true}`, verifica UI muestra "✓ enviado".
- [ ] `ConnectionsPanel.test.tsx::test_button_shows_error_on_failure` — mock con `{ok: false, receipts: [{error: "HTTP 401..."}]}`, verifica error mostrado.

### Integration (manual + Playwright e2e)

- [ ] **Manual (Samuel)**: Crear bot en BotFather, conseguir chat_id, abrir avatar ▾ → Conexiones → pegar credenciales → Guardar → Probar envío, confirmar que llega mensaje "TEST" en Telegram. **Este es el gate principal del feature** — sin esto el spec no se considera shipped.
- [ ] **Playwright e2e (yo)**: Abrir el dashboard, click avatar ▾, click "Conexiones", type creds fake, save, verificar DB tiene `notify_channels` con ambos campos, verificar que GET /preferences los devuelve.

## Estimación

| Componente | Effort |
|---|---|
| Backend endpoint + 3 tests | ~2h |
| Frontend `ConnectionsPanel.tsx` + CSS + 5 tests | ~3h |
| Wire-up: App.tsx OverlayKind, UserMenu.tsx onClick, api.ts wrapper, types.ts | ~30 min |
| Manual + Playwright integration | ~1h |
| PR review iteration | ~1h |
| **Total** | **~½ – 1 día** |

## Next phases (out of scope acá)

- **Phase B — Bot webhook + deep-link invites** (deferido hasta Epic B #253 user-onboarding): `POST /telegram/webhook` recibe updates de Telegram, captura `/start <invite_token>` y auto-popula `notify_channels.telegram_chat_id` del usuario invitado. Tabla nueva `telegram_invite_tokens`. Removes the manual `getUpdates` step.
- **Future — Encryption-at-rest** de tokens (deferred per §7). Disparado por el momento en que se invite a un operador no-trusted al sistema.
- **Future — Per-user `min_score` UI**: el field ya existe en `user_preferences`, el GET/PUT lo soporta. Solo falta exponerlo en el panel (slider 0-9). Bajo nivel de prioridad mientras hay un solo usuario.
- **Future — Per-user `symbol_filter` UI**: lista de checkboxes de los 10 símbolos curados. Same story.
