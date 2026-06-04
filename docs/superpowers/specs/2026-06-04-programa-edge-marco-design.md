# Programa de Investigación de Edge — Marco (Edición 1) — Design

**Fecha:** 2026-06-04
**Estado:** DISEÑO (constitución del programa) — pendiente de revisión de Samuel antes del plan.
**Lineaje:** double-FAIL direccional → PASS de funding-carry → directiva de Samuel: el proyecto se redefine como programa de investigación que estudia TODAS las celdas de edge, no escoge una ganadora. Junta de 7 lentes (Voronov/Adrian/Halberg/Lyra/Cassian/Null Vale/Richter) + Axiom-0 (2026-06-04). Voronov consultado ×3 (ontología del programa, deflación cross-celda, materialización).
**Rol de este documento:** constitución — adjudica en disputa, no gobierna el día a día. El runbook operativo es `.mex/patterns/estudiar-una-celda.md`; el estado vivo es `.mex/programa/INDEX.md`. (Ley de Voronov: autoridad = frecuencia de relectura forzada, no calidad de redacción.)
**No dispara:** holdout #322. Ninguna celda del programa toca `data/holdout/`.

---

## §0 · Invariante (Axiom-0)

> **Un veredicto no vale por su signo, sino por la incertidumbre que mata y el mundo donde la mata; lo que no reduce incertidumbre en ningún mundo no es conocimiento.**

Cada celda es un par `(incertidumbre-que-su-veredicto-destruye, mundo-donde-esa-incertidumbre-es-medible)`. **Catalogar** (asignar el par) y **comparar** (restar veredictos) son operaciones distintas: el programa es total sobre el catálogo y parcial sobre la comparación. De aquí se derivan todas las reglas de este marco.

**Estándar congelado:** riguroso interno. Pre-registro privado + gates de máquina + roster adversarial como peer-review. NO: peer-review humano externo, pre-registro público, forma paper (Richter: teatro para un operador solo).

---

## §1 · Función generadora y Edición 1

El programa no estudia "9 celdas" — estudia el espacio generado por:

```
tipo_de_edge(pagador_estructural, incertidumbre_que_caduca)
```

Dos celdas son distintas ⟺ tienen pagador estructural distinto Y su incertidumbre caduca en lugar distinto.

**Confesión pre-registrada de no-exhaustividad:** las 9 celdas de la junta 2026-06-03 ([[edge-landscape-funding-carry]]) NO son una partición del universo. Son la **primera carta de un atlas abierto** — una enumeración producida por un panel de 6 lentes con web research en una tarde. Las celdas se solapan (el propio carry es simultáneamente celda 2, pariente de la 4 y con componente de la 9), admiten interpolación, y el generador producirá celdas nuevas. Presentar esta cobertura parcial como marco cerrado sería la discrete-cells fallacy (Voronov, 7º reframe) aplicada al espacio de edges.

**Semántica de ediciones (condición de término):**
- La **Edición 1** congela su catálogo = las 9 celdas listadas en §2.
- La Edición 1 **termina** cuando cada una de las 9 tiene su artefacto de cierre según su verbo (§3). Al cerrar la 9ª celda se produce el writeup final de la edición y el programa queda CERRADO.
- Una celda nueva descubierta NO extiende la Edición 1: se anota en la lista de **candidatas** del INDEX. Estudiarla requiere un acto explícito de apertura de Edición 2 (con su propia junta y justificación).
- Razón (Null Vale): sin condición de término, el bucle FAIL→reformular-dominio no puede cerrarse nunca — "estudiar todo" se vuelve un loop semántico y la invulnerabilidad metodológica es indistinguible de la imposibilidad de aprender.

---

## §2 · Catálogo de la Edición 1 (congelado)

Asignación de verbo pre-registrada (base: dictamen de feasibility de Halberg, 2026-06-04):

