# Spec — Conexión Binance por-tenant: SOLO LECTURA (reconciliación de la realidad)

**Fecha:** 2026-06-10 · **REV 3** (tras 2º audit de Adrian sobre REV 2: 2 BLOCKER + 4 HIGH + 3 MEDIUM + 1 LOW nuevos, todos direccionados) · **Estado:** PROPUESTO.
**Tipo:** integración de broker externo (read-only) + custodia de credenciales sensibles.
**Frontera (decisión de Samuel, 2026-06-10):** **SOLO LECTURA.** El sistema LEE la cuenta de Binance del tenant y refleja su realidad; NUNCA coloca ni cancela órdenes (ver §1). Única excepción tipada: la sonda de validación usa `/api/v3/order/test`, que **no coloca nada** (§2.4).
**Mercado (decisión de Samuel, 2026-06-10):** **v0.1 = SPOT únicamente.** Leer futuros exige `enableFutures`, indivisible e incluye trading (verificado en doc oficial: no hay "futuros read-only"; spot sí tiene `enableReading` puro). Futuros = v0.2 con su propia postura (§4.5, §7).
**Infra (decisión de Samuel, 2026-06-10):** VPS con **IP estática** → IP-whitelist obligatorio.
**Alcance partido:** v0.1 = FUNDACIÓN DE LECTURA SPOT (objetivo viernes 2026-06-12, tenant 2 = papá). v0.1.5 = display de staleness/mark_ts. v0.2 = futuros + cost-basis + auto-descubrimiento + plano vivo.
**Origen:** petición de Samuel — "para que la herramienta le sea totalmente funcional al papá necesita conexión directa con Binance vía API, que es donde ocurre todo".
**Funda:** junta del roster 2026-06-10 (7 lentes, convergieron sin Axiom-0) + 2 audits de Adrian (REV 1 y REV 2).
**Relacionado:** `2026-06-09-posiciones-externas-control-domain-spec.md` (este spec es su v0.2 de reconciliación; **enmienda CD-4**, preserva CD-1/CD-5, extiende CD-2), `2026-06-09-integracion-eje-conducta-spec.md` (la ley conducta⊥resultado que §5 protege).

---

## REV 3 — qué cambió (changelog del 2º audit de Adrian)

Adrian confirmó los **5 BLOCKERs de REV 1 CERRADOS**. REV 3 direcciona los nuevos que introdujeron los mecanismos de REV 2:

1. **BLOCKER-nuevo-1 (`entry=NO-DISPONIBLE` viola `entry_price NOT NULL`):** v0.1 **NO auto-crea filas** para holds spot no-registrados (no hay cost-basis en el endpoint de cuenta). El sync **reconcilia las filas EXTERNAL existentes** (qty desde Binance, detecta cierres) y **señala** holds no-registrados para que el operador los registre. `entry_price` sigue `NOT NULL`; cero cirugía al canon, cero entry inventado (§4.1).
2. **BLOCKER-nuevo-2 (cambio de firma de `price_lookup` rompe callsites/tests + `mark_ts` sin fuente):** v0.1 **NO cambia la firma de `compute_real_equity`**. El display de `mark_ts`/staleness se alinea con la diferición que el spec CD ya hizo a **v0.1.5**. v0.1 usa el marcado etiquetado existente (§4.6).
3. **HIGH (factory no forzable):** la garantía de tipo pasa a ser **estructural** vía un **CHECK** `market IS NOT NULL ⟹ control_domain='EXTERNAL'`. La columna `market` hace doble función (idempotencia + tipo). Un INSERT que setee `market` pero omita `control_domain` (defaulteando INTERNAL) **falla el CHECK** (§4.3).
4. **HIGH (sonda BNC-9 coloca orden):** la sonda usa **`/api/v3/order/test`** (valida permisos sin colocar). BNC-1 se precisa: "no coloca/cancela órdenes"; `order/test` no coloca (§2.4, BNC-1).
5. **HIGH (hueco del índice `market IS NULL`):** `market` es **NOT NULL para EXTERNAL** (factory obligatorio + el CHECK del punto 3 lo fuerza); ninguna EXTERNAL escapa de la idempotencia (§4.2/§4.3b).
6. **HIGH (ciclo de vida de `observed_closed_pending`):** flag **persistido**; **suprimido** cuando la credencial es no-`ACTIVE` (no se puede distinguir cerrado de ciego); **limpiado** cuando el balance reaparece (UPSERT); excluido del equity; lo resuelve un humano (§4.7).
7. **MEDIUM (AUTH_FAILED degrada remediación):** el **onboarding** desambigua (condiciones controladas: sabemos qué IP whitelisteamos, probamos lectura); solo el `-2015` de **runtime** colapsa a `AUTH_FAILED` con remediación ordenada (§4.4).
8. **MEDIUM (`mark_ts` sin fuente):** diferido con el display a v0.1.5 (punto 2). **MEDIUM (MultiFernet sin versión):** columna `key_version` añadida; v0.1 rotación = re-encrypt in-place (N pequeño), sin claim de "sin downtime" (§2.3). **LOW ("derivado o persistido"):** resuelto = **persistido** (§4.7).

