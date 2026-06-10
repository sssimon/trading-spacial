# Spec — Binance v0.2: auto-creación de holdings por OBSERVABILIDAD (procedencia AUTO_DERIVED + señal de riesgo)

**Fecha:** 2026-06-10 · **REV 3** (tras 2º audit de Adrian sobre REV 2: BLOCKER-1/2/3 de REV1 CERRADOS; 1 BLOCKER nuevo F1 + HIGH F2/F4/F5 + MEDIUM, todos direccionados) · **Estado:** PROPUESTO.

## REV 3 — changelog del 2º audit de Adrian
- **F1 (BLOCKER — §7 sin precio para no-roster):** la señal de riesgo obtiene precios **de los símbolos tenidos** vía Binance público (`/api/v3/ticker/price`, no solo el `_PRICE_CACHE` de 10 símbolos) y **SE ABSTIENE** (estado `no_valuado`, NO "sin riesgo") si falta precio (§7).
- **F2 (HIGH — entry_ts salta en round-trip):** `entry_ts` = ts del fill que **inició el holding continuo actual** (último cruce 0→>0). Resetea en un round-trip completo — y eso es CORRECTO (un recompra ES un holding nuevo) (§3).
- **F4/F5 (HIGH — adopción destruía entry tecleado + colisión de índice):** **NO se adopta.** Las 2 filas manuales (BTC/ETH) se quedan `OPERATOR` (su entry tecleado intacto, en conducta). Se auto-derivan **solo símbolos SIN fila existente** (holds nuevos: BNB/PEPE…). Cumple la meta (el papá no registra los nuevos) y esquiva el overwrite irreversible + la colisión market NULL→SPOT (§5).
- **F8 (MEDIUM — ACB truncado en sync parcial):** si la paginación de `myTrades` de un símbolo viene incompleta (429/418), **NO se persiste un ACB truncado** — se marca el símbolo `ingest_incompleto` y se omite esa actualización (§3, §4).
- **F9 (MEDIUM — descubrimiento solo USDT):** enumera las 4 quotes (`USDT/USDC/BUSD/FDUSD`) como `reconcile_spot`; si un hold no tiene trades en ninguna → ACB `no_reconstruible`, se abstiene (no fabrica) (§4).
- **F6 (MEDIUM — migración origin CREATE vs ALTER):** `origin` sigue el patrón EXACTO de `market`/`control_domain` (v0.1 Task 3): ALTER PRAGMA-guarded + enhebrado en los 4 `CREATE positions_new` + `TARGET_COLS` (select-expr fallback `'SIGNAL'`). El ALTER guarded evita el "duplicate column" en fresh-DB (§2).
- **F7 (MEDIUM — backfill mislabel EXTERNAL+scan_id):** backfill con guarda extra `AND scan_id IS NULL` (§2).
- **F3 (HIGH — staleness/Earn):** declarado: el equity es "al último sync" (cadencia manual/periódica v0.2); un hold movido a Earn cae del equity spot (no está en spot) — su valor en Earn NO se refleja (Earn diferido). Se documenta, no se oculta (§6).

---

**Fecha-orig:** 2026-06-10 · REV 2 (tras audit de Adrian sobre REV 1: 3 BLOCKER nuevos + HIGH/MEDIUM, todos direccionados) · **Estado:** PROPUESTO.
**Tipo:** extensión de modelo de dominio (eje de procedencia + señal de riesgo de holding) + ingest de historial de trades (read-only).
**Frontera:** SOLO LECTURA (v0.1). v0.2 = SPOT. Futuros y `PositionClosure(mode=OBSERVED)` quedan FUERA (§8).
**Reencuadre (Samuel):** el objetivo es OBSERVABILIDAD. Observabilidad ⊥ conducta (§1).
**Keystone (Voronov):** una fila auto-derivada NO es un acto del operador; necesita un eje de procedencia (`origin`) para que el sistema CONFIESE que la fabricó él, y así reflejarla en el equity pero EXCLUIRLA del instrumento de conducta (§2).
**Decisión de Samuel (Hallazgo 5 de Adrian):** sobre un holding observado que está underwater/aguantado, el sistema **observa Y levanta una bandera de RIESGO** (hecho del holding, no acto inventado) — preserva el valor original ("violación=disciplina") sin la mentira ontológica (§7).
**Funda:** junta v0.2 (Voronov/Serrano/Null Vale/Cassian) + 2 audits de Adrian + grounding (Binance trade-history API + mapa de consumidores del read-model de conducta).
**Relacionado:** `2026-06-10-conexion-binance-solo-lectura-spec.md` (v0.1), `2026-06-09-integracion-eje-conducta-spec.md` (read-model conducta), `2026-06-09-posiciones-externas-control-domain-spec.md` (CD-1..6).

