"""Detector neutral de soporte/resistencia (D.1) — cálculo puro, sin red, sin DB.

Afirma SOLO hechos observables en las velas: dónde la trayectoria cambió
(pivotes), agrupados en zonas. NUNCA emite veredicto ni ranking — hereda la
disciplina de screener/valley_filter.py. Spec §3.

Contrato de barras: list[dict] diarias ascendentes con claves
{open_time, open, high, low, close, volume, quote_volume}.
"""
from __future__ import annotations

import math
from statistics import median

# ── Constantes de arranque (calibrables, spec §3.6) ─────────────────────────
PIVOT_REACH = 3          # velas a cada lado para confirmar un giro
CLUSTER_TOL_PCT = 0.0075  # 0.75% → pivotes más cercanos = misma zona
LOOKBACK_DAYS = 365      # un año de velas diarias (lo pide el endpoint)
MIN_TOUCHES = 2          # zona defendida ≥2 veces (=1 mostraría cada giro suelto)


def _pivots(bars: list[dict], k: int) -> tuple[list[float], list[float]]:
    """Precios de pivotes confirmados → (pivote-altos, pivote-bajos).

    Pivote-alto: `high` estrictamente mayor que el de los k vecinos a cada lado.
    Pivote-bajo: `low` estrictamente menor. Las primeras y últimas k velas se
    excluyen (sin k vecinos confirmatorios → sin look-ahead, sin pivote prematuro).
    La comparación estricta hace que una meseta plana no produzca pivote."""
    altos: list[float] = []
    bajos: list[float] = []
    n = len(bars)
    for i in range(k, n - k):
        hi = float(bars[i]["high"])
        lo = float(bars[i]["low"])
        vecinos = bars[i - k:i] + bars[i + 1:i + 1 + k]
        if all(hi > float(b["high"]) for b in vecinos):
            altos.append(hi)
        if all(lo < float(b["low"]) for b in vecinos):
            bajos.append(lo)
    return altos, bajos


def _round_confluence(precio_bajo: float, precio_alto: float) -> list[float]:
    """Números redondos notables dentro de [bajo, alto]. ANOTACIÓN observable —
    NO reubica el nivel (spec §3.3). Paso a un orden por debajo de la magnitud:
    step = 10^(floor(log10(alto)) - 1). Como las bandas son estrechas, da 0–1."""
    if precio_alto <= 0:
        return []
    step = 10 ** (math.floor(math.log10(precio_alto)) - 1)
    if step <= 0:
        return []
    primero = math.ceil(precio_bajo / step)
    ultimo = math.floor(precio_alto / step)
    return [round(step * m, 10) for m in range(primero, ultimo + 1)]


def _cluster(precios: list[float], tipo: str, tol_pct: float,
             min_touches: int) -> list[dict]:
    """Agrupa pivotes cercanos en zonas. Un precio entra al clúster si está
    dentro de tol_pct del CENTRO corriente (mediana); si no, abre uno nuevo.
    Cada clúster con ≥min_touches produce una zona de HECHOS (spec §3.2)."""
    if not precios:
        return []
    ordenados = sorted(precios)
    grupos: list[list[float]] = [[ordenados[0]]]
    for p in ordenados[1:]:
        centro = median(grupos[-1])
        if centro > 0 and abs(p - centro) / centro <= tol_pct:
            grupos[-1].append(p)
        else:
            grupos.append([p])

    zonas: list[dict] = []
    for g in grupos:
        if len(g) < min_touches:
            continue
        bajo, alto = min(g), max(g)
        zonas.append({
            "tipo": tipo,
            "precio_bajo": bajo,
            "precio_alto": alto,
            "centro": float(median(g)),
            "toques": len(g),
            "confluencia_redondo": _round_confluence(bajo, alto),
        })
    return zonas