| # | Celda | Verbo | Estado al congelar | Cierre esperado |
|---|---|---|---|---|
| 1 | Direccional un-activo (TA) | F | **CERRADA** — double-FAIL pre-registrado (IC-gate + blind-exit chandelier). Artefactos: `data/retune/2026-06-03-arm-a-blind-exit/` y previos | hecho |
| 2 | Carry/funding delta-neutral | F | **CERRADA-PASS** — 6.33%/año net-of-v3, CI95[5.02,7.45], 9/9 líquidos. Artefacto: `data/retune/2026-06-03-funding-carry-falsification/`. Hijos (shadow v0.1/v0.2, kill-rule, sizing) = proyectos hijos, FUERA de la unidad de estudio | hecho |
| 3 | Cross-sectional / factor | F | ABIERTA — bloqueada por data (necesita ~50-100 nombres; hoy ~10). Track 0 (ingest ancho Binance Vision spot) corre | estudio F cuando la data madure |
| 4 | Stat-arb (pairs/cointegración) | F | ABIERTA — **primer estudio pesado** de la edición (mejor ratio: reusa ingest+v3+gates de carry; `statsmodels` única dependencia nueva) | estudio F ~1 semana |
| 5 | Market-making | R | a cerrar por dictamen — PnL = función de queue position, inobservable en cualquier snapshot histórico; `walk_book` mide taker-cross (lo opuesto). Solo falsificable paper/live | dictamen R ~medio día |
| 6 | MEV / latencia | C | a cerrar por teorema — el edge ES latencia + order-flow privado subastado (PBS); sin acceso searcher/relay desde retail. Sin binding posible | teorema C ~medio día |
| 7 | Variance risk premium (opciones) | R | a cerrar por dictamen — REQUIERE-INFRA: cadenas históricas de pago (Tardis/Amberdata) + motor de griegas (v3 NO aplica a opciones). Proxy DVOL anotado como posible F-lite futuro | dictamen R ~medio día |
| 8 | On-chain flow | D | a cerrar por necrología — degradado post-ETF (flujos de custodia ETF no aparecen como exchange-flows clásicos). Fechar la muerte con fuentes | necrología D ~medio día |
| 9 | Event/calendar (token unlocks) | F | ABIERTA — segundo estudio pesado. Lado señal testeable retroactivo (calendario público + precios); survivorship bias DEBE corregirse pre-reg o el CI miente hacia arriba. El borrow/costo del short es inobservable en histórico → queda declarado como límite de realizabilidad (R) dentro del findings, no como gate | estudio F ~1 semana |

**No perseguir dentro de la Edición 1** (heredado de la junta 2026-06-03, sin cambio): cross-sectional sobre 10 símbolos, MEV operativo, on-chain data-mining NVT, airdrop farming.

---

## §3 · Los cuatro verbos de "estudiar"

"Estudiar" no es un acto único. Cada celda se estudia con UN verbo (asignado pre-registro en §2), que determina el tipo de artefacto y el criterio de cierre. Forzar los cuatro a un esquema común hace que tres mientan (p.ej. "MEV: N/A" se lee como "no medido aún" cuando significa "demostrado inaccesible").

### F — Falsificable in-silico
- **Acto:** correr un falsificador pre-registrado (dato histórico + cost-model + gates + deflación).
- **Artefacto (unidad de Cassian, 5 piezas):** spec pre-registrada commiteada ANTES de correr (`docs/superpowers/specs/`) · código determinista (`tools/<celda>/`, seed fijo, cero holdout) · `verdict.json` (PASS|FAIL + manifest + coordenada §4) · datos del veredicto (`per_symbol.json` o equivalente) · `findings.md` (veredicto línea 1, gates con números, scope explícito, qué-significa-PASS/FAIL).
- **Cierre:** verdict emitido + **poder declarado pre-verdict** — la spec pre-reg DEBE declarar el efecto mínimo detectable aproximado con el N disponible (vía ancho esperado del bootstrap CI o equivalente). Un FAIL sin poder declarado no distingue "no hay edge" de "no tenía cómo verlo" (cf. Brazo A, n=27) y NO cierra la celda.
- **Denominación obligatoria:** $-denominado, net-of-v3 (esquiva el mirage sharpe↔net_pnl).

