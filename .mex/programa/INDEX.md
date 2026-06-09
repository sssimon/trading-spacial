---
name: programa-index
description: Estado vivo de la Edición 1 del programa de investigación de edge. Tabla de celdas (verbo/estado/veredicto/coordenada/artefacto), candidatas a Edición 2, preguntas abiertas. Constitución: docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md. Runbook: ../patterns/estudiar-una-celda.md.
last_updated: 2026-06-09
---

# Programa de investigación de edge — Edición 1

**Regla de lectura (semántica — la vigila la revisión de PR, no el test):**
prohibida la comparación cardinal cross-verbo. Los veredictos solo se comparan
dentro del mismo `(verbo, mundo)`. Esta tabla es un atlas tipado, no un
ranking. `tests/test_programa_celdas.py` atrapa el lapsus estructural; la
comparación en prosa la atrapa la revisión.

## Celdas (catálogo congelado 2026-06-04)

| # | Celda | Verbo | Estado | Veredicto | Coordenada | Artefacto |
|---|-------|-------|--------|-----------|------------|-----------|
| 1 | direccional-un-activo | F | CERRADA | DOUBLE-FAIL | E1/F/n_F=1 | data/retune/2026-06-03-arm-a-blind-exit/ |
| 2 | carry-funding | F | CERRADA | PASS (6.33%/año net-v3, CI95[5.02,7.45]) | E1/F/n_F=2 | data/retune/2026-06-03-funding-carry-falsification/ |
| 3 | cross-sectional-factor | F | ABIERTA | — | — | — |
| 4 | stat-arb | F | ABIERTA | — | — | — |
| 5 | market-making | R | CERRADA | INVIABLE-RETAIL | E1/R/n_F=2 | data/retune/programa-celdas/celda5-market-making/ |
| 6 | mev-latencia | C | CERRADA | EXCLUIDA | E1/C/n_F=2 | data/retune/programa-celdas/celda6-mev-latencia/ |
| 7 | vrp-opciones | R | CERRADA | INVIABLE-RETAIL | E1/R/n_F=2 | data/retune/programa-celdas/celda7-vrp-opciones/ |
| 8 | on-chain-flow | D | CERRADA | DEGRADADA | E1/D/n_F=2 | data/retune/programa-celdas/celda8-on-chain-flow/ |
| 9 | event-unlocks | F | ABIERTA | — | — | — |

Notas de coordenada retroactiva (los fósiles de `data/retune/2026-06-03-*` no
se mutan; la coordenada vive aquí): `n_F` = celdas F efectivamente CORRIDAS al
momento del verdict, esta incluida. Direccional corrió primera (n_F=1), carry
segunda (n_F=2). El PASS de carry NO está deflactado por selección cross-celda
— y no debe estarlo (dictamen Voronov 2026-06-04: el roster tipó el espacio,
no seleccionó; torneo de un competidor). La regla de activación vive en el
spec §4.

Los hijos del PASS de carry (shadow v0.1/v0.2, kill-rule, sizing) son
proyectos hijos FUERA de la unidad de estudio — no reabren la celda 2.

**Data del programa (T0, 2026-06-05):** panel de 187 símbolos spot 1h
(2021-01→2026-05, regla pre-registrada anti-survivorship con 73 delistados
retenidos) en `data/program_ohlcv.db` (regenerable:
`python -m tools.program_ingest.run`). Universo y cobertura:
`data/retune/2026-06-05-programa-t0-ingest/`. Esto desbloqueó la celda 3.
La selección fina de símbolos para cada estudio F es de ESE estudio.

## Candidatas a Edición 2

(Celdas nuevas descubiertas se anotan aquí — NO se estudian en la Edición 1.
Abrir la Edición 2 es un acto explícito con su propia junta.)

| Candidata | Origen | Sondeo pre-celda | ¿Abrir? |
|---|---|---|---|
| sentiment (Fear&Greed) | junta tipado 2026-06-09 (C1) | **FAIL** — contrarian gross-flat/negativa net-of-v3 post-2021 (CI95 pooled [−39.1,−5.1] excluye cero negativo). `data/retune/2026-06-09-fng-probe/` | **NO** (reapertura solo con hipótesis de-tendenciada por régimen, pre-registrada) |
| order-flow / microestructura | junta tipado 2026-06-09 (C2) | MUERTO-AL-LLEGAR — sin order book histórico (1 snapshot); inalcanzable retail; vecino de celdas 5 (MM) y 6 (MEV) cerradas | NO (requiere ingest forward L2/tick de meses) |
| fundamentales discrecionales | junta tipado 2026-06-09 (C3) | MUERTO-AL-LLEGAR — verbo discrecional (resucita q3_pass:false); parte medible = celda 8 (DEGRADADA) | NO (solo la cláusula de reapertura de celda 8: Chi/Chu/Hao USDT-flows net-v3) |

## Preguntas abiertas de la Edición 1

- Política de la bala única del holdout cross-celda (`HOLDOUT_FIRE_BUDGET=1`
  por ventana compartida — blocker #1 de Adrian, 2026-06-04). DIFERIDA: toda
  la Edición 1 es pre-holdout; ninguna celda dispara #322.
- Si el namespacing de `source` por celda en el trial-registry resulta
  insuficiente (lo evalúa el primer estudio F nuevo: stat-arb), el cambio al
  contrato del `selection_fingerprint` se especifica como estudio de impacto
  propio.
- **La moneda de costos de las celdas anchas (Voronov V1, 2026-06-05):** el
  mandato constitucional "net-of-v3" solo es cobrable en el dominio curado de
  10 símbolos (`tier_for_symbol` se niega fuera). La celda 4 definió **v3w**
  (extensión declarada, tier por dollar-volume, spec celda 4 §3-bis) y su
  verdict es net-of-v3w — incomparable con el PASS de carry sin re-pricing
  explícito. Las celdas 3 y 9 heredan el mismo problema. PENDIENTE: decidir si
  v3w se promueve a moneda estándar de celdas anchas o si cada celda declara
  la suya; la regla de activación de deflación cross-celda (marco §4)
  presupone conmensurabilidad que hoy NO existe entre carry y las anchas.

## Cierre de la Edición

La Edición 1 termina cuando las 9 celdas estén CERRADAS, cada una con el
artefacto de su verbo (spec §3). Al cerrar la 9ª: writeup final de la edición
y el programa queda CERRADO hasta acto de apertura de Edición 2.
