# Plan de implementación — Binance v0.2: auto-creación por observabilidad

**Spec:** `docs/superpowers/specs/es/2026-06-10-binance-v02-autocreacion-observabilidad-spec.md` (REV 3, build-ready).
**Rama:** `feat/binance-v02-autocreacion` (off main, que ya tiene v0.1).
**Proceso:** TDD por tarea (RED→GREEN), implementación SECUENCIAL en un solo árbol (regla dura: nunca implementers en paralelo), revisión adversarial por tarea (workflows read-only), gate verde, PR. Claude NO mergea.
**Gate local:** `python -m pytest tests/ -m "not network" -n auto -q`.

## Invariantes que el build DEBE respetar (del spec §9)
- **BNC-12:** el read-model de conducta lee SOLO `origin IN ('SIGNAL','OPERATOR')`; nunca AUTO_DERIVED. El filtro va en el QUERY del fetcher, NO en `episode.py` (proyección pura).
- **BNC-13:** `entry_price`/`size_usd` de AUTO_DERIVED son reconstrucciones (mutan); inertes para conducta.
- **BNC-14:** el sistema nunca escribe `closed` sobre EXTERNAL por su cuenta (No-Negociable #1). `qty→0` = revisión humana; NO auto-clasifica venta/transfer; NO `PositionClosure(OBSERVED)`.
- **BNC-15:** idempotencia por `(tenant,symbol,market,direction)`; ACB recomputado completo; re-sync no doble-cuenta.
- **BNC-16:** foto de holds vivos (no reconstruye cerradas).
- **BNC-17:** la señal de riesgo afirma HECHOS (underwater/age/sin_stop), nunca infiere acto.
- **BNC-1/4/11 heredados:** read-only (myTrades USER_DATA); trigger market⟹EXTERNAL; firma de `compute_real_equity` intacta.
- **Lección `market`:** toda columna nueva del canon DEBE enhebrarse en las 4 recreaciones `positions_new` + `TARGET_COLS` o se borra en `init_db`.

## Tareas (ordenadas por dependencia)

### Task 1 — Eje `origin`: schema + canon + migración + backfill (FUNDACIÓN, delicada)
- `db/positions_schema.py`: añadir `origin TEXT NOT NULL DEFAULT 'SIGNAL'` a `CANONICAL_POSITIONS_COLUMNS`; CHECK de dominio `{SIGNAL,OPERATOR,AUTO_DERIVED}`.
- `db/schema.py`: ALTER PRAGMA-guarded (añade si falta); enhebrar `origin` en los **4** `CREATE TABLE positions_new` + `TARGET_COLS` (select-expr fallback `'SIGNAL'`, patrón market/control_domain); backfill idempotente `UPDATE positions SET origin='OPERATOR' WHERE control_domain='EXTERNAL' AND origin='SIGNAL' AND scan_id IS NULL`.
- **Tests (RED→GREEN):** canon test pasa; `origin` SOBREVIVE las recreaciones (regresión, como `test_market_survives_reinit_recreations`); backfill idempotente + NO toca AUTO_DERIVED ni re-etiqueta en re-corrida; INTERNAL legacy → SIGNAL, EXTERNAL manual → OPERATOR.

### Task 2 — Filtro `origin` en los 2 fetchers de conducta (BNC-12)
- `tools/tenant_realization/report.py:_QUERY`: añadir `AND origin IN ('SIGNAL','OPERATOR')` + seleccionar `origin`.
- `api/agent/tools/handlers.py::get_closed_trades`: añadir `AND origin IN ('SIGNAL','OPERATOR')` (o vía `db_get_positions`).
- **Tests:** una fila AUTO_DERIVED cerrada NO entra al read-model de conducta; SIGNAL/OPERATOR sí; equity (compute_real_equity) SIGUE incluyendo AUTO_DERIVED (no se filtró).

### Task 3 — Cliente `myTrades` firmado + exchangeInfo + ticker público
- `data/providers/binance_account.py`: `get_my_trades(symbol, from_id=0)` paginado (limit 1000, `_signed_get`, weight); `get_exchange_filters(symbols)` (minNotional/LOT_SIZE de `/api/v3/exchangeInfo`, público); `get_ticker_prices(symbols)` (`/api/v3/ticker/price`, público, para §7).
- **Tests:** firma correcta (reusa patrón v0.1), paginación por fromId, manejo de error transport/auth (reusa), ticker/exchangeInfo parsean.

### Task 4 — Reconstrucción de cost-basis ACB (módulo puro)
- Nuevo módulo (p.ej. `binance_costbasis.py`): `reconstruct_acb(fills, complete: bool) -> {qty_viva, avg_entry, entry_ts, status}`. ACB weighted-avg; `entry_ts` = inicio del holding continuo actual (último cruce 0→>0); comisiones (base/quote/BNB); **abstain si `complete=False`** (truncado) → `status='ingest_incompleto'`.
- **Tests:** ACB correcto vs fixture; round-trip resetea entry_ts (correcto); comisión en base/quote/BNB; abstención si truncado; qty_viva = Σbuys−Σsells.

### Task 5 — Descubrimiento + auto-creación (extender binance_sync)
- `binance_sync.py`: descubrir assets>0 → pares con las **4 quotes** → myTrades por par → ACB → filtro minNotional → auto-crear fila `AUTO_DERIVED` (`market='SPOT'`) SOLO si no existe fila `(tenant,symbol,market,dir)` EXTERNAL (NO pisa OPERATOR). Abstain `no_reconstruible`/`ingest_incompleto`.
- `tools/register_external_position.py` o helper: extender para setear `origin` + `market` en el INSERT; idempotencia por tupla.
- **Tests:** crea solo símbolos nuevos (no BTC/ETH OPERATOR existentes); respeta minNotional (dust fuera); abstiene si ACB no-reconstruible; idempotente (re-sync no duplica); trigger BNC-4 no aborta.

### Task 6 — Señal de riesgo de holding §7 (BNC-17)
- Nuevo módulo (p.ej. `api/holding_risk.py`): read-only on-read sobre EXTERNAL holds (OPERATOR + AUTO_DERIVED); precio vía ticker público + **abstain `no_valuado`** si falta; `underwater`/`age_days`/`sin_stop`; bandera `underwater AND age≥horizonte AND valuado`. NUNCA infiere acto.
- **Tests:** abstiene sin precio (no asume "sin riesgo"); bandera correcta; lee OPERATOR + AUTO_DERIVED; no toca scan_id/apertura_discrecional.

### Task 7 — Orquestador / CLI + --dry-run
- Extender `tools/sync_binance_spot.py` (o nuevo) para encadenar discovery+ACB+auto-create con `--dry-run` (imprime qué crearía sin escribir). Mantener fail-closed de v0.1.
- **Tests:** dry-run no escribe; orquestación end-to-end con mocks.

### Task 8 — Revisión holística + gate + PR
- Gate verde completo; revisión adversarial holística (workflow multi-lente); push; PR. Samuel mergea.

## Diferido (POST-SHIP, explícito)
`PositionClosure(OBSERVED)` (auto-realizar cierres); auto-clasificación venta/transfer; Earn (LD*); cursor incremental; reconstrucción de cerradas; futuros; re-derivar BTC/ETH con ACB real.

## Estado de implementación (2026-06-10 — TODAS las tareas HECHAS)
- **T1** eje `origin` — `48cb14f`+`bb2b7e1` (revisada Adrian+Halberg: CORRECTA, corre seguro en prod).
- **T2** filtro conducta (BNC-12) — `25f88b1` (revisada: BNC-12 cerrado, sin bypass).
- **T3** cliente myTrades/exchangeInfo/ticker — `efee4a9`.
- **T4** ACB — `5e1a289`+`4239ba4` (fix eps RELATIVO para memecoins, Halberg).
- **T6** señal de riesgo §7 — `3a989e3`.
- **T5** descubrimiento + auto-creación — `5755b1f`.
- **T7** orquestador CLI + dry-run — `d3e2515`.
- **Revisión holística** (Adrian+Halberg+coherencia): 0 BLOCKERS, 6 invariantes BNC-12..17 + 6 fixes F1/F2/F4/F8/F9/eps CONFIRMADOS. Una salvedad de Halberg → refactor.
- **Refactor writer-lock** (`db463f5`, decisión de Samuel): el `sync --autocreate` hacía I/O de red DENTRO del `BEGIN IMMEDIATE` (reproducía la contención del login). Split `plan_spot_autocreate` (I/O, sin lock) + `apply_spot_autocreate` (writes, tx corta); `sync_tenant` fase1 I/O / fase2 writes. **El writer-lock ya NO se sostiene durante la red.**
- Gate verde **3347 passed** en el checkpoint final.

## Notas de implementación / scope (de las revisiones)
- **Adrian #4:** el INSERT AUTO_DERIVED se hizo en una función NUEVA `_create_auto_derived` (binance_sync.py), NO extendiendo `register_external` (que mantiene su idempotencia-por-entry_ts del path manual). Funcionalmente mejor: no contamina el path OPERATOR. (La spec §5 decía "extender register_external"; se implementó como función dedicada.)
- **Multi-quote (Adrian #2) — fuera de scope v0.2:** si un asset tiene trades en DOS quotes, se usa el primero de `_QUOTES`; el ACB cubre solo ese par. Aceptable para el caso del papá (BNB/PEPE single-quote). Merge cross-quote = POST-SHIP.
- **Dust-sin-precio (Adrian #3):** si no se puede valuar (sin ticker/minNotional), se CREA (create-in-doubt — tiene trades reales = posición real; §7 lo marca `no_valuado`). Política deliberada; alternativa "abstenerse" = decisión futura.
- **§11-abierta (health.py):** `compute_rolling_metrics` no filtra origin; inerte en v0.2 (AUTO_DERIVED nunca llega a `closed` salvo cierre humano). Añadir filtro SI se habilita `PositionClosure(OBSERVED)`.
