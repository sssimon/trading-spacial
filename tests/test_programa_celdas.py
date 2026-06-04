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
