# El Instrumento — Fase 2 (simulador determinista refutador) · Diseño

**Fecha:** 2026-06-13
**Rama:** `feat/instrumento-fase2-simulador`
**Parte de:** el instrumento completo (`docs/superpowers/specs/es/2026-06-12-instrumento-lifecycle-conducta-design.md`), Fase 2 de §3/§9.

## §0 — Qué es F2, y qué NO es (decidido por el roster)

F2 es **el simulador determinista refutador** de la columna. Toma el plan derivado de D.1 (`instrument/plan.py::derive_plan`), lo corre **en piloto automático** sobre las velas históricas hacia adelante generando eventos deterministas, los alimenta a la **misma máquina de estados pura de F1** (`instrument/lifecycle.py::step`), y verifica **paridad** contra el envelope de las posiciones reales ya cerradas. Es el **segundo refutador** de la falsación progresiva (F1 envelope real → **F2 simulación histórica determinista** → F3 libro de fills vivo).

**Lo que F2 NO produce (veto del roster, decisión registrada):** ningún **GAP de PnL**, ninguna columna de retorno agregado, ninguna "deuda de conducta". El roster (Voronov, Null Vale, Cassian) rechazó la opción "línea base plan-vs-conducta" por dos razones:
- **Voronov:** el GAP plan-vs-actual resta un contrafactual mecánico (eje real, precio) menos una conducta observada (eje imaginario `i`) — peras menos manzanas; y es PnL-contra-PnL, que **viola INV-1 al revés** (certifica el acto contra un resultado fabricado). La comparación plan-vs-conducta legítima pertenece a **F3**, contra el libro de fills vivo, **campo por campo, no por resta de PnL**.
- **Null Vale:** el plan-en-autopilot es "tu conducta con la **suerte de otro**" — un contrafactual idealizado sin el order book/slippage/mecha reales. `R = C × L`; el GAP contamina `C` con la diferencia de `L`.

Por tanto F2 entrega: **(1)** el mecanismo de cierre determinista que el operador pidió ("hacer determinista el cierre para un backtest funcional"), y **(2)** confianza en las transiciones de la máquina antes de F3 — vía un reporte de paridad, sin reclamar rentabilidad.

## §1 — La regla de cierre determinista (decidida por el roster: unánime A)

**Velas diarias + regla conservadora (SL antes que TP intra-vela).** El roster votó unánime A sobre intradía:
- **Halberg (datos duros):** la `ohlcv.db` tiene **cero gaps** en 1d para los 10 símbolos; el 1h tiene **7 cortes / 14 barras faltantes en 8 de 10** (ventanas de mantenimiento). El determinismo intradía es condicional a una grilla rota → una entrada cerca de un gap daría una falla de paridad que es **ruido de datos disfrazado de bug**, destruyendo la señal del refutador.
- **Null Vale:** la vela de 1h sigue ocultando el orden intra-hora; intradía solo encoge la mentira. La regla diaria pesimista es una **cota inferior auditable** con sesgo de un solo signo conocido.
- **Cassian:** un refutador con regla ingenua que falsea de más grita más fuerte (falso positivo = investigación, no bug embarcado). Intradía es gold-plating hasta ver la primera divergencia diaria.

**Frontera de swap (Cassian, adoptada):** la regla de fill vive aislada tras una firma estable `resolve_fills(plan, state, candle) → eventos`. El upgrade futuro a intradía (si F3 lo pide) es un cambio de implementación, no cirugía.

## §2 — Arquitectura

Reutiliza F1; una pieza nueva pura + un arnés.

| Pieza | Archivo | Naturaleza |
|---|---|---|
| Regla de fill aislada | `instrument/simulate.py` → `resolve_fills(plan, state, candle)` | **Pura.** Vela diaria + estado → lista de eventos. La frontera de swap. |
| Caminata de simulación | `instrument/simulate.py` → `simulate_plan(plan, candles)` | **Pura.** Recorre velas, llama `resolve_fills` + `step` de F1, hila el estado → `(events, final_state)`. |
| Arnés refutador | `tools/plan_simulator.py` | I/O (red/DB): posiciones reales (filtro BNC-12) + frames diarios + paridad + reporte. |

