# Marco del Programa de Edge (Edición 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materializar el marco del programa de investigación de edge (spec `docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md`) en sus 4 piezas restantes: INDEX vivo, pattern enrutado, candado-test, y cableado al ROUTER.

**Architecture:** El spec (ya commiteado, `eaa0173`) es la constitución. Este plan crea: `.mex/programa/INDEX.md` (estado vivo de la Edición 1 con coordenadas retroactivas), `.mex/patterns/estudiar-una-celda.md` (runbook por verbo, cableado a `patterns/INDEX.md` y al ROUTER), y `tests/test_programa_celdas.py` (candado sintáctico honesto estilo `test_holdout_isolation.py`: checks reales + proxy declarado). Sin código de producción — el test valida markdown y JSON por forma.

**Tech Stack:** Python 3 + pytest (stdlib only: `json`, `pathlib`, `re`). Markdown para INDEX/pattern. Convenciones del repo: pattern frontmatter `name/description/last_updated`, candado con "Known limitations" declaradas.

**Branch:** `feat/programa-edge-marco` (desde main local, incluye el commit del spec).

---

### Task 0: Crear la rama

**Files:** ninguno (git)

- [ ] **Step 1: Branch desde main**

```bash
git checkout -b feat/programa-edge-marco
```

Expected: `Switched to a new branch 'feat/programa-edge-marco'`

---

### Task 1: Candado-test — tests del INDEX (failing)

**Files:**
- Create: `tests/test_programa_celdas.py`

- [ ] **Step 1: Escribir el test con los checks del INDEX**

Crear `tests/test_programa_celdas.py` con exactamente este contenido:

```python
"""Candado sintáctico del programa de investigación de edge (Edición 1).

Constitución: docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md (§6).
Runbook: .mex/patterns/estudiar-una-celda.md. Estado vivo: .mex/programa/INDEX.md.

Checks REALES (sintácticos, enforcement de verdad):
  1. ``.mex/programa/INDEX.md`` existe, tiene exactamente 9 filas de celda con
     verbo ∈ {F,R,C,D} y estado ∈ {ABIERTA, BLOQUEADA-POR-DATA, CERRADA}.
  2. Toda celda CERRADA tiene coordenada ``E<n>/<verbo>/n_F=<int>`` consistente
     con su columna Verbo, y su artefacto apuntado existe en el repo.
  3. Todo ``data/retune/programa-celdas/*/verdict.json`` declara verbo válido y
     coordenada bien formada (edicion, celda, verbo, n_f_corridas_a_la_fecha),
     con verdict del vocabulario de su verbo (spec §3).
  4. Tipado de columnas: filas con verbo R/C/D no llevan valores cardinales
     (%/$) en su columna Veredicto — un teorema/dictamen/necrología no tiene
     retorno. Darle la columna invita a releer "EXCLUIDA" como "pendiente de
     medir" (dictamen de Voronov, junta 2026-06-04).

Known limitations
-----------------
Este candado es **defense against a distracted human, not a motivated
attacker** (mismo contrato que ``test_holdout_isolation.py``). La regla
semántica real del marco — "prohibida la comparación cardinal cross-verbo"
(spec §3, Regla del atlas) — NO es decidible por regex: una comparación en
prosa ("la celda de carry rindió más que las cerradas") pasa este test. Esa
regla vive como gotcha en ``.mex/patterns/estudiar-una-celda.md`` y su
backstop es la revisión de PR. Este test solo atrapa el lapsus estructural:
columnas mal tipadas, coordenadas malformadas, artefactos colgantes.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / ".mex" / "programa" / "INDEX.md"
ARTEFACTS_DIR = REPO_ROOT / "data" / "retune" / "programa-celdas"

VERBOS = {"F", "R", "C", "D"}
ESTADOS = {"ABIERTA", "BLOQUEADA-POR-DATA", "CERRADA"}
COLUMNS = ("num", "celda", "verbo", "estado", "veredicto", "coordenada", "artefacto")

COORD_RE = re.compile(r"^E(?P<edicion>\d+)/(?P<verbo>[FRCD])/n_F=(?P<n_f>\d+)$")
# Valores cardinales: "6.33%", "27 %", "$1254", "1254 USD"
CARDINAL_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|USD)|\$\s*\d")


def _load_index_rows() -> list[dict[str, str]]:
    """Parsea las filas de celda (las que empiezan con ``| <num> |``) del INDEX."""
    text = INDEX_PATH.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        assert len(parts) == len(COLUMNS), (
            f"Fila de celda malformada ({len(parts)} columnas, esperaba "
            f"{len(COLUMNS)}): {line!r}"
        )
        rows.append(dict(zip(COLUMNS, parts)))
    return rows


# ---------------------------------------------------------------------------
# 1-2. INDEX: existencia, forma, celdas cerradas
# ---------------------------------------------------------------------------

def test_index_existe():
    assert INDEX_PATH.is_file(), (
        f"Falta el estado vivo del programa: {INDEX_PATH}. "
        "Ver spec 2026-06-04-programa-edge-marco-design.md §6."
    )


def test_index_tiene_9_celdas():
    rows = _load_index_rows()
    assert len(rows) == 9, f"La Edición 1 congela 9 celdas; el INDEX tiene {len(rows)}"


def test_index_verbos_y_estados_validos():
    for row in _load_index_rows():
        assert row["verbo"] in VERBOS, f"celda {row['celda']}: verbo {row['verbo']!r}"
        assert row["estado"] in ESTADOS, f"celda {row['celda']}: estado {row['estado']!r}"


def test_celdas_cerradas_coordenada_y_artefacto():
    for row in _load_index_rows():
        if row["estado"] != "CERRADA":
            continue
        m = COORD_RE.match(row["coordenada"])
        assert m, (
            f"celda {row['celda']}: CERRADA sin coordenada bien formada "
            f"(tiene {row['coordenada']!r}, esperaba E<n>/<verbo>/n_F=<int>)"
        )
        assert m.group("verbo") == row["verbo"], (
            f"celda {row['celda']}: verbo de la coordenada ({m.group('verbo')}) "
            f"≠ columna Verbo ({row['verbo']})"
        )
        artefacto = REPO_ROOT / row["artefacto"].rstrip("/")
        assert artefacto.exists(), (
            f"celda {row['celda']}: artefacto apuntado no existe: {row['artefacto']}"
        )
```

