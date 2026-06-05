"""Gates del verdict de la celda 4 (spec §5), ORDEN FORZADO (F10).

Orden de evaluación pre-registrado, IRREVOCABLE:
    (1) kill criteria → (2) gate de poder → (3) Gate A → (4) Gate B → (5) LOO.
Ningún paso lee el resultado de un paso posterior.

Kill criteria (matan el estudio SIN emitir PASS/FAIL — verdict N-INSUFICIENTE o
ARTEFACTO, la celda NO cierra):
  1. N < MIN_POSITIONS (30) → N-INSUFICIENTE.
  2. Concentración (F11): entre posiciones con net>0 agrupadas por (pair, window),
     la mayor contribución de grupo / Σ contribuciones positivas > CONCENTRATION_MAX
     (0.50) → ARTEFACTO. INERTE si el pooled net total <= 0 (el CI de Gate A
     resuelve).
  3. LOO inviable (F9): algún subset LOO (drop-símbolo o drop-año) queda con
     < MIN_POSITIONS → N-INSUFICIENTE.
  4. Gate-B subset (windows con inicio >= GATE_B_START) < MIN_POSITIONS →
     N-INSUFICIENTE.

Gate de poder (lee power.json; si no existe → RuntimeError, orden violado): si
power_ok es False → N-INSUFICIENTE (la celda NO cierra).

Gate A (¿hubo edge?): bootstrap por window (resample windows con reemplazo;
statistic = suma de nets resampleados; BOOTSTRAP_N, Generator sembrado SEED);
gate_a = ci_lo > 0.

Gate B (¿sigue vivo?, V4): mismo bootstrap sobre windows con inicio >=
GATE_B_START; gate_b = ci_lo > 0.

LOO: por cada símbolo y por cada año calendario, mismo bootstrap por window sobre
las posiciones restantes; se exige all ci_lo > 0.

verdict = "PASS" si gate_a ∧ gate_b ∧ all-LOO; "FAIL" en otro caso.

Determinismo: usa un numpy Generator sembrado con SEED por cada bootstrap (cada
sub-bootstrap es un Generator nuevo sembrado igual → reproducible y no acoplado
al orden). Mismo SEED que power.py, generadores independientes (ver power.py).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np

from .constants import (
    BOOTSTRAP_N,
    CONCENTRATION_MAX,
    GATE_B_START,
    MIN_POSITIONS,
    SEED,
)

_HOUR_MS = 3_600_000


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


_GATE_B_START_MS = _to_ms(GATE_B_START)


def _year_of(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).year


def _symbols_of(pos: dict) -> tuple[str, str]:
    """Los dos símbolos de la posición desde su `pair` ("X/Y")."""
    x, _, y = pos["pair"].partition("/")
    return x, y


def _window_nets(positions: list[dict]) -> list[float]:
    """Suma de `net` por window_start_ms, orden por window."""
    by_window: dict[int, float] = {}
    for p in positions:
        by_window[p["window_start_ms"]] = by_window.get(p["window_start_ms"], 0.0) + p["net"]
    return [by_window[k] for k in sorted(by_window)]


def _bootstrap_window(positions: list[dict]) -> dict:
    """Bootstrap por trading-window (F8): resample windows con reemplazo, statistic
    = suma de nets resampleados; CI95 de percentiles 2.5/97.5. Generator sembrado
    SEED (determinista). gate (ci_lo>0) lo decide el caller."""
    nets = _window_nets(positions)
    if not nets:
        return {"point": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "n_windows": 0}
    arr = np.asarray(nets, dtype=float)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(arr), size=(BOOTSTRAP_N, len(arr)))
    boot = arr[idx].sum(axis=1)
    return {
        "point": float(arr.sum()),
        "ci_lo": float(np.percentile(boot, 2.5)),
        "ci_hi": float(np.percentile(boot, 97.5)),
        "n_windows": len(nets),
    }


def _bootstrap_per_position(positions: list[dict]) -> dict:
    """Bootstrap por POSICIÓN (descriptivo, sesgado a estrecho bajo clustering
    temporal — declarado). NO gatea (spec §5)."""
    nets = [p["net"] for p in positions]
    if not nets:
        return {"point": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "n_positions": 0}
    arr = np.asarray(nets, dtype=float)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(arr), size=(BOOTSTRAP_N, len(arr)))
    boot = arr[idx].sum(axis=1)
    return {
        "point": float(arr.sum()),
        "ci_lo": float(np.percentile(boot, 2.5)),
        "ci_hi": float(np.percentile(boot, 97.5)),
        "n_positions": len(nets),
    }


def _per_year_net(positions: list[dict]) -> dict:
    """P&L neto por año calendario (curva descriptiva, spec V4)."""
    out: dict[str, float] = {}
    for p in positions:
        y = str(_year_of(p["window_start_ms"]))
        out[y] = out.get(y, 0.0) + p["net"]
    return dict(sorted(out.items()))


def _kill_check(positions: list[dict]) -> dict | None:
    """Devuelve un dict-verdict de kill, o None si ningún kill aplica.

    Orden interno: N total → concentración → LOO inviable → Gate-B subset.
    """
    # 1. N total.
    if len(positions) < MIN_POSITIONS:
        return {
            "verdict": "N-INSUFICIENTE",
            "reason": f"N={len(positions)} < MIN_POSITIONS={MIN_POSITIONS}",
        }

    # 2. Concentración (F11): entre net>0, agrupar por (pair, window). Inerte si
    #    el pooled net total <= 0.
    total_net = sum(p["net"] for p in positions)
    if total_net > 0:
        pos_groups: dict[tuple[str, int], float] = {}
        for p in positions:
            if p["net"] > 0:
                key = (p["pair"], p["window_start_ms"])
                pos_groups[key] = pos_groups.get(key, 0.0) + p["net"]
        sum_positive = sum(pos_groups.values())
        if sum_positive > 0:
            max_group = max(pos_groups.values())
            if max_group / sum_positive > CONCENTRATION_MAX:
                return {
                    "verdict": "ARTEFACTO",
                    "reason": (
                        f"concentración {max_group / sum_positive:.4f} > "
                        f"CONCENTRATION_MAX={CONCENTRATION_MAX}"
                    ),
                }

    # 3. LOO inviable (F9): algún subset LOO (drop-símbolo o drop-año) < MIN_POSITIONS.
    symbols = set()
    for p in positions:
        symbols.update(_symbols_of(p))
    for sym in sorted(symbols):
        subset = [p for p in positions if sym not in _symbols_of(p)]
        if len(subset) < MIN_POSITIONS:
            return {
                "verdict": "N-INSUFICIENTE",
                "reason": f"LOO drop-símbolo {sym!r} deja N={len(subset)} < {MIN_POSITIONS}",
            }
    years = sorted({_year_of(p["window_start_ms"]) for p in positions})
    for yr in years:
        subset = [p for p in positions if _year_of(p["window_start_ms"]) != yr]
        if len(subset) < MIN_POSITIONS:
            return {
                "verdict": "N-INSUFICIENTE",
                "reason": f"LOO drop-año {yr} deja N={len(subset)} < {MIN_POSITIONS}",
            }

    # 4. Gate-B subset (windows con inicio >= GATE_B_START) < MIN_POSITIONS.
    gate_b_subset = [p for p in positions if p["window_start_ms"] >= _GATE_B_START_MS]
    if len(gate_b_subset) < MIN_POSITIONS:
        return {
            "verdict": "N-INSUFICIENTE",
            "reason": (
                f"subset Gate-B (>= {GATE_B_START}) deja N={len(gate_b_subset)} "
                f"< {MIN_POSITIONS}"
            ),
        }
    return None


def evaluate(positions: list[dict], output_dir: str) -> dict:
    """Evalúa el verdict de la celda 4 en ORDEN FORZADO (spec §5).

    (1) kills → (2) poder (lee power.json; ausente → RuntimeError) → (3) Gate A →
    (4) Gate B → (5) LOO. PASS ⟺ gate_a ∧ gate_b ∧ all-LOO; si no, FAIL. Kills
    devuelven N-INSUFICIENTE / ARTEFACTO (la celda NO cierra; run.py NO escribe
    verdict.json en ese caso).
    """
    # (1) KILLS.
    kill = _kill_check(positions)
    if kill is not None:
        return kill

    # (2) PODER — lee power.json (escrito por compute_power ANTES). Orden forzado.
    power_path = os.path.join(output_dir, "power.json")
    if not os.path.exists(power_path):
        raise RuntimeError("orden violado: power.json no existe")
    with open(power_path, encoding="utf-8") as f:
        power = json.load(f)
    if not power.get("power_ok", False):
        return {"verdict": "N-INSUFICIENTE", "reason": "power gate"}

    # (3) GATE A.
    a = _bootstrap_window(positions)
    gate_a = bool(a["ci_lo"] > 0.0)

    # (4) GATE B — windows con inicio >= GATE_B_START.
    gate_b_positions = [p for p in positions if p["window_start_ms"] >= _GATE_B_START_MS]
    b = _bootstrap_window(gate_b_positions)
    gate_b = bool(b["ci_lo"] > 0.0)

    # (5) LOO — por símbolo y por año. all ci_lo > 0 requerido.
    loo_symbol: dict[str, dict] = {}
    loo_ok = True
    symbols = set()
    for p in positions:
        symbols.update(_symbols_of(p))
    for sym in sorted(symbols):
        subset = [p for p in positions if sym not in _symbols_of(p)]
        res = _bootstrap_window(subset)
        loo_symbol[sym] = res
        if not (res["ci_lo"] > 0.0):
            loo_ok = False
    loo_year: dict[str, dict] = {}
    years = sorted({_year_of(p["window_start_ms"]) for p in positions})
    for yr in years:
        subset = [p for p in positions if _year_of(p["window_start_ms"]) != yr]
        res = _bootstrap_window(subset)
        loo_year[str(yr)] = res
        if not (res["ci_lo"] > 0.0):
            loo_ok = False

    verdict = "PASS" if (gate_a and gate_b and loo_ok) else "FAIL"

    descriptive = _bootstrap_per_position(positions)
    return {
        "verdict": verdict,
        "gate_a": {**a, "pass": gate_a},
        "gate_b": {**b, "pass": gate_b, "start": GATE_B_START},
        "loo": {
            "pass": loo_ok,
            "by_symbol": loo_symbol,
            "by_year": loo_year,
        },
        "descriptive_per_position_ci": {**descriptive, "label": "descriptive_biased_narrow"},
        "per_year_net": _per_year_net(positions),
        "n_positions": len(positions),
    }