---

## REV 2 — qué cambió (changelog del audit de Adrian sobre REV 1)

1. **BLOCKER-1 (exclusión apuntaba a `episode.py`, que es proyección pura):** el filtro `origin` va en el **QUERY del fetcher**, no en la función pura. Grounding confirmó: el read-model de conducta es **CLI-only**, lo alimenta UNA query (`tools/tenant_realization/report.py:_QUERY`); el otro consumidor es `api/agent/tools/handlers.py::get_closed_trades`. Ahí va el filtro (§2). El equity NO se filtra.
2. **BLOCKER-2/3 (venta vs transferencia; cobertura de sell-trades):** **DIFERIDO.** v0.2 NO auto-clasifica venta vs transfer. Se mantiene el flag de v0.1 (`qty→0` ⟹ marca de revisión) + **el humano disambigua** (sabe si vendió o movió a Earn). Sin estado nuevo, sin pedir trades de símbolos en balance-0 (§6).
3. **HIGH-4 (cursor incremental sin contrato):** **ELIMINADO.** v0.2 **recomputa el ACB del historial completo en cada sync** (paginar `fromId` 0→fin por símbolo) — idempotente, sin watermark ni estado-ACB persistido. El costo (decenas de llamadas) es aceptable para un sync periódico/manual (§3, §4).
4. **HIGH-5 (adopción saca de conducta los holds underwater):** resuelto por la decisión de Samuel — la adopción OPERATOR→AUTO_DERIVED procede, pero el valor "underwater sin stop" se preserva en la **señal de riesgo de holding** (§7), que lee hechos sin fingir acto.
5. **HIGH-9 (backfill por EXTERNAL re-etiqueta AUTO_DERIVED):** el backfill se predica para no tocar AUTO_DERIVED (§2): `WHERE control_domain='EXTERNAL' AND origin='SIGNAL'` (solo las legacy default-etiquetadas), idempotente frente a re-corridas de init_db.
6. **MEDIUM-8 (decisiones de build disfrazadas de §10):** `origin` = **`TEXT NOT NULL DEFAULT 'SIGNAL'`** (decidido, §2). La relación derivada↔manual = decidida (adopta + señal de riesgo).
7. **MEDIUM-6/7/10:** comisiones BNB (§3, fuente de precio flag); `size_usd` de AUTO_DERIVED recomputado, inerte en conducta (excluida) (§3); umbral unificado a minNotional para inclusión Y cierre (§4, §6).

---

## 0. Qué es / qué NO es

**Es:** capa de OBSERVABILIDAD que (a) descubre los holdings spot reales del papá desde su cuenta, (b) reconstruye su cost-basis (ACB) recomputando el historial completo de trades, (c) **auto-crea** las filas EXTERNAL marcándolas `origin='AUTO_DERIVED'` (equity/holdings reflejan la realidad sin tecleo), y (d) levanta una **señal de riesgo** sobre los holdings underwater/aguantados (hecho, no acto).

**NO es:**
- NO alimenta el instrumento de conducta. Las filas `AUTO_DERIVED` quedan EXCLUIDAS del read-model de conducta (§2).
- NO cierra posiciones ni auto-clasifica venta-vs-transferencia. Se mantiene el flag `qty→0` de v0.1 + revisión humana (§6). `PositionClosure(mode=OBSERVED)` DIFERIDO (§8).
- NO lee futuros, NO lee Earn (LD*), NO reconstruye posiciones ya cerradas (§4, §8).
- NO cambia la firma de `compute_real_equity` (BNC-11). NO usa cursor incremental (recomputa completo, §3).

