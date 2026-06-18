# Evidencia: la firma estadística del canal "musikito de laptop" (2019)

**Fecha:** 2026-06-18
**Estado:** brief para sesión de roster. NO es spec todavía — es la evidencia que el roster debe interpretar.
**Origen:** Samuel — "no es adivinarlo, es evaluarlo estadísticamente lo que debes hacer y luego pasarlo al roster."

---

## 0. Por qué este documento existe

El detector de valles actual (`screener/valley_filter.py`) está conceptualmente roto:
busca **consolidación lateral de baja volatilidad** (rango de 84 días ≤ 25%), una métrica
de amplitud **invariante al orden** (ciega a la tendencia y a *dónde* está el precio dentro
de su rango). El polish de la vista única expuso que **TRX no es un valle** bajo ninguna
lectura honesta de la estrategia que queremos replicar.

La estrategia a replicar es la del canal real de Samuel, "musikito de laptop" (export de
Telegram, feb–may 2019). En vez de **adivinar** otra vez el criterio de selección (adivinar
produjo el detector roto), lo **medimos**: extrajimos las features técnicas pre-llamada de
cada moneda llamada y las contrastamos contra una línea base aleatoria de pares BTC del
mismo período. Este documento es el resultado de esa medición.

---

## 1. El dato

- **89 llamadas** con datos de klines (de ~102 parseadas) vs **130 muestras de línea base**
  (pares aleatorios del mismo universo y fechas).
- Features calculadas en `t` (el día de la llamada), antes de cualquier resultado.
- CSV crudo: `C:\Users\simon\.claude\uploads\musikito\setup_features.csv`.

### 1.1 Firma de selección (medianas, PICK vs BASE)

| Feature | PICK (n=89) | BASE (n=130) | Lectura |
|---|---|---|---|
| `pos_in_30d_range` | **0.165** | 0.256 | Compraba en el **cuarto inferior** de su rango de 30d |
| `pos_in_90d_range` | **0.195** | 0.258 | Idem en 90d — abajo, no arriba |
| `rsi14` | **38.7** | 45.4 | Más **sobrevendido** que el azar |
| `pct_vs_sma20` | **−6.35%** | −3.27% | Más por **debajo** de la SMA20 |
| `pct_vs_sma50` | **−9.12%** | −6.28% | Más por debajo de la SMA50 |
| `consol_30d` | **40.6%** | 49.1% | Consolidación reciente **más apretada** |
| `vol_3d_vs_30d` | **0.71** | 0.87 | Volumen reciente más **callado** |
| `drawdown_from_90h` | −37.5% | −41.7% | Similar (ambos lejos de máximos) |

**Lectura 1 (la firma es real y coherente):** musikito compraba **debilidad técnica en
corrección** — coins en la parte baja de su rango, sobrevendidas, por debajo de sus medias
móviles, en consolidación apretada y callada, dentro de un macro alcista (alt-season 2019).
NO compraba consolidación lateral neutral (lo que mide el detector actual), ni breakouts ya
disparados. La pieza que el detector actual **no mide y debería**: la **posición dentro del
rango** y el **contexto de tendencia** (sobreventa / distancia a SMA).

### 1.2 Resultado forward (la parte incómoda)

| Feature | PICK | BASE |
|---|---|---|
| `max_fwd_7d` (mediana) | **6.67%** | 7.56% |
| `max_fwd_14d` (mediana) | **9.92%** | 12.54% |

**Lectura 2 (el edge de selección NO es medible):** las llamadas **no superaron** a la línea
base aleatoria de alts en el mismo período — en la mediana, fueron **marginalmente peores**.
Todas las correlaciones feature→retorno son débiles (|r| < 0.25). La conclusión honesta: el
retorno de 2019 fue **beta del bull market de alts + timing de ciclo**, no selección de coin
individual. El edge real de musikito estaba en *cuándo* estaba operando (alt-season), no en
*cuál* coin elegía esa semana.

### 1.3 Caveats de datos (no sobre-interpretar)

- `hit_t1_7d` salió ≈0 para casi todas las picks: el parseo de los **precios target** del
  template ("SELL: t1 - t2 - t3 - t4") no fue confiable (los `buy_hi` del CSV no están
  normalizados a precio). Los retornos forward (`max_fwd_*`) SÍ son confiables (de klines).
  → No usar `hit_t1` como evidencia de nada.
- n=89 es chico; el período es un único régimen (alt bull 2019). La firma puede ser un
  artefacto del régimen, no una ley.
- La línea base es de pares BTC; en 2019 casi todo el universo era "alt en corrección dentro
  de bull". Eso puede **comprimir** la diferencia PICK vs BASE (todos se parecían).

---

## 2. Lo que el detector actual mide vs lo que la firma pide

`screener/valley_filter.py::measure_consolidation` calcula:
- `pct_rango = (hi−lo)/mediana` sobre 84 días → **amplitud, invariante al orden**.
- `semanas` = bloques de 7 días consecutivos con rango propio ≤ 25% → "sin spike semanal".
- `vol_percentil` (calculado, **no usado como filtro**).

`evaluate_symbol` filtra por `vivo AND en_rango`. **No mide:** posición-en-rango, RSI,
distancia a SMA20/50, dirección del volumen. Por eso marcaría igual una coin consolidando en
el **techo** de su rango (sobrecomprada) que una en el **piso** (la zona de musikito) — lo
opuesto de lo que la firma dice que importaba.

---

## 3. Las dos preguntas para el roster

**P1 — Detector.** Dado §1.1, ¿cómo se redefine el detector de "setup" para que capture la
firma medida (debilidad técnica en corrección: posición-en-rango baja + sobreventa + bajo
SMA + consolidación apretada) en vez de la amplitud lateral ciega que mide hoy? ¿Qué features
entran al filtro, cuáles quedan como hechos descriptivos, y cómo se mantiene la doctrina
anti-veredicto (exhibe hechos, nunca firma)?

**P2 — La verdad incómoda.** Dado §1.2 (sin edge de selección medible; el retorno fue
régimen/ciclo, no elección de coin), ¿qué implica eso para Valles? ¿Estamos construyendo un
"elige ganadores" que la evidencia dice que no existe como tal? ¿O la lectura correcta es que
Valles debe **exhibir el setup + ser honesto de que el edge depende del régimen** — lo cual
*refuerza* la doctrina anti-veredicto en vez de contradecirla? ¿Hace falta una pieza de
**contexto de régimen/ciclo** (¿es alt-season?) que hoy no existe?

> **Resolución de P2 (2026-06-18):** el subproyecto 1 (pieza de régimen "¿es alt-season?")
> implementa el eje de régimen. Ver `docs/superpowers/specs/es/2026-06-18-alt-season-regimen-design.md`.

## 4. Doctrina (no negociable, recordatorio)

Valles **exhibe hechos, nunca firma un veredicto** (5 candados server-side). Cualquier
detector corregido debe seguir siendo descriptivo: "esta coin muestra el setup que musikito
cazaba", nunca "esta coin va a subir". La costura "esto sale de tus niveles · la decisión es
tuya" se mantiene.