- [ ] **Step 2: Correr y verificar que FALLA por INDEX ausente**

Run: `python -m pytest tests/test_programa_celdas.py -v`
Expected: 4 tests, los 4 FAIL — `test_index_existe` con el mensaje "Falta el estado vivo del programa"; los otros 3 con `FileNotFoundError` desde `_load_index_rows`.

---

### Task 2: Crear `.mex/programa/INDEX.md` (los tests del INDEX pasan)

**Files:**
- Create: `.mex/programa/INDEX.md`

- [ ] **Step 1: Crear el INDEX con el catálogo congelado y coordenadas retroactivas**

Crear `.mex/programa/INDEX.md` con exactamente este contenido:

```markdown
---
name: programa-index
description: Estado vivo de la Edición 1 del programa de investigación de edge. Tabla de celdas (verbo/estado/veredicto/coordenada/artefacto), candidatas a Edición 2, preguntas abiertas. Constitución: docs/superpowers/specs/2026-06-04-programa-edge-marco-design.md. Runbook: ../patterns/estudiar-una-celda.md.
last_updated: 2026-06-04
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
| 5 | market-making | R | ABIERTA | — | — | — |
| 6 | mev-latencia | C | ABIERTA | — | — | — |
| 7 | vrp-opciones | R | ABIERTA | — | — | — |
| 8 | on-chain-flow | D | ABIERTA | — | — | — |
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
```

- [ ] **Step 2: Correr los tests del INDEX y verificar que PASAN**

