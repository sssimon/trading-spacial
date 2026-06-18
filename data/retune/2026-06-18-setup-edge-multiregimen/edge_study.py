"""
Estudio: ¿el setup de musikito tiene edge fuera de su régimen? (multi-régimen 2020–2025)

Implementación CONGELADA según METODOLOGIA.md (2026-06-18). No cambia la metodología.

- Descarga klines diarias de Binance (API pública) para todo el universo spot USDT.
- Calcula features ex-ante por símbolo/fecha.
- Aplica regla MÍNIMA y CONJUNTA, baselines B1 (control de estado, matcheado por fecha) y
  B2 (universo vivo), retornos forward, régimen ex-ante (breadth).
- Escribe results.json + findings.md.

Período de SEÑALES: 2020-01-01 → 2025-12-31. Se descarga desde 2019-06-01 para tener
historia (SMA200 / ventanas de 30d) al inicio del período.

Sólo stdlib + requests/pandas/numpy/scipy (todos presentes en el entorno).
"""

import json
import sys
import time
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Windows stdout es cp1252 por defecto y revienta con símbolos no-latinos
# (Binance lista pares con caracteres CJK, p.ej. 币安人生USDT). Forzar UTF-8 con
# fallback 'replace' para que el LOGGING nunca tumbe el estudio.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "raw_klines"
RAW_DIR.mkdir(exist_ok=True)

BINANCE_BASE = "https://api.binance.com"
INTERVAL = "1d"
DAY_MS = 86_400_000

# Descargar desde 2019-06-01 para tener ventanas/SMA200 al iniciar el período de señales.
DOWNLOAD_START = datetime(2019, 6, 1, tzinfo=timezone.utc)
SIGNAL_START = pd.Timestamp("2020-01-01", tz="UTC")
SIGNAL_END = pd.Timestamp("2025-12-31", tz="UTC")

STABLES = {"USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "GUSD", "USDD", "PYUSD"}
LEVERAGED_TOKENS = ("UP", "DOWN", "BULL", "BEAR")  # antes de USDT

# Gate de vida
MIN_MEDIAN_QUOTE_VOL_30D = 500_000.0

# Reglas
POS_THRESHOLD = 0.25
RSI_THRESHOLD = 40.0
CONSOL_THRESHOLD = 45.0
VOL_RATIO_THRESHOLD = 0.85

# Forward
TP = 0.20
SL = -0.12
RULE_HOLD_DAYS = 14
WIN15_THRESHOLD = 0.15

# Baseline matching
B1_MATCH_RATIO = 3  # hasta 1:3 (setup:baseline) por fecha

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "edge-study/1.0"})

# Stats de descarga (para logging honesto)
DOWNLOAD_STATS = {
    "exchangeinfo_usdt_trading_spot": 0,
    "excluded_stablecoins": [],
    "excluded_leveraged": [],
    "candidate_symbols": 0,
    "symbols_with_data": 0,
    "symbols_skipped_no_data": [],
    "symbols_skipped_insufficient_history": [],
}


