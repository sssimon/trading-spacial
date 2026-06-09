# Spec — Integración del eje-conducta: el `EpisodioDeConducción` (REV 2)

**Fecha:** 2026-06-09 · **REV 2** (tras roast adversarial de Adrian + Halberg + Null Vale).
**Tipo:** integración de modelo de dominio (NO frontend, NO plan de build todavía).
**Origen:** orden de Samuel — *"hasta que no aceptemos esta verdad y la integremos no tenemos sistema, es como cuando se crearon los números imaginarios… el frontend es solo un reflejo del entendimiento."*
**Juntas que lo fundan:** J-constructiva 2026-06-09 (Voronov, El Cuantitativo, Halberg, Adrian, Richter, Null Vale + Axiom-0). Verificación de terreno 2026-06-09. Roast REV 1→2 2026-06-09.
**Relacionado:** `2026-06-08-panel-disciplina-suerte-aware-spec.md` (REV 3 = primera proyección parcial de este objeto).

---

## REV 2 — qué cambió tras el roast (changelog)

El roast encontró 5 blockers y los aplanamientos que yo (autor) introduje al bajar la construcción a tierra. REV 2 cierra:

1. **INV-1 reescrito** de "ninguna función que juzgue conducta recibe pnl" (sobre-formulado, mataba κ y la bandera #3) a **no-validación-por-resultado** — fiel a lo que El Cuantitativo y Null Vale construyeron. Con esto, κ y la bandera #3 dejan de ser "excepciones": son legales. (B1)
2. **Máquina de estados** cierra el camino `DELETE→cancelled` con un estado terminal `CANCELLED` (conducta con outcome nulo). `SELECTED`/`ABANDONED` salen del ciclo canónico v0.1 (Q4 REFUTED) a un apéndice DIFERIDO. (B2)
3. **Materialización en dos tiempos:** v0.1 = **read-model derivado** (proyección pura read-only, sin tabla, sin escritura); v0.2 = **entidad persistida de 1ª clase** (tabla propia). Se disuelve la contradicción "1ª clase vs vista de positions". (B3)
4. **Bug del proto-instrumento corregido:** `report.py` clasifica `MANUAL_AGENT` del lado del resultado (`MANUAL_REASONS={"MANUAL"}`). Es un defecto, no evidencia de coherencia; arreglarlo es uno de los deltas de v0.1. §7 reescrito. (B4)
5. **INV-5 degradado** de "invariante de runtime v0.1" a **principio de diseño para v0.2** (rige el plano vivo `CONDUCTING`, que v0.1 no construye). (B5)
6. **conduct = proyecciones, no campos crudos** (`cierre_discrecional` boolean, no `exit_reason` crudo). **κ** tipado `RETROSPECTIVO`, y en v0.1 degradado a ratio de pnl realizados (la κ-por-forma necesita MAE/MFE = v0.2). Inmutabilidad de veredictos emitidos declarada. `costo_piso` con fuente de tier declarada. Cifra de CI95 volátil retirada. `q2` citado con su tipo correcto (concentración, no conducta-adaptada).

---

## 0. Qué es este documento (y qué NO)

Declara la primitiva nueva del sistema y su contrato, para que todo lo que se construya después —incluido el frontend— sea su reflejo fiel. No es diseño de UI, no es plan de build, **no promete edge**.

Es el **movimiento de los números imaginarios**: el avance no fue volver a probar que √−1 "no existe en los reales" —eso ya se sabía— sino aceptar √−1 como tipo nuevo (`i`) y construir el sistema coherente que se sigue (el plano complejo, que *contiene* la recta real intacta).

**Candado de honestidad mayor (Null Vale):** `i` fue *conservador* — no afirmó nada nuevo sobre los reales. Esta integración tampoco: **no resucita ningún edge muerto** (la señal sigue gross-flat, la selección sigue `q3_pass:false`, la media sigue cero). **No hace ganar plata. Produce coherencia** — lo que faltaba.

---

## 1. El axioma y la primitiva

### 1.1 Axiomas aceptados (fundación, no se re-litigan)

- **A1.** El edge de la estrategia direccional NO es determinista. Vive en el **proceso conducido** por el humano.
- **A2.** La **suerte domina** el resultado de cada operación. Fundacional, no defecto.
- **A3.** La herramienta debe ayudar a (a) aprovechar una buena ráfaga de suerte y (b) minimizar la pérdida.

### 1.2 La primitiva — el análogo de `i`

> **`i` := la DECISIÓN ADAPTADA — el acto del operador en el instante `t`, medido solo con lo legible en `t`, declarado ortogonal al resultado.**

Tres cláusulas: (1) es un **ACTO**, no un resultado; (2) vive en **`𝓕ₜ`** (lo ya ocurrido); (3) es **ortogonal al resultado** — no se *valida* con él. `i` no es la suerte (eje real, intocado); es el eje **perpendicular** a la suerte.

### 1.3 El cambio de TIPO

El sistema deja de ser **"generador de señales + ejecutor"** (oráculo, humano = ruido) y pasa a ser **un INSTRUMENTO que mide la decisión adaptada sobre un proceso estocástico que no controla.** La señal no existe para acertar — da **unidades** a la conducta. `R = C × L` (resultado = conducta × suerte); el acto del sistema es factorizar `R` ex-post, no predecirlo.

---

## 2. El sistema de dos planos

| Eje | Qué es | Estatus | Quién lo toca |
|---|---|---|---|
| **Eje-resultado (real)** | señal, P&L, edge | media ≈ 0, **intocado** | la suerte |
| **Eje-conducta (`i`)** | la decisión adaptada | el plano nuevo | el operador |

**Ley de no-mezcla (la definición del tipo):**

> **Ningún resultado individual valida la decisión individual que lo produjo, y conducta y resultado no se renderizan juntos de forma que inviten esa atribución.** Separados como parte real e imaginaria.

> **Aclaración (resuelve la tensión κ/INV-1):** "no-mezcla" NO significa "nunca leer R". Caracterizar la **forma agregada** de muchos resultados ex-post (κ, asimetría de captura, aguante relativo) **sí** lee R y es legítimo — porque describir la forma de una distribución no es validar un acto con su resultado. Lo prohibido es lo puntual: que el resultado de *un* trade certifique la calidad de *esa* decisión ("gané, luego decidí bien").

---

## 3. El objeto: `EpisodioDeConducción`

> Un **`EpisodioDeConducción`** es la lectura de las decisiones humanas asociadas a una posición. Tiene dos componentes ortogonales (`conduct`, `outcome`) y la ley de no-mezcla entre ellos.

- **Alcance v0.1:** desde la **apertura** hasta el cierre. (La fase de *selección* previa a la apertura está DIFERIDA — §6 Q4, apéndice A.)
- **Identidad:** 1-a-1 con `positions.id` (toda fila de `positions`, incluidas las `cancelled` — §4.1).
- **`outcome`** (componente-resultado): `pnl_usd`, `pnl_pct`, y la causa de cierre cruda (`exit_reason`, `exit_price`). Dominado por suerte (A2). **El sistema NO se evalúa aquí.**
- **`conduct`** (componente-conducta): las **proyecciones** que aíslan lo que el operador decidió (§4.2). **Único plano con autoridad para juzgar, frenar o reportar valor.**

### 3.1 Materialización — v0.1 read-model, v0.2 entidad de 1ª clase

- **v0.1 — read-model derivado.** El episodio es una **proyección pura, read-only, sin tabla nueva y sin escritura**, computada sobre `positions` cerradas (función pura, estilo `compute_report`). Es honesto *porque* solo lee episodios ya `REALIZED`/`CANCELLED` y nunca escribe un veredicto colgado de `positions`.
- **v0.2 — entidad persistida de 1ª clase.** Cuando se construyan los planos vivos (`CONDUCTING`), el log intra-posición y los tipos por cifra, el episodio pasa a **tabla propia** (no un campo de `positions`). La advertencia de Richter —"o el sistema llamará disciplina a la suerte"— rige **aquí**: la gravedad del eje-resultado arrastra al eje-conducta **solo cuando se PERSISTE un veredicto** junto a la posición. Por eso la entidad persistida v0.2 debe ser independiente. El read-model v0.1 no corre ese riesgo porque no persiste nada.

---

## 4. Contrato del objeto

### 4.1 Estados y ciclo de vida (v0.1)

| Estado | Significado | `conduct` | `outcome` | Disponibilidad |
|---|---|---|---|---|
| `CONDUCTING` | posición abierta | (vivo, opaco) | NO | **no materializado en v0.1** (sin precio vivo ni log intra-posición). El episodio existe pero es opaco hasta cerrar. |
| `REALIZED` | cerrada vía `PositionClosure` | **completo (legible de entry/exit)** | completo | **v0.1 — el único totalmente observable** |
| `CANCELLED` | `DELETE→status='cancelled'`: el operador la mató antes de correr | **conducta pura** (acto humano) | **nulo** (`exit_reason=NULL`, sin P&L significativo) | v0.1 *parcial*: el estado existe en el contrato, pero el fetch actual del monitor (`status='closed' AND pnl_usd IS NOT NULL`) lo **excluye** → surfacearlo es un delta enumerado (§7). |

**Transiciones:** `CONDUCTING → REALIZED` (cierre vía `PositionClosure`) · `CONDUCTING → CANCELLED` (`DELETE`). `REALIZED` y `CANCELLED` son **terminales e irreversibles**.

**Precondición dura:** el único camino legal a `REALIZED` pasa por `PositionClosure` (no-negociable #1); `db_close_position_sql` escribe 6 columnas (`status, exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct`). `CANCELLED` **no** pasa por `PositionClosure` (`DELETE` directo, `api/positions.py:435`) — por eso su `outcome` es nulo y su atribución es 100% conducta.

> **Nota de alcance (honesta):** v0.1 sostiene limpio solo `REALIZED`; `CANCELLED` existe en el contrato pero hoy es invisible al monitor (delta a abrir); `CONDUCTING` es opaco. **El instrumento v0.1 es RETROSPECTIVO sobre episodios cerrados.** Los estados de *selección* (`SELECTED`/`ABANDONED`) NO pertenecen al ciclo v0.1 — apéndice A.

### 4.2 Composición de `conduct` (proyecciones, no campos crudos)

Cada campo de `conduct` es una **proyección** que aísla la parte-conducta; el campo crudo del que deriva queda como procedencia del lado-`outcome`. (Fija la lista en INV-3; congelada al resolverse Q2, §6.)

| Campo `conduct` (proyección) | Deriva de | Notas / límite |
|---|---|---|
| `cierre_discrecional` (bool) | `exit_reason ∈ {MANUAL, MANUAL_AGENT}` | la "conducta categórica" del cierre; el `exit_reason` crudo NO entra al plano-conducta (Adrian: un campo mitad-outcome no es conducta). `{SL_HIT, TP_HIT}`=outcome, `{TIME_LIMIT_HIT}`=sistema, `CANCELLED`=conducta (NULL). **Caveat C:** un `SL_HIT` puede haber sido inducido por el ratchet automático (no-discrecional); como el log del ratchet es in-memory y se pierde, la atribución de `SL_HIT` es estructuralmente imposible de limpiar en v0.1 → se trata como `outcome` con esa limitación declarada (capturar eventos de ratchet = v0.2). |
| `apertura_discrecional` (bool) | `positions.scan_id IS NULL` | `scan_id=NULL` (apertura manual, sin señal) = **acto de conducta positivo**, NO "dato faltante". Es el 63% del P&L del papá (todo MANUAL). El antiguo campo `selection` se reinterpreta así: no "operó la señal X", sino "abrió con/sin señal". |
| `size_usd` | `positions.size_usd` | base de la señal de sobre-sizing. |
| `entry_gap_to_prior_loser` | `positions.entry_ts` − cierre del perdedor previo del tenant | base de la señal revenge-trade. |
| `hold_hours` | `exit_ts − entry_ts` | base del aguante relativo. |
| `costo_piso` | `size_usd × RT_FLOOR_BPS[tier]/10⁴` | **cota inferior** de fricción (INV-4). `tier` vía `tier_for_symbol` (`backtest_costs.py`, 10 símbolos curados; `costs_calibration.json` v3: major 13 / mid 18 / small 30 bps). **Para un símbolo sin tier mapeado, `costo_piso` se marca NO-DISPONIBLE, no se fabrica** (legibilidad en `t`: el tier es estructural, estable). |

**NO disponible en v0.1** (frontera; requiere instrumentación nueva, §9): timeline intra-posición · **MAE/MFE por posición** (sin esto, la κ-por-forma de §5 no es computable) · traza de selección (apéndice A).

### 4.3 Invariantes

- **INV-1 (no-validación-por-resultado).** Ningún resultado individual certifica la calidad de la decisión individual que lo produjo. La conducta se juzga contra una **regla declarada ANTES del resultado**, nunca contra el resultado mismo. Las métricas de **forma agregada** de la distribución (κ, asimetría de captura, aguante relativo) **pueden** leer `R` ex-post para caracterizar el estilo de conducta — caracterizar la forma de muchos resultados no es validar un acto con su resultado. *(Esta es la formulación correcta; la de REV 1 —"ninguna función recibe pnl"— era más fuerte que la construcción y se contradecía con κ y la bandera #3.)*
- **INV-2 (suerte declarada).** El `outcome` entra con `luck_dominated = TRUE`. Prohibido derivar de un conjunto de `outcome` una afirmación de skill/edge/mejora de la media.
- **INV-3 (atribución cerrada).** Todo lo "evaluable" se deriva EXCLUSIVAMENTE de la lista congelada de §4.2. Una feature que necesita un campo fuera de la lista no se agrega: se abre la pregunta de si es conducta o suerte.
- **INV-4 (el costo es el único puente, y NO es edge).** El `costo_piso` es conducta (el operador eligió símbolo/tier/tamaño/hold). Reducir costo **no mueve la media de la señal**: elimina un término determinista de signo conocido (negativo) que vive *entre* gross y net. Es **subir el piso** (quitar fricción), **no** crear edge. *La línea, explícita: "tocar el resultado con signo conocido" ≠ "palanca sobre la media de la señal"; lo primero es restar un costo, lo segundo no existe (A2).*
- **INV-5 (reloj epistémico — DIFERIDO a v0.2).** Cuando exista el plano vivo (`CONDUCTING`), cada campo declarará en qué instante se volvió legible y el runtime **rehusará** exponer un campo de futuro en el contexto de decisión (fail-closed), con **un solo objeto** que aplique la frontera `𝓕ₜ` igual en vivo y en backtest. **En v0.1 NO rige** (el instrumento es retrospectivo sobre datos ya realizados, ya legibles; no hay "contexto de decisión en `t` que rehusar"). Hoy ese objeto único cross-mundo no existe (vivo=`PositionClosure`, backtest=`simulate_strategy`); nombrarlo es prerequisito de v0.2.
- **INV-6 (las tres puertas — Null Vale).** Todo objeto nuevo del eje-conducta es legítimo **sii**: (a) se mide en el mismo mundo donde se cobra; (b) deja el eje real intacto (no resucita nada falsificado); (c) se evalúa contra una regla declarada ANTES del resultado. La **justificación** de una métrica debe nacer DENTRO del eje-conducta (una regla de disciplina pre-declarada), **no** de un hecho del eje-resultado ("el papá la exhibe" / "es la más accionable" NO son criterios admisibles — Null Vale).
- **INV-7 (tipo declarado por cifra).** Cada número lleva tipo `RETROSPECTIVO | RUIDO | LEY`. Un `RETROSPECTIVO` **describe** ("aguantaste perdedores más que ganadores"); **no** prescribe ("deja de hacerlo") — solo un `LEY` genera acción sugerida, y **hoy hay CERO `LEY`**. La mutación `RETROSPECTIVO → LEY` exige un test declarado antes del resultado; mientras no exista, ninguna cifra es accionable. El tipo lo estampa la **función pura** que emite cada métrica (en v0.2 se persiste en el esquema). **Inmutabilidad:** un veredicto emitido es un *snapshot* en `t` (INV-5); si llega dato nuevo, se emite un veredicto **nuevo versionado** — el viejo no se reescribe.

---

## 5. La matemática del eje-conducta (El Cuantitativo)

El operador no actúa sobre la media (cero, intocable). Actúa sobre el **operador de forma adaptado** `𝓢_g`: el funcional que deforma la distribución de resultados realizados usando solo `𝓕ₜ`. Problema bien planteado:

> **min `P(τ_b < τ_H)`  (ruina)   ∧   max `κ`  (captura asimétrica)   sujeto a   `E[R] = 0`.**

- **Control de varianza / ruina** — SÓLIDO, el piso. Sobre `E[R]=0`, Doob fija la media pero no la varianza: menor tamaño por trade → menor `P(ruina)`, monótono. **Hecho matemático, no consejo:** el instrumento **muestra** este hecho como `RETROSPECTIVO` (INV-7); **no** prescribe sizing (§8) y **no** lo co-renderiza con la racha de ganancia que motivó un sobre-sizing (§2).
- **`κ` (captura asimétrica)** — legal bajo INV-1 (lee `R` agregado para caracterizar forma). Tipo `RETROSPECTIVO`. **Límite v0.1:** la κ-por-FORMA-del-path (skew, colas, "corté pérdidas / dejé correr ganancias") necesita MAE/MFE por posición, que **no existe**. En v0.1 `κ` se reduce a un **ratio crudo de pnl realizados** `E[R|R>0]/|E[R|R<0]|` — pierde la semántica de "forma del path"; la κ-por-forma es v0.2.
- **"Aprovechar la ráfaga" (A3a)** — NO predecir la racha (humo, dañino). SÍ **retener la realización favorable ya ocurrida** vía trailing stop (mira el máximo pasado, `𝓕ₜ`). **"Minimizar la pérdida" (A3b)** = el mismo objeto del otro lado. Las dos mitades de A3 son la misma ecuación por sus dos colas. *(Construible pleno en v0.2 con MAE/MFE.)*

Las tres palancas del panel REV 3 caen exactas: **costo visible** = INV-4; **presupuesto de pérdida** = `τ_b`; **banderas de conducta** = medidores `RETROSPECTIVO` de κ/aguante.

---

## 6. Resolución de las preguntas de Adrian

- **Q5 — `exit_reason` ¿conducta o resultado? → es procedencia; entra una PROYECCIÓN.** Al plano-conducta entra `cierre_discrecional` (bool, §4.2), no el campo crudo. Caveats C (ratchet) y D (`cancelled`) resueltos en §4.1/§4.2.
- **Q4 — ¿traza seleccionadas-no-operadas? → NO existe (REFUTED).** `SELECTED`/`ABANDONED` y la disciplina de selección quedan **fuera del ciclo v0.1** (apéndice A). Confirma el parqueo del contrafactual.
- **Q2 — ¿"perdedor" sin `outcome`? → RESUELTO: la bandera #3 es LEGAL bajo INV-1 corregido.** "Aguantar perdedores" es una **regla de disciplina pre-declarada** ("sostener un perdedor más que tus ganadores es indisciplina, gane o no este trade"), evaluada contra esa regla, NO contra el resultado de cada trade. Lee `R` agregado para caracterizar forma — permitido por INV-1. **No es excepción.** Tipo `RETROSPECTIVO` (describe, no prescribe). **Justificada desde dentro del eje-conducta**, NO porque "el papá la exhibe" (Null Vale: ese criterio colapsa el plano). *Límite v0.1:* sin MAE/MFE, "perdedor" se define por el signo del P&L realizado del trade cerrado — aceptable como **caracterización agregada** (no validación puntual); la versión "aguantó mientras estaba underwater" es v0.2 (MAE/MFE).
- **Q1 — lista congelada de `conduct`:** §4.2 (`cierre_discrecional, apertura_discrecional, size_usd, entry_gap_to_prior_loser, hold_hours, costo_piso`). **Congelada** (Q2 resuelto).
- **Q3 — agregador canónico:** el **episodio individual** es la unidad atómica; **ventana-30** (banderas) y **semana-ISO** (presupuesto) son **vistas derivadas**.

---

## 7. Dónde vive / qué se reusa (corregido)

- **Proto-instrumento:** `tools/tenant_realization/report.py::compute_report` (función pura) es la base de v0.1, **pero NO es ya el eje-conducta limpio.** Hoy:
  - `descomposicion_q2` (MANUAL vs señal) aísla **parcialmente** la conducta. **Bug a corregir:** `MANUAL_REASONS={"MANUAL"}` (`report.py:28`) clasifica `MANUAL_AGENT` del lado **señal/resultado**, contradiciendo §4.2. Debe ampliarse a `{MANUAL, MANUAL_AGENT}`.
  - **Aviso de tipo (Null Vale):** la `descomposicion_q2` mide **concentración del P&L** (2 MANUAL = 63%) — eso es **atribución**, el `q2` de tipo-concentración, NO conducta-adaptada ni edge. Citarla como "proyección del eje-i" exige declarar que proyecta *concentración*, no ley-de-control.
  - `por_semana_iso` (los relojes) y `per_trade_pct.ci95` (el portón de ruido) ya existen. *(Estado vivo del CI omitido: corre contra prod, data familiar no-versionada; se referencia la PROPIEDAD "cruza cero ⇒ ruido", no un literal volátil.)*
- **v0.1 = monitor + 5 deltas enumerados** (no "el monitor actual tal cual"): (1) `costo_piso` v3 (INV-4, hoy ausente); (2) fix bucket `MANUAL_AGENT`; (3) proyecciones `conduct` de §4.2; (4) tope de pérdida editable (`τ_b`); (5) tipos `RETROSPECTIVO|RUIDO|LEY` por cifra (INV-7).
- **Cierre:** `PositionClosure` único camino (no-negociable #1). El read-model nunca escribe `positions`.
- **Multi-tenant:** `tenant_id` en `positions` (papá = **tenant 2**). Por-tenant, read-only sobre su sesión.
- **Panel REV 3** = primera proyección parcial del objeto; sus huecos (estados, agregadores, bandera #3) se cierran con este contrato.

---

## 8. Fuera de alcance (NO es esto)

Frontend; `CONDUCTING` vivo; disciplina de selección (`SELECTED`/`ABANDONED`); timeline intra-posición; MAE/MFE; **cualquier afirmación de edge**; **consejo de sizing** (el instrumento *muestra* el hecho de varianza, no prescribe); detección de rachas; el actuador de cierre-forzado (track C / v0.2 del panel).

---

## 9. Decisiones abiertas (de Samuel) + lo que habría que construir

1. **Bandera #3 — RESUELTA en REV 2 como legal bajo INV-1 corregido** (asunción confirmable). Si prefieres diferirla hasta MAE/MFE, dilo; la dejé activa como `RETROSPECTIVO` descriptiva en v0.1.
2. **`MANUAL_AGENT` — ¿conducta del operador? → REV 2 asume SÍ** (humano en el loop confirma el cierre). Confirmable. Implica corregir `MANUAL_REASONS` en el monitor.
3. **`CANCELLED` — ¿se surfacea en v0.1?** El estado existe en el contrato; el monitor hoy lo excluye. Decisión: abrir el fetch para incluirlo (delta barato) o diferirlo. **Recomiendo incluirlo** (es conducta pura de máxima atribución).
4. **Orden de las trazas a construir** (las que el modelo de datos hoy niega): **costo-piso v3** (barato) → **MAE/MFE por posición** (medio; habilita κ-por-forma y la bandera #3 limpia) → **traza de selección** (caro; des-parquea el contrafactual) → **captura de eventos de ratchet** (limpia la atribución de `SL_HIT`).
5. **¿Siguiente paso: plan de build de v0.1** (read-model + los 5 deltas), o re-roast de esta REV 2 antes?

---

## Apéndice A — DIFERIDO: la fase de selección (`SELECTED` / `ABANDONED`)

Fuera del ciclo de vida v0.1. La traza "seleccionadas-pero-no-operadas por tenant" **no existe ni es reconstruible fiable** (Q4 REFUTED): `notifications_sent` (`event_key='signal:{symbol}'`, sin `scan_id`) y `positions` son disjuntas; `scan_id` en `positions` es nullable. Para hacerla medible hace falta construir un registro `(tenant, scan_id, operated|declined)`. Hasta entonces, la disciplina de selección y el contrafactual realizado-menos-mecánico permanecen **parqueados** (orden de Samuel) y el frontend **no** debe renderizar estos estados.

---

*Fuente de verdad del entendimiento integrado. El frontend será su reflejo — y solo tendrá sentido cuando el `EpisodioDeConducción` sea de primera clase (v0.2), no un veredicto colgado de `positions` (Richter).*