`instrument/simulate.py` es **puro** (sin red, sin DB), hermano de `instrument/lifecycle.py`. La red (frames vía `backtest.py::get_cached_data`) y la lectura DB (posiciones) viven en el arnés.

## §3 — `resolve_fills` (la regla, pura)

`resolve_fills(plan, state, candle) -> list[dict]`. `candle = {"open","high","low","close"}` (diaria). Devuelve los eventos que esa vela dispara, en orden:

1. **SL primero (pesimista):** si `candle["low"] <= state.sl_actual` (y `state.sl_actual > 0`, es decir el SL ya está fijado) → devuelve `[{"tipo":"STOP_HIT","procedencia":"observado"}]` y nada más (la vela cierra la posición).
2. Si no cerró por SL: por cada rung `i` **no** en `state.rungs_llenos`, en orden ascendente de `tp_price`, con `candle["high"] >= plan.rungs[i].tp_price`:
   - emite `{"tipo":"RUNG_FILLED","order_id":f"sim-r{i}","rung_index":i,"procedencia":"observado"}`.
   - tras incluir el rung `0` por primera vez, emite además `{"tipo":"SL_MOVED","nuevo_sl":plan.entry_price,"procedencia":"observado"}` (el autopilot **sigue** la regla BE del plan: mover SL a break-even tras TP1).
3. Si nada disparó: devuelve `[]`.

Notas:
- El `sl_actual` inicial del estado es `0.0` (F1). El arnés inicializa `sl_actual = plan.sl_price` en el estado de arranque (ver §4), así que el chequeo de SL del paso 1 es contra el SL vigente (que pasa a `entry_price` tras el BE).
- Idempotencia: los `order_id` son deterministas (`sim-r{i}`); `step` ya ignora un `order_id` ya consumido. `resolve_fills` además salta los rungs ya en `rungs_llenos`.
- El orden SL-antes-que-TP es el sesgo pesimista declarado: si una vela toca ambos, la máquina ve `STOP_HIT` y no los `RUNG_FILLED`.

## §4 — `simulate_plan` (la caminata, pura)

`simulate_plan(plan, candles) -> tuple[list[dict], LifecycleState]`. `candles` = velas diarias ascendentes desde la entrada.

1. Estado inicial: `LifecycleState(plan_id=0, fase="CONFIRMED", sl_actual=plan.sl_price, size_restante_frac=1.0)` precedido conceptualmente por `PLAN_CONFIRMED` (el autopilot confirma de una). Registrar el evento `PLAN_CONFIRMED` al frente de la lista devuelta.
2. Para cada vela: `for e in resolve_fills(plan, state, candle): state = step(state, e, plan)`; acumular los eventos. Si `state.fase == "CLOSED"`: parar.
3. Si se agotan las velas sin cerrar: emitir `{"tipo":"SIM_END","procedencia":"observado"}` y aplicarlo. (Añadir al reductor de F1 el evento `SIM_END → CLOSED` con `close_reason="SIM_END"`; es una divergencia honesta = el plan habría aguantado más que los datos disponibles.)
4. Devuelve `(events, final_state)`.

> **Cambio mínimo en F1:** `instrument/lifecycle.py::step` gana una transición `SIM_END → CLOSED (close_reason="SIM_END")`, terminal como las demás. No altera ninguna transición existente.

## §5 — El arnés `tools/plan_simulator.py` (I/O, refutador)

Read-only sobre `positions`; sin `PositionClosure`; red fuera de tx.

