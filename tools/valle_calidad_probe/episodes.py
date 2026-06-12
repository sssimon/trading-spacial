"""Detección de episodios valle / no-valle (puro, sin red).

Para cada día evaluable (índice >= CONSOLIDATION_WINDOW_DAYS) clasifica
en_rango con measure_consolidation del screener A (única fuente de verdad).
Runs contiguos del mismo estado = episodios. La entrada es el PRIMER día de
cada episodio. Pre-registro §"Estrategia"."""
from __future__ import annotations

from screener.valley_filter import measure_consolidation
from .constants import CONSOLIDATION_WINDOW_DAYS


def detect_episodes(bars: list[dict]) -> list[dict]:
    """Devuelve la lista de episodios [{tipo: 'valle'|'no_valle', entry_idx,
    end_idx}], en orden temporal. entry_idx = primer día del run; end_idx =
    último día del run (inclusive). Días con índice < ventana no se clasifican
    (no hay historia suficiente para measure_consolidation)."""
    if len(bars) <= CONSOLIDATION_WINDOW_DAYS:
        return []
    # Serie booleana en_rango[t] para t evaluable.
    estados: list[tuple[int, bool]] = []
    for t in range(CONSOLIDATION_WINDOW_DAYS, len(bars)):
        ventana = bars[: t + 1]                       # measure_consolidation mira los últimos 84
        en_rango = measure_consolidation(ventana)["en_rango"]
        estados.append((t, en_rango))

    episodios: list[dict] = []
    run_tipo: bool | None = None
    run_start = 0
    for idx, (t, en_rango) in enumerate(estados):
        if en_rango != run_tipo:
            if run_tipo is not None:
                episodios.append({
                    "tipo": "valle" if run_tipo else "no_valle",
                    "entry_idx": estados[run_start][0],
                    "end_idx": estados[idx - 1][0],
                })
            run_tipo = en_rango
            run_start = idx
    # Cerrar el último run.
    if run_tipo is not None:
        episodios.append({
            "tipo": "valle" if run_tipo else "no_valle",
            "entry_idx": estados[run_start][0],
            "end_idx": estados[-1][0],
        })
    return episodios