### R — Realizabilidad-acotada
- **Acto:** caracterizar el gap entre el paper y lo realizable PARA ESTE OPERADOR (capital 5 cifras, retail, Windows local, sin colo).
- **Artefacto:** dictamen pre-registrado — criterios de descarte declarados ANTES de investigar (análogo cualitativo de un gate) + survey con 3-6 fuentes fechadas + `verdict.json` (INVIABLE-RETAIL | REQUIERE-INFRA-X) + findings.
- **Cierre:** verdict + **condición de reapertura explícita** ("reabre si: se compra data de cadenas / se corre un nodo / cambia X estructural"). Pre-registrado y falsable: el criterio se declaró antes de buscar, no se racionalizó después.

### C — Cerrada estructuralmente
- **Acto:** demostrar la cota de imposibilidad — la capacidad que el operador categóricamente no posee ni puede adquirir desde su posición.
- **Artefacto:** teorema de exclusión — "desde retail, este edge es inaccesible porque P" con P estructural y fuentes + `verdict.json` (EXCLUIDA).
- **Cierre:** verdict + condición de reapertura. Una celda C cerrada es **conocimiento terminal**, no backlog — el registro debe impedir que se relea como "pendiente".

### D — Degradada / históricamente contingente
- **Acto:** forense — fechar la muerte del edge y caracterizar qué la mató (cambio de régimen externo).
- **Artefacto:** necrología con causa y fuentes + `verdict.json` (DEGRADADA).
- **Cierre:** verdict + condición de reapertura (nueva hipótesis específica + fuente de data, no "revisitar a ver").

### Regla del atlas (la regla semántica central)
**Prohibida la comparación cardinal cross-verbo.** Los veredictos solo se comparan dentro del mismo `(verbo, mundo)`. Un "ranking de celdas por retorno" es un artefacto incoherente — es el error que el `selection_fingerprint` existe para prohibir, un nivel más arriba. La comparación legítima cross-celda es ordinal y tipada, nunca cardinal y agregada.
Esta regla vive operativamente como gotcha en el pattern (es semántica — un test solo puede vigilar su proxy sintáctico, §6).

---

## §4 · Procedencia A′ (coordenada de programa, no deflación inventada)

Dictamen de Voronov (2026-06-04): "deflactar por selección cross-celda" con N=celdas-enumeradas es un category error — la deflación (DSR) presupone N competidores intercambiables (mismo estimando, mismo mundo), y las celdas son formas lógicas distintas. El roster no seleccionó carry entre 9: lo tipó como la única celda F-testeable-YA (torneo de un competidor, nada que deflactar). Inventar una fórmula de "deflación cross-celda" dentro de este marco sería legislación disfrazada de aritmética.

**Lo que sí se hace:**

1. **Coordenada de procedencia.** Todo `verdict.json` nuevo carga:
   ```json
   "coordenada": {
     "edicion": 1,
     "celda": "<slug>",
     "verbo": "F|R|C|D",
     "n_f_corridas_a_la_fecha": <int>   // celdas F efectivamente CORRIDAS al momento del verdict, esta incluida
   }
   ```
2. **Retroactivo:** carry (`n_f_corridas=2`) y direccional (`n_f_corridas=1`) reciben su coordenada **en el INDEX**. Los fósiles de `data/retune/2026-06-03-*` NO se mutan (región inmutable).
3. **Regla de activación (la puerta que se define ahora, mientras N=2 y no cuesta nada):** el día que exista una ELECCIÓN entre ≥2 VERDICTs F PASS comparables (p.ej. para promoción a dossier de deploy), esa selección DEBE deflactarse con **N = celdas F corridas, nunca enumeradas** — la misma ley que `registering-a-trial.md` ya impone intra-celda ("N es DISTINCT configs comparables, no raw COUNT").