---

## 0. Qué es / qué NO es

**Es:** una capa que (a) custodia cifrada una API key **read-only spot** por tenant, (b) lee balances spot con un cliente firmado, (c) **reconcilia las posiciones EXTERNAL ya registradas** (actualiza qty desde Binance, detecta cierres) y (d) **señala holds no-registrados** para que el operador los registre.

**NO es:**
- NO coloca ni cancela órdenes (frontera read-only; §1). La sonda usa `order/test` (no coloca).
- NO auto-crea filas para holds spot no-registrados (no hay cost-basis; §4.1) — los señala.
- NO auto-cierra (CD-5 intacto; cierre observado → flag + exclusión de equity, NO `closed`; §4.7).
- NO lee futuros / margin (v0.2).
- NO cambia la firma de `compute_real_equity` ni el display de staleness (v0.1.5; §4.6).
- NO websocket (polling; v0.2). NO UI multi-tenant (key inyectada por comando; v0.2). NO promete edge.

## 1. El tipado — "conectar la realidad de Binance" son CUATRO objetos (Voronov)

| Objeto | Dónde vive | v0.1 lo trata como |
|---|---|---|
| **La cuenta** (balances, permisos) | Binance (autoridad) | leído read-only (spot) |
| **La posición** (qty abierta) | Binance custodia | espejo → **ventana** (broker-confirmado); identidad = `(tenant,symbol,market,direction)` |
| **El fill** (ts/precio/fee de ejecución) | Binance (inmutable) | **NO modelado en v0.1** — identidad de fill (`orderId`) y cost-basis = v0.2 |
| **El acto** (decidir, aguantar) = la **conducta**, eje `i` | **el papá, NO Binance** | NO se toca; §5 lo protege |