1. Lee las posiciones reales cerradas con el **mismo filtro BNC-12 que F1**: `status='closed' AND tenant_id=? AND origin IN ('SIGNAL','OPERATOR')` (AUTO_DERIVED excluido).
2. Para cada una: reconstruye las zonas de D.1 al momento de la entrada (`detect_levels` sobre velas diarias hasta `entry_ts` — reutiliza el `_bars_as_of` de `tools/lifecycle_falsifier.py` o su equivalente), `derive_plan`.
3. Trae las velas diarias **hacia adelante** desde `entry_ts` (`get_cached_data(symbol, "1d", entry_ts)`), recortadas hasta `exit_ts` + un margen.
4. `simulate_plan(plan, candles)` → `(events, final_state)`.
5. **Paridad** contra el envelope real:
   - **máquina legal:** `final_state` nunca quedó en un estado imposible (lo garantiza `step`; se afirma).
   - **paridad:** ¿el cierre del sim corresponde al cierre real? Mapear: real `exit_reason` SL-like ↔ sim `close_reason ∈ {SL_HIT, BE_HIT}`; real TP-like ↔ sim cerró por rungs; y el `exit_price` real cae cerca del nivel donde el sim cerró (tolerancia, reutilizar `_close` 0.5%).
   - **divergencia:** si no hay paridad, registrar el caso con su motivo (sim dice X, realidad Y).
6. **Reporte tabular:** contadores `{simuladas, máquina_legal, paridad, divergencias}` + la lista de divergencias con motivo. **Sin tabla nueva, sin PnL.**

Uso: `python -m tools.plan_simulator` (network-marked; corre a propósito).

## §6 — Fuera de alcance (defer / delete)
- **Intradía (1h) + secuencia temporal** — DEFER tras la primera tanda de divergencias diarias (swap detrás de `resolve_fills`).
- **Modelado de slippage / fills parciales** — DELETE hasta que F3 lo pida.
- **PnL agregado / retorno del plan** — DELETE (fuera del mandato del refutador).
- **Persistir las corridas del sim en tabla** — DEFER a F3 (el refutador reporta, no persiste).
- **GAP plan-vs-conducta** — movido a F3 (medición campo-por-campo contra el libro de fills vivo, no resta de PnL).

## §7 — Pruebas

**Puras** (`tests/test_instrument_simulate.py`, sin red/DB):
- `resolve_fills`: doble toque TP+SL en una vela → solo `STOP_HIT` (pesimista); solo TP1 → `RUNG_FILLED(0)` + `SL_MOVED(entry)`; TP2 sin TP1 previo en la misma vela → ambos `RUNG_FILLED` en orden; ninguno → `[]`; rung ya lleno no se re-emite.
- `simulate_plan`: serie que toca TP1 luego SL-en-BE → cierra `BE_HIT`; serie que solo cae → `SL_HIT`; serie que sube por toda la escalera → cierra por el último rung; serie que nunca toca nada → `SIM_END`; el evento `PLAN_CONFIRMED` va al frente.
- `step` (F1): nueva transición `SIM_END → CLOSED (close_reason="SIM_END")`; terminal.

**Arnés** (`network`-marcado): un smoke que corre `simulate_plan` sobre una posición con frames reales y confirma que produce un `final_state` CLOSED y un veredicto de paridad (sin asersión sobre el valor exacto, que depende de datos vivos).

## §8 — Invariantes preservados
- **No edge:** cero PnL, cero claim de rentabilidad. El reporte cuenta transiciones y paridad, no retornos.
- **Pureza:** `resolve_fills` y `simulate_plan` sin red ni DB.
- **Frontera dura de F1 intacta:** el arnés es read-only sobre `positions`, sin `PositionClosure`, sin escritura a `positions.status`. No persiste nada nuevo.
- **BNC-12:** el arnés excluye AUTO_DERIVED (conducta lee solo SIGNAL/OPERATOR).
- **Procedencia:** los eventos del sim son `observado` (derivados de velas reales del mercado); el sim no fabrica conducta declarada.
