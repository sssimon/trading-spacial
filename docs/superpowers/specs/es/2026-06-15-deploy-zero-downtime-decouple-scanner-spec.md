# Deploy zero-downtime — desacoplar el scanner del proceso de la API

**Fecha:** 2026-06-15
**Estado:** Diseño v2 (tras panel w6jzy5vev + pase de críticos Serrano + Halberg)
**Relacionados:** [[2026-04-29-trading-sdar-dev-deploy-design]] · [[docs/atomic-deploy-migration.md]] (PR #377 abandonado) · CLAUDE.md non-negotiable #8 (frescura), #1 (PositionClosure)

> **Changelog v2 (pase de críticos).** Serrano (clínico) + Halberg (runtime) revisaron v1 contra el código real. El *orden* del cutover y el modelo WAL se confirmaron correctos, pero se cerraron bloqueadores que solo aparecen bajo fallas reales:
> - **Reboot:** `After=` ≠ readiness → la API arrancaba con `SKIP_DB_INIT=1` contra schema no-migrado → crash-loop. **Fix:** `trading-scanner.service` es `Type=notify` y envía `sd_notify(READY=1)` tras el DDL; las APIs lo `After=`+`Wants=` (§4.3).
> - **Deploys concurrentes:** `cancel-in-progress: true` cancelaba a media-mutación → include a un color muerto → 502 persistente. **Fix:** `cancel-in-progress: false` + self-heal del include (§4.5).
> - **"Un solo escritor por construcción" era falso:** `_bootstrap_first_user` (boot) y `/scan` (POST) escriben desde la API web-only. **Fix:** gatear ambos; reformular a "un solo escritor **periódico** por construcción; writes bajo demanda serializados por WAL como hoy" (§2, §4.1).
> - **SIGTERM:** no hay handler de señal en el repo. `scanner_main.py` lo trae (§4.2).
> - **Corrección (C):** faltaban `/ticker` y `/symbols` (también leen `_scanner_state`); contadores de observabilidad se perdían; contrato 200/503 de `/health` indefinido. **Fix:** §3 + §5 ampliados.
> - **Otros:** `backup_db` a tmpfile+rename (no deja backups truncados al kill); escritura atómica del include; `--timeout-graceful-shutdown 30` en las APIs; `/health/live` valida tabla canónica; sudoers acotado; reconciliación de unit drift en el deploy.

## 1. Problema y causa raíz

Cada deploy a `main` hace `systemctl restart trading-spacial`, que tumba el **único proceso uvicorn** ~10-15s mientras (a) re-importa la app y (b) corre el **DDL de boot** + arranca el **scanner in-process** (5 threads) sobre la DB. Durante esa ventana nginx no tiene upstream → **ráfaga de 503** al frontend (que hace polling de muchos endpoints). Verificado en prod: 117 respuestas 503 hoy, todas en las ventanas de restart de los deploys #598/#599.

**Causa raíz:** el scanner (pesado, dueño de frescura) vive en el *path de arranque del request-server*. Cualquier reinicio del request-server arrastra el costo de arrancar el scanner. Para zero-downtime hay que **sacar el scanner del proceso de la API**.

## 2. Decisión: decouple-scanner

Mover el scanner a su **propio servicio systemd** (`trading-scanner.service`). La API queda **web-only** y arranca en ~2s (sin scanner, sin DDL) → se vuelve **blue-green** detrás de nginx con **~0 downtime de cliente** vía `nginx -s reload` (no failover frágil con `proxy_next_upstream` reintentando POSTs no idempotentes).

Por qué gana (criterio en orden):
1. **Zero-downtime real:** ataca la causa raíz; la API arranca en ~2s.
2. **Un solo escritor PERIÓDICO por construcción:** solo `trading-scanner.service` corre `start_scanner_thread()` (los 5 threads escritores de alta frecuencia: scanner, health-monitor, kill-switch-calibrator, screener, sync). Las APIs (`RUN_SCANNER=0`) nunca arrancan ese escritor periódico — garantizado por systemd, no por un lock en runtime. **Honestidad (críticos):** la API web-only sigue siendo escritora BAJO DEMANDA en tres rutas, que se neutralizan o se aceptan explícitamente: (a) `_bootstrap_first_user` en boot → **se gatea** bajo `SKIP_DB_INIT` (§4.1); (b) `/scan` (POST, corre `execute_scan_for_symbol`) → **devuelve 409** cuando `RUN_SCANNER=0` (§4.1); (c) las acciones de operador (PositionClosure → `BEGIN IMMEDIATE`, persistencia de turnos del agente) ya coexisten hoy con el scanner serializadas por WAL + `busy_timeout=15000` (`db/connection.py:96`) — NO son escritores nuevos. Tras (a) y (b), la API web-only solo escribe lo que ya escribía hoy un proceso único, serializado por WAL.
3. **Migración sin downtime y reversible** (§6).
4. **No resucita ninguno de los 3 bloqueos del PR #377:** layout plano intacto, cero symlinks, mismo `.venv`/`.env`/DB; la DB se resuelve vía `__file__` (`db/connection.py`), no vía cwd-symlink.

**Descartados** (cada uno con killer confirmado en código): blue-green con scanner gateado *dentro* del proceso (la instancia web-only daría 503 perpetuo en `/health` y violaría #8); gunicorn `--workers 1` + HUP (no es readiness-gated → la ventana de 10-15s regresa; flock Linux-only rompe el suite de Windows); socket-activation como solución final (solo convierte 503→cuelgue de 10-15s); resucitar el atomic-deploy de symlinks (#377). Ver el panel para el detalle.

## 3. Las tres correcciones obligatorias (el panel las exigió)

El killer compartido de los approaches de failover: **`api/health.py:107-153` (`/health`) deriva la liveness de `btc_api._scanner_state` (memoria de proceso)** y exige `scanner=='ok'` para HTTP 200. Una instancia web-only nunca pone `running=True` → 503 perpetuo. Y eso es, además, una violación del #8 (estado vivo en memoria, no en el contrato). Correcciones:

- **(A) `GET /health/live` — readiness desacoplado.** Devuelve 200 si uvicorn responde + el schema está presente (`snapshot_connection` + `SELECT 1 FROM users LIMIT 1` — tabla canónica, **no** un `SELECT 1` pelado, para detectar schema incompleto; Halberg #7), **sin tocar `_scanner_state`**. Es el endpoint que pollean el deploy y el health-gate del blue-green. (Como uvicorn no liga el socket hasta que el lifespan startup termina, un 200 en `/health/live` ⇒ lifespan completo ⇒ middlewares montados ⇒ listo para tráfico — confirmado por Halberg.)
- **(B) `SKIP_DB_INIT=1` en las APIs web-only.** El lifespan (`btc_api.py:247-255`) corre `init_db()`/`init_auth_db`/`init_system_state` **y `_bootstrap_first_user()`** (este último abre `BEGIN IMMEDIATE` y puede `INSERT INTO users` en primer boot — Serrano #1). **TODO ese bloque** se envuelve en `if os.getenv("SKIP_DB_INIT") != "1": ...`. El `trading-scanner.service` es el **dueño único del schema y del bootstrap del primer usuario** (no setea el flag → migra + bootstrapea). Las APIs lo setean → cero writes en boot. La existencia del schema cuando la API arranca la garantiza el `Type=notify` del scanner (§4.3), no el `After=` solo.
- **(C) Frescura + estado del scanner desde la DB, no desde memoria (#8).** Siete rutas leen `_scanner_state` de memoria; en una API web-only reportarían falso. Migración por ruta:
  - **`/health`, `/status`, `/`** (liveness del scanner): derivar de la **DB** — `scans_total`/`signals_total` de `COUNT` sobre las tablas (no del contador en memoria); `last_scan_ts` del **`MAX(ts)` de la tabla `scans`** (nombrar la columna explícito; hoy `get_latest_scan` ordena por `id DESC`, hay que derivar el ts real — Serrano #14), envuelto en `freshness.LiveSnapshot` (`fresco`/`rancio`/`muerto` contra `scan_interval_sec*3`). El contador `errors` (solo-memoria, sin fuente DB) **se elimina del contrato** de `/health` (declararlo; Serrano #7).
  - **`/ticker`, `/symbols`** (Serrano #2): hoy leen `_scanner_state["symbols_active"]` con fallback a `get_active_symbols()`. Cambiar a leer `get_active_symbols()` directo (la config es la fuente de verdad), eliminando el read de memoria → mismo watchlist en toda instancia.
- **Contrato 200/503 de `/health`** (Serrano #8): `/health` deja de gatear sobre `_scanner_state["running"]` y pasa a gatear sobre la **frescura DB**: `200` si la frescura del scanner es `fresco`; `503` si `rancio`/`muerto` o DB caída. Así un monitor externo (Docker/uptime) alerta cuando el `trading-scanner.service` muere — que es justo lo que `/health` debe vigilar ahora.

## 4. Arquitectura

### 4.1 Gates de entorno (en `btc_api.py` lifespan)

| Env var | Default | API web-only | scanner-service | Unit viejo (compat) |
|---|---|---|---|---|
| `RUN_SCANNER` | `'1'` | `'0'` | `'1'` | (ausente → `'1'`, scanner in-process como hoy) |
| `SKIP_DB_INIT` | ausente (`!= '1'`) | `'1'` | ausente (migra) | (ausente → corre DDL como hoy) |
| `RUN_AS_SERVICE` | (existente) `'1'` | `'1'` | `'1'` | `'1'` |

Backward-compatible: el unit viejo `trading-spacial` no setea ninguna → defaults preservan el comportamiento actual (scanner + DDL in-process). `python btc_api.py` y los tests (sin `RUN_SCANNER`) también arrancan el scanner como hoy.

Cambios en el lifespan (el `_bootstrap_first_user` entra DENTRO del gate — Serrano #1):
```python
if os.getenv("SKIP_DB_INIT") != "1":
    log.info("Initializing DB schema…")
    init_db()
    with transaction() as con:
        init_auth_db(con)
        init_system_state(con)
    _bootstrap_first_user()   # abre BEGIN IMMEDIATE / puede INSERT — solo el dueño del schema
else:
    log.info("SKIP_DB_INIT=1 — schema y bootstrap los hace trading-scanner.service")
if os.getenv("RUN_SCANNER", "1") == "1":
    log.info("Starting scanner thread…")
    start_scanner_thread()
else:
    log.info("RUN_SCANNER=0 — instancia web-only, scanner desacoplado")
```
`_jwt_secret()` (`:245`, fail-fast, read) se mantiene FUERA del gate. `stop_managed_threads()` (teardown, `:267`) ya es idempotente → sin cambio (en la web-only no hay threads que juntar).

**`/scan` (POST) gateado** (Serrano #4): `execute_scan_for_symbol` es un escritor; en la API web-only no debe correr. Al inicio del handler: `if os.getenv("RUN_SCANNER", "1") != "1": raise HTTPException(409, "scan corre en trading-scanner.service")`.

### 4.2 `scanner_main.py` (NUEVO, raíz) — entrypoint del scanner-service

- Setea `RUN_AS_SERVICE=1`, `RUN_SCANNER=1`.
- Corre el DDL (dueño del schema): `init_db()` + `init_auth_db` + `init_system_state` + `_bootstrap_first_user()`.
- **`sd_notify("READY=1")` JUSTO DESPUÉS de que el DDL termina** (antes de `start_scanner_thread`). Implementación sin dependencia: escribir `READY=1` al socket de `$NOTIFY_SOCKET` (raw `AF_UNIX/SOCK_DGRAM`, ~10 líneas; no requiere la lib `systemd`). Esto es lo que hace que `Type=notify` + el `After=` de las APIs **espere a que el schema exista** (cierra el crash-loop de reboot, Halberg #1).
- `start_scanner_thread()` (los 5 threads: scanner, health-monitor, kill-switch-calibrator, screener, sync).
- **Handler de `SIGTERM` (NO existe en el repo hoy — Halberg #3):** `signal.signal(signal.SIGTERM, handler)` (y SIGINT) donde `handler` (a) llama `stop_managed_threads()` y (b) setea el evento que desbloquea el `wait()` del main. Requiere exportar `_thread_stop_event` desde `scanner/runtime.py`.
- Bloquea en `_thread_stop_event.wait()`. **NO arranca uvicorn.**
- Emite su frescura vía `freshness.LiveSnapshot` (ya escribe `data/symbols_status.json` por ciclo + los scans a la DB con timestamp — la fuente de #8).
- **NOTA de teardown (Halberg #3):** el calibrator no chequea `stop_event` entre sliders del grid (`run_optimization_v2`), así que una iteración en vuelo puede tardar decenas de segundos. Los threads son `daemon=True`: si exceden el join, el proceso sale y el intérprete los mata. SQLite WAL + `BEGIN IMMEDIATE` cortado **no corrompe** (rollback implícito). Pero `backup_db` cortado deja un backup truncado → ver §8 (fix: tmpfile+rename). `TimeoutStopSec=60` (no 20) para dar margen al grid del calibrator.

### 4.3 systemd (3 units)

- **`trading-scanner.service`** (NUEVO): **`Type=notify`** (envía `READY=1` tras el DDL — §4.2), `ExecStart=.../python /var/www/trading/scanner_main.py`, `Environment=RUN_SCANNER=1 RUN_AS_SERVICE=1`, `EnvironmentFile=/var/www/trading/.env`, `WorkingDirectory=/var/www/trading`, `ProtectSystem=strict`, `ReadWritePaths=/var/www/trading`, `Restart=on-failure`, `TimeoutStopSec=60` (margen para `stop_managed_threads` + un slider del calibrator en vuelo, sin SIGKILL a media transacción).
- **`trading-api@.service`** (NUEVO, template): `ExecStart=.../uvicorn btc_api:app --host 127.0.0.1 --port %i --timeout-graceful-shutdown 30` (drena requests/SSE en vuelo en el stop — Halberg #5), `Environment=RUN_SCANNER=0 SKIP_DB_INIT=1 RUN_AS_SERVICE=1`, `EnvironmentFile=/var/www/trading/.env`, **`Wants=trading-scanner.service` + `After=trading-scanner.service`** — con el scanner en `Type=notify`, el `After=` **espera el `READY=1`** (post-DDL) antes de arrancar la API → el schema existe cuando la API web-only (`SKIP_DB_INIT=1`) arranca, incluso en reboot frío (cierra Halberg #1). `After=` NO crea dependencia de runtime: si el scanner cae después, la API sigue. Mismas restricciones. Instancias `@8100` y `@8101` (blue/green).
- El unit viejo **`trading-spacial`** se conserva (disabled) en disco para rollback inmediato.

### 4.4 nginx (include + upstream)

- Extraer el upstream a `/etc/nginx/conf.d/trading-upstream.conf`: `upstream trading_api { server 127.0.0.1:8100; }` (un solo color activo a la vez; el deploy reescribe SOLO este include 8100↔8101).
- En `trading.sdar.dev.conf`: cambiar `proxy_pass http://127.0.0.1:8100/` (en `location /api/` Y `location = /api/auth/login`) a `http://trading_api/...` preservando el path-rewrite.
- **Location aislado `/api/agent/`** para SSE: `proxy_buffering off; proxy_read_timeout 3600s;` (los streams del copiloto son long-lived). Sin `proxy_next_upstream` (un stream a medias no se reintenta).
- **Escritura ATÓMICA del include** (Halberg #4): el deploy escribe el include a un tmpfile y hace `mv` (rename atómico en el mismo filesystem), nunca un redirect `>` in-place — para que `nginx -t`/un reload concurrente nunca lea un include parcial/vacío. Luego `nginx -t && nginx -s reload` (el reload **no corta** conexiones establecidas; arranca workers nuevos antes de retirar los viejos).

### 4.5 `deploy.yml` blue-green

**`concurrency` (Halberg #7 — BLOQUEANTE):** cambiar `cancel-in-progress: true` → **`false`** para `deploy-production`. Un deploy que muta estado de infra en pasos no-atómicos NO debe ser cancelable a media-secuencia (cancelar deja el include apuntando a un color muerto → 502 persistente de minutos). Los deploys se **serializan/encolan** — aceptable.

Reemplazar el step `pip install + restart service` por:
0. **Self-heal (paso 0):** leer el color al que apunta el include; si ese color **no** tiene proceso vivo (`systemctl is-active trading-api@<color>` != active), reparar antes de empezar (reapuntar al color sano o arrancar el que falte). Cubre un deploy previo que quedó a medias.
1. `pip install --upgrade -r requirements.txt` (venv compartido, una vez).
2. **Verificar estado de puertos** (Serrano #12): `systemctl is-active` de ambos `trading-api@8100/8101` + `ss -ltnp` para confirmar quién escucha dónde, antes de decidir colores.
3. Detectar el color **activo** (el del include) y el **inactivo**.
4. Arrancar el color **inactivo** (`systemctl start trading-api@<inactivo>`).
5. Poll `curl -fsS http://localhost:<inactivo>/health/live` hasta 200 (~2s).
6. **Reescribir el include ATÓMICAMENTE** (tmpfile + `mv`) → `server 127.0.0.1:<inactivo>;` + `nginx -t && nginx -s reload`.
7. `systemctl stop trading-api@<activo-viejo>` (drena vía `--timeout-graceful-shutdown 30`; el cliente ya va al nuevo).
8. Scanner: `systemctl restart trading-scanner` (reinicia ~15s pero **cero impacto de cliente** — ningún endpoint depende del proceso scanner; solo retrasa un ciclo de 300s).
9. Health-poll final apunta a **`/health/live`**, no a `/health`.
10. **Reconciliación de unit drift** (Serrano #9): un step que `diff` los `deploy/*.service` versionados contra los instalados en `/etc/systemd/system/` y falle (o reinstale + `daemon-reload`) si divergen — el invariante "API nunca migra" no debe depender de un archivo editado a mano una vez.

## 5. Contrato de frescura (#8)

- El **freshness owner** de `scanner_loop`/`screener_loop`/`sync_loop`/health-monitor/calibrator pasa de "lifespan de la API" a **`trading-scanner.service`**. Actualizar [[docs/superpowers/inventario-estado-vivo.md]] + el spec de liveness, y `mex log`. **Sin esto es violación del gate #8.**
- La liveness que la API reporta (`/health`, `/status`, `/`) se deriva de la DB (último scan ts) vía `LiveSnapshot`, no de `_scanner_state`. Un dato `rancio`/`muerto` se reporta honesto (el scanner-service caído → frescura `muerto`, no un mute falso).

## 6. Cutover de prod — zero-downtime y reversible

**El orden importa.** En todo el proceso el cliente nunca queda sin upstream.

1. **Mergear PRIMERO el PR de solo-código** (gates + `/health/live` + `scanner_main.py` + frescura-desde-DB + tests) por el deploy ACTUAL. En runtime nada cambia (el unit viejo no setea env → defaults = como hoy). Este es el **último deploy con el restart de siempre**. Verificar `/health` verde.
2. (Manual, server, sin downtime) Crear los 3 units + el include nginx (apuntando a `:8100`, el proceso viejo), **sin activarlos**. `daemon-reload`. Ampliar sudoers. Respaldar el `.conf` de nginx.
3. (Manual) Editar nginx → `upstream trading_api { server 127.0.0.1:8100; }`, cambiar los `proxy_pass`, añadir el location SSE. `nginx -t && nginx -s reload`. Cero impacto (aún apunta al proceso viejo en `:8100`).
4. **CUTOVER sin hueco** (el orden lo confirmó Halberg: cero hueco de cliente; el viejo `:8100` sirve hasta 4d). **Minimizar la ventana 4a→4d** — durante ella corren 2 scanners (transitorio):
   - (4a) `systemctl start trading-scanner` (dueño del schema). Verificar en journalctl que envía `READY=1`, los 5 threads arrancan y escribe scans.
   - (4b) `systemctl start trading-api@8101` (web-only, puerto libre — verificar `:8101` libre antes). Poll `:8101/health/live` hasta 200 (~2s).
   - (4c) Reescribir el include ATÓMICAMENTE → `:8101` + `nginx -t && nginx -s reload`. El cliente ahora va a `:8101` (sin scanner).
   - (4d) `systemctl stop trading-spacial` **inmediatamente tras 4c** (mata el segundo scanner in-process y libera `:8100`). **Desde aquí: un solo scanner.**
   - (4e) `systemctl start trading-api@8100`; poll `/health/live`; queda como color standby.
   - (4f) `systemctl disable trading-spacial`; verificar `is-active` == inactive (cierra el riesgo de doble-scanner por reboot). Unit en disco para rollback.
   - **Transitorio 2 scanners (4a→4d, segundos):** seguro contra corrupción (WAL + `BEGIN IMMEDIATE` serializan). Efectos lógicos aceptados en esa ventana: **notificación Telegram duplicada** (el dedupe es por-proceso en memoria + bypass `critical` — Serrano #3, Halberg) y posible doble-evaluación de stops. El cierre va por **`PositionClosure` (non-negotiable #1), idempotente/TOCTOU-guarded** → no hay doble-cierre; el riesgo es a lo sumo una notificación repetida. La ventana de segundos lo acota.
5. **Mergear el PR de `deploy.yml` blue-green.** Deploy de prueba (commit no-op) con `while true; do curl -s -o /dev/null -w '%{http_code}\n' https://trading.sdar.dev/api/health/live; sleep 0.5; done` corriendo → confirmar **CERO 502/503** en la ventana del swap. Confirmar en journalctl que **solo** el scanner-service loguea el arranque del scanner (un solo escritor).
6. Actualizar inventario/spec de liveness + `mex log`.

**Rollback** (en cualquier punto): reapuntar el include nginx → puerto del color sano + reload; o re-habilitar `trading-spacial` (en disco) + parar los 3 units nuevos + include → `:8100`. El código es backward-compatible (defaults = como hoy).

## 7. Tests (CI)

- Con `RUN_SCANNER=0` el lifespan **NO** registra threads en `_managed_threads` (debe fallar el merge si arranca el scanner).
- Con `SKIP_DB_INIT=1` el lifespan **NO** ejecuta `init_db` **NI `_bootstrap_first_user`** (spy/mock) → **ningún `BEGIN IMMEDIATE` en boot** de la API web-only (Serrano #13).
- `/scan` (POST) devuelve **409** cuando `RUN_SCANNER=0`.
- `/health/live` devuelve 200 con el scanner detenido (DB+schema ok); y **503/no-200** si falta una tabla canónica (schema incompleto).
- La liveness del scanner reportada por `/health`,`/status`,`/` se deriva de la DB (mock de scan ts viejo → `/health` 503 `rancio`/`muerto`; reciente → 200 `fresco`). `/ticker`,`/symbols` no leen `_scanner_state`.
- Gate: `python -m pytest tests/ -m "not network" -n auto -q`.

## 8. Riesgos residuales

- **Doble-escritor accidental por disciplina (no por lock):** si `trading-spacial` viejo revive (reboot que lo reactiva, alguien lo `enable`) junto al scanner-service, hay 2 scanners → scans/notificaciones Telegram duplicadas + 2 calibradores (degradación lógica, NO corrupción — `BEGIN IMMEDIATE` serializa). Mitigación bloqueante: `systemctl disable --now trading-spacial` + verificar `is-active`=inactive. **Mitigación dura recomendada (no bloqueante v1):** un *writer-lease* — heartbeat PID con TTL en una fila de DB que haga que un segundo scanner se rehúse a arrancar. Fuera de alcance de v1; anotado como follow-up.
- **SSE del copiloto se corta al PARAR el color viejo:** `nginx -s reload` NO los corta (drena), pero el `systemctl stop` del color sí, tras el graceful-shutdown window. El cliente SSE del frontend usa `fetch()+ReadableStream` (no `EventSource`) → NO auto-reconecta con Last-Event-ID; un stream cortado muestra error y el operador reintenta. **Degradación aceptada** (deploys infrecuentes; rara vez mid-turn). Mitigación: `--timeout-graceful-shutdown` alto (60-120s) en uvicorn.
- **Auto-cierre de stops (TP/SL/time-limit):** vive dentro de `execute_scan_for_symbol` → se mueve **entero** al scanner-service. Durante los ~10-15s del restart del scanner-service nadie vigila stops. NO es regresión (hoy ya se chequean 1×/ciclo de 300s, no continuo), pero un boot fallido del scanner deja stops sin cobertura hasta que `Restart=on-failure` lo levante. Vigilar con alerta de frescura.
- **`backup_db` truncado al kill** (Halberg #6): `db/connection.py:100-124` abre el backup sin `busy_timeout` y `src.backup(dst)` itera páginas; un SIGKILL a media-backup deja un `.db` truncado que la rotación trata como válido (bomba para rollback). **Fix:** escribir el backup a un tmpfile y `os.rename` al destino al completar (atómico) — un kill deja el tmpfile basura, nunca un backup "válido" parcial.
- **Sudoers acotado** (Serrano #10): conceder al runner de CI SOLO los comandos exactos con args fijos — `systemctl start/stop trading-api@8100`, `trading-api@8101`, `systemctl restart trading-scanner`, `systemctl is-active ...`, `systemctl daemon-reload`, `nginx -t`, `nginx -s reload`, y el `mv` del include a `/etc/nginx/conf.d/trading-upstream.conf`. NO un `systemctl *` abierto ni escritura libre a `/etc/nginx/`.
- **Alerta de frescura del scanner** (Serrano #11): el auto-cierre de stops vive en el scanner-service; un crash-loop lo deja sin vigilante. `/health` (que ahora gatea sobre frescura DB) es el hook: un monitor externo sobre `/health` 503 = scanner `muerto`/`rancio` → alerta. Definir el destinatario/umbral antes del cutover (no aspiracional).
- **Complejidad operativa permanente:** 3 units + include nginx editado por CI + sudoers acotado. El include apuntando a un color parado = 502; mitigado por el self-heal (paso 0) + health-gate estricto a `/health/live`. Drift repo↔server mitigado por la reconciliación del paso 10.
- **Techo arquitectónico sin tocar:** sigue siendo SQLite de un solo escritor en un solo nodo. Esto resuelve el dolor actual de forma proporcional; no escala a multi-nodo (eso sería Postgres, epic separado).

## 9. Fuera de alcance

- Writer-lease duro (heartbeat con TTL) — follow-up recomendado, no v1.
- Postgres / multi-nodo.
- Atomic-deploy de symlinks (PR #377, abandonado).
- Zero-downtime para SSE (best-effort con reconexión del cliente).

## 10. Mapa de archivos

**Código (PR 1 — solo-código, backward-compatible):**
- `btc_api.py` — gates `RUN_SCANNER`/`SKIP_DB_INIT` (con `_bootstrap_first_user` DENTRO del gate) en el lifespan (`:245-258`); `/scan` → 409 si `RUN_SCANNER!=1`; `/ticker`,`/symbols`,`/status`,`/` dejan de leer `_scanner_state` (liveness/symbols desde DB/config).
- `api/health.py` — `GET /health/live` (readiness, `SELECT 1 FROM users`); `/health` deriva frescura del scanner de la DB y gatea 200/503 sobre ella; quita el contador `errors` del contrato.
- `scanner_main.py` (NUEVO) — entrypoint del scanner-service: DDL + bootstrap + `sd_notify(READY=1)` + `start_scanner_thread` + handler SIGTERM/SIGINT + `wait()`.
- `scanner/runtime.py` — exportar `_thread_stop_event` (para el handler de `scanner_main`).
- `db/connection.py` — `backup_db` a tmpfile + `os.rename` (no deja backups truncados).
- (helper) un módulo chico para derivar la frescura del scanner de la DB (`MAX(ts)` de `scans`, counts) envuelta en `LiveSnapshot`.
- `tests/test_deploy_decouple.py` (NUEVO) — los tests de §7.

**Infra (PR 2 — deploy.yml; + setup manual one-time en server):**
- `.github/workflows/deploy.yml` — `cancel-in-progress: false`; rutina blue-green con self-heal (paso 0), verificación de puertos, escritura atómica del include, reconciliación de unit drift (§4.5).
- `deploy/trading-scanner.service` (`Type=notify`, `TimeoutStopSec=60`), `deploy/trading-api@.service` (`Type=notify`-aware vía `After/Wants`, `--timeout-graceful-shutdown 30`), `deploy/trading-upstream.conf` (NUEVOS, versionados como fuente de verdad).
- `deploy/sudoers-trading` (NUEVO) — el allow-list acotado de comandos para CI.
- `docs/superpowers/inventario-estado-vivo.md` — owner → `trading-scanner.service`.

## 11. Orden de ejecución

```
PR 1 (solo-código): gates + /health/live + frescura-DB + scanner_main.py + tests  → merge (último deploy "viejo")
  → setup manual server (3 units + nginx include, sin activar)
  → cutover zero-downtime (scanner-service → api@8101 → swap nginx → stop viejo → api@8100 standby → disable viejo)
  → PR 2 (deploy.yml blue-green) → merge → deploy de prueba con monitor de 503 == 0
  → actualizar inventario #8 + mex log
```