# ----------------------------------------------------------------------------
# Descarga
# ----------------------------------------------------------------------------
def _get(url, params=None, max_retries=6):
    """GET con backoff para 429/418 (rate limit / ban)."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"  net error {e}; retry in {delay:.1f}s")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code in (429, 418):
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else delay
            print(f"  rate-limited ({r.status_code}); sleeping {wait:.1f}s")
            time.sleep(wait)
            delay = min(delay * 2, 120)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"max retries exceeded for {url} {params}")


def get_universe():
    """exchangeInfo → símbolos spot USDT TRADING, sin stables ni apalancados."""
    r = _get(f"{BINANCE_BASE}/api/v3/exchangeInfo")
    data = r.json()
    raw = [
        s for s in data["symbols"]
        if s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
        and s.get("isSpotTradingAllowed") is True
    ]
    DOWNLOAD_STATS["exchangeinfo_usdt_trading_spot"] = len(raw)

    universe = []
    for s in raw:
        sym = s["symbol"]
        base = s.get("baseAsset", "")
        if base in STABLES:
            DOWNLOAD_STATS["excluded_stablecoins"].append(sym)
            continue
        # tokens apalancados: el base termina en UP/DOWN/BULL/BEAR (antes de USDT)
        if any(base.endswith(suf) for suf in LEVERAGED_TOKENS):
            DOWNLOAD_STATS["excluded_leveraged"].append(sym)
            continue
        universe.append(sym)
    DOWNLOAD_STATS["candidate_symbols"] = len(universe)
    return sorted(universe)


def download_symbol(symbol):
    """Descarga klines diarias paginando con startTime. Cachea en disco."""
    cache = RAW_DIR / f"{symbol}.json"
    if cache.exists():
        try:
            rows = json.loads(cache.read_text())
            if rows:
                return rows
        except Exception:
            pass

    all_rows = []
    start_ms = int(DOWNLOAD_START.timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    while start_ms < end_ms:
        r = _get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": INTERVAL,
                    "startTime": start_ms, "limit": 1000},
        )
        batch = r.json()
        if not batch:
            break
        all_rows.extend(batch)
        last_open = batch[-1][0]
        new_start = last_open + DAY_MS
        if new_start <= start_ms:
            break
        start_ms = new_start
        if len(batch) < 1000:
            break
        time.sleep(0.25)  # amable con la API
    if all_rows:
        cache.write_text(json.dumps(all_rows))
    return all_rows


def rows_to_df(rows):
    """Klines → DataFrame diario con open/high/low/close/quote_vol indexado por fecha UTC."""
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_vol", "trades", "tb_base", "tb_quote", "ignore",
    ])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.normalize()
    for c in ("open", "high", "low", "close", "quote_vol"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[["date", "open", "high", "low", "close", "quote_vol"]].copy()
    df = df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
    df = df.set_index("date")
    return df


# ----------------------------------------------------------------------------
# Features ex-ante
# ----------------------------------------------------------------------------
def wilder_rsi(close, period=14):
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    # Wilder smoothing == EMA con alpha = 1/period (com = period-1)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # si roll_down==0 (sólo subidas) → RSI 100
    rsi = rsi.where(roll_down != 0.0, 100.0)
    return rsi


def compute_features(df):
    """Añade columnas de features ex-ante (todas con datos ≤ t). Sin lookahead."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    qv = df["quote_vol"]

    # pos_in_30d_range (ventana de 30 incluye t → es ex-ante: usa datos ≤ t)
    roll_min_low = low.rolling(30, min_periods=30).min()
    roll_max_high = high.rolling(30, min_periods=30).max()
    denom = (roll_max_high - roll_min_low)
    clamp = (1e-9 * close).abs()
    denom_clamped = np.maximum(denom, clamp)
    df["pos_in_30d_range"] = (close - roll_min_low) / denom_clamped

    df["rsi14"] = wilder_rsi(close, 14)

    sma20 = close.rolling(20, min_periods=20).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    df["sma20"] = sma20
    df["sma50"] = sma50
    df["pct_vs_sma20"] = (close - sma20) / sma20 * 100.0
    df["pct_vs_sma50"] = (close - sma50) / sma50 * 100.0

    median_close_30 = close.rolling(30, min_periods=30).median()
    df["consol_30d"] = (roll_max_high - roll_min_low) / median_close_30 * 100.0

    med_qv_3 = qv.rolling(3, min_periods=3).median()
    med_qv_30 = qv.rolling(30, min_periods=30).median()
    df["vol_ratio"] = med_qv_3 / med_qv_30.replace(0.0, np.nan)

    # --- Gate de vida ---
    df["median_qv_30d"] = med_qv_30
    # no agonizante: mediana últimos 90d ≥ 0.5 × mediana de los 90d previos (90..180 atrás)
    med_qv_90 = qv.rolling(90, min_periods=90).median()
    med_qv_90_prev = med_qv_90.shift(90)
    df["not_dying"] = med_qv_90 >= 0.5 * med_qv_90_prev
    # < 50% velas planas en 90d (vela plana = high == low)
    flat = (high == low).astype(float)
    df["flat_frac_90d"] = flat.rolling(90, min_periods=90).mean()

    alive = (
        (df["median_qv_30d"] >= MIN_MEDIAN_QUOTE_VOL_30D)
        & (df["not_dying"] == True)  # noqa: E712
        & (df["flat_frac_90d"] < 0.5)
    )
    df["alive"] = alive.fillna(False)

    # close > SMA50 para breadth (régimen)
    df["above_sma50"] = (close > sma50)

    # --- Forward (entrada = open en t+1) ---
    entry = df["open"].shift(-1)
    df["entry_next_open"] = entry
    for k in (7, 14, 30):
        # max high en los k días [t+1 .. t+k]
        # rolling max sobre high, alineado a futuro
        fwd_max_high = high.shift(-1).rolling(k, min_periods=1).max().shift(-(k - 1))
        df[f"max_fwd_{k}d"] = (fwd_max_high - entry) / entry

    # rule_return: primero de +20% TP / −12% SL / cierre en t+14
    df["rule_return"] = _compute_rule_return(df)

    # win15
    df["win15"] = df["max_fwd_14d"] >= WIN15_THRESHOLD

    return df


