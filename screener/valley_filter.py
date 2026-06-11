"""Cálculo puro del screener de valles (Vista Valles A) — sin red, sin DB.

Afirma SOLO hechos verificables en t: "está viva" (classify_liveness) y
"está en rango" (measure_consolidation). NUNCA rankea por atractivo — eso es
la celda B del programa. Spec §1, §5.1.

Contrato de barras: list[dict] diarias ascendentes con claves
{open_time, open, high, low, close, volume, quote_volume}. quote_volume = USDT.
"""
from __future__ import annotations

from statistics import median

# ── Umbrales de vida (arranque; calibrables, spec §2) ───────────────────────
MIN_VOLUME_USD_DAY = 500_000.0     # piso absoluto de volumen diario USDT
MIN_HISTORY_DAYS = 120             # historia mínima para juzgar consolidación
AGONY_LOOKBACK_DAYS = 90           # ventana vieja vs nueva para tendencia de volumen
AGONY_RATIO = 0.5                  # nuevo < ratio × viejo ⟹ agonizante
FLAT_RANGE_PCT = 0.005             # (high-low)/close < esto ⟹ vela "plana"
FLAT_MAX_FRACTION = 0.5            # > esta fracción de velas planas ⟹ libro muerto
FLAT_WINDOW_DAYS = 90              # ventana reciente para medir velas planas


def _quote_vols(bars: list[dict]) -> list[float]:
    return [float(b["quote_volume"]) for b in bars]


def classify_liveness(bars: list[dict]) -> tuple[bool, list[str]]:
    """¿La moneda está viva y operable? Devuelve (vivo, razones_de_muerte).

    vivo=True sólo si NINGUNA señal de muerte mecánica dispara. Las 4 señales
    (spec §2): volumen bajo piso, volumen agonizante, velas planas, historia
    insuficiente. Cada razón es un hecho, no un juicio."""
    razones: list[str] = []

    if len(bars) < MIN_HISTORY_DAYS:
        razones.append("historia_insuficiente")
        return (False, razones)  # sin historia no se puede juzgar el resto

    vols = _quote_vols(bars)

    # 1. Volumen bajo el piso (mediana reciente de 30 días).
    vol_reciente = median(vols[-30:])
    if vol_reciente < MIN_VOLUME_USD_DAY:
        razones.append("volumen_bajo_piso")

    # 2. Volumen agonizante: mediana de los últimos AGONY_LOOKBACK_DAYS vs la
    #    de la ventana inmediatamente anterior del mismo tamaño.
    if len(bars) >= 2 * AGONY_LOOKBACK_DAYS:
        nuevo = median(vols[-AGONY_LOOKBACK_DAYS:])
        viejo = median(vols[-2 * AGONY_LOOKBACK_DAYS:-AGONY_LOOKBACK_DAYS])
        if viejo > 0 and nuevo < AGONY_RATIO * viejo:
            razones.append("volumen_agonizante")

    # 3. Velas planas: fracción de velas recientes con rango ≈0 o volumen 0.
    ventana = bars[-FLAT_WINDOW_DAYS:]
    planas = 0
    for b in ventana:
        close = float(b["close"]) or 1.0
        rango_pct = (float(b["high"]) - float(b["low"])) / close
        if rango_pct < FLAT_RANGE_PCT or float(b["quote_volume"]) <= 0:
            planas += 1
    if ventana and planas / len(ventana) > FLAT_MAX_FRACTION:
        razones.append("velas_planas")

    return (len(razones) == 0, razones)
