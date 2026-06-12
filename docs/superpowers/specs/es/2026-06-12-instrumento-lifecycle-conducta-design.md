# El Instrumento — acompañante del lifecycle de conducta · Diseño

**Fecha:** 2026-06-12
**Rama:** `feat/instrumento-lifecycle-conducta`
**Un solo spec, construido por fases.** Cada fase es una rebanada vertical del MISMO instrumento, no un panel nuevo.

## §0 — Qué es, y por qué hasta hoy no existía

El sistema NO es un edge. Ese arco se cerró (Epic A waived, #316/#338/#357). El sistema es **un instrumento que acompaña al operador en su suerte vía control emocional**, midiendo el **eje conducta `i`** — la decisión adaptada, perpendicular al eje real donde el edge dio ~0. Ver `docs/superpowers/specs/es/2026-06-09-integracion-eje-conducta-spec.md`.

Las piezas A (`screener/valley_filter.py`), C (`research/dossier.py`) y D.1 (`screener/sr_levels.py` + `api/levels.py`) ya están en `main`, pero son **tres sensores espaciales pre-entrada** colgados de una tabla. El instrumento es un objeto **temporal**: vive entre `entry_ts` y `exit_ts`. La junta (Voronov, Null Vale, Halberg, Cassian) lo diagnosticó: *"apilar termómetros no hace un termostato"*; el sustrato *"no tiene memoria"* (ningún tipo conoce el verbo "aguantar"); y la integración *"existe en el CSS, no en el grafo de llamadas"*. El instrumento no existía porque se construyó solo la capa sensorial, y ninguna capa de acompañamiento del lifecycle donde la conducta realmente ocurre.

A/C/D.1 son los **órganos de entrada** del instrumento. La columna es el **lifecycle de la posición**.

## §1 — La decisión central: el instrumento deriva el plan y lo sostiene

El operador eligió: el instrumento **deriva** un plan desde D.1, el operador lo **confirma en un gate manual** (ahí entra su juicio + su gate de fundamentales, una vez, en frío), y a partir de ahí el instrumento **sostiene el plan como ley** y mide si el operador lo honró.

Esto NO es el instrumento usurpando el juicio del operador. Es **control emocional hecho máquina**: el plan se fija cuando el operador está tranquilo (en el gate); la autoridad del instrumento es la disciplina externa que las emociones del operador **no pueden renegociar** a mitad de la posición. La conducta `i` se mide como "¿honraste la ley que aprobaste, o la emoción la sobrescribió?".

**Guardrail de neutralidad (Null Vale):** el plan derivado es **disciplinado, no rentable**. Los TPs en las resistencias de D.1 no afirman "esto va a subir" — afirman "estas son las paredes; una salida disciplinada escalona contra ellas". El instrumento nunca dice que el plan gana; solo mide si se cumplió.

## §2 — El lazo de conducta es por venue

Una posición tiene un **`venue`/origen**. El instrumento conoce la conducta del operador por dos caminos, según el venue:

- **Binance** → lectura automática por el **enlace directo** que ya existe (`observed_orders` v0.3, `data/providers/binance_account.py`, `binance_sync.py`). Conducta = **hecho observado**.
- **Otras plataformas** (Bybit, KuCoin, etc.) → el operador **declara** la posición y la actualiza a mano. Conducta = **declarada**.

**Invariante de honestidad de fuente:** cada evento y cada campo de conducta lleva su **procedencia** (`observado` | `declarado`). El instrumento **NUNCA** hace pasar un `declarado` por un `observado` — el "límite que miente" que Null Vale marcó. Un auto-reporte jamás se lava como observación.

El instrumento **nunca ejecuta** órdenes (claves read-only + Telegram one-way + gate manual). Deriva el plan, sigue, alerta; el operador ejecuta en el venue.

## §3 — Arquitectura por fases (el consenso del roster)

El roster (Cassian, Voronov, Halberg) convergió en el orden. NO es el vivo-con-dinero primero: ese pone el primer bug sobre capital real contra una máquina de estados que aún no existe (Halberg: sin domicilio de estado, sin idempotencia, y contradiciendo el filtro CD-1 que hoy protege de cerrar EXTERNAL por error). La discrepancia única —¿columna *diseñada* pura (C) o *descubierta* de lo real (Voronov)?— se reconcilia: **la columna pura, cuyo primer acto es ser falsada contra lo real.**

### Fase 1 — La columna, falsada contra lo real
- `derive_plan(sr_levels, entry) → Plan` (puro). §4.
- Máquina de estados del lifecycle `step(estado, evento) → estado` (pura), transiciones idempotentes por `order_id`/escalón. §5.
- **Domicilio del estado del plan**: tabla nueva que guarda el estado incremental (qué escalón tocó, SL movido). No se re-deriva cada tick. §5.
- **Arnés de falsación**: re-juega la máquina sobre las posiciones `REALIZED` reales del operador (read-only) y confirma que reproduce lo que pasó. §6.
- **Frontera dura:** cero vivo, cero `CONDUCTING`, cero escritura a `positions.status`, cero `PositionClosure`. Solo deriva, clasifica, falsa.

### Fase 2 — Backtest determinista
Alimenta la MISMA máquina pura con frames históricos deterministas → el "cierre determinista para un backtest funcional" que el operador pidió. Segundo refutador de la columna.

### Fase 3 — Acompañante en vivo (`CONDUCTING`)
Una vez que la transición es un oráculo, expón la máquina en vivo: deriva → gate → sigue la posición real (Binance auto + otros declarados) → alerta en cada transición (TP tocado, hora de mover SL a BE) → mide conducta. Tercer refutador (libro de fills vivo). Aquí cuelga la **tarjeta de selección compuesta** (A+C+D.1 en una vista por moneda) como cara de entrada. Toca `PositionClosure` (no-negociable #1) — se diseña su propio sub-plan cuando lleguemos.

**Falsación progresiva:** envelopes reales (F1) → simulación histórica determinista (F2) → libro de fills vivo (F3). Cada fase trae un refutador nuevo; la columna se prueba un mundo a la vez.

## §4 — `derive_plan` (Fase 1, puro)

Dado las zonas de D.1 (`detect_levels` → resistencias y soportes) + `entry_price`, produce un `Plan` inmutable:

```
Plan {
  entry_price:  float
  entry_zone:   SrZona | None      # la zona soporte de D.1 donde se sienta el entry
  sl_price:     float              # bajo el soporte inmediato (piso de D.1), con margen
  rungs: [                         # escalera ascendente, una por resistencia sobre el entry
    { tp_price: float,             #   = centro de la resistencia de D.1
      size_frac: float,            #   fracción de la posición a salir en este escalón
      zona_origen: SrZona }
  ]
  runner_frac:  float              # fracción que queda ABIERTA sin TP ("OPEN TARGET")
  be_rule:      "mover SL a entry_price tras llenarse rungs[0] (TP1)"
}
```

### Derivación (todo desde D.1, cero claim de rentabilidad)
- **SL** = bajo el `piso` (soporte inmediato bajo el entry) de D.1. `sl_price = piso.precio_bajo * (1 - SL_MARGIN_PCT)`. Margen calibrable (default elegido: con colchón, para que un mecha no saque).
- **Escalera de TPs** = las resistencias de D.1 por encima del entry, ascendentes: `rungs[0].tp_price = techo.centro` (resistencia inmediata), luego las siguientes. Cap en `MAX_RUNGS = 4` (TP1–TP4).
- **OPEN TARGET (runner)** = `runner_frac` queda abierta sin TP y cabalga; su SL queda en BE. **Activado por default** (práctica real del operador en su canal de 2019).
- **Tamaños parciales** front-loaded, `rungs[0].size_frac ≥ 0.50`. Default calibrable:

  | Escalón | Fracción |
  |---|---|
  | TP1 | 0.50 |
  | TP2 | 0.20 |
  | TP3 | 0.15 |
  | TP4 | 0.10 |
  | OPEN TARGET (runner) | 0.05 |

  Si D.1 encuentra **menos de 4 resistencias**, la escalera se trunca a las disponibles y las fracciones se **renormalizan** (reservando siempre `runner_frac` si el runner está activo). La suma de `size_frac` de los rungs + `runner_frac` = 1.0.
- **Regla BE** = al llenarse TP1, el SL se mueve a `entry_price`. De ahí en adelante la posición no puede perder.

### Constantes de arranque (calibrables)
```python
MAX_RUNGS      = 4
SL_MARGIN_PCT  = 0.01    # colchón bajo el borde del soporte
RUNNER_ON      = True    # reserva la fracción OPEN TARGET
SIZE_SCHEDULE  = [0.50, 0.20, 0.15, 0.10]   # front-loaded; el resto va al runner
RUNNER_FRAC    = 0.05
```

## §5 — La máquina de estados del lifecycle (Fase 1, pura)

### El estado vive en una tabla (su domicilio) — no se re-deriva cada tick

```
LifecycleState {
  plan_id:             int
  fase:                'PLANNED' | 'CONFIRMED' | 'RUNNING' | 'CLOSED'
  rungs_llenos:        set[int]        # índices de escalón ya tocados
  consumed_order_ids:  set[str]        # idempotencia: cada order_id se consume UNA vez
  sl_actual:           float           # dónde está el SL ahora
  be_movido:           bool            # ¿se movió a break-even?
  size_restante_frac:  float           # cuánta posición queda viva
  close_reason:        str | None      # al llegar a CLOSED
}
```

### El reductor puro `step(estado, evento) → estado`

Cada evento lleva su **procedencia** (`observado` | `declarado`).

| Evento | Transición | Idempotencia |
|---|---|---|
| `PLAN_CONFIRMED` (gate) | `PLANNED → CONFIRMED` | no-op si ya ≥ CONFIRMED |
| `RUNG_FILLED(order_id, i)` | marca rung `i` en `rungs_llenos`, resta `size_frac`; si `i==0` habilita BE; pasa a `RUNNING` | **no-op si `order_id ∈ consumed_order_ids`** |
| `SL_MOVED(nuevo_sl)` | `sl_actual = nuevo_sl`; si `nuevo_sl == entry_price` ⟹ `be_movido = True` | — |
| `STOP_HIT` | `→ CLOSED`, reason `BE_HIT` si `be_movido` else `SL_HIT` | no-op si ya CLOSED |
| `MANUAL_EXIT` | `→ CLOSED`, reason `MANUAL` (fuera de plan) | no-op si ya CLOSED |
| `POSITION_GONE` | `→ CLOSED`, reason `RECONCILED` | no-op si ya CLOSED |

**Idempotencia clave (Halberg):** `RUNG_FILLED` se cobra por `order_id`, NO por precio. Un TP parcialmente lleno que reaparece en el snapshot de Binance no se cuenta dos veces — su `order_id` ya está en `consumed_order_ids`. Esto resuelve el doble-conteo que Halberg detectó en el modelo snapshot de `observed_orders`.

### Qué produce al cerrar
Al llegar a `CLOSED`, emite un **`EpisodioDeConducción` `REALIZED`** (vocabulario de la spec del eje-conducta `2026-06-09`, §4.1): la secuencia de eventos realizados vs. el plan confirmado. De ahí salen los campos de conducta (§7).

### Frontera dura (Fase 1)
El reductor es **puro**: no llama a Binance, no escribe `positions.status`, no toca `PositionClosure`. El `→ CLOSED` del reductor es del **estado del plan**, no del cierre real de la posición (eso sigue siendo territorio exclusivo de `PositionClosure` en la Fase 3).

## §6 — El arnés de falsación (Fase 1)

Toma las posiciones **ya cerradas y reales** del operador (`status='closed'`, tenant EXTERNAL, **read-only**). Para cada una:
1. Reconstruye las zonas de D.1 **al momento de la entrada** (velas diarias históricas vía el fetch de D.1).
2. Corre `derive_plan` → el plan que el instrumento *habría* derivado.
3. Re-juega la secuencia de eventos a través de `step(...)` y confirma que la máquina llega a `CLOSED` reproduciendo lo que **de verdad pasó** (el envelope: entry / SL / TP / exit reales coherentes con el plan).

Si la máquina no reproduce una posición real → la máquina está mal. Ese es el refutador (Voronov: *"la columna se descubre, no se diseña"*).

**Honestidad sobre la resolución (Halberg):** `observed_orders` es un snapshot, no un libro de fills históricos. La Fase 1 falsa lo que los datos permiten: consistencia del **envelope** + secuencias de evento **sintéticas**. La falsación de la secuencia completa de fills requiere el libro que la Fase 3 empieza a capturar. El arnés **reporta honestamente qué pudo refutar y qué no** (cuántas posiciones reproducidas, cuántas con datos insuficientes).

## §7 — Los campos de conducta `i`

### La medición es independiente del PnL (la tesis entera)
Un trade puede **perder y ser conducta perfecta** (aguantaste el plan, el SL pegó donde debía) o **ganar y ser conducta pobre** (saliste en pánico en TP1 y la suerte acompañó). El instrumento **nunca** califica el trade por si ganó — mide si honraste la ley que aprobaste en frío. Eso es cobrar en el eje `i`, perpendicular al eje real de la suerte.

### Los campos (cada uno un HECHO, con procedencia `observado | declarado`)
Al cerrar el episodio, comparando la secuencia realizada contra el plan confirmado:

| Campo | Qué mide |
|---|---|
| `entry_en_zona` | ¿entró dentro de la `entry_zone` del plan, o persiguió fuera? |
| `sl_respetado` | ¿mantuvo el SL del plan, o lo **ensanchó** para evitar la pérdida? |
| `adherencia_be` | tras TP1, ¿movió el SL a break-even? |
| `rungs_honrados` | cuántos escalones del plan se llenaron vs. planeados |
| `escalono_vs_panico` | ¿salida escalonada por los TPs, o salida única fuera de plan? |
| `cierre_en_plan` | ¿cerró por TP/SL/runner (en plan) o por `MANUAL_EXIT` fuera de plan? (`cierre_discrecional` de la spec) |
| `hold_hours` | `exit_ts − entry_ts` |

### Sin score de conducta
Igual que A/C/D.1: **cero número único de "calidad de conducta".** Solo los campos. Mezclar adherencia + aguante + pánico en una cifra violaría la no-mezcla de tipos (INV-7 de la spec del eje-conducta) y volvería el instrumento un oráculo. El humano lee los hechos de su propia conducta y se ve a sí mismo — un espejo honesto, no un juez.

## §8 — Flujo, errores, pruebas (Fase 1)

- **Pureza:** `derive_plan` y `step` son funciones puras → TDD exhaustivo, sin red, sin DB. Hermanos de `screener/valley_filter.py` y `screener/sr_levels.py`.
- **Domicilio del estado:** tabla nueva (migración en `db/schema.py` siguiendo el patrón de las migraciones existentes); escrituras en tx cortas; la lectura de venue va **fuera** de tx.
- **Falla de venue sin corromper estado:** el reductor solo avanza con eventos **confirmados**. Si Binance se cae o devuelve un snapshot truncado, **no se emite evento** ese tick — la máquina no avanza. Nunca avanza con datos a medias (equivalente al `ingest_incompleto`/F8 que Halberg citó).
- **Procedencia honesta:** cada evento y campo de conducta lleva `observado | declarado`; nunca se lava un declarado como observado.
- **Frontera dura F1:** cero escritura a `positions.status`, cero `PositionClosure`, cero vivo.
- **Pruebas:**
  - Unidad pura: `derive_plan` (SL bajo soporte con margen; escalera = resistencias; truncado+renormalización con <4 resistencias; runner reservado; suma de fracciones = 1.0; BE rule presente).
  - Reductor: cada transición de la tabla §5; **idempotencia por `order_id`** (un `RUNG_FILLED` repetido es no-op); no-op tras CLOSED.
  - Domicilio: migración de la tabla; persistencia incremental.
  - Arnés de falsación: read-only sobre posiciones reales; reporte honesto de huecos (reproducidas vs. datos insuficientes).

## §9 — Fuera de alcance de la Fase 1 (fases siguientes del mismo spec)
- **Fase 2:** backtest determinista (alimentar la máquina con frames históricos).
- **Fase 3:** acompañante vivo (`CONDUCTING`), alertas en tiempo real, integración con `PositionClosure`, sincronización venue↔plan en runtime (con la resolución de las contradicciones CD-1 y la race sync↔stops que Halberg detectó), libro de fills vivo, y la tarjeta de selección compuesta A+C+D.1 como cara de entrada.
- **Esquema multi-TP en `positions`:** hoy `positions` tiene un solo `sl_price`/`tp_price`. El modelo multi-rung vive en el domicilio nuevo de la Fase 1; cuándo (si) se fusiona con `positions` es decisión de la Fase 3.