## 1. El reencuadre — observabilidad ⊥ conducta (Samuel)
Dos planos (ley del 06-09): **observabilidad** = lo que el papá TIENE (resultado/realidad); **conducta** = lo que DECIDIÓ (acto, eje `i`). La auto-creación produce datos de observabilidad. La marca de procedencia (§2) mantiene los planos separados. La señal de riesgo (§7) vive en el plano de observabilidad: lee HECHOS del holding, nunca infiere un acto.

## 2. El eje de PROCEDENCIA `origin` (keystone — cierra BLOCKER-1/4/5/9, MEDIUM-8)
Columna nueva `positions.origin TEXT NOT NULL DEFAULT 'SIGNAL'`, dominio `{SIGNAL, OPERATOR, AUTO_DERIVED}`:
- `SIGNAL` — nacida de un scan (INTERNAL).
- `OPERATOR` — registrada deliberadamente por el operador (EXTERNAL manual).
- `AUTO_DERIVED` — fabricada por el sistema desde el trade history (v0.2).

**Schema (lección `market`/CD-6):** añadir a `CANONICAL_POSITIONS_COLUMNS` + test de canonicidad; enhebrar en los 4 `CREATE TABLE positions_new` + `TARGET_COLS` (select-expr: copiar si existe, si no `'SIGNAL'`); migración idempotente.

**Backfill (idempotente, NO re-etiqueta AUTO_DERIVED — HIGH-9):** una sola vez, `UPDATE positions SET origin='OPERATOR' WHERE control_domain='EXTERNAL' AND origin='SIGNAL'`. Solo toca las filas EXTERNAL legacy que el `DEFAULT 'SIGNAL'` etiquetó mal; NUNCA una AUTO_DERIVED (esas nacen con `origin='AUTO_DERIVED'` explícito). Re-correr init_db es seguro (la condición `origin='SIGNAL'` ya no matchea las OPERATOR ni las AUTO_DERIVED).

**Dónde se filtra `origin` (BLOCKER-1 — el filtro va en el QUERY, NO en `episode.py` que es proyección pura):**
- **AÑADIR `AND origin IN ('SIGNAL','OPERATOR')`** a: `tools/tenant_realization/report.py:_QUERY` (el único fetcher del read-model de conducta) y `api/agent/tools/handlers.py::get_closed_trades` (contexto de trades cerrados del copiloto — que no razone sobre filas fabricadas).
- **NO filtrar** (incluyen AUTO_DERIVED): `api/equity.py::compute_real_equity` y `binance_sync.py::reconcile_spot` (observabilidad). 
- `health.py::compute_rolling_metrics` (no filtra control_domain hoy): si es user-facing, alinear con conducta (excluir AUTO_DERIVED) — **§11 abierta**.
- `episode.py::project_conduct` queda intacta (pura); como el fetcher ya no le pasa AUTO_DERIVED, su `apertura_discrecional ← scan_id IS NULL` solo aplica a SIGNAL/OPERATOR (correcto).

## 3. Cost-basis ACB — recomputado del historial completo (cierra HIGH-4, MEDIUM-5/7/11)
- **Recomputar en cada sync, sin cursor ni estado persistido:** por símbolo, paginar `myTrades` `fromId` 0→fin (limit 1000, weight 20). Recalcular `qty_viva = Σbuys − Σsells`, `costo_remanente` (ACB), `avg_entry = costo_remanente / qty_viva`. Idempotente por construcción (re-correr da el mismo resultado). El cursor incremental queda como optimización POST-SHIP si el volumen lo exige.
- **ACB truncado = NO se persiste (REV 3, cierra F8).** Un ACB sobre un PREFIJO de la historia (paginación cortada por 429/418) no es "incompleto", es **incorrecto** (Σbuys parcial con Σsells completo da qty/ACB sin sentido). Si la paginación de un símbolo no llegó a fin → marcar `ingest_incompleto`, **omitir** la actualización de ese símbolo ese ciclo (deja el valor previo o lo deja sin valuar; §7 se abstiene). Solo se persiste un ACB de historia COMPLETA.
- **Comisiones:** `commissionAsset`==base → descuenta de qty; ==quote → suma al costo; ==BNB/otro → convertir a quote (precio BNB/quote del instante; fuente exacta = **§11 abierta**, best-effort si no hay precio).
- **`entry_price` y `size_usd` (= qty×ACB) son reconstrucciones** (BNC-13): mutan con fills nuevos. Inertes para conducta (excluida, §2). El equity usa qty×precio-vivo (no `entry_price`/`size_usd`). La señal de riesgo (§7) usa el ACB como costo de referencia (es justo lo que se quiere: P&L no realizado vs costo promedio).
- **`entry_ts` = ts del fill que INICIÓ el holding continuo actual (REV 3, cierra F2)** — el último cruce de qty acumulada `0 → >0` que sigue vivo. NO el primer-fill-de-siempre. Resetea en un round-trip completo (vendió-todo-y-recompró) — y eso es CORRECTO: un recompra ES un holding nuevo, su `age_days` debe contar desde ahí. Estable mientras el holding sea continuo.

