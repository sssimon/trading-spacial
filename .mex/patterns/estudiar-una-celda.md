---
name: estudiar-una-celda
description: Runbook del programa de investigación de edge (Edición 1). Use al abrir, correr o cerrar una celda del catálogo. Define los 4 verbos de estudio (F/R/C/D), sus artefactos de cierre, la coordenada de procedencia y la regla del atlas.
last_updated: 2026-06-04
---

# Estudiar una celda del programa de edge

## Purpose

El programa (constitución: `docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md`)
estudia el espacio `tipo_de_edge(pagador_estructural, incertidumbre_que_caduca)`.
Cada celda se estudia con UN verbo pre-asignado que determina su artefacto y su
criterio de cierre. El estado vivo es `.mex/programa/INDEX.md`. El candado es
`tests/test_programa_celdas.py`.

## When

Al abrir, correr o cerrar cualquier celda del catálogo de la Edición 1; al
descubrir una celda nueva (→ sección Candidatas del INDEX, NO se estudia en
esta edición); al preparar un dossier de deploy (spec §5).

## Steps

1. Lee `.mex/programa/INDEX.md` — verbo y estado de la celda. El verbo es
   pre-registro: NO se cambia sin reabrir la decisión contra el spec.
2. Según el verbo:
   - **F (falsificable in-silico):**
     1. Spec pre-registrada en `docs/superpowers/specs/` y commiteada ANTES de
        correr: hipótesis, gates numéricos, kill criteria, y **poder declarado**
        (efecto mínimo detectable con el N disponible — sin esto un FAIL no
        distingue "no hay edge" de "no tenía cómo verlo" y NO cierra la celda).
     2. Paquete `tools/<celda>/` clonando la forma de `tools/funding_carry/`
        (ingest → simulate → evaluate → run). Determinista, seed fijo, cero
        holdout. P&L $-denominado, net-of-v3.
     3. Trials con `source` namespaceado `celdaN-<slug>/<sweep>` (ver
        [registering-a-trial.md](registering-a-trial.md)); la deflación-N se
        computa intra-celda filtrando por ese namespace.
     4. UNA corrida del evaluador pre-registrado → `verdict.json` con
        `verdict: PASS|FAIL` + coordenada (ver Gotchas).
     5. Artefacto en `data/retune/programa-celdas/<celda>/` (verdict.json +
        datos + findings.md con el veredicto en la línea 1).
   - **R (realizabilidad-acotada):** dictamen pre-registrado — criterios de
     descarte declarados ANTES de investigar; survey con 3-6 fuentes fechadas;
     `verdict: INVIABLE-RETAIL | REQUIERE-INFRA-<x>`; condición de reapertura
     explícita en findings.md.
   - **C (cerrada estructural):** teorema de exclusión — la capacidad
     estructural faltante + fuentes; `verdict: EXCLUIDA`; condición de
     reapertura. Es conocimiento terminal, no backlog.
   - **D (degradada):** necrología forense — qué murió, cuándo, qué la mató,
     fuentes; `verdict: DEGRADADA`; condición de reapertura = hipótesis
     específica + fuente de data, no "revisitar a ver".
3. Actualiza la fila de la celda en `.mex/programa/INDEX.md` (estado,
   veredicto, coordenada, artefacto) y el `last_updated`.
4. `mex log "programa E1: celda <slug> cerrada — <verdict>"`.
5. Si era la 9ª celda: writeup final de la Edición 1; el programa queda
   CERRADO hasta acto explícito de apertura de Edición 2.

## Gotchas

- **Regla del atlas (semántica — la vigilas TÚ y la revisión de PR, no el
  test):** prohibida la comparación cardinal cross-verbo. Nunca un ranking de
  celdas por retorno; nunca un cardinal en el Veredicto de una celda R/C/D.
  El test solo atrapa el proxy sintáctico.
- **Coordenada de procedencia:** todo verdict.json carga
  `{"edicion", "celda", "verbo", "n_f_corridas_a_la_fecha"}` donde n_F =
  celdas F efectivamente **CORRIDAS** al momento del verdict (esta incluida).
  Corridas, nunca enumeradas (dictamen Voronov 2026-06-04).
- **Regla de activación de deflación cross-celda:** al ELEGIR entre ≥2
  VERDICTs F PASS comparables (p.ej. promoción a dossier de deploy), la
  selección se deflacta con N = celdas F corridas. No existe (ni se inventa)
  fórmula de "deflación cross-celda" fuera de ese caso.
- **Cero holdout (#322):** ninguna celda de la Edición 1 toca `data/holdout/`.
- **Fósiles inmutables:** `data/retune/` no se edita retroactivamente;
  correcciones de tipo/coordenada van al INDEX.
- **Reapertura:** una celda CERRADA solo se reabre si su condición de
  reapertura pre-registrada se cumple; la reapertura entra al registry como
  namespace nuevo. Re-correr "hasta que pase" = data-dredging, prohibido.
- **Deploy:** ningún sub-PASS autoriza. Solo el dossier completo (6 campos,
  spec §5) + decisión explícita de Samuel registrada en `mex log`.
- **Hijos fuera de la unidad:** shadow-deploys y sub-estudios post-PASS son
  proyectos hijos; no reabren ni extienden el estudio de la celda.

## Verify Checklist

- [ ] `python -m pytest tests/test_programa_celdas.py -q` pasa
- [ ] (F) spec pre-reg commiteada ANTES de la corrida, con poder declarado
- [ ] (R/C/D) criterios de descarte declarados ANTES del survey
- [ ] findings.md: veredicto en línea 1 + condición de reapertura (R/C/D) o
      qué-significa-PASS/FAIL (F)
- [ ] INDEX actualizado (fila + last_updated) + `mex log` emitido
- [ ] Nada tocó `data/holdout/`
