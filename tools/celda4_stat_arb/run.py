"""Orquesta la falsificación de la celda 4 (stat-arb) end-to-end → artefactos.

Run:  python -m tools.celda4_stat_arb.run [--dry-run]
Lee `data/program_ohlcv.db` (perp_klines + perp_funding) en READ-ONLY. Escribe
sólo bajo OUTPUT_DIR. CERO holdout (ningún módulo del paquete toca el dataset
bloqueado ni su lector — candado test_paquete_no_referencia_holdout).

ORDEN (spec §5, F10): fingerprint (F14) → form_pairs → simulate → compute_power
(escribe power.json) → evaluate → verdict.json (o killed.json si el estudio muere
por un kill criterion).

**LA CORRIDA REAL ES ONE-SHOT** (spec): requiere decisión humana explícita. El
flag `--dry-run` para DESPUÉS del fingerprint (prueba la orquestación sin quemar
la bala). Este módulo NO se corre como parte de la implementación.

verdict.json valida contra tests/test_programa_celdas.py::_validate_verdict
(verbo F, vocabulario PASS|FAIL). Kills (N-INSUFICIENTE / ARTEFACTO) NO se
escriben como verdict.json (violarían el vocabulario F) — se escriben en
killed.json y la celda permanece ABIERTA.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from backtest_costs import (
    calibration_identity_hash,
    load_calibration,
)

from . import evaluate as evaluate_mod
from . import pairs as pairs_mod
from . import power as power_mod
from . import simulate as simulate_mod
from .constants import (
    OUTPUT_DIR,
    SOURCE_PRIMARY,
    STUDY_END,
    STUDY_START,
)
from .costs import derive_tier_cutoffs

DB_PATH = "data/program_ohlcv.db"

# Coordenada al verdict (spec preámbulo / §8): E1/F/n_F=3.
COORDENADA = {
    "edicion": 1,
    "celda": "stat-arb",
    "verbo": "F",
    "n_f_corridas_a_la_fecha": 3,
}


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ro(db_path: str):
    return closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True))


def input_fingerprint(con, study_end_ms: int) -> dict:
    """Fingerprint del input (spec §3 F14), acotado a < STUDY_END.

    Congela: membresía exacta panel∩perps realmente USADA (símbolos con barras
    < STUDY_END), row-counts por símbolo en perp_klines/perp_funding < STUDY_END,
    y min/max open_time por símbolo (< STUDY_END). NINGUNA consulta cruza la
    frontera del holdout en TIEMPO (NV-A).
    """
    klines: dict[str, dict] = {}
    rows = con.execute(
        "SELECT symbol, COUNT(*), MIN(open_time), MAX(open_time) "
        "FROM perp_klines WHERE open_time < ? GROUP BY symbol ORDER BY symbol",
        (study_end_ms,),
    ).fetchall()
    for sym, cnt, mn, mx in rows:
        klines[sym] = {"rows": int(cnt), "min_open_time": int(mn), "max_open_time": int(mx)}

    funding: dict[str, int] = {}
    frows = con.execute(
        "SELECT symbol, COUNT(*) FROM perp_funding WHERE funding_time_ms < ? "
        "GROUP BY symbol ORDER BY symbol",
        (study_end_ms,),
    ).fetchall()
    for sym, cnt in frows:
        funding[sym] = int(cnt)

    members = sorted(klines)
    return {
        "study_window": [STUDY_START, STUDY_END],
        "study_end_ms": study_end_ms,
        "panel_perps_members": members,
        "n_members": len(members),
        "perp_klines_by_symbol": klines,
        "perp_funding_rowcount_by_symbol": funding,
    }


def register_trial(verdict: str, fingerprint: dict) -> int:
    """Registra la corrida primaria en el ledger `db/trials.py` (tabla `trials`).

    source = SOURCE_PRIMARY ("celda4-stat-arb/primary"); study_type="confirmatory"
    (estudio pre-registrado, NO una sweep de selección — la pre-registro protege
    contra selección post-data; la deflación-N de la primaria se congela en SU
    corrida, spec §7 F12). claim-then-execute: claim ANTES, finalize DESPUÉS.

    Modo de fallo CLARO: si el ledger no está disponible (durabilidad agotada),
    `claim_trial`/`finalize_trial` RAISE — este wrapper NO lo silencia (spec/plan:
    la registración no se salta en silencio). El llamador (main) decide si la
    corrida real aborta; los tests lo ejercitan con un tmp db monkeypatcheado.

    Devuelve el trial_id. Importa claim_trial/finalize_trial al TOP de la función
    (vía el módulo db.trials) para que los tests puedan monkeypatchear
    `tools.celda4_stat_arb.run.claim_trial` / `.finalize_trial`.
    """
    combo = {
        "config": "primary",
        "celda": "stat-arb",
        "study_window": [STUDY_START, STUDY_END],
        "n_members": fingerprint.get("n_members"),
    }
    trial_id = claim_trial(
        source=SOURCE_PRIMARY,
        combo=combo,
        window_label=f"{STUDY_START}..{STUDY_END}",
        study_type="confirmatory",
    )
    finalize_trial(trial_id, status="ok", metrics={"verdict": verdict})
    return trial_id


# Importadas al MÓDULO (no dentro de register_trial) para que el monkeypatch de
# los tests sobre `tools.celda4_stat_arb.run.claim_trial` tenga efecto.
from db.trials import claim_trial, finalize_trial  # noqa: E402


def _build_positions(con, cutoffs: dict, calibration) -> tuple[list[dict], list[dict]]:
    """Itera trading_windows: eligible → form_pairs → simulate_window. Devuelve
    (positions, pairs_formed). Respeta la frontera STUDY_END vía simulate."""
    positions: list[dict] = []
    pairs_formed: list[dict] = []
    for fstart, fend, tstart, tend in simulate_mod.trading_windows():
        eligible = pairs_mod.eligible_symbols(con, fstart, fend)
        if not eligible:
            continue
        formed = pairs_mod.form_pairs(con, eligible, fstart, fend)
        pairs_formed.extend(formed)
        if not formed:
            continue
        positions.extend(
            simulate_mod.simulate_window(con, formed, tstart, tend, cutoffs, calibration)
        )
    return positions, pairs_formed


def main(argv: list[str] | None = None, *, db_path: str = DB_PATH,
         output_dir: str = OUTPUT_DIR, run_date: str | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Celda 4 stat-arb falsificador (one-shot)")
    parser.add_argument("--dry-run", action="store_true",
                        help="para tras el fingerprint (no quema el one-shot)")
    args = parser.parse_args(argv)

    os.makedirs(output_dir, exist_ok=True)
    study_end_ms = _to_ms(STUDY_END)

    with _ro(db_path) as con:
        # Assert: ingest presente (perp_klines no vacío < STUDY_END).
        n_klines = con.execute(
            "SELECT COUNT(*) FROM perp_klines WHERE open_time < ?", (study_end_ms,)
        ).fetchone()[0]
        if not n_klines:
            raise RuntimeError(
                f"ingest ausente o vacío: perp_klines sin barras < {STUDY_END} en "
                f"{db_path}. La corrida real requiere el ingest completo."
            )

        fingerprint = input_fingerprint(con, study_end_ms)

        if args.dry_run:
            with open(os.path.join(output_dir, "fingerprint.json"), "w", encoding="utf-8") as f:
                json.dump(fingerprint, f, indent=2)
            print(f"[dry-run] fingerprint: {fingerprint['n_members']} símbolos panel∩perps")
            return {"dry_run": True, "fingerprint": fingerprint}

        cutoffs_full = derive_tier_cutoffs(db_path)
        cutoffs = {"cutoff_large": cutoffs_full["cutoff_large"],
                   "cutoff_mid": cutoffs_full["cutoff_mid"]}
        calibration = load_calibration()

        positions, pairs_formed = _build_positions(con, cutoffs, calibration)

    # Artefactos de datos (spec §8).
    with open(os.path.join(output_dir, "pairs_formed.json"), "w", encoding="utf-8") as f:
        json.dump(pairs_formed, f, indent=2)
    with open(os.path.join(output_dir, "positions.json"), "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2)

    # Gate de poder (escribe power.json ANTES de evaluate — orden forzado F10).
    power = power_mod.compute_power(positions, output_dir)
    result = evaluate_mod.evaluate(positions, output_dir)

    fecha = run_date or os.environ.get("STUDY_RUN_DATE")
    if not fecha:
        raise RuntimeError(
            "fecha de corrida ausente: pasa run_date= o exporta STUDY_RUN_DATE "
            "(sin default — la fecha del verdict no se inventa)."
        )

    cal = load_calibration()
    cost_model = {
        "active_model": cal.active_model,
        "calibration_identity_hash": calibration_identity_hash(cal),
    }

    if result["verdict"] in ("N-INSUFICIENTE", "ARTEFACTO"):
        # Kill: la celda NO cierra. NO se escribe verdict.json (violaría el
        # vocabulario F). Se escribe killed.json con la razón.
        killed = {
            "outcome": result["verdict"],
            "reason": result.get("reason", ""),
            "coordenada": COORDENADA,
            "fingerprint": fingerprint,
            "power": power,
            "config": "primary",
            "source": SOURCE_PRIMARY,
            "cost_model": cost_model,
            "fecha": fecha,
            "note": "kill criterion: la celda permanece ABIERTA; sin verdict F.",
        }
        with open(os.path.join(output_dir, "killed.json"), "w", encoding="utf-8") as f:
            json.dump(killed, f, indent=2)
        print(f"KILL: {result['verdict']} — {result.get('reason', '')} (celda ABIERTA, sin verdict)")
        return killed

    verdict_doc = {
        "verdict": result["verdict"],
        "coordenada": COORDENADA,
        "fingerprint": fingerprint,
        "gates": {
            "gate_a": result["gate_a"],
            "gate_b": result["gate_b"],
            "loo": result["loo"],
            "descriptive_per_position_ci": result["descriptive_per_position_ci"],
            "per_year_net": result["per_year_net"],
        },
        "power": power,
        "config": "primary",
        "source": SOURCE_PRIMARY,
        "cost_model": cost_model,
        "fecha": fecha,
    }
    with open(os.path.join(output_dir, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(verdict_doc, f, indent=2)

    register_trial(result["verdict"], fingerprint)
    print(f"VERDICT: {result['verdict']}  (A={result['gate_a']['pass']} "
          f"B={result['gate_b']['pass']} LOO={result['loo']['pass']})")
    return verdict_doc


if __name__ == "__main__":
    main()