def _compute_rule_return(df):
    """rule_return vectorizado-ish: recorre t+1..t+14 buscando TP/SL intrabar, sino cierre t+14.

    Convención de orden intrabar: si en el mismo día se tocan TP y SL (high≥tp y low≤sl),
    asumimos que SL se toca primero (conservador). Entrada = open en t+1.
    """
    n = len(df)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    entries = df["entry_next_open"].to_numpy()
    out = np.full(n, np.nan)
    for i in range(n):
        entry = entries[i]
        if not np.isfinite(entry) or entry <= 0:
            continue
        tp_price = entry * (1.0 + TP)
        sl_price = entry * (1.0 + SL)
        # ventana t+1 .. t+14
        start = i + 1
        end = min(i + RULE_HOLD_DAYS, n - 1)  # índice del día de cierre (t+14)
        if start > n - 1:
            continue
        resolved = False
        for j in range(start, min(start + RULE_HOLD_DAYS, n)):
            hj = highs[j]
            lj = lows[j]
            # SL primero si ambos se tocan (conservador)
            if lj <= sl_price:
                out[i] = SL
                resolved = True
                break
            if hj >= tp_price:
                out[i] = TP
                resolved = True
                break
        if not resolved:
            # cierre en el último día de la ventana disponible
            close_idx = min(start + RULE_HOLD_DAYS - 1, n - 1)
            out[i] = (closes[close_idx] - entry) / entry
    return out


# ----------------------------------------------------------------------------
# Construcción del panel
# ----------------------------------------------------------------------------
def build_panel(symbol_dfs):
    """Concatena todos los símbolos en un panel largo, recortado al período de señales."""
    frames = []
    for sym, df in symbol_dfs.items():
        sub = df.copy()
        sub["symbol"] = sym
        frames.append(sub.reset_index())
    panel = pd.concat(frames, ignore_index=True)
    # período de señales (la fecha t debe poder evaluarse: tener t+14 disponible se maneja por NaN)
    panel = panel[(panel["date"] >= SIGNAL_START) & (panel["date"] <= SIGNAL_END)]
    return panel


def compute_breadth(panel):
    """breadth_t = fracción del universo vivo con close>SMA50 en t. Régimen ex-ante."""
    alive = panel[panel["alive"]].copy()
    grp = alive.groupby("date")
    breadth = grp["above_sma50"].mean()
    breadth.name = "breadth"
    return breadth


def regime_bucket(b):
    if not np.isfinite(b):
        return "unknown"
    if b >= 0.6:
        return "alt-bull"
    if b >= 0.4:
        return "neutral"
    return "bear"


# ----------------------------------------------------------------------------
# Selección de setups y baselines
# ----------------------------------------------------------------------------
def date_seed(date_str, modulo):
    """Semilla determinista derivada de la fecha (hash del string % N)."""
    h = int(hashlib.sha256(date_str.encode()).hexdigest(), 16)
    return h % max(modulo, 1)


