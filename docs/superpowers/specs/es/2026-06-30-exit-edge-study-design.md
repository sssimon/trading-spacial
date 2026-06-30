# ¿El exit de musikito tiene edge point-in-time? — Diseño (prueba de falsación)

**Fecha:** 2026-06-30
**Estado:** diseño aprobado (brainstorming), pendiente de implementación.
**Origen:** las tres reorientaciones midieron que NO hay edge de SELECCIÓN per-coin (ni debilidad, ni momentum, ni régimen — el gate salió INVERTIDO, PR #623). El único lugar donde la aguja se movió a edge fue el EXIT: el confirm study 2026-06-20 dio +3pp realizado con la escalera de musikito vs el stop mecánico — pero sobre el panel VIVO de Binance (survivorship-biased) y marcado FRÁGIL (`breakeven_p=1.5%`).

## Pregunta decisiva

¿El edge del exit (la escalera de targets + runner de musikito) **sobrevive el panel anti-survivorship point-in-time**, o se desvanece como la fragilidad advertía? La calibración del gate enseñó que el survivorship puede **voltear** conclusiones — el panel con delistadas hizo la inversión del gate más clara, no menos. Hay que correr el exit sobre el mismo panel honesto.

## Qué se mide

Sobre el panel anti-survivorship (`data/program_ohlcv.db`, hasta 2025-04-29), el retorno **realizado** de dos poblaciones bajo dos exits:

| Población | Exit = stop mecánico | Exit = escalera musikito |
|---|---|---|
| **Candidatas** (vivo AND `pos_in_30d_range≤0.25`) | (ya medido: −6 a −12%) | **NUEVO** |
| **B2** (cualquier alt viva, muestra) | (ya medido: −6.3%) | **NUEVO** |

**Dos comparaciones clave:**
1. **Exit-edge:** candidatas+escalera vs candidatas+stop. ¿El exit ayuda?
2. **Selection-edge (decomposición):** candidatas+escalera vs B2+escalera. ¿La *selección* aporta algo una vez que usas el buen exit? Si candidatas+escalera ≈ B2+escalera, el edge es **la escalera aplicada a cualquier entrada razonable**, no elegir — resultado central.

## Definición congelada del exit (reusada verbatim de `confirm_study.py`)

`ladder_return(entry, highs, lows, close_last)` con `TPS=[0.15,0.30,0.50,0.90]`, `FRACS=[0.25,0.25,0.20,0.15]`, `DISASTER=-0.50`, `HORIZON=30`:
- Entrada = `open` en t+1. Por cada target ascendente, si el `high` en [t+1..t+30] lo alcanza, vende esa fracción a ese precio.
- Si NO se cobró ningún target Y el `low` tocó −50% → toda la posición a −50% (catástrofe).
- El runner (fracción no vendida) se cierra al `close` de t+30.

El stop mecánico es el `rule_return` ya en `calib_study.py` (TP +20% / SL −12% / cierre t+14, SL primero).

## Criterio de aceptación (PRE-COMPROMETIDO — fijado antes de correr)

Se reporta TODO; el veredicto "hay edge del exit, vale construir la jugada" exige:
- **(a) Positivo:** la escalera sobre las candidatas realiza **mediana > 0** (hace plata, no "menos malo").
- **(b) Le gana al stop:** mediana(escalera) − mediana(stop) **≥ +5 pp**, Mann-Whitney one-sided `p<0.01`.
- **(c) Robusto:** (a)+(b) no se desvanecen al desglosar por sub-período (años) — que no sea un solo año.

**Decomposición reportada (no es gate, es información):** mediana(candidatas+escalera) vs mediana(B2+escalera). Si la selección no añade, el edge es el exit puro.

**Resultados posibles:**
- **PASA (a∧b∧c):** edge del exit real point-in-time → construir/despertar el Instrumento/la jugada de Valles (zona, targets, SL, runner — el esqueleto ya existe).
- **NO PASA / negativo:** ni el exit mecánico nos salva point-in-time. El edge sería puramente discrecional (no backtesteable); decisión separada de si construir el framework igual (el mecánico es un PISO; el humano discrecional mejora el mecánico, pero sin respaldo de backtest).

## No-negociables

- **#2/#3:** solo `data/program_ohlcv.db`, período hasta **2025-04-29** (antes del holdout 2025-04-30→2026-04-30). Sin `open_holdout`, sin `simulate_strategy`, sin `data/holdout/`.
- **#4:** no toca sizing/`RISK_PER_TRADE` (es un backtest de retorno por-trade, no de capital).

## Scope / salidas

Extensión chica de `data/retune/2026-06-23-calibracion-gate-regimen/calib_study.py` (reusa el panel + features + `ladder_return` verbatim). Un script `exit_study.py` + un test co-ubicado (sintético) + `exit_findings.md` con el veredicto. No sistema nuevo. El estudio NO entra a CI (vive en data/retune/, como los otros).

## Caveats que el findings DEBE declarar
- Aunque el panel retiene delistadas, su cobertura no es total (187 símbolos).
- El `ladder_return` usa fill intrabar OPTIMISTA (asume que se cobra el target si el high lo toca) — es una cota superior del exit mecánico; el discrecional real puede diferir en ambas direcciones.
- Retorno en USDT incluye beta de BTC.
- B2 es muestra del universo vivo (mismo sesgo de supervivencia que las candidatas — el delta entre ambos es lo informativo).