## 4. Descubrimiento + scope (cierra BLOCKER-6, MEDIUM-12/13, HIGH-9-cobertura)
- **Descubrimiento:** `GET /api/v3/account` → assets con saldo > 0 → derivar pares con **las 4 quotes** (`ASSET+{USDT,USDC,BUSD,FDUSD}`, como `binance_sync._QUOTES` — REV 3, cierra F9; NO solo USDT) → `myTrades` de los pares con trades. `myTrades` exige `symbol`. Un hold cuyos trades no aparecen en ninguna quote → ACB `no_reconstruible` → se ABSTIENE (no fabrica un entry).
- **Scope = TODOS los holds con valor real** (no solo direccional): como van `AUTO_DERIVED` y NO contaminan conducta (§2), reflejar la cartera completa es seguro y es el objetivo (observabilidad). Inclusión: `qty × precio ≥ minNotional` (filtro `NOTIONAL` de `exchangeInfo` por símbolo — unificado con el umbral de cierre, MEDIUM-13). Dust fuera.
- **Earn (LD*) DIFERIDO** (BLOCKER-7): wallet aparte, sin trades, sin entry; siguen `untracked` (v0.1).
- **Foto, no historial (HIGH-9/BNC-16):** solo reconstruye holds VIVOS (descubiertos por balance). Posiciones ya cerradas no se reconstruyen — se declara explícito; aceptable porque conducta no lee AUTO_DERIVED.
- **Sync parcial (MEDIUM-12):** resumable por símbolo; un símbolo no leído (429/418) NO deriva flag de cierre (marcar "ingest incompleto").

## 5. Identidad e idempotencia (cierra BLOCKER-1-identidad/10)
- Identidad de fila = `(tenant_id, symbol, market, direction)` (índice `idx_positions_external_identity` de v0.1). UNA fila por símbolo/dirección con el ACB agregado. NO por `orderId`/`tradeId` (en v0.2 no hay cursor — se recomputa completo, §3).
- **NO se adoptan las 2 filas manuales (BTC/ETH) — REV 3, cierra F4/F5.** Se quedan `OPERATOR` (su `entry_price` tecleado INTACTO, siguen en el read-model de conducta; v0.1 `reconcile_spot` les sigue actualizando qty). Auto-derivar SOLO símbolos **SIN fila existente** (holds nuevos: BNB/PEPE…). Esto (a) cumple la meta — el papá no registra los NUEVOS holds; (b) NO destruye el entry tecleado (irreversible, F4); (c) NO dispara la colisión de índice market NULL→SPOT (F5). La señal de riesgo §7 igual lee TODAS las EXTERNAL (OPERATOR + AUTO_DERIVED), así que el "rojo" de BTC/ETH se preserva. (Re-derivar BTC/ETH con ACB real = slice posterior si Samuel lo pide.)
- `register_external` se extiende: setear `origin='AUTO_DERIVED'` + `market='SPOT'` en el INSERT; idempotencia por la tupla (no `entry_ts`, BNC-5); el INSERT se salta si ya existe una fila `(tenant,symbol,market,direction)` EXTERNAL (no pisa OPERATOR ni re-crea).

