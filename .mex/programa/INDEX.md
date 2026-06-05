---
name: programa-index
description: Estado vivo de la Edición 1 del programa de investigación de edge. Tabla de celdas (verbo/estado/veredicto/coordenada/artefacto), candidatas a Edición 2, preguntas abiertas. Constitución: docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md. Runbook: ../patterns/estudiar-una-celda.md.
last_updated: 2026-06-05
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
| 3 | cross-sectional-factor | F | BLOQUEADA-POR-DATA | — | — | — |
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

## Candidatas a Edición 2

(Celdas nuevas descubiertas se anotan aquí — NO se estudian en la Edición 1.
Abrir la Edición 2 es un acto explícito con su propia junta. Ninguna por ahora.)

## Preguntas abiertas de la Edición 1

- Política de la bala única del holdout cross-celda (`HOLDOUT_FIRE_BUDGET=1`
  por ventana compartida — blocker #1 de Adrian, 2026-06-04). DIFERIDA: toda
  la Edición 1 es pre-holdout; ninguna celda dispara #322.
- Si el namespacing de `source` por celda en el trial-registry resulta
  insuficiente (lo evalúa el primer estudio F nuevo: stat-arb), el cambio al
  contrato del `selection_fingerprint` se especifica como estudio de impacto
  propio.

## Cierre de la Edición

La Edición 1 termina cuando las 9 celdas estén CERRADAS, cada una con el
artefacto de su verbo (spec §3). Al cerrar la 9ª: writeup final de la edición
y el programa queda CERRADO hasta acto de apertura de Edición 2.
