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
@router.post(
    "/test",
    summary="Send a test message to current tenant's Telegram",
    dependencies=[Depends(verify_api_key)],
)
def post_preferences_test(tenant_id: int = Depends(get_current_tenant_id)):
    """Verifica end-to-end que las credenciales de Telegram del usuario
    funcionan, mandando un mensaje "ping" a su bot.

    Bypassa `notify()` y `dispatch_signal_to_users` a propósito (ver §Notas):
    construye un `TelegramChannel` directamente con el cfg-con-overlay del
    usuario y llama `.send()`. Eso evita:
      - dedup collisions si en el futuro alguien cambia el default window
        de event_type=signal (hoy es 0, pero defensive design).
      - side-effect en NotificationBell: cada test press NO crea una row
        en notifications_sent (no usa notify(), no llama record_delivery).
      - filter false-negatives: el endpoint NO debería ser bloqueado por
        symbol_filter / min_score del usuario — esos aplican a signals
        reales, no a "verificá tu config".

    Trade-off: NO ejercita el dispatcher per-user. Aceptable porque el
    dispatcher está bien testeado (tests/test_notifier_dispatch_per_user.py)
    y lo que el usuario quiere verificar es "¿llega un mensaje a mi bot
    cuando lo configuro?", no "¿el dispatcher me rutea correcto?".
    """
    from api.config import load_config
    from db.user_preferences import db_get_user_preferences
    from notifier.channels.telegram import TelegramChannel

    prefs = db_get_user_preferences(tenant_id) or {}
    notify_channels = prefs.get("notify_channels") or {}
    base_cfg = load_config()
    user_cfg = {**base_cfg, **notify_channels}

    token = (user_cfg.get("telegram_bot_token") or "").strip()
    chat_id = (user_cfg.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        return {
            "ok": False,
            "receipts": [],
            "reason": "no_telegram_configured",
        }

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

**Decisión de diseño documentada** (Option 2 del review 2026-05-21): bypassar `notify()` y `dispatch_signal_to_users` es deliberado. Las alternativas consideradas:

| Opción | Pros | Cons |
|---|---|---|
| 1. Nuevo `SignalTestEvent` con dedupe_key random | Ejercita dispatcher real | Requiere new dataclass + 2 entries en mappings + filtro en NotificationBell para no mostrar tests |
| **2. Bypass notify, llamar TelegramChannel direct** ✅ | Simple, sin side-effects, sin dedup edge-cases | No ejercita dispatcher (aceptable — dispatcher cubierto por otros tests) |
| 3. Override dedupe_key por-call | Invasivo, cambia API pública de notify() | Rechazada |

**Auth**: `dependencies=[Depends(verify_api_key)]` por consistencia con el PUT existente (`api/user_preferences.py:60`). Hoy `api_key` está vacío en config → open access via JWT only (mismo behavior que PUT). Si en el futuro alguien setea el api_key, ambos endpoints se gatean.

**No-op si el usuario no tiene credenciales seteadas**: response `{ok: false, receipts: [], reason: "no_telegram_configured"}`. La UI usa el `reason` field para mostrar un mensaje claro ("Configurá tu token y chat_id primero").

#### Frontend (2 archivos nuevos + 4 modificaciones)

**Discovery clave**: `UserMenu.tsx:31` ya tiene un item placeholder `{ icon: '◐', label: 'Conexiones', hint: 'Telegram · Webhook', badge: '1' }` con `onClick` sin wirear. La UI scaffold ya estaba diseñada para este feature. Solo hay que poner el click + crear el panel que abre.

**Crear: `frontend/src/components/ConnectionsPanel.tsx`** — slide-out panel inspirado en `ConfigPanel.tsx` (mismo patrón: slide-out from right + backdrop blur). Una sola sección activa por ahora: "Telegram". Estructura preparada para futuras (Webhook, Discord, etc).

Sección "Telegram":
- Input `telegram_bot_token`: type=password, placeholder hint "123456789:ABCdef...", botón "ojo" para revelar/ocultar. Pre-fill con masked value desde GET; al typear nuevo value reemplaza, sin tipear preserva (detection vía `****`).
- Input `telegram_chat_id`: type=text, placeholder "123456789".
- Botón "Guardar" → llama `updateUserPreferences({notify_channels: {telegram_bot_token, telegram_chat_id}})`. Skip token en el body si el value sigue siendo masked (no se modificó).
- Botón "Probar envío" (deshabilitado mientras dirty unsaved): llama nuevo wrapper `testPreferencesDelivery()` → POST /preferences/test. Muestra resultado inline (✓ enviado / ✗ error con detail).
- Botón "Eliminar credenciales" (review fix): llama `updateUserPreferences({notify_channels: null})`. Limpia los inputs + muestra confirmación inline ("Credenciales eliminadas"). Use case principal: rotation de tokens, o usuario que quiere parar de recibir signals sin perder su user account.
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

**Badge "1" condicional** (review fix): el badge actual `'1'` está hardcoded en `UserMenu.tsx:31`. Wire-arlo a "número de conexiones no configuradas": si `notify_channels.telegram_bot_token` está seteado, badge desaparece (undefined); si no, badge='1'. Implementation: 3 líneas — leer prefs desde el contexto del UserMenu (props desde App.tsx) y pasar conditional al item.

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
11. Backend: lookup `db_get_user_preferences(tenant_id) → prefs.notify_channels`, overlay sobre `load_config()` → if `token` + `chat_id` ambos presentes, `TelegramChannel(user_cfg).send("*Crypto Scanner — prueba de conexión*…")`; else return `{ok: false, receipts: [], reason: "no_telegram_configured"}`.
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

UI-side: el campo `telegram_bot_token` es `<input type="password">`, no se loggea en el browser, ni se envía al `/agent/conversations` audit.

**Token masking en el GET response** (review fix): el endpoint `GET /api/preferences` enmascara el `telegram_bot_token` antes de devolverlo. Shape:

```python
def _mask_token(token: str) -> str:
    """123456789:ABCdef...wxyz → 123456789:****wxyz (preserva últimos 4 chars).

    Nota defensiva: tokens reales de Telegram tienen ~46 chars
    (<bot_id>:<35-char-secret>), así que `len < 10` solo se dispara
    para inputs basura (vacío, corrupto). Devolver "" en ese caso
    significa que el frontend va a pre-fillear vacío, indistinguible
    de "sin configurar" — aceptable porque en prod este path no se
    debería disparar. No agregar tests específicos para bordes
    `len ∈ {1..9}`, no son escenarios reales.
    """
    if not token or len(token) < 10:
        return ""
    return f"{token[:10]}****{token[-4:]}"
```

El frontend muestra el masked value como pre-fill con label "Token guardado · pegá uno nuevo para reemplazar". Si el usuario NO modifica el campo (value sigue conteniendo `****`), el PUT detecta esto y preserva el valor existente (no sobrescribe con el masked). Si el usuario pega un token nuevo (sin `****`), reemplaza.

Esto reduce el blast radius de un XSS/script-injection ~90% sin tocar schema. Documentado como mitigación cheap. Encryption-at-rest sigue deferred per arriba.

**Threat asymmetry** (matiz al framing inicial): un bot token comprometido permite postear como ese bot a todos los chats que lo añadieron (hoy solo Samuel). Un JWT secret comprometido controla toda la auth de la app. JWT es estrictamente peor, pero la asimetría no es perfecta — son riesgos del mismo orden de magnitud. La mitigación de masking arriba reduce uno de los dos.

## Rate-limit decision (review note)

Telegram Bot API tiene un rate limit de ~1 msg/sec por chat. El path real de signals tiene rate-limit via `notifier.ratelimit.bucket_for("telegram")` (token bucket por canal). Pero el endpoint POST /preferences/test bypassa `notify()` (ver Option 2 arriba) y por ende también el rate-limit del notifier.

**Decisión**: NO agregar rate-limit explícito al /test en esta iteración. Justificación:
- El usuario es operator-trust (sesión JWT autenticada, no usuario público).
- Click loco de "Probar envío" en peor caso satura SU PROPIO chat de Telegram — Telegram devuelve 429, el receipt mostrará el error, no afecta a otros usuarios.
- Si en algún momento esto se vuelve un problema operativo (logs llenos de 429s), agregar un in-memory cooldown trivial (~10s per tenant_id) es 5 líneas. Frame as deferred.

Documentado para que el reviewer del próximo Phase no se sorprenda.

## Failure modes

| # | Síntoma observable | Causa probable | Recovery |
|---|---|---|---|
| F1 | "Probar envío" responde `{ok: false, receipts: [], reason: "no_telegram_configured"}` | Uno o ambos campos vacíos en DB (notify_channels null, o token sin chat_id, o viceversa) | Llenar token + chat_id en el panel, Save, retry. Early-return del endpoint atrapa esto antes de llamar a TelegramChannel — el error "telegram not configured" del channel no llega a aparecer por este path. |
| F3 | Receipt error `"HTTP 401: Unauthorized"` | Token mal copiado (typo, espacio extra) o bot deleted en BotFather | Re-issue token vía `/token` en BotFather, paste de nuevo |
| F4 | Receipt error `"HTTP 400: Bad Request: chat not found"` | chat_id mal escrito O usuario nunca hizo `/start` del bot (Telegram requiere "permiso explícito") | Mandar `/start` en Telegram, re-verificar chat_id en getUpdates, paste de nuevo |
| F5 | Receipt error `"HTTP 403: Forbidden: bot was blocked by the user"` | Usuario bloqueó el bot en Telegram | Desbloquear el bot, re-test |
| F6 | Signals reales (no el test) no llegan aunque "Probar envío" ✓ | min_score del usuario o symbol_filter excluyen los signals que aparecen | Verificar min_score vía GET /preferences; si demasiado alto, bajarlo |
| F7 | Después de save, "Probar envío" sigue fallando con creds que parecen correctas | Race: el PUT response volvió 200 pero load_config cachea (en realidad no — re-lee cada request) | Recargar la página, GET /preferences debería mostrar valores correctos |

## Rollback

Reversible 100%:
- Si el feature rompe algo en prod: revert los commits de backend + frontend. Schema sin cambios (sigue siendo `notify_channels: dict[str, Any]`), el shape extendido se ignora gracefully por TelegramChannel cuando ambos campos no están.
- Si el operator decide volver al modelo admin-side global: descartar este spec y volver al [`2026-05-21-telegram-multitenant-phase-a-pre-reg.md`](2026-05-21-telegram-multitenant-phase-a-pre-reg.md) original.
- **Feature flag opcional** (review polish, no implementado por defecto): si se quisiera kill-switch sin redeploy, agregar `cfg.features.telegram_per_user` (read-time, default `true`) y gate el endpoint POST /preferences/test + esconder el menu item "Conexiones" cuando false. ~5 líneas de code. Decisión: no implementar porque el feature es backward-compatible y bajo riesgo; revert via PR es suficiente. Si después de shipping aparece un bug que no se puede contener con revert (ej: persistencia corrupta), agregar el flag entonces.

## Test plan / acceptance gates

### Unit tests (backend)

- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_no_channels_returns_no_telegram_configured` — usuario sin `notify_channels` → `{ok: false, receipts: [], reason: "no_telegram_configured"}`.
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_token_only_returns_no_telegram_configured` — usuario con token pero sin chat_id → mismo `reason: "no_telegram_configured"`.
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_with_telegram_routes_correctly` — usuario con `{telegram_bot_token, telegram_chat_id}` → llama `TelegramChannel.send` con cfg-con-overlay, receipt status=ok. (Telegram API mockeada).
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_two_calls_within_window_both_succeed` — 2 calls consecutivos del mismo tenant retornan ambos ok: true. Verifica que el bypass de `notify()` evita la dedup collision potencial (defensive design — hoy `signal` event_type tiene dedup_window=0 pero el bypass blinda contra cambios futuros).
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_does_not_write_to_notifications_sent` — pre-condition: count rows en `notifications_sent`. Llamar /test. Post-condition: count idéntico. Confirma que el bypass de `notify()` evita el side-effect en NotificationBell.
- [ ] `tests/test_api_user_preferences.py::test_test_endpoint_isolated_per_tenant` — con 2 usuarios en DB (cada uno con su token+chat_id distinto), tenant_A llamando /test solo recibe receipt del envío a SU bot, no del de tenant_B.
- [ ] `tests/test_api_user_preferences.py::test_get_preferences_masks_token` — GET con token en DB → response.notify_channels.telegram_bot_token tiene shape `"<10chars>****<4chars>"`, no plain.
- [ ] `tests/test_api_user_preferences.py::test_put_preferences_preserves_masked_token` — PUT con `notify_channels.telegram_bot_token` que contiene `****` → DB queda con el token original, no se sobrescribe.
- [ ] `tests/test_api_user_preferences.py::test_put_preferences_replaces_when_token_unmasked` — PUT con token nuevo plain (sin `****`) → DB queda con el nuevo.

### Unit tests (frontend, vitest)

- [ ] `ConnectionsPanel.test.tsx::renders_form_with_masked_token` — mock GET /preferences con notify_channels populado (token masked), verifica que el input muestra el masked value + label "Token guardado · ...".
- [ ] `ConnectionsPanel.test.tsx::save_preserves_token_when_unchanged` — type chat_id pero NO token (queda masked), click Save → updateUserPreferences body solo incluye chat_id + masked token. Backend skip-detection se ejercita server-side, frontend solo manda lo que el usuario tipeó.
- [ ] `ConnectionsPanel.test.tsx::save_replaces_token_when_user_pastes_new` — type token nuevo plain + save → body con token plain.
- [ ] `ConnectionsPanel.test.tsx::test_button_disabled_when_dirty` — type sin save → "Probar envío" debe estar disabled.
- [ ] `ConnectionsPanel.test.tsx::test_button_shows_ok_on_success` — mock POST /preferences/test con `{ok: true}`, verifica UI muestra "✓ enviado".
- [ ] `ConnectionsPanel.test.tsx::test_button_shows_error_on_failure` — mock con `{ok: false, receipts: [{error: "HTTP 401..."}]}`, verifica error mostrado.
- [ ] `ConnectionsPanel.test.tsx::test_button_shows_no_telegram_reason` — mock con `{ok: false, receipts: [], reason: "no_telegram_configured"}`, verifica UI muestra "Configurá tu token y chat_id primero".
- [ ] `ConnectionsPanel.test.tsx::delete_clears_db_row` — click "Eliminar credenciales" → confirma `updateUserPreferences({notify_channels: null})` called.
- [ ] `UserMenu.test.tsx::badge_visible_when_telegram_unconfigured` — render con prefs.notify_channels === null → badge '1' visible.
- [ ] `UserMenu.test.tsx::badge_hidden_when_telegram_configured` — render con prefs.notify_channels.telegram_bot_token set → badge undefined/hidden.

### Integration (manual + Playwright e2e)

- [ ] **Manual (Samuel) — gate principal**: Crear bot en BotFather, conseguir chat_id, abrir avatar ▾ → Conexiones → pegar credenciales → Guardar → Probar envío, confirmar que llega mensaje "*Crypto Scanner — prueba de conexión*..." en Telegram. **Sin esto el spec no se considera shipped.**
- [ ] **Manual (Samuel) — sub-gate NotificationBell**: después de 3 presses consecutivos de "Probar envío" (todos exitosos), el NotificationBell del header sigue mostrando 0 entries nuevas — verifica que el bypass de `notify()` evita el side-effect que llenaría el bell con "TEST" duplicates.
- [ ] **Manual (Samuel) — sub-gate rapid-fire**: 5 presses rápidos de "Probar envío" — todos retornan ok=true. Verifica que no hay dedup collision aunque el dispatcher per-user lo tendría (defensive design del Option 2).
- [ ] **Playwright e2e (yo)**: Abrir el dashboard, click avatar ▾, click "Conexiones", type creds fake, save, verificar DB tiene `notify_channels` con ambos campos, verificar que GET /preferences los devuelve masked, click "Eliminar credenciales", verificar DB `notify_channels=null`.

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