## 6. Cierre observado — DIFERIDO, sin auto-clasificación (cierra BLOCKER-2/3, MEDIUM-13)
- Se mantiene el mecanismo de v0.1: cuando un holding `AUTO_DERIVED` cae a `qty ≤ minNotional`, se marca para **revisión humana** y se excluye del equity (`compute_real_equity` filtra qty>0). `status` sigue `open`. Lo cierra un humano (`PositionClosure(USER)`).
- **v0.2 NO auto-clasifica venta vs transferencia** (el caso real ETH→Earn): `qty→0` se presenta al humano como "revisar: ¿vendido o movido?" — el humano sabe. No se persiste estado nuevo (`moved_or_transferred` ELIMINADO), no se piden trades de símbolos en balance-0. Auto-realizar el cierre (`PositionClosure(OBSERVED)` + su exit_reason) = POST-SHIP.
- **Staleness / Earn (REV 3, declara F3):** el equity/observabilidad es "al ÚLTIMO sync" (cadencia manual/periódica en v0.2; no es tiempo real). Un hold movido a Earn deja el balance spot → cae del equity spot en el próximo sync (Earn es otro wallet, DIFERIDO) → su valor en Earn NO se refleja todavía. Se documenta, no se oculta: el equity spot es exacto para lo que vive en spot, no para Earn.

## 7. Señal de RIESGO de holding (decisión de Samuel — Hallazgo 5)
Lectura **read-only, on-read** (como `compute_real_equity`), sobre holdings EXTERNAL (incl. AUTO_DERIVED **y** las manuales OPERATOR — el "rojo" de BTC/ETH se preserva). Reporta HECHOS del holding, NUNCA un acto:
- **Precio (REV 3, cierra F1):** la señal obtiene precio de **los símbolos tenidos** vía Binance público (`GET /api/v3/ticker/price`, cubre todo el catálogo — NO solo el `_PRICE_CACHE` de los 10 del roster, que dejaba ciegos a BNB/PEPE). Si igual falta precio para un símbolo → estado `no_valuado` (se ABSTIENE), **nunca** asume "sin riesgo".
- `underwater` = precio_vivo < ACB de referencia (P&L no realizado negativo vs costo promedio). Para OPERATOR (BTC/ETH) la referencia es su `entry_price` tecleado; para AUTO_DERIVED, el ACB.
- `age_days` = ahora − `entry_ts` (= inicio del holding continuo, §3; estable salvo round-trip, que es correcto).
- `sin_stop` = `sl_price IS NULL` (siempre cierto en spot — informativo solo combinado).
- **Bandera de riesgo** = `underwater AND age_days ≥ horizonte` (horizonte = `time_limit_hours[símbolo]` o un default; **§11**), SOLO si el holding está `valuado`. "Aguantas X underwater hace N días sin stop."
- **NO usa `scan_id`/`apertura_discrecional`** (no infiere acto). NO entra al read-model de conducta. Vive en el plano de observabilidad. Es el sucesor honesto del "rojo de violación" del spec de posiciones externas: el rojo afirma un HECHO de riesgo del holding, no una decisión deliberada que el sistema no observó.
- El ACB mutante es CORRECTO aquí (P&L no realizado vs costo promedio actual = justo lo que se quiere ver).

## 8. Alcance
- **v0.2:** eje `origin` + canon/migración/backfill; filtro `origin` en los 2 fetchers de conducta; cliente `myTrades` firmado read-only; ACB recomputado completo; auto-creación de holds spot > minNotional como `AUTO_DERIVED`; señal de riesgo de holding (§7); equity refleja derivados (sin tocar firma); cierre = flag v0.1 + revisión humana.
- **DIFERIDO:** `PositionClosure(OBSERVED)` (auto-realizar cierres); auto-clasificación venta/transfer; Earn (LD*); cursor incremental; reconstrucción de cerradas; futuros.