**Principio (Voronov):** LEER concede `observar` (mueve "autoridad del dato", respeta CD-1); EJECUTAR concede `actuar` (mata CD-1, CD-5, No-Negociable #1 = otro sistema). "Binance es donde ocurre todo" es verdad para el resultado, falso para la conducta. v0.1 lee el resultado con fidelidad y tiene prohibido contaminar la conducta (§5).

## 2. Arquitectura de credenciales

### 2.1 Permisos en Binance — mínimo privilegio
Única key aceptada en v0.1: **`enableReading` PURA (spot read-only)**. `enableFutures` NO (indivisible-con-trading → v0.2). Spot/Margin Trading NO. **Withdrawals JAMÁS.** **IP-whitelist a la IP estática del VPS: OBLIGATORIO.** Efecto: peor caso de fuga = "leen el balance" (privacidad), no movimiento de fondos. Referente: 3Commas (DB sin cifrar, ~$20M). Prerequisito futuros (v0.2, para el dossier): la cuenta de futuros debe abrirse antes de crear la key; Portfolio Margin borra `enableFutures`.

### 2.2 Almacenamiento per-tenant
Tabla nueva, **NO** en `config.secrets.json` (global+texto plano), **NO** en `positions`:
```
binance_credentials (
    id, tenant_id NOT NULL,            -- = users.id
    api_key_public TEXT NOT NULL,      -- pública; no se devuelve completa
    secret_enc BLOB NOT NULL,          -- Fernet(secret); nunca en claro en reposo
    key_version INTEGER NOT NULL DEFAULT 1,  -- versión de master key (rotación, §2.3)
    scope_detected TEXT,               -- 'READ_ONLY_SPOT' (sonda §2.4)
    ip_whitelisted INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',  -- §4.4
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)
UNIQUE INDEX idx_binance_cred_tenant ON binance_credentials(tenant_id)
```
Molde de `capital`/`user_preferences` (una fila por tenant, UNIQUE INDEX, UPSERT).

### 2.3 Cifrado-at-rest (Fernet)
- **`cryptography.Fernet`** — encrypt-then-MAC (AES-128-CBC + HMAC-SHA256), versión + timestamp; **no es AEAD canónico, no soporta associated data** (no asumir AAD). Suficiente para un secreto opaco. Dependencia nueva auditada; hoy el repo no tiene cifrado simétrico.
- **Master key** (`TRADING_BINANCE_MASTER_KEY`, 32 bytes) en `EnvironmentFile` de systemd, `chmod 600`, owner del servicio, **excluida de backup/repo/CI**.
- **Alcance honesto:** protege DB-robada-sola, backup-filtrado, insider-read-only. NO protege server-fully-owned.
- **Política de pérdida (MEDIUM):** master key perdida ⟹ `secret_enc` irrecuperables **por diseño** ⟹ el papá **re-onboardea**. Sin recovery ni custodia fuera-de-banda en v0.1. Se **declara** en el dossier (el operador no debe asumir que el backup la cubre — la excluye a propósito).
- **Rotación de master key (v0.1):** re-encrypt in-place de las N filas (N pequeño) vía tool one-shot, incrementando `key_version`. La columna `key_version` permite identificar con qué master key se cifró cada fila. NO se promete "sin downtime" en v0.1 (`MultiFernet` = mejora v0.2 si hace falta).
- Descifrar lo más tarde posible (variable local al firmar); nunca en reposo descifrada, a disco o a log.

### 2.4 Invariantes de no-fuga (tests/candados verdes ANTES de aceptar una key real)
1. NUNCA a logs (set de strip propio para esta tabla). 2. NUNCA al frontend (GET devuelve metadatos: existe/no, últimos 4 de la api_key pública, `scope_detected`, `ip_whitelisted`, `status`; nunca la secret). 3. NUNCA a git (test/CI anti-patrón de key Binance en fixtures). 4. NUNCA en excepción/traceback (test: provocar error, assert key/firma ausente de `str(exc)`). 5. Constant-time donde aplique. 6. Cifrar ANTES del INSERT, descifrar justo antes de firmar.

**Sonda de scope (HIGH-7 de REV 1, refinada):** condición DECIDIBLE y SEGURA. Al onboarding: (a) `GET /api/v3/account` firmado responde OK (lectura spot funciona); (b) `POST /api/v3/order/test` con una orden **bien-formada** (para no fallar por parámetro) → si devuelve `-2015`/permiso-denegado, **trading deshabilitado = correcto**; si devuelve éxito (`{}`), **la key tiene trading → RECHAZAR la key**. `order/test` **NO coloca ninguna orden** (valida y descarta); es la única excepción tipada a "ningún método coloca órdenes" (BNC-1). (c) lectura `/fapi` no-autorizada/vacía confirma `enableFutures` off. Caveat honesto: el scope perfecto es inverificable desde fuera; `order/test` es el máximo decidible sin colocar nada.

### 2.5 Rotación / revocación de la key del tenant
Rotación: key nueva → pega → cifra + UPSERT por `tenant_id` + sonda → borra la vieja en Binance. Una activa; sin limbo. **Revocación — fuente de verdad = Binance** (borrar la key en su panel; instantáneo); en el sistema `DELETE`/flag detiene el firmado.

## 3. El cliente firmado (Halberg — subsistema aislado)
SEPARADO del adapter público de klines. **NO reusar `fetch_with_failover`** (Bybit no tiene la cuenta = category error). Firma HMAC-SHA256 sobre `totalParams`, header `X-MBX-APIKEY`, `timestamp` + `recvWindow=5000`. **Sync de reloj obligatorio** contra `/api/v3/time` + NTP (sin esto, deriva > recvWindow → `-1021` tumba todos los requests firmados en bloque). Rate budget: 6000 weight/min **por IP**; leer `X-MBX-USED-WEIGHT-1M` y backoff; bucket SEPARADO del de klines. Endpoints v0.1: `get_spot_account()` (`/api/v3/account`) + la sonda `order/test`. Cero métodos que coloquen/cancelen órdenes reales.

## 4. La reconciliación (spot, v0.1)

### 4.1 Autoridad por campo + NO auto-creación (cierra BLOCKER-nuevo-1)
| Campo | Autoridad | Razón |
|---|---|---|
| **existencia** (hold presente/ausente) | **Binance** | es la realidad; v0.1 marca, no cierra (§4.7) |
| **qty** | **Binance** | el balance real es el hecho; el sync actualiza la fila existente |
| **direction** | **sistema = LONG** | en spot solo se está LONG; no hay SHORT |
| **entry_price / entry_ts** | **tecleado / primera observación; NO Binance** | el endpoint de cuenta spot no da cost-basis; permanece inmutable |
| **control_domain** | **siempre EXTERNAL** | forzado por CHECK estructural (§4.3) |

**v0.1 NO auto-crea filas** para holds que no tienen fila registrada (`entry_price` es `NOT NULL` y no hay cost-basis para inventarlo honestamente). El sync:
- **Reconcilia** cada fila EXTERNAL spot existente: actualiza `qty` desde el balance de Binance; detecta cierre (§4.7).
- **Señala** (notificación/flag de "hold no-registrado detectado") los balances spot > dust que no tienen fila — el operador los registra a mano con su entry (vía el factory existente). Auto-descubrimiento con cost-basis = v0.2 (historial de trades).

Consecuencia honesta: el equity v0.1 refleja las posiciones **registradas** (mantenidas frescas), no todo balance arbitrario. Para el papá (pocas posiciones, conocidas) esto entrega el valor central: dejar de re-teclear qty + detectar cierres.

### 4.2 Identidad de posición e idempotencia (cierra BLOCKER-1 de REV 1)
Una posición spot se identifica por `(tenant_id, symbol, market, direction)` — NO por `orderId` (orderId/tradeId = fill = v0.2). El sync hace UPSERT por esa tupla; re-sincronizar actualiza la misma fila, no inserta otra. `market` es **NOT NULL para toda fila EXTERNAL** (forzado por §4.3) → ninguna escapa de la idempotencia.

### 4.3 Garantía de tipo ESTRUCTURAL (cierra BLOCKER-2 de REV 1 + HIGH factory + HIGH hueco-índice)
La exención de auto-cierre vive en un literal (`api/positions.py:163`) y el default de la columna es `'INTERNAL'`. La garantía de que ninguna fila de Binance entre como INTERNAL **NO es el factory** (eso es convención, el mismo error del helper del spec CD) sino un **CHECK a nivel de DB**:

> **`CHECK (market IS NULL OR control_domain = 'EXTERNAL')`**

La columna `market` se setea SOLO en filas nacidas del sync de Binance (las INTERNAL/manuales tienen `market=NULL`). El invariante garantiza: **cualquier fila con `market` seteado es EXTERNAL**. Un INSERT/UPDATE que ponga `market` pero deje `control_domain` distinto de EXTERNAL **aborta** ruidosamente — la landmine REALIZED-falso se vuelve **estructuralmente imposible** para el camino de Binance, no por convención. El factory único sigue siendo la ruta recomendada (y testeada), pero la garantía dura es a nivel de DB.

**Realización (ver plan, Task 3):** SQLite no permite `ALTER TABLE ADD CONSTRAINT CHECK` sin recrear la tabla (el canon documenta que unificar los 4 `CREATE TABLE positions_new` "es un proyecto, no un PR"). Por eso el invariante se implementa con un **TRIGGER `BEFORE INSERT/UPDATE`** equivalente (`trg_market_implies_external_*`), que además cubre UPDATE, sin recrear `positions`. El efecto es idéntico al CHECK descrito. (Se descarta el `scan_id IS NULL ⟹ EXTERNAL` de REV 1: era falso — una INTERNAL manual lleva `scan_id=NULL`, `episode.py:83`.)

### 4.3b Cambios de schema (lección CD-6/B2)
Columna `positions.market TEXT` (dominio `{SPOT, FUTURES}`; NULL para INTERNAL/manuales). Debe: añadirse a `CANONICAL_POSITIONS_COLUMNS` (test de canonicidad actualizado con justificación en el PR); añadirse a cada `CREATE TABLE positions_new` de las migraciones que recrean + sus `TARGET_COLS`; ALTER idempotente para DBs migradas. Más:
- el CHECK `(market IS NULL OR control_domain='EXTERNAL')` (§4.3) en el canon y recreaciones.
- índice de idempotencia: `UNIQUE(tenant_id, symbol, market, direction) WHERE control_domain='EXTERNAL'`. (Como toda EXTERNAL synced tiene `market` NOT NULL por el CHECK + factory, no hay escape.)

### 4.4 Estados de credencial (HIGH-6: desambiguación al onboarding)
`status` fail-closed, visible al papá:

| status | gatillo | comportamiento |
|---|---|---|
| `ACTIVE` | sonda OK | sync normal |
| `AUTH_FAILED` | `-2015` en **runtime** | Binance no distingue key/IP/permiso en runtime → mensaje que nombra las 3 causas, ordenado por probabilidad. **Al onboarding sí se desambigua** (sabemos la IP whitelisteada, probamos lectura, corremos la sonda §2.4) → el papá recibe la causa precisa la primera vez |
| `REVOKED` | key borrada en Binance | detener sync; última lectura **stale** |
| `RATE_BANNED` | `-1003`/418/429 | backoff; NO reintento eterno |
| `CLOCK_SKEW` | `-1021` | re-sync de reloj; alerta |

Una credencial no-`ACTIVE` NUNCA muestra holds viejos como vivos, y **suprime** el cómputo de cierre-observado (§4.7).

### 4.5 Spot únicamente (cierra BLOCKER-3 de REV 1)
v0.1 = SPOT. `compute_real_equity` (`cash + Σ(qty×precio)`) ya modela spot, **sin cambios de firma** (§4.6). Balance spot > dust con fila registrada → reconciliado; sin fila → señalado (§4.1). Futuros = v0.2 (key trading-capable IP-encadenada + modelo de equity de margen/PnL/liquidación + `/fapi`).

### 4.6 Marcado de precio y staleness (cierra BLOCKER-nuevo-2)
v0.1 **NO cambia la firma de `compute_real_equity`** (`price_lookup: Mapping[str,float]` se mantiene; cambiarla rompería `_snapshot_prices`, `health.py:1173/1236` y los tests, y `_PRICE_CACHE` no porta `mark_ts`). El equity se marca con el precio del último scan (como hoy), **etiquetado** "al último scan; no es tu saldo en Binance" (igual que el spec CD §5). El display explícito de `mark_ts` + banda de staleness + el cambio de `_PRICE_CACHE` para portar timestamp = **v0.1.5** (alineado con la diferición que el spec CD ya hizo). Símbolo sin precio → `missing_prices` (ya existe), equity **declarado incompleto**, nunca inventado.

### 4.7 El cierre observado (cierra BLOCKER-5 de REV 1 + HIGH ciclo-de-vida; CD-5 intacto)
Cuando Binance ya no reporta el hold pero la DB tiene la fila `open` + credencial `ACTIVE`:
- **NO escribe `closed`** (No-Negociable #1 intacto; la fila sigue `status='open'`).
- Marca un flag `observed_closed_pending` (NO un estado del lifecycle). **Realización (ver plan, Task 5/6):** el sync actualiza `qty` desde Binance (autoridad §4.1); al cerrar el papá, el balance → 0 ⟹ `qty` → 0, así que el equity-fantasma se cierra **en la raíz** por reconciliación de qty. El flag se **deriva** de `qty ≤ dust` con credencial `ACTIVE` (señal "confirma el cierre"), no requiere columna persistida.
- **EXCLUYE la fila de `compute_real_equity`** (`qty ≤ 0` ⟹ no entra al SELECT, contribución → 0): el equity no se infla con un hold fantasma.
- **Ciclo de vida (resuelto):** el flag **solo se computa con credencial `ACTIVE`** (con no-`ACTIVE` no se puede distinguir "cerrado" de "ciego" → suprimido, §4.4); **se limpia** cuando el balance reaparece (el UPSERT del §4.2 encuentra la posición de nuevo); lo **resuelve un humano** (el papá confirma y cierra vía `PositionClosure(USER)`, o re-registra si fue error). Una fila puede quedar pending hasta que el humano actúe — es aceptable (está señalada **y** excluida del equity; no es bug de correctitud, es un nudge).
El tipo `PositionClosure(mode=OBSERVED)` = v0.2.

### 4.8 Merge de bootstrap (cierra BLOCKER-4 de REV 1)
Las 2 filas tecleadas (BTC LONG @64390, ETH LONG @1700 del spec CD) **si son spot**: al primer sync se **adoptan** por identidad `(tenant=2, symbol, market=SPOT, direction=LONG)` (no por `entry_ts` → sin duplicar). El sync setea `market='SPOT'`, actualiza `qty` desde Binance, **conserva** `entry_price`/`entry_ts` tecleados. **Si son FUTUROS:** el sync spot no las encuentra → permanecen intactas hasta v0.2 → **pregunta abierta §10.1.**

## 5. Guardarraíles ontológicos — la ley conducta⊥resultado (Voronov + Null Vale)
- **CD-2-EXT (presentación):** el equity/balance vivo NO comparte plano de lectura con el `EpisodioDeConducción`. Física de la atención: cuando el resultado es la cifra más fresca, la conducta se vuelve invisible. El instrumento no debe degradarse a tablero de P&L.
- **No acuñar conducta automáticamente:** v0.1 no marca cierres observados como `exit_reason='MANUAL'` (un cierre observado/liquidación no es acto deliberado); la reconciliación no acuña lecturas de conducta.
- **Ruido de exchange ≠ acto:** filtrar a holds > umbral de dust; rebalanceos/dust/conversiones no entran como actos de conducta.

## 6. Enmienda a los invariantes del spec CD (este es su v0.2)
- **CD-4 — ENMENDADO. CD-4′:** *una fila EXTERNAL con credencial `ACTIVE` es ventana broker-confirmada para existencia y qty (autoridad = Binance, §4.1); su `entry_price`/`entry_ts` permanecen de la primera observación (spot no da cost-basis); si Binance reporta el hold ausente, se marca `observed_closed_pending` y se excluye del equity (§4.7); con credencial no-`ACTIVE`, revierte a espejo no-autoritativo, se marca stale y no se computa cierre-observado.*
- **CD-5 — PRESERVADO** (§4.7). **CD-1 — PRESERVADO** (leer ≠ actuar). **CD-2 — EXTENDIDO** por CD-2-EXT (§5).

## 7. Alcance
- **v0.1 (viernes 06-12):** tabla `binance_credentials` (con `key_version`) + Fernet + invariantes de no-fuga (tests verdes) + sonda `order/test`; cliente firmado read-only spot (clock-sync, rate budget propio); columna `positions.market` + CHECK `(market IS NULL OR control_domain='EXTERNAL')` + índice de idempotencia (canon + recreación); sync que reconcilia EXTERNAL spot del tenant 2 (qty autoridad-Binance, detecta cierres) y señala holds no-registrados; estados de credencial fail-closed; `observed_closed_pending` (persistido, excluido del equity, suprimido con credencial no-ACTIVE); merge de bootstrap (§4.8). Detrás de flag `binance_sync_enabled` por-tenant; si el sync falla, el scan NO cae.
  - **Línea de corte (Cassian) si el viernes aprieta:** cablear el sync al ciclo de scan (correrlo a mano una vez al día ya entrega "dejar de teclear"); el auto-loop es v0.1.1.
- **v0.1.5:** display de `mark_ts`/staleness (cambia `_PRICE_CACHE` para portar ts; sin tocar la firma de `compute_real_equity` en v0.1).
- **v0.2:** futuros (key trading-capable IP-encadenada + equity de margen/PnL/liquidación + `/fapi`); cost-basis + auto-descubrimiento de holds (historial de trades, idempotencia por `orderId`); user-data-stream (websocket); UI multi-tenant; `PositionClosure(mode=OBSERVED)`; partición de rate budget multi-tenant; `MultiFernet`.

## 8. Invariantes (BNC-*)
- **BNC-1.** La key de v0.1 es `enableReading` pura (spot read-only): rechaza trading/futures/withdrawal. Ningún método **coloca ni cancela** órdenes; la única llamada al dominio de órdenes es `order/test`, que **valida sin colocar**.
- **BNC-2.** La secret nunca en reposo sin cifrar, nunca a log/frontend/git/traceback; se descifra solo en memoria al firmar.
- **BNC-3.** Credenciales por-tenant en `binance_credentials` (cifradas), nunca en `config.secrets.json` ni en `positions`.
- **BNC-4 (estructural).** El CHECK `(market IS NULL OR control_domain='EXTERNAL')` hace imposible que una fila con `market` seteado sea INTERNAL; toda fila del sync setea `market` ⟹ nunca puede entrar como INTERNAL. (No depende de convención de callsite.)
- **BNC-5.** Idempotencia de posición por `(tenant_id, symbol, market, direction)` con `market` NOT NULL para EXTERNAL; nunca por `entry_ts`. (orderId/fill = v0.2.)
- **BNC-6.** Credencial no-`ACTIVE` nunca presenta holds viejos como vivos ni computa cierre-observado; fail-closed.
- **BNC-7.** Cliente firmado aislado: clock-sync contra `/api/v3/time`, rate budget **por-IP** (v0.1 = un tenant; partición multi-tenant = v0.2), sin failover a otro broker.
- **BNC-8.** El equity vivo no comparte plano de lectura con la conducta (CD-2-EXT); los cierres observados no acuñan `exit_reason` de conducta.
- **BNC-9 (precondición de despliegue, DECIDIBLE).** No se acepta una key real hasta verdes: (a) sonda §2.4 (lectura OK + `order/test` rechazado-por-permiso + `/fapi` no-autorizado); (b) IP-whitelist aplicada; (c) Fernet con master key fuera de backup/DB; (d) los 6 invariantes de no-fuga (§2.4). Caveat: scope perfecto inverificable; `order/test` es el máximo decidible sin colocar nada.
- **BNC-10.** Una fila `observed_closed_pending` se excluye del equity, se computa solo con credencial `ACTIVE`, se limpia al reaparecer el balance, y nunca pasa a `closed`/`cancelled` por el sistema.
- **BNC-11.** v0.1 no cambia la firma de `compute_real_equity` ni el contrato de `price_lookup` (el display de staleness es v0.1.5).

## 9. Consistencia cross-documento
- Spec CD §7 v0.2 ("reconciliación real ledger↔Binance"): **este spec la realiza** (spot/abiertas; cost-basis + correspondencia de cierre = v0.2). Spec CD §5 ya difirió el display de staleness a v0.1.5 — BNC-11 se alinea.
- Spec-conducta: `apertura_discrecional ← scan_id IS NULL` se mantiene; el sync no rellena `scan_id`. El CHECK estructural usa `market`, no `scan_id` → no toca la primitiva de conducta.
- `compute_real_equity` spot-only y su firma intacta en v0.1 — consistentes con `health.py:1173/1236`, `_snapshot_prices` (`kill_switch_v2_shadow.py:39`) y `test_real_equity.py`. Futuros y staleness = sucesores en v0.2/v0.1.5.
- `entry_price NOT NULL` (canon) preservado: v0.1 no crea filas sin entry (§4.1).

## 10. Preguntas abiertas (residuales)
1. **¿Las 2 filas conocidas del papá (BTC/ETH) son spot o futuros?** — determina si v0.1 las adopta (§4.8) o esperan a v0.2.
2. Umbral de dust (§4.1/§5) y umbral de staleness (v0.1.5, sugerido 2× scan_interval).
3. ¿El sync corre en el ciclo de scan (~5min) o como job separado? (línea de corte de Cassian.)
4. La señal de "hold no-registrado detectado" (§4.1): ¿notificación (Telegram/pull) o solo flag en la vista? (recomendado: pull, coherente con el panel de disciplina.)

## 11. Kill del spec
Si una verificación encuentra que (a) la secret se persiste/loguea sin cifrar, (b) el sistema escribe `closed`/`cancelled` sobre una EXTERNAL por su cuenta, (c) una fila de Binance entra como INTERNAL (el CHECK §4.3 debe impedirlo), (d) la idempotencia depende de `entry_ts`, (e) el equity vivo se co-renderiza con la conducta, (f) la key v0.1 tiene trading/futures/withdrawal habilitado, (g) el cliente firmado reusa el failover a otro broker, (h) una `observed_closed_pending` queda sumando al equity o se computa con credencial no-ACTIVE, (i) la sonda coloca una orden real (debe ser `order/test`), o (j) se cambia la firma de `compute_real_equity` en v0.1 — esa pieza se corta o re-tipa antes de codificar.
