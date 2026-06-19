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

# ── Umbrales de consolidación geométrica (arranque; calibrables, spec §4) ───
CONSOLIDATION_WINDOW_DAYS = 84     # 12 semanas
RANGE_BAND_MAX = 0.25              # (max-min)/mediana ≤ esto ⟹ en rango
VOL_PERCENTILE_WINDOW_DAYS = 365   # historia para el percentil de volatilidad

# ── Setup "parte baja del rango" — réplica del filtro histórico de musikito (SP2) ──
# Provisionales, sin calibrar (POST-SHIP). Mediana de musikito 2019 = 0.165; corte 0.25
# = el que midió el estudio multi-régimen. SOLO pos_in_30d_range gatea; el resto son hechos.
SETUP_POS_MAX = 0.25
RANGE_WINDOW_DAYS = 30
SMA_FAST = 20
SMA_SLOW = 50
DRAWDOWN_WINDOW_DAYS = 90
VOL_FAST_DAYS = 3
VOL_SLOW_DAYS = 30


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


def _realized_vol(bars: list[dict]) -> float:
    """Desviación de retornos diarios close-to-close (proxy de volatilidad)."""
    closes = [float(b["close"]) for b in bars]
    if len(closes) < 2:
        return 0.0
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes)) if closes[i - 1] != 0]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def measure_consolidation(bars: list[dict]) -> dict:
    """¿El precio está geométricamente en rango AHORA? Hecho descriptivo del
    presente — NO afirma que sea buena entrada (spec §4).

    Devuelve SIEMPRE las 4 claves:
      en_rango (bool), pct_rango (float), semanas (int), vol_percentil (float).
    pct_rango = (max-min)/mediana sobre la ventana de consolidación.
    semanas = cantidad de bloques consecutivos de 7 días (desde lo más reciente
    hacia atrás sobre TODA la serie) cuyo rango ≤ RANGE_BAND_MAX; no acotado por
    CONSOLIDATION_WINDOW_DAYS.
    vol_percentil = posición de la volatilidad de 30d en su historia de 1 año
    (0.0 = la más baja que ha tenido; 1.0 = la más alta)."""
    ventana = bars[-CONSOLIDATION_WINDOW_DAYS:]
    closes = [float(b["close"]) for b in ventana]
    med = median(closes) if closes else 1.0
    hi = max(float(b["high"]) for b in ventana) if ventana else 0.0
    lo = min(float(b["low"]) for b in ventana) if ventana else 0.0
    pct_rango = (hi - lo) / med if med else float("inf")
    en_rango = pct_rango <= RANGE_BAND_MAX

    # Semanas dentro de banda: cuenta semanas recientes (bloques de 7 días)
    # cuyo rango propio ≤ RANGE_BAND_MAX, desde la más reciente hacia atrás.
    # NOTA: semanas NO está acotado por CONSOLIDATION_WINDOW_DAYS — cuenta hacia
    # atrás por toda la serie, así que puede exceder las 12 semanas de la ventana.
    semanas = 0
    i = len(bars)
    while i - 7 >= 0:
        bloque = bars[i - 7:i]
        b_hi = max(float(b["high"]) for b in bloque)
        b_lo = min(float(b["low"]) for b in bloque)
        b_med = median([float(b["close"]) for b in bloque]) or 1.0
        if (b_hi - b_lo) / b_med <= RANGE_BAND_MAX:
            semanas += 1
            i -= 7
        else:
            break

    # Percentil de volatilidad: vol de los últimos 30d vs la distribución de
    # vol de ventanas de 30d a lo largo del último año.
    vol_actual = _realized_vol(bars[-30:])
    hist = bars[-VOL_PERCENTILE_WINDOW_DAYS:]
    muestras = []
    for j in range(30, len(hist) + 1, 7):  # paso semanal para no sobre-muestrear
        muestras.append(_realized_vol(hist[j - 30:j]))
    if muestras:
        menores = sum(1 for v in muestras if v <= vol_actual)
        vol_percentil = menores / len(muestras)
    else:
        vol_percentil = 0.0

    return {"en_rango": en_rango, "pct_rango": pct_rango,
            "semanas": semanas, "vol_percentil": vol_percentil}