def select_rule_minimal(panel):
    return panel["alive"] & (panel["pos_in_30d_range"] <= POS_THRESHOLD)


def select_rule_conjunct(panel):
    return (
        panel["alive"]
        & (panel["pos_in_30d_range"] <= POS_THRESHOLD)
        & (panel["rsi14"] < RSI_THRESHOLD)
        & (panel["close"] < panel["sma20"])
        & (panel["close"] < panel["sma50"])
        & (panel["consol_30d"] <= CONSOL_THRESHOLD)
        & (panel["vol_ratio"] <= VOL_RATIO_THRESHOLD)
    )


def build_b1_for_setup(panel, setup_mask):
    """B1: por cada fecha con hits del setup, muestrea pares VIVOS ese día con pos>0.25.

    Matching determinista (semilla por fecha), ratio hasta 1:B1_MATCH_RATIO. Devuelve
    (b1_index_list, ratio_used_avg).
    """
    setup_rows = panel[setup_mask]
    if setup_rows.empty:
        return [], 0.0
    # candidatos B1: vivos con pos>0.25 (estado distinto)
    cand_mask = panel["alive"] & (panel["pos_in_30d_range"] > POS_THRESHOLD)
    cand = panel[cand_mask]
    cand_by_date = {d: g.index.to_list() for d, g in cand.groupby("date")}

    setup_counts = setup_rows.groupby("date").size()
    b1_idx = []
    total_target = 0
    total_picked = 0
    for date, n_setup in setup_counts.items():
        pool = cand_by_date.get(date, [])
        if not pool:
            continue
        want = min(len(pool), n_setup * B1_MATCH_RATIO)
        total_target += n_setup
        # selección determinista: ordena pool, rota por semilla de fecha, toma 'want'
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        pool_sorted = sorted(pool)
        offset = date_seed(date_str, len(pool_sorted))
        rotated = pool_sorted[offset:] + pool_sorted[:offset]
        picked = rotated[:want]
        b1_idx.extend(picked)
        total_picked += len(picked)
    ratio = (total_picked / total_target) if total_target else 0.0
    return b1_idx, ratio


def build_b2(panel):
    """B2: cualquier par vivo en t."""
    return panel[panel["alive"]].index.to_list()


# ----------------------------------------------------------------------------
# Métricas
# ----------------------------------------------------------------------------
def cell_stats(rows):
    """n, mediana/media de max_fwd_14d, mediana rule_return, win15. Sobre filas con forward válido."""
    valid = rows.dropna(subset=["max_fwd_14d"])
    n = int(len(valid))
    if n == 0:
        return {"n": 0, "median_max_fwd_14d": None, "mean_max_fwd_14d": None,
                "median_rule_return": None, "win15": None}
    rr = valid["rule_return"].dropna()
    return {
        "n": n,
        "median_max_fwd_14d": float(valid["max_fwd_14d"].median()),
        "mean_max_fwd_14d": float(valid["max_fwd_14d"].mean()),
        "median_rule_return": float(rr.median()) if len(rr) else None,
        "win15": float(valid["win15"].mean()),
    }


def mann_whitney(setup_vals, base_vals):
    s = pd.Series(setup_vals).dropna().to_numpy()
    b = pd.Series(base_vals).dropna().to_numpy()
    if len(s) < 3 or len(b) < 3:
        return {"p_value": None, "note": "n insuficiente para Mann-Whitney"}
    try:
        stat, p = mannwhitneyu(s, b, alternative="greater")
        return {"u_statistic": float(stat), "p_value": float(p),
                "alternative": "setup > baseline (one-sided)"}
    except Exception as e:
        return {"p_value": None, "note": f"Mann-Whitney falló: {e}"}


def stratify(rows, panel):
    """Devuelve dict por bucket de régimen y por año."""
    out = {"global": cell_stats(rows)}
    by_regime = {}
    for bucket, g in rows.groupby("regime"):
        by_regime[bucket] = cell_stats(g)
    out["by_regime"] = by_regime
    by_year = {}
    for yr, g in rows.groupby(rows["date"].dt.year):
        by_year[str(int(yr))] = cell_stats(g)
    out["by_year"] = by_year
    return out