## 9. Invariantes (BNC v0.2)
- **BNC-12 (procedencia).** Toda fila lleva `origin ∈ {SIGNAL,OPERATOR,AUTO_DERIVED}`. El read-model de conducta (los 2 fetchers, §2) lee SOLO `SIGNAL`/`OPERATOR`; nunca AUTO_DERIVED. El backfill nunca re-etiqueta AUTO_DERIVED.
- **BNC-13 (ACB es reconstrucción).** `entry_price`/`size_usd` de una fila AUTO_DERIVED son reconstrucciones (mutan); válidas para observabilidad/equity/riesgo, inválidas como "precio de un acto"; conducta no las consume.
- **BNC-14 (cierre observado ≠ acto).** El sistema no escribe `closed` sobre una EXTERNAL por su cuenta (No-Negociable #1). `qty→0` = revisión humana; v0.2 no auto-clasifica venta/transfer; `PositionClosure(OBSERVED)` no existe en v0.2.
- **BNC-15 (identidad).** Idempotencia por `(tenant_id, symbol, market, direction)`; ACB recomputado completo cada sync (sin cursor); re-sync no doble-cuenta.
- **BNC-16 (foto, no historial).** Refleja holds vivos; no reconstruye cerradas (sesgo de supervivencia declarado; inerte porque conducta no lee AUTO_DERIVED).
- **BNC-17 (riesgo ≠ conducta).** La señal de riesgo (§7) afirma HECHOS del holding (underwater, age, sin_stop); nunca infiere apertura/cierre deliberado; vive en observabilidad, no en el eje `i`.
- **BNC-18 (I/O fuera del writer-lock — Halberg, revisión holística).** TODA la I/O de red (balances + myTrades/ticker/exchangeInfo) corre FUERA de cualquier transacción (`plan_spot_autocreate`, fase 1); solo los writes (reconcile + INSERTs) van en una tx CORTA (`apply_spot_autocreate`, fase 2). El `BEGIN IMMEDIATE` NUNCA se sostiene durante la latencia de Binance → no reproduce el incidente de contención del login (2026-06-10). Esto hace el sync seguro concurrente con tráfico vivo y apto para el auto-loop futuro.
- Heredados: BNC-1 (read-only; myTrades USER_DATA), BNC-4 (trigger market⟹EXTERNAL), BNC-11 (firma de compute_real_equity intacta).

## 10. Consistencia cross-documento
- **Spec-conducta:** la distinción `apertura_discrecional ← scan_id IS NULL` se mantiene para SIGNAL/OPERATOR; el read-model ahora **filtra `origin` en el fetcher** (`report.py:_QUERY`) para que AUTO_DERIVED nunca llegue a `project_conduct`. Editar el spec-conducta para nombrar el eje `origin` y el filtro.
- **Spec v0.1:** el bloque `untracked` de `reconcile_spot` pasa de "reportar" a "auto-crear AUTO_DERIVED" (con ACB), salvo Earn/dust. El flag `qty→0` de v0.1 se reusa para el cierre (§6).
- **control_domain (CD-1..6):** AUTO_DERIVED son `control_domain='EXTERNAL'` (observadas, nunca actuadas). CD-1/CD-5 intactos.
- **Equity (BNC-11):** `compute_real_equity` incluye AUTO_DERIVED (qty>0) sin filtrar origin; firma intacta.

## 11. Preguntas abiertas (residuales — no bloquean el build del núcleo)
1. `health.py::compute_rolling_metrics` (no filtra hoy): ¿es user-facing y debe excluir AUTO_DERIVED, o es interno? Verificar antes de tocar.
2. Comisiones en BNB/otro asset: fuente exacta del precio BNB→quote (klines del instante vs ignorar el fee no-quote). Best-effort aceptable en v0.2.
3. Horizonte de la bandera de riesgo (§7): `time_limit_hours[símbolo]` vs un default global.
4. ¿`origin='OPERATOR'` debería distinguir "siguió-señal-pero-ejecutó-afuera" (Richter, diferido en spec CD §10)? — no para v0.2.
5. Earn (LD*): slice siguiente o nunca (los 0.448 ETH del papá viven ahí).

## 12. Kill del spec
Si una verificación encuentra que (a) una fila `AUTO_DERIVED` entra al read-model de conducta (el filtro `origin` no está en el fetcher, está solo en `episode.py`), (b) el sistema escribe `closed` sobre una EXTERNAL por su cuenta, (c) v0.2 intenta auto-clasificar venta/transfer o marca cierre sin revisión humana, (d) la idempotencia depende de `entry_ts`/`orderId`, (e) `origin` no sobrevive las recreaciones o el backfill re-etiqueta AUTO_DERIVED, (f) la señal de riesgo (§7) infiere un acto (apertura/cierre deliberado) en vez de un hecho, o (g) se cambia la firma de `compute_real_equity` — esa pieza se corta o re-tipa antes de codificar.