Run: `python -m pytest tests/test_programa_celdas.py -v`
Expected: 4 PASS. Si `test_celdas_cerradas_coordenada_y_artefacto` falla por artefacto inexistente, verificar el nombre real del directorio con `Get-ChildItem data/retune/ | Select-String 2026-06-03` y corregir la fila del INDEX (no el test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_programa_celdas.py .mex/programa/INDEX.md
git commit -m "feat(programa): INDEX vivo Edicion 1 + candado del INDEX (4 checks)"
```

---

### Task 3: Validador de `verdict.json` (TDD dentro del módulo de test)

**Files:**
- Modify: `tests/test_programa_celdas.py` (append al final)

- [ ] **Step 1: Añadir tests unitarios del validador (failing)**

Append al final de `tests/test_programa_celdas.py`:

```python
# ---------------------------------------------------------------------------
# 3. verdict.json: esquema y vocabulario por verbo (spec §3-§4)
# ---------------------------------------------------------------------------

_VERDICT_OK_F = {
    "verdict": "PASS",
    "coordenada": {
        "edicion": 1,
        "celda": "stat-arb",
        "verbo": "F",
        "n_f_corridas_a_la_fecha": 3,
    },
}


def test_validador_acepta_verdict_f_valido():
    assert _validate_verdict(_VERDICT_OK_F) == []


def test_validador_acepta_dictamen_r():
    data = {
        "verdict": "REQUIERE-INFRA-opciones",
        "coordenada": {
            "edicion": 1,
            "celda": "vrp-opciones",
            "verbo": "R",
            "n_f_corridas_a_la_fecha": 2,
        },
    }
    assert _validate_verdict(data) == []


def test_validador_rechaza_verbo_invalido():
    data = {
        "verdict": "PASS",
        "coordenada": {
            "edicion": 1,
            "celda": "x",
            "verbo": "Z",
            "n_f_corridas_a_la_fecha": 1,
        },
    }
    assert any("verbo" in e for e in _validate_verdict(data))


def test_validador_rechaza_coordenada_ausente():
    assert any("coordenada" in e for e in _validate_verdict({"verdict": "PASS"}))


def test_validador_rechaza_vocabulario_cruzado():
    # Un verbo C no puede emitir "PASS" — su vocabulario es EXCLUIDA (spec §3)
    data = {
        "verdict": "PASS",
        "coordenada": {
            "edicion": 1,
            "celda": "mev-latencia",
            "verbo": "C",
            "n_f_corridas_a_la_fecha": 2,
        },
    }
    assert any("vocabulario" in e for e in _validate_verdict(data))


def test_verdicts_reales_en_artefactos():
    """Integración: todo verdict.json bajo data/retune/programa-celdas/ valida."""
    if not ARTEFACTS_DIR.is_dir():
        pytest.skip("aún no hay artefactos de celda nuevos — check vacuo")
    paths = sorted(ARTEFACTS_DIR.glob("*/verdict.json"))
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = _validate_verdict(data)
        assert not errors, f"{path.relative_to(REPO_ROOT)}: {errors}"
```

- [ ] **Step 2: Correr y verificar que FALLA con NameError**

Run: `python -m pytest tests/test_programa_celdas.py -v`
Expected: los 5 tests nuevos del validador FAIL con `NameError: name '_validate_verdict' is not defined`; `test_verdicts_reales_en_artefactos` SKIP (directorio no existe); los 4 del INDEX siguen PASS.

- [ ] **Step 3: Implementar `_validate_verdict`**

Insertar en `tests/test_programa_celdas.py`, justo después de `_load_index_rows`:

```python
def _validate_verdict(data: dict) -> list[str]:
    """Valida forma y vocabulario de un verdict.json del programa (spec §3-§4).

    Devuelve lista de errores (vacía = válido).
    """
    errors: list[str] = []
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        errors.append("'verdict' ausente o no-string")
        verdict = ""
    coord = data.get("coordenada")
    if not isinstance(coord, dict):
        errors.append("'coordenada' ausente o no-dict")
        return errors
    verbo = coord.get("verbo")
    if verbo not in VERBOS:
        errors.append(f"coordenada.verbo inválido: {verbo!r}")
    if not isinstance(coord.get("edicion"), int) or coord["edicion"] < 1:
        errors.append(f"coordenada.edicion inválida: {coord.get('edicion')!r}")
    if not isinstance(coord.get("celda"), str) or not coord.get("celda"):
        errors.append("coordenada.celda ausente")
    n_f = coord.get("n_f_corridas_a_la_fecha")
    if not isinstance(n_f, int) or n_f < 0:
        errors.append(f"coordenada.n_f_corridas_a_la_fecha inválida: {n_f!r}")
    # Vocabulario por verbo (spec §3): F→PASS|FAIL, R→INVIABLE-RETAIL|REQUIERE-INFRA-*,
    # C→EXCLUIDA, D→DEGRADADA. Cruzar vocabularios es el registro plano que miente.
    if verbo == "F" and verdict not in {"PASS", "FAIL"}:
        errors.append(f"vocabulario F es PASS|FAIL, no {verdict!r}")
    elif verbo == "R" and not (
        verdict == "INVIABLE-RETAIL" or verdict.startswith("REQUIERE-INFRA")
    ):
        errors.append(f"vocabulario R es INVIABLE-RETAIL|REQUIERE-INFRA-*, no {verdict!r}")
    elif verbo == "C" and verdict != "EXCLUIDA":
        errors.append(f"vocabulario C es EXCLUIDA, no {verdict!r}")
    elif verbo == "D" and verdict != "DEGRADADA":
        errors.append(f"vocabulario D es DEGRADADA, no {verdict!r}")
    return errors
```

- [ ] **Step 4: Correr y verificar que PASA**

Run: `python -m pytest tests/test_programa_celdas.py -v`
Expected: 9 PASS, 1 SKIP (`test_verdicts_reales_en_artefactos`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_programa_celdas.py
git commit -m "feat(programa): validador de verdict.json (esquema + vocabulario por verbo)"
```

---

### Task 4: Proxy tipado de columnas (R/C/D sin valores cardinales)

**Files:**
- Modify: `tests/test_programa_celdas.py` (append al final)

- [ ] **Step 1: Añadir el check tipado + autotest del detector**

Append al final de `tests/test_programa_celdas.py`:

```python
# ---------------------------------------------------------------------------
# 4. Proxy declarado: tipado de la columna Veredicto (ver docstring del módulo)
# ---------------------------------------------------------------------------

def test_detector_cardinal_funciona():
    """Autotest del regex — el proxy debe atrapar lo que dice atrapar."""
    assert CARDINAL_RE.search("PASS (6.33%/año net-v3)")
    assert CARDINAL_RE.search("carry de $1254 en tier chico")
    assert CARDINAL_RE.search("retorno 27 % total")
    assert not CARDINAL_RE.search("EXCLUIDA")
    assert not CARDINAL_RE.search("REQUIERE-INFRA-opciones")
    assert not CARDINAL_RE.search("DOUBLE-FAIL")


def test_filas_rcd_sin_valores_cardinales():
    """Una celda R/C/D no tiene retorno; un cardinal en su Veredicto es un
    error de tipo (spec §3) — o un lapsus que este proxy atrapa."""
    for row in _load_index_rows():
        if row["verbo"] not in {"R", "C", "D"}:
            continue
        assert not CARDINAL_RE.search(row["veredicto"]), (
            f"celda {row['celda']} (verbo {row['verbo']}): valor cardinal en "
            f"Veredicto: {row['veredicto']!r}. Los verbos R/C/D no tienen "
            "retorno — ver Regla del atlas (spec §3)."
        )
```

- [ ] **Step 2: Correr y verificar que PASA**

Run: `python -m pytest tests/test_programa_celdas.py -v`
Expected: 11 PASS, 1 SKIP.

- [ ] **Step 3: Commit**

```bash
git add tests/test_programa_celdas.py
git commit -m "feat(programa): proxy tipado de columnas (R/C/D sin cardinales) + autotest"
```

---

### Task 5: Pattern `estudiar-una-celda.md` + cableado (patterns/INDEX + ROUTER)

**Files:**
- Create: `.mex/patterns/estudiar-una-celda.md`
- Modify: `.mex/patterns/INDEX.md` (añadir fila a la tabla)
- Modify: `.mex/ROUTER.md` (añadir fila a la Routing Table)

- [ ] **Step 1: Crear el pattern**

Crear `.mex/patterns/estudiar-una-celda.md` con exactamente este contenido:

```markdown
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
```

- [ ] **Step 2: Añadir la fila a `.mex/patterns/INDEX.md`**

En la tabla de `.mex/patterns/INDEX.md`, después de la fila de `cost-model-v3.md`, añadir:

```markdown
| Abrir, correr o cerrar una celda del programa de edge (Edición 1) | [estudiar-una-celda.md](estudiar-una-celda.md) |
```

Y actualizar el frontmatter `last_updated:` a `2026-06-04`.

- [ ] **Step 3: Añadir la fila a la Routing Table de `.mex/ROUTER.md`**

En la tabla "Routing Table" de `.mex/ROUTER.md`, después de la fila de `verb-taxonomy.md`, añadir:

```markdown
| Estudiar una celda de edge (programa Edición 1), preparar dossier de deploy | `patterns/estudiar-una-celda.md` + `programa/INDEX.md` |
```

Y actualizar el frontmatter `last_updated:` del ROUTER a `2026-06-04`.

- [ ] **Step 4: Verificar consistencia con mex**

Run: `mex check`
Expected: sin findings nuevos REALES sobre los archivos tocados (los `MISSING_PATH` de URLs/globs son ruido conocido — ver CLAUDE.md §Interpreting `mex check` output).

- [ ] **Step 5: Commit**

```bash
git add .mex/patterns/estudiar-una-celda.md .mex/patterns/INDEX.md .mex/ROUTER.md
git commit -m "feat(programa): pattern estudiar-una-celda + cableado a patterns/INDEX y ROUTER"
```

---

### Task 6: Gate rápido + cierre

**Files:** ninguno (verificación)

- [ ] **Step 1: Correr el gate rápido completo**

Run: `python -m pytest tests/ -m "not network" -n auto -q`
Expected: todo verde (~49s). Si algo falla, NO es admisible asumir flake — leer el failure y resolver (ver `.mex/context/ci-discipline.md`).

- [ ] **Step 2: Registrar el cierre del T2**

```bash
mex log "programa E1: marco materializado (spec eaa0173 + INDEX vivo + pattern enrutado + candado test_programa_celdas). T2 done; siguen T1 (dictamenes 5/6/7/8) y T0 (ingest ancho)."
```

- [ ] **Step 3: Push y PR (requiere confirmación de Samuel)**

```bash
git push -u origin feat/programa-edge-marco
gh pr create --title "Programa de investigación de edge — marco Edición 1 (spec + INDEX + pattern + candado)" --body "..."
```

Expected: PR contra main con el spec + las 4 piezas. El PR es la primera revisión del backstop semántico (regla del atlas).

---

## Self-review del plan

- **Cobertura del spec:** §1/§2 → INDEX (Task 2); §3 → vocabularios del validador (Task 3) + pattern Steps (Task 5); §4 → coordenadas retroactivas en INDEX (Task 2) + coordenada en validador (Task 3) + gotchas de namespacing/activación (Task 5); §5 → gotcha de deploy en pattern (Task 5; el template del dossier vive en el pattern como gotcha — el dossier completo se redacta cuando un edge se acerque, YAGNI); §6 → las 5 piezas (Tasks 1-5, el spec ya está commiteado); §7 → nada del negative space se construye; §8 riesgo 3 → docstring del test (Task 1).
- **Placeholders:** ninguno — todo el contenido de archivos está completo en los tasks.
- **Consistencia de tipos:** `_load_index_rows`/`_validate_verdict`/`COORD_RE`/`CARDINAL_RE` definidos en Task 1/3 y usados con esas firmas en Task 3/4. Columnas del INDEX (7) = `COLUMNS` (7). Vocabularios del validador = §3 del spec = pattern Steps.