**Anti-contaminación del registry (blocker de Adrian, resuelto sin tocar el contrato del fingerprint):**
- Cada estudio F registra sus trials con `source`/`study_type` **namespaceado por celda** (convención: `celdaN-<slug>/<sweep>`), y computa su deflación-N **intra-celda** filtrando por su namespace.
- El N cross-celda solo entra por la regla de activación de arriba.
- El contrato del `selection_fingerprint` NO se modifica en esta edición. Si el primer estudio nuevo (stat-arb) demuestra que el namespacing no basta, el cambio al digest se especifica como estudio de impacto propio (no como side-effect).

**Holdout:** todo el programa es pre-holdout. #322 intacto. La política de la bala única (`HOLDOUT_FIRE_BUDGET=1` por ventana compartida entre celdas — blocker #1 de Adrian) queda **explícitamente diferida** y anotada como pregunta abierta de la Edición 1 en el INDEX. Ninguna celda dispara el holdout dentro de esta edición.

---

## §5 · Puerta estudio→deploy (el dossier)

Hallazgo de Null Vale: la frontera estudio→deploy hoy no existe — el PASS de carry cruzó a shadow factorizado en 3 sub-estimadores (decay / fricción / capital) que individualmente nunca autorizan ("deployability has no joint estimator yet"). Sin puerta, el deploy ocurre por acumulación de sub-PASSes sin que nadie apriete un botón.

**La puerta (pre-registrada desde el día 1, template en el pattern):**

Un edge solo cruza a deploy real (capital en exchange) mediante un **dossier de deploy** completo + **una decisión explícita única** de Samuel registrada en `mex log`. Campos del dossier:

1. Verdict F PASS con coordenada de procedencia (§4), deflactado según la regla de activación si hubo elección entre PASSes.
2. Realizabilidad vigente del shadow: decay del edge vs umbral pre-registrado, a la fecha del dossier (no a la fecha del PASS — el edge caduca en tiempo-de-mercado, el catálogo no).
3. Fricción de ejecución medida (estilo exec-realism v0.2), misma `calibration_identity`.
4. Sizing tail-aware contra el escenario de shock G2 pre-registrado.
5. Kill live pre-registrado (condición, umbral, quién/qué lo evalúa).
6. Decisión explícita: entrada en `mex log` con el hash del dossier.

**Regla dura:** ningún sub-PASS autoriza. El dossier completo es condición *necesaria*; la decisión explícita es la *suficiente*. La coincidencia de `calibration_identity_hash` entre sub-estudios NO constituye autorización (es exactamente el pathway de cruce-sin-decisión que Null Vale señaló).

---

## §6 · Materialización (cinco piezas, cuatro tipos)

| Pieza | Lugar | Tipo | Rol |
|---|---|---|---|
| Este spec | `docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md` | fósil de apelación | constitución: adjudica en disputa, no gobierna |
| Pattern | `.mex/patterns/estudiar-una-celda.md` **+ fila en `patterns/INDEX.md`** | releído por coacción (ROUTER) | runbook: pasos por verbo, gotchas (regla del atlas, namespacing, poder declarado), checklist de cierre |
| INDEX del programa | `.mex/programa/INDEX.md` | estado vivo mutable | tabla de la Edición 1 (celda/verbo/estado/artefacto/coordenada) + lista de candidatas a Edición 2 + preguntas abiertas (política holdout) |
| Test | `tests/test_programa_celdas.py` | candado sintáctico | red para el distraído (ver contrato abajo) |
| Artefactos por celda | `data/retune/programa-celdas/<celda>/` (nuevos) | fósiles de evidencia | verdicts, dictámenes, teoremas, necrologías. Los artefactos pre-existentes (carry, direccional) se referencian donde están, no se mueven |