def delta_and_pval(setup_rows, base_rows):
    """Delta setup−B1 (mediana y media max_fwd_14d) + p-value, global/por bucket."""
    out = {}

    def _block(srows, brows):
        s = cell_stats(srows)
        b = cell_stats(brows)
        d_med = (None if s["median_max_fwd_14d"] is None or b["median_max_fwd_14d"] is None
                 else s["median_max_fwd_14d"] - b["median_max_fwd_14d"])
        d_mean = (None if s["mean_max_fwd_14d"] is None or b["mean_max_fwd_14d"] is None
                  else s["mean_max_fwd_14d"] - b["mean_max_fwd_14d"])
        mw = mann_whitney(srows["max_fwd_14d"], brows["max_fwd_14d"])
        return {
            "setup": s, "baseline": b,
            "delta_median_max_fwd_14d": d_med,
            "delta_mean_max_fwd_14d": d_mean,
            "mann_whitney": mw,
        }

    out["global"] = _block(setup_rows, base_rows)
    out["by_regime"] = {}
    buckets = set(setup_rows["regime"].unique()) | set(base_rows["regime"].unique())
    for bucket in sorted(buckets):
        sr = setup_rows[setup_rows["regime"] == bucket]
        br = base_rows[base_rows["regime"] == bucket]
        out["by_regime"][bucket] = _block(sr, br)
    return out


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    t0 = time.time()
    print("== exchangeInfo ==")
    universe = get_universe()
    print(f"universo candidato: {len(universe)} símbolos "
          f"(excluidos {len(DOWNLOAD_STATS['excluded_stablecoins'])} stables, "
          f"{len(DOWNLOAD_STATS['excluded_leveraged'])} apalancados)")

    print("== descarga klines ==")
    symbol_dfs = {}
    for i, sym in enumerate(universe, 1):
        try:
            rows = download_symbol(sym)
        except Exception as e:
            print(f"  [{i}/{len(universe)}] {sym}: ERROR {e}")
            DOWNLOAD_STATS["symbols_skipped_no_data"].append(sym)
            continue
        df = rows_to_df(rows)
        if df is None or len(df) < 250:
            DOWNLOAD_STATS["symbols_skipped_insufficient_history"].append(sym)
            if i % 25 == 0 or len(universe) - i < 5:
                print(f"  [{i}/{len(universe)}] {sym}: insuficiente historia "
                      f"({0 if df is None else len(df)} barras)")
            continue
        df = compute_features(df)
        symbol_dfs[sym] = df
        if i % 25 == 0 or i == len(universe):
            print(f"  [{i}/{len(universe)}] procesados {len(symbol_dfs)} con datos "
                  f"({time.time()-t0:.0f}s)")
    DOWNLOAD_STATS["symbols_with_data"] = len(symbol_dfs)
    print(f"símbolos con datos suficientes (≥250 barras): {len(symbol_dfs)}")

    print("== panel ==")
    panel = build_panel(symbol_dfs)
    print(f"filas panel (período señales 2020-2025): {len(panel)}")

    breadth = compute_breadth(panel)
    panel = panel.merge(breadth.rename("breadth"), left_on="date",
                        right_index=True, how="left")
    panel["regime"] = panel["breadth"].apply(regime_bucket)

    # Sólo filas con forward 14d evaluable se usan en las celdas (cell_stats dropea NaN).
    n_eval = int(panel["max_fwd_14d"].notna().sum())
    print(f"filas con forward 14d evaluable: {n_eval}")

    # Máscaras de regla
    m_min = select_rule_minimal(panel)
    m_conj = select_rule_conjunct(panel)
    n_min_hits = int((m_min & panel["max_fwd_14d"].notna()).sum())
    n_conj_hits = int((m_conj & panel["max_fwd_14d"].notna()).sum())
    print(f"hits regla-mínima (con forward): {n_min_hits}")
    print(f"hits regla-conjunta (con forward): {n_conj_hits}")

    # Baselines
    b1_min_idx, b1_min_ratio = build_b1_for_setup(panel, m_min)
    b1_conj_idx, b1_conj_ratio = build_b1_for_setup(panel, m_conj)
    b2_idx = build_b2(panel)
    print(f"B1 ratio (baseline:setup picked/target) mínima={b1_min_ratio:.2f} "
          f"conjunta={b1_conj_ratio:.2f} (target hasta 1:{B1_MATCH_RATIO})")

    # Subconjuntos de filas
    rows_min = panel[m_min]
    rows_conj = panel[m_conj]
    rows_b1_min = panel.loc[b1_min_idx]
    rows_b1_conj = panel.loc[b1_conj_idx]
    rows_b2 = panel.loc[b2_idx]

    # ----- results.json -----
    results = {
        "meta": {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "signal_period": ["2020-01-01", "2025-12-31"],
            "download_start": DOWNLOAD_START.date().isoformat(),
            "thresholds": {
                "pos": POS_THRESHOLD, "rsi": RSI_THRESHOLD,
                "consol": CONSOL_THRESHOLD, "vol_ratio": VOL_RATIO_THRESHOLD,
                "min_median_quote_vol_30d": MIN_MEDIAN_QUOTE_VOL_30D,
                "tp": TP, "sl": SL, "rule_hold_days": RULE_HOLD_DAYS,
                "win15": WIN15_THRESHOLD,
            },
            "regime_buckets": {"alt-bull": ">=0.6", "neutral": "0.4-0.6", "bear": "<0.4"},
            "b1_match_ratio_target": B1_MATCH_RATIO,
            "b1_ratio_used": {"minimal": round(b1_min_ratio, 3),
                              "conjunct": round(b1_conj_ratio, 3)},
            "download_stats": {
                "exchangeinfo_usdt_trading_spot":
                    DOWNLOAD_STATS["exchangeinfo_usdt_trading_spot"],
                "candidate_symbols": DOWNLOAD_STATS["candidate_symbols"],
                "symbols_with_data": DOWNLOAD_STATS["symbols_with_data"],
                "n_excluded_stablecoins": len(DOWNLOAD_STATS["excluded_stablecoins"]),
                "n_excluded_leveraged": len(DOWNLOAD_STATS["excluded_leveraged"]),
                "n_skipped_insufficient_history":
                    len(DOWNLOAD_STATS["symbols_skipped_insufficient_history"]),
                "n_skipped_no_data": len(DOWNLOAD_STATS["symbols_skipped_no_data"]),
                "excluded_leveraged": DOWNLOAD_STATS["excluded_leveraged"],
                "excluded_stablecoins": DOWNLOAD_STATS["excluded_stablecoins"],
            },
            "panel_rows": int(len(panel)),
            "rows_forward_evaluable": n_eval,
            "hits": {"rule_minimal": n_min_hits, "rule_conjunct": n_conj_hits},
            "caveats": [
                "SESGO DE SUPERVIVENCIA: exchangeInfo sólo lista símbolos que cotizan HOY. "
                "Los delistados (que tipicamente cayeron) no aparecen → niveles absolutos "
                "de retorno inflados para setup Y baseline por igual. El DELTA setup−baseline "
                "sigue siendo informativo; los niveles absolutos no.",
                "Retorno en USDT incluye el movimiento de BTC (beta del mercado).",
                "rule_return: si TP y SL se tocan el mismo día, se asume SL primero "
                "(conservador).",
            ],
        },
        "tables": {
            "rule_minimal": {
                "setup": stratify(rows_min, panel),
                "B1": stratify(rows_b1_min, panel),
                "B2": stratify(rows_b2, panel),
                "delta_vs_B1": delta_and_pval(rows_min, rows_b1_min),
            },
            "rule_conjunct": {
                "setup": stratify(rows_conj, panel),
                "B1": stratify(rows_b1_conj, panel),
                "B2": stratify(rows_b2, panel),
                "delta_vs_B1": delta_and_pval(rows_conj, rows_b1_conj),
            },
        },
    }

    out_json = HERE / "results.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"escrito {out_json}")

    write_findings(results)
    print(f"== done en {time.time()-t0:.0f}s ==")
    return results


