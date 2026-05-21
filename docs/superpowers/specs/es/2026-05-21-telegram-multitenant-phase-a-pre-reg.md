# Telegram multi-tenant — Phase A (backend wiring) — pre-reg

**Fecha:** 2026-05-21
**Operator:** Samuel (en nombre de Simon)
**Estado:** pre-reg pre-ejecución
**Estimación:** ~30 min wall-clock

## Contexto

El operator reportó que la app no está enviando señales a Telegram. Investigación con `superpowers:systematic-debugging` (este turno, transcript inline) descartó la hipótesis inicial "deploy borró credentials" — el operator nunca configuró Telegram. La app nunca ha mandado señales porque `config.json` jamás tuvo `telegram_bot_token` ni equivalente por env (`TRADING_TELEGRAM_BOT_TOKEN`).

El operator quiere configurar Telegram en modo **multi-tenant**, no como canal único de broadcast. Cada usuario del sistema (hoy solo Simon; en el futuro post-Epic B #253) recibirá sus señales en su **propio chat** de Telegram, gestionado vía `user_preferences.notify_channels.telegram_chat_id`.

Phase A es el primer sub-proyecto de un epic más amplio (ver §10). Cubre **solo el wiring de backend** que pone a Simon recibiendo señales hoy. UI y bot webhook quedan para Phases B/C.

## Goal

Después de Phase A:

- Simon recibe en su chat de Telegram cada signal con `score ≥ 4` (default `signal_filters.min_score`) que no esté dentro del dedup window de 30 min.
- El dispatch ocurre por la **ruta per-user** (`notifier/dispatch_per_user.py:dispatch_signal_to_users`), no por el fallback global broadcast.
- `journalctl -u trading-spacial` muestra explícitamente `dispatch: user_id=1 symbol=<X> score=<Y> receipts=1` para cada signal entregado.

## Out of scope para Phase A

- Frontend UI para que un usuario gestione sus propias preferences (Phase B).
- `/webhook/test` compatibilidad con per-user dispatch — hoy usa `cfg["telegram_chat_id"]` global (Phase B incluirá un endpoint `/preferences/test` que sí ejercita el path per-user).
- Bot webhook (`POST /telegram/webhook`) que reciba `/start` y capture chat_ids automáticamente — Phase C, deferido hasta que Epic B abra usuarios.
- Cualquier cambio de código. Phase A es **pure config + DB row update**.

## Arquitectura — qué existe vs qué cambia

### Ya implementado (no se toca)

- `notifier/dispatch_per_user.py` (B.4 #257): fan-out por usuario activo con filtro `symbol_filter` + `min_score` + overlay `notify_channels` sobre la base cfg.
- `notifier/channels/telegram.py`: `TelegramChannel` lee `cfg["telegram_bot_token"]` y `cfg["telegram_chat_id"]`, hace POST a la Bot API con retries (3 attempts + backoff exponencial, 429-aware).
- `db/user_preferences.py` + `api/user_preferences.py`: schema `user_preferences.notify_channels_json` (JSON column), endpoints `GET /api/preferences` y `PUT /api/preferences`. Hoy `verify_api_key` es open (no hay `api_key` en config), así que `PUT` acepta JWT solo.
- `api/config.py:_env_map`: mapea `TRADING_TELEGRAM_BOT_TOKEN` → `cfg["telegram_bot_token"]`. `load_config()` re-lee per-request (validado por el flip del agent earlier today).
- Scanner loop (`scanner/runtime.py:execute_scan_for_symbol`) ya llama `push_telegram_direct(rep, cfg)` que internamente delega a `dispatch_signal_to_users` cuando hay usuarios activos.

### Qué se cambia (estado)

1. `/var/www/trading/.env`: se agrega línea `TRADING_TELEGRAM_BOT_TOKEN=<token>`.
2. Process env de `trading-spacial`: tras restart, contiene la nueva var.
3. Fila de `user_preferences` con `tenant_id=1`: `notify_channels_json={"telegram_chat_id": "<chat_id>"}` (upsert).

## Pasos de ejecución

### Owner: Samuel (operator, en Telegram + en server)

1. **Crear bot** vía [@BotFather](https://t.me/BotFather):
   - `/newbot` → nombre → username → recibe token `123456789:ABC...`
2. **Conseguir chat_id**:
   - Mandar `/start` (o cualquier mensaje) al bot recién creado.
   - Abrir en browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Copiar `result[0].message.chat.id` (entero positivo para chats 1-a-1). **Importante**: el chat_id se persiste como **string** en `notify_channels_json` — `TelegramChannel.send` (`notifier/channels/telegram.py:26`) llama `.strip()` y crashearía sobre un int. JSON.stringify del paso 5 ya lo serializa correcto si el valor JS es string.
3. **Agregar token al `.env`** (SSH al server):
   ```bash
   ssh aws-server "echo 'TRADING_TELEGRAM_BOT_TOKEN=<token>' | sudo tee -a /var/www/trading/.env >/dev/null"
   ```
   Verificar (key-only, sin valor): `ssh aws-server "sudo grep -c '^TRADING_TELEGRAM_BOT_TOKEN=' /var/www/trading/.env"` → esperado `1`.
4. **Restart del service**:
   ```bash
   ssh aws-server "sudo systemctl restart trading-spacial"
   ```
   2-3s downtime. Verificar: `systemctl is-active trading-spacial` → `active` y `curl localhost:8100/health` → `healthy: true`.

### Owner: Claude (vía browser Playwright, sesión JWT activa)

5. **PUT chat_id a preferences** — vía `browser_evaluate` con la cookie de Samuel ya cargada:
   ```js
   fetch('/api/preferences', {
     method: 'PUT',
     credentials: 'include',
     headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
     body: JSON.stringify({ notify_channels: { telegram_chat_id: '<chat_id>' } })
   })
   ```
   Verificar response 200 + `body.preferences.notify_channels.telegram_chat_id === '<chat_id>'`.

6. **Force scan + verify dispatch**:
   - `POST /api/scan` (force) o esperar 5 min al próximo ciclo.
   - `ssh aws-server "sudo journalctl -u trading-spacial -f --since '30 seconds ago'"` — esperar línea `dispatch: user_id=1 symbol=<X> score=<Y> receipts=1`.
   - Samuel confirma recepción en Telegram del bot.

## Failure modes + recovery

| # | Síntoma observable | Causa probable | Recovery |
|---|---|---|---|
| F1 | journalctl: `dispatch_signal_to_users: no active users — broadcast skipped` | `users.is_active=0` para Samuel (no debería pasar — está logueado) | Query DB: `SELECT id, email, is_active FROM users` |
| F2 | `dispatch: ... receipts=0` o receipts con `status=failed` | Per-user `notify_channels` no aplicó o token vacío en process env | Re-verificar PUT response + `sudo systemctl restart` para releer .env |
| F3 | TelegramChannel log: `telegram not configured (missing token or chat_id)` | Token no llegó al proceso (restart no leyó .env) o chat_id no en prefs | `systemctl restart trading-spacial` + verificar PUT row en DB |
| F4 | TelegramChannel log: `HTTP 400: Bad Request: chat not found` | chat_id mal escrito o el bot nunca recibió `/start` (Telegram requiere "permiso") | Re-DM al bot + re-fetch getUpdates + re-PUT chat_id |
| F5 | TelegramChannel log: `HTTP 401: Unauthorized` | Token mal copiado o bot deleted en BotFather | Re-issue token vía `/token` en BotFather + re-set .env + restart |
| F6 | Service no levanta post-restart | `.env` con syntax error (typo en línea agregada) | `journalctl -u trading-spacial -n 50 --no-pager`, editar .env manualmente, retry |

## Rollback

Reversible 100%:
- Para deshacer **token**: `sudo sed -i '/^TRADING_TELEGRAM_BOT_TOKEN=/d' /var/www/trading/.env && sudo systemctl restart trading-spacial`.
- Para deshacer **chat_id en prefs**: `PUT /api/preferences` con `{"notify_channels": null}`.

Estado pre-Phase-A queda exactamente como antes (sin telegram, sin prefs).

## Test plan / criterios de aceptación

Se considera Phase A completo cuando **los 3 son verdaderos**:

- [ ] (Backend) `journalctl -u trading-spacial --since '5 minutes ago' | grep 'dispatch: user_id=1'` muestra **al menos 1 línea** con `receipts=1` para un signal real (no /webhook/test).
- [ ] (User) Samuel confirma haber recibido **al menos 1 mensaje** del bot en su chat de Telegram, con el formato esperado de signal (símbolo + score + precio + dirección + SL/TP).
- [ ] (Database) Query verifica que la fila persistió:
  ```python
  json.loads(con.execute("SELECT notify_channels_json FROM user_preferences WHERE tenant_id=1").fetchone()[0])
  # → {"telegram_chat_id": "<chat_id>"}
  ```

## Estimación + responsibilities

| Owner | Paso | Tiempo |
|---|---|---|
| Samuel | 1 — Bot creation en BotFather | 2 min |
| Samuel | 2 — Chat ID discovery vía getUpdates | 2 min |
| Samuel | 3 — Token al `.env` | 1 min |
| Samuel | 4 — `systemctl restart` | 30 sec |
| Claude | 5 — PUT preferences | 30 sec |
| Claude | 6a — POST /api/scan | 10 sec |
| Both | 6b — Verify journalctl + Telegram receipt | 1-5 min (wait for signal) |

Total wall-clock: **~10-15 min de trabajo activo + hasta ~5 min de espera** del próximo signal con score ≥ 4.

## Next phases (pointers, no scope acá)

- **Phase B — Frontend UI self-service**: nuevo componente `NotificationSettings` en el dropdown del usuario, con form para gestionar `chat_id` + `min_score` + `symbol_filter` y botón "send test" que ejercita el path per-user (necesita nuevo endpoint `POST /api/preferences/test` o extender `/webhook/test`). Prereq: Phase A. Spec separado cuando Phase A esté en prod.
- **Phase C — Bot webhook + deep-link invites**: `POST /telegram/webhook` recibe updates de Telegram, captura `/start <invite_token>` y auto-popula `notify_channels.telegram_chat_id` del usuario invitado. Tabla nueva `telegram_invite_tokens(token, tenant_id, expires_at, used_at)`. Prereq: Phase B (UI para "invite user" admin action). Spec separado, deferido hasta que Epic B (#253) habilite onboarding.