def _wilder_rsi(closes: list[float], period: int = 14) -> float:
    """RSI de Wilder sobre la última barra (semilla = promedio simple de los primeros
    `period` cambios, luego suavizado de Wilder). Hecho EXHIBIDO, no gate. 50.0 si no
    hay datos suficientes; 100.0 si no hubo bajadas en la ventana suavizada."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def measure_setup(bars: list[dict]) -> dict:
    """Hechos del setup "parte baja del rango" (reframe SP2). PURA. SIEMPRE 7 claves.
    Solo pos_in_30d_range es gate (en evaluate_symbol); el resto son hechos EXHIBIDOS.
    Todo denominador clampeado — datos ralos/muertos producen un hecho degradado, NUNCA
    nan/inf (espejando la protección `or 1.0` de measure_consolidation)."""
    closes = [float(b["close"]) for b in bars]
    qvols = [float(b["quote_volume"]) for b in bars]
    close = closes[-1]
    eps = 1e-9 * close if close else 1e-9

    rango = bars[-RANGE_WINDOW_DAYS:]
    lo30 = min(float(b["low"]) for b in rango)
    hi30 = max(float(b["high"]) for b in rango)
    pos = (close - lo30) / max(hi30 - lo30, eps)

    sma20 = (sum(closes[-SMA_FAST:]) / min(len(closes), SMA_FAST)) or eps
    sma50 = (sum(closes[-SMA_SLOW:]) / min(len(closes), SMA_SLOW)) or eps
    med30 = median([float(b["close"]) for b in rango]) or eps
    consol30 = (hi30 - lo30) / med30 * 100.0

    qv30 = median(qvols[-VOL_SLOW_DAYS:]) if qvols else 0.0
    qv3 = median(qvols[-VOL_FAST_DAYS:]) if qvols else 0.0
    vol_ratio = (qv3 / qv30) if qv30 else 0.0

    hist = bars[-DRAWDOWN_WINDOW_DAYS:]
    hi90 = max(float(b["high"]) for b in hist) or eps
    drawdown = (close - hi90) / hi90 * 100.0

    return {
        "pos_in_30d_range": pos,
        "rsi14": _wilder_rsi(closes, 14),
        "pct_vs_sma20": (close - sma20) / sma20 * 100.0,
        "pct_vs_sma50": (close - sma50) / sma50 * 100.0,
        "consol_30d": consol30,
        "vol_ratio": vol_ratio,
        "drawdown_from_90h": drawdown,
    }


def liquidity_value(bars: list[dict]) -> float:
    """Liquidez como HECHO: mediana del volumen USDT de los últimos 30 días.
    Es el criterio de ORDEN NEUTRAL — un hecho, no una medida de 'calidad'."""
    vols = _quote_vols(bars[-30:])
    return median(vols) if vols else 0.0


def _distancia_ath_pct(bars: list[dict]) -> float:
    """% por debajo del máximo histórico de la serie (dato informativo, NO
    criterio de filtro ni de orden — spec §4)."""
    ath = max(float(b["high"]) for b in bars) if bars else 0.0
    last = float(bars[-1]["close"]) if bars else 0.0
    if ath <= 0:
        return 0.0
    return (ath - last) / ath


def evaluate_symbol(symbol: str, bars: list[dict]) -> dict | None:
    """Evalúa un símbolo. Devuelve la candidata (dict de HECHOS) si está VIVA
    y EN RANGO; None en cualquier otro caso. Cero ranking, cero claim.

    El dict resultante NO incluye ningún score de 'atractivo' — sólo hechos
    descriptivos que el humano interpreta (spec §0, §1)."""
    vivo, razones = classify_liveness(bars)
    if not vivo:
        return None
    cons = measure_consolidation(bars)
    if not cons["en_rango"]:
        return None
    return {
        "symbol": symbol,
        "price": float(bars[-1]["close"]),
        "pct_rango": cons["pct_rango"],
        "semanas_consolidando": cons["semanas"],
        "vol_percentil": cons["vol_percentil"],
        "volumen_usd_dia": liquidity_value(bars),
        "distancia_ath_pct": _distancia_ath_pct(bars),
        "razones_vida": razones,  # [] cuando viva; presente por simetría
    }


def order_neutral(candidatas: list[dict]) -> list[dict]:
    """Orden NEUTRAL por liquidez descendente (hecho). NO ordena por 'calidad
    de valle' — ese ranking es la celda B del programa, prohibido aquí."""
    return sorted(candidatas, key=lambda c: c.get("volumen_usd_dia", 0.0), reverse=True)