def _fmt_pct(x):
    return "n/a" if x is None else f"{x*100:.1f}%"


def _fmt_p(mw):
    if mw.get("p_value") is None:
        return mw.get("note", "n/a")
    return f"p={mw['p_value']:.4g}"


def write_findings(results):
    """Genera findings.md con el veredicto honesto."""
    conj = results["tables"]["rule_conjunct"]["delta_vs_B1"]
    minimal = results["tables"]["rule_minimal"]["delta_vs_B1"]

    g = conj["global"]
    altbull = conj["by_regime"].get("alt-bull", {})
    neutral = conj["by_regime"].get("neutral", {})
    bear = conj["by_regime"].get("bear", {})

    def block_line(name, blk):
        if not blk or "setup" not in blk:
            return f"- **{name}**: sin datos"
        s, b = blk["setup"], blk["baseline"]
        d = blk["delta_median_max_fwd_14d"]
        return (f"- **{name}**: setup n={s['n']} mediana max_fwd_14d="
                f"{_fmt_pct(s['median_max_fwd_14d'])} win15={_fmt_pct(s['win15'])} "
                f"| B1 n={b['n']} mediana={_fmt_pct(b['median_max_fwd_14d'])} "
                f"win15={_fmt_pct(b['win15'])} | Δmediana={_fmt_pct(d)} "
                f"({_fmt_p(blk['mann_whitney'])})")

    # Veredictos booleanos
    def beats(blk):
        if not blk or blk.get("delta_median_max_fwd_14d") is None:
            return None
        d = blk["delta_median_max_fwd_14d"]
        p = blk["mann_whitney"].get("p_value")
        sig = (p is not None and p < 0.05)
        return d > 0, sig

    g_beat = beats(g)
    ab_beat = beats(altbull)
    # "fuera de alt-bull" = neutral + bear combinados
    nb_blk = _combine_outside_altbull(results, "rule_conjunct")
    nb_beat = beats(nb_blk)

    def verdict_txt(beat):
        if beat is None:
            return "sin datos suficientes"
        better, sig = beat
        if better and sig:
            return "SÍ (Δ>0, p<0.05)"
        if better and not sig:
            return "marginal (Δ>0 pero no significativo a 0.05)"
        return "NO (Δ≤0)"

    lines = []
    lines.append("# Findings — ¿el setup de musikito tiene edge fuera de su régimen?")
    lines.append("")
    lines.append(f"_Generado {results['meta']['generated_utc']}. "
                 f"Período señales 2020-01-01 → 2025-12-31. "
                 f"Universo: {results['meta']['download_stats']['symbols_with_data']} "
                 f"símbolos spot USDT con ≥250 barras._")
    lines.append("")
    lines.append("## Veredicto")
    lines.append("")
    lines.append(f"**Regla-conjunta vs B1 (control de estado, matcheado por fecha), "
                 f"métrica = mediana de max_fwd_14d, Mann-Whitney one-sided:**")
    lines.append("")
    lines.append(f"- (a) **Global**: {verdict_txt(g_beat)}")
    lines.append(f"- (b) **En alt-bull** (breadth≥0.6): {verdict_txt(ab_beat)}")
    lines.append(f"- (c) **Fuera de alt-bull** (neutral+bear): {verdict_txt(nb_beat)}")
    lines.append("")

    # Párrafo de prosa
    edge_conditional = (ab_beat and ab_beat[0] and (not nb_beat or not nb_beat[0]))
    para = []
    if g_beat and g_beat[0] and g_beat[1]:
        para.append("La regla-conjunta supera a B1 globalmente con significancia estadística")
    elif g_beat and g_beat[0]:
        para.append("La regla-conjunta supera a B1 globalmente pero el margen NO es "
                    "estadísticamente significativo")
    else:
        para.append("La regla-conjunta NO supera a B1 globalmente")
    para[-1] += (f" (Δmediana max_fwd_14d = {_fmt_pct(g['delta_median_max_fwd_14d'])}, "
                 f"{_fmt_p(g['mann_whitney'])}).")
    if edge_conditional:
        para.append("El edge parece **condicional al régimen**: aparece en alt-bull y se "
                    "desvanece (o invierte) fuera de él, justo lo que predijo §1.2 de la "
                    "metodología.")
    elif ab_beat and ab_beat[0] and nb_beat and nb_beat[0]:
        para.append("El edge NO parece puramente condicional al régimen: persiste tanto "
                    "dentro como fuera de alt-bull (aunque revisar magnitudes y n).")
    elif (not ab_beat or not ab_beat[0]) and (nb_beat and nb_beat[0]):
        para.append("Sorprendentemente el edge aparece más FUERA de alt-bull que dentro; "
                    "revisar n por bucket antes de concluir.")
    else:
        para.append("No se observa edge claro ni dentro ni fuera de alt-bull para la "
                    "regla-conjunta sobre este universo y período.")
    lines.append(" ".join(para))
    lines.append("")

    # Tabla por bucket
    lines.append("## Regla-conjunta vs B1 por régimen")
    lines.append("")
    lines.append(block_line("Global", g))
    lines.append(block_line("alt-bull", altbull))
    lines.append(block_line("neutral", neutral))
    lines.append(block_line("bear", bear))
    lines.append("")

    lines.append("## Regla-mínima vs B1 (contraste: ¿el gate de posición solo ya basta?)")
    lines.append("")
    lines.append(block_line("Global", minimal["global"]))
    lines.append(block_line("alt-bull", minimal["by_regime"].get("alt-bull", {})))
    lines.append(block_line("neutral", minimal["by_regime"].get("neutral", {})))
    lines.append(block_line("bear", minimal["by_regime"].get("bear", {})))
    lines.append("")

    lines.append("## Caveats (honestos)")
    lines.append("")
    for c in results["meta"]["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    lines.append(f"- B1 se matcheó por fecha con ratio usado "
                 f"{results['meta']['b1_ratio_used']} (target hasta "
                 f"1:{results['meta']['b1_match_ratio_target']}), muestreo determinista "
                 f"(semilla = hash del string de fecha % tamaño-del-pool).")
    lines.append("")

    (HERE / "findings.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"escrito {HERE / 'findings.md'}")


def _combine_outside_altbull(results, rule_key):
    """Combina neutral+bear en un bloque sintético recomputando desde los subconjuntos.

    Como delta_and_pval ya separó por bucket pero no guardó las series crudas, aquí
    recomputamos un bloque agregado a partir de las celdas (delta de medianas no es
    aditivo, así que usamos las celdas combinadas vía promedio ponderado de medianas
    es impreciso; mejor: marcamos la combinación a nivel de veredicto usando el peor caso).
    """
    by_reg = results["tables"][rule_key]["delta_vs_B1"]["by_regime"]
    neutral = by_reg.get("neutral")
    bear = by_reg.get("bear")
    blocks = [b for b in (neutral, bear) if b and b.get("setup", {}).get("n", 0) > 0]
    if not blocks:
        return None
    # Veredicto "fuera de alt-bull": Δ>0 sólo si TODOS los buckets fuera tienen Δ>0
    # y p<0.05 en al menos uno (criterio conservador).
    deltas = [b["delta_median_max_fwd_14d"] for b in blocks
              if b["delta_median_max_fwd_14d"] is not None]
    pvals = [b["mann_whitney"].get("p_value") for b in blocks
             if b["mann_whitney"].get("p_value") is not None]
    if not deltas:
        return None
    agg_delta = min(deltas)  # peor caso de delta
    agg_p = min(pvals) if pvals else None
    # bloque sintético compatible con beats()
    return {
        "delta_median_max_fwd_14d": agg_delta,
        "mann_whitney": {"p_value": agg_p},
        "setup": {"n": sum(b["setup"]["n"] for b in blocks)},
    }


if __name__ == "__main__":
    main()