**Cableado obligatorio (el acto de materialización, no opcional):** fila nueva en `.mex/patterns/INDEX.md` ("Abrir/correr/cerrar una celda del programa de edge → estudiar-una-celda.md") y referencia en el ROUTER si la routing table lo amerita. Un pattern no enrutado es un cuarto fósil (Voronov).

**Contrato del test (honestidad estilo `test_holdout_isolation.py`):**
- **Enforcement real (sintáctico):** (a) todo `data/retune/programa-celdas/*/verdict.json` declara `verbo ∈ {F,R,C,D}` y coordenada bien formada (§4); (b) toda fila de celda del INDEX tiene verbo válido y artefacto apuntado existente (para celdas cerradas).
- **Proxy declarado:** regex contra tablas/expresiones comparativas cardinales cross-verbo en el INDEX. Docstring obligatorio: *defense against a distracted human, not a motivated attacker* — la comparación en prosa la atrapa la revisión, no el regex. La regla semántica vive en el pattern.

---

## §7 · Negative space (qué NO se construye en la Edición 1)

- Scaffolder/generador de estudios (copiar `tools/funding_carry/` a mano hasta el 3er estudio F; abstraer solo cuando duela).
- Validador CLI / dashboard / LMS del programa (el INDEX a mano ES el programa).
- Fórmula inventada de "deflación cross-celda" (§4 — legislación disfrazada de aritmética).
- Cambios al contrato del `selection_fingerprint` (diferido a estudio de impacto propio si el namespacing no basta).
- Pre-registro público, peer-review humano externo, forma paper.
- Infra de opciones / nodo on-chain / recolector L2 (las celdas que los requieren se cierran por dictamen R con condición de reapertura).
- Nada que dispare #322.

---

## §8 · Riesgos pre-declarados

1. **El único PASS decae mientras el programa cataloga** (Lyra/Richter/Null Vale convergentes). Mitigación: el shadow de carry tiene su propio reloj y kill automático; el dossier exige realizabilidad VIGENTE (§5.2), no histórica. El programa no pausa el shadow.
2. **Identity shadow** (Null Vale): "académico cuando falla, trading cuando pasa". Mitigación estructural: la puerta §5 hace el cruce explícito e indivisible; las ediciones §1 hacen el término falsable.
3. **El proxy del test no atrapa comparaciones en prosa.** Aceptado y declarado; el backstop es la revisión de PR (mismo contrato que el holdout).
4. **Survivorship bias en la celda 9** y **p-hacking en cualquier reapertura de la 8**: ambos deben resolverse en la spec pre-reg de esa celda, no aquí.
5. **Re-apertura de celdas falsadas** (blocker #9 de Adrian): una celda CERRADA solo se reabre si se cumple su condición de reapertura pre-registrada; la reapertura entra al registry como población nueva (namespace nuevo). Re-correr una celda matada con parámetros nuevos "hasta que pase" es data-dredging y está prohibido por este marco.

---

## §9 · Estado al congelar y primer movimiento

- Cerradas: 2/9 (direccional double-FAIL, carry PASS).
- Tracks en paralelo (decisión 2026-06-04): **T0** ingest universo ampliado (~80 símbolos spot, Binance Vision — habilita celdas 3 y 4) · **T1** INDEX + dictámenes/teorema/necrología de las celdas 5, 6, 7, 8 (cierres baratos) · **T2** este marco (spec + pattern + INDEX + test).
- Primer estudio F pesado: **celda 4 (stat-arb)**. Segundo: celda 9 (unlocks, lado señal). Celda 3 cuando la data del T0 madure.

**Referencias:** junta 2026-06-04 (memoria `programa_investigacion_9_celdas.md`) · landscape 2026-06-03 (`edge_landscape_funding_carry.md`) · dictámenes Voronov ×3 (ontología/deflación/materialización) · feasibility Halberg · blockers Adrian · asignación Lyra · unidad Cassian · pathways Null Vale · principios Richter · Axiom-0.
