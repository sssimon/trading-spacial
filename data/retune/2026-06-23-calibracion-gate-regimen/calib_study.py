"""
Calibración del gate de exposición por régimen — prueba de falsación.
Implementación CONGELADA según METODOLOGIA.md (2026-06-23). No cambia la metodología.

Lee data/program_ohlcv.db (anti-survivorship) + btc_dominance.csv (congelado).
NO toca data/holdout/, NO llama open_holdout/simulate_strategy. Período de señales
termina 2025-04-29 (barra antes del holdout 2025-04-30→2026-04-30).
Reusa regime.alt_season.compose_regime para el voto de 3 componentes (fidelidad).
"""
import json as _json
import sqlite3
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.stats import mannwhitneyu

HERE = Path(__file__).resolve().parent
# raíz del repo: .../data/retune/<dir>/ → subir 3
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

SIGNAL_START = pd.Timestamp("2021-01-01", tz="UTC")
SIGNAL_END = pd.Timestamp("2025-04-29", tz="UTC")   # barra antes del holdout
HOLDOUT_START = pd.Timestamp("2025-04-30", tz="UTC")


_DEFAULT_DB = str(REPO_ROOT / "data" / "program_ohlcv.db")
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def load_spot_daily(db_path: str, symbols=None) -> dict:
    con = sqlite3.connect(db_path)
    try:
        if symbols is None:
            symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM spot_klines")]
        out = {}
        for sym in symbols:
            rows = con.execute(
                "SELECT open_time, open, high, low, close, volume FROM spot_klines "
                "WHERE symbol=? ORDER BY open_time", (sym,)).fetchall()
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("ts")
            daily = df.resample("1D").agg(_AGG).dropna(subset=["close"])
            daily["quote_vol"] = daily["volume"] * daily["close"]
            daily.index = daily.index.normalize()
            out[sym] = daily
        return out
    finally:
        con.close()


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


def load_btc_dominance(csv_path: str) -> pd.Series:
    """CSV (date,dominance) → Series index=fecha UTC, valor=fracción 0-1.
    Normaliza desde 0-100 si el máximo sugiere porcentaje (>1.5)."""
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.normalize()
    s = pd.Series(df["dominance"].astype(float).values, index=df["date"])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.max() > 1.5:
        s = s / 100.0
    s.name = "btc_dominance"
    return s


# ----------------------------------------------------------------------------
# Features ex-ante
# Definición canónica: screener/valley_filter.measure_setup + edge_study.py
# Copiado VERBATIM de data/retune/2026-06-18-setup-edge-multiregimen/edge_study.py
# líneas 203-326. Única adición: ret_30d (forward 30d para outperf de régimen).
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

    # ret_30d: retorno forward 30d para outperf del régimen
    df["ret_30d"] = (close - close.shift(30)) / close.shift(30)

    return df


# ----------------------------------------------------------------------------
# Selección — verbatim de edge_study.py línea 373-374
# ----------------------------------------------------------------------------
def select_rule_minimal(panel):
    return panel["alive"] & (panel["pos_in_30d_range"] <= POS_THRESHOLD)


# ----------------------------------------------------------------------------
# Métricas — verbatim de edge_study.py líneas 433-460
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


# ----------------------------------------------------------------------------
# Criterio de aceptación pre-comprometido
# ----------------------------------------------------------------------------
MARGEN_PP = 2.0
P_MAX = 0.01


def evaluate_acceptance(by_estado: dict, b2_stats: dict) -> dict:
    alts = by_estado.get("alts", {})
    btc = by_estado.get("btc", {})
    if not alts.get("max_fwd_14d") or not btc.get("max_fwd_14d"):
        return {"verdict": "NO_PASA", "razon": "sin datos en alts o btc", "delta_pp": None,
                "p_value": None, "rule_return_inverts": None}
    med_alts = float(np.median(alts["max_fwd_14d"]))
    med_btc = float(np.median(btc["max_fwd_14d"]))
    delta_pp = (med_alts - med_btc) * 100.0
    mw_fwd = mann_whitney(alts["max_fwd_14d"], btc["max_fwd_14d"])        # alts > btc
    mw_inv = mann_whitney(btc["max_fwd_14d"], alts["max_fwd_14d"])        # btc > alts
    p_fwd = mw_fwd.get("p_value")
    p_inv = mw_inv.get("p_value")
    # rule_return: ¿la dirección se mantiene? (alts >= btc en realizado)
    rr_alts = float(np.median(alts["rule_return"])) if alts.get("rule_return") else None
    rr_btc = float(np.median(btc["rule_return"])) if btc.get("rule_return") else None
    rr_inverts = (rr_alts is not None and rr_btc is not None and rr_alts < rr_btc)
    btc_below_b2 = med_btc < (b2_stats.get("median_max_fwd_14d") or float("inf"))

    if (delta_pp >= MARGEN_PP and btc_below_b2 and p_fwd is not None and p_fwd < P_MAX
            and not rr_inverts):
        verdict, razon = "PASA", "separación direccional + significativa + btc<B2 + rr no invierte"
    elif (delta_pp <= -MARGEN_PP and p_inv is not None and p_inv < P_MAX):
        verdict, razon = "INVERTIDO", "btc le gana a alts con significancia — el gate está al revés"
    else:
        verdict, razon = "NO_PASA", "no se cumple el criterio pre-comprometido"
    return {"verdict": verdict, "delta_pp": delta_pp, "p_value": p_fwd,
            "p_value_invertido": p_inv, "rule_return_inverts": rr_inverts,
            "btc_below_b2": btc_below_b2, "razon": razon}


def regime_by_date(panel, btc_dom, thresholds=None):
    """Régimen de 3 componentes por fecha. Reusa compose_regime (fidelidad)."""
    from regime.alt_season import compose_regime
    BTC = "BTCUSDT"
    out = {}
    for date, g in panel.groupby("date"):
        alive = g[g["alive"]]
        n_universe = len(g)
        n_eval = len(alive)
        coverage_ratio = (n_eval / n_universe) if n_universe else 0.0
        alts = alive[alive["symbol"] != BTC]
        alt_contribs = [
            {"above_sma50": bool(r.above_sma50), "ret_30d": float(r.ret_30d)}
            for r in alts.itertuples() if pd.notna(r.ret_30d)
        ]
        btc_row = alive[alive["symbol"] == BTC]
        btc_ret_30d = float(btc_row["ret_30d"].iloc[0]) if len(btc_row) and pd.notna(btc_row["ret_30d"].iloc[0]) else None
        dom = btc_dom.get(date, None)
        dom = float(dom) if dom is not None and pd.notna(dom) else None
        res = compose_regime(alt_contribs, btc_ret_30d, dom, coverage_ratio, thresholds=thresholds)
        out[date] = res["estado"]
    return pd.Series(out, name="regime")


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


# ---------------------------------------------------------------------------
# Task 6: orquestación + salidas + grid exploratorio
# ---------------------------------------------------------------------------

# CSV de BTC dominance congelado (externo; debe existir antes de correr main())
_DEFAULT_BTC_DOM_CSV = str(HERE / "btc_dominance.csv")


def _build_panel(symbol_dfs: dict) -> pd.DataFrame:
    frames = []
    for sym, df in symbol_dfs.items():
        sub = df.copy()
        sub["symbol"] = sym
        frames.append(sub.reset_index().rename(columns={"index": "date", "ts": "date"}))
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"], utc=True).dt.normalize()
    return panel[(panel["date"] >= SIGNAL_START) & (panel["date"] <= SIGNAL_END)]


def _bucketed(panel: pd.DataFrame, regime_series: pd.Series, mask: pd.Series):
    rows = panel[mask].copy()
    rows = rows.merge(
        regime_series.rename("regime"), left_on="date", right_index=True, how="left"
    )
    rows = rows.dropna(subset=["max_fwd_14d"])
    by: dict = {}
    for estado, g in rows.groupby("regime"):
        by[estado] = {
            "max_fwd_14d": g["max_fwd_14d"].dropna().tolist(),
            "rule_return": g["rule_return"].dropna().tolist(),
            "stats": cell_stats(g),
        }
    return by, rows


def run_study(db_path: str, btc_dom_csv: str) -> dict:
    from regime.alt_season import effective_thresholds
    btc_dom = load_btc_dominance(btc_dom_csv)
    symbol_dfs = {s: compute_features(df) for s, df in load_spot_daily(db_path).items()}
    panel = _build_panel(symbol_dfs)
    regime_prod = regime_by_date(panel, btc_dom, thresholds=None)
    m_min = select_rule_minimal(panel)
    by_estado, _ = _bucketed(panel, regime_prod, m_min)
    b2_stats = cell_stats(panel[panel["alive"]].dropna(subset=["max_fwd_14d"]))
    acceptance = evaluate_acceptance(by_estado, b2_stats)
    grid = grid_search(panel, btc_dom)
    return {
        "by_estado": {k: v["stats"] for k, v in by_estado.items()},
        "b2": b2_stats,
        "verdict": acceptance["verdict"],
        "acceptance": acceptance,
        "production_thresholds": effective_thresholds(None),
        "grid_exploratory": grid,
        "signal_period": [str(SIGNAL_START.date()), str(SIGNAL_END.date())],
        "caveats": [
            "Survivorship: panel retiene delistadas pero su cobertura no es total (187 símbolos del ingest 2026-06-05).",
            "quote_vol derivado = volume × close (≈ quote-vol de Binance).",
            "BTC.D de fuente externa congelada (ver METODOLOGIA §Procedencia).",
            "Retorno en USDT incluye beta de BTC.",
            "Grid-search es EXPLORATORIO (overfitting); la decisión es a umbrales de producción.",
        ],
    }


def grid_search(panel: pd.DataFrame, btc_dom: pd.Series) -> list:
    """Exploratorio: varía umbrales en grilla gruesa, reporta el mejor delta. Cota superior."""
    from regime.alt_season import effective_thresholds
    best = []
    for breadth_alt in (0.55, 0.60, 0.65, 0.70):
        for outperf_alt in (0.0, 0.05, 0.10):
            ov = {"BREADTH_ALT": breadth_alt, "OUTPERF_ALT": outperf_alt}
            reg = regime_by_date(panel, btc_dom, thresholds=effective_thresholds(ov))
            by, _ = _bucketed(panel, reg, select_rule_minimal(panel))
            a = by.get("alts", {}).get("stats", {}).get("median_max_fwd_14d")
            b = by.get("btc", {}).get("stats", {}).get("median_max_fwd_14d")
            if a is not None and b is not None:
                best.append({
                    "overrides": ov,
                    "delta_pp": (a - b) * 100.0,
                    "n_alts": by["alts"]["stats"]["n"],
                    "n_btc": by["btc"]["stats"]["n"],
                })
    best.sort(key=lambda x: x["delta_pp"], reverse=True)
    return best[:10]


def _fmt_pp(v) -> str:
    return f"{v:.1f}" if isinstance(v, (int, float)) else "?"


def write_findings(res: dict) -> None:
    """Genera findings.md con el veredicto honesto."""
    verdict = res["verdict"]
    acceptance = res.get("acceptance", {})
    by_estado = res.get("by_estado", {})
    grid = res.get("grid_exploratory", [])
    signal_period = res.get("signal_period", ["?", "?"])
    caveats = res.get("caveats", [])

    prose = {
        "PASA": (
            f"El gate pasa los criterios pre-comprometidos: "
            f"delta={_fmt_pp(acceptance.get('delta_pp'))}pp, "
            f"p_alts_gt_btc={acceptance.get('p_value')}, "
            f"btc<B2={acceptance.get('btc_below_b2')}, "
            f"rr no invierte. Condición técnica de activación cumplida."
        ),
        "NO_PASA": (
            f"El gate NO pasa: {acceptance.get('razon', 'criterios incumplidos')}. "
            f"Delta={_fmt_pp(acceptance.get('delta_pp'))}pp "
            f"(umbral mínimo requerido ≥{MARGEN_PP}pp). "
            f"No hay evidencia suficiente para encender."
        ),
        "INVERTIDO": (
            f"El gate está INVERTIDO: en régimen btc el retorno forward supera a alts "
            f"(delta={_fmt_pp(acceptance.get('delta_pp'))}pp, "
            f"p_btc_gt_alts={acceptance.get('p_value_invertido')}). "
            f"Encender el gate causaría daño."
        ),
    }.get(verdict, f"Veredicto inesperado: {verdict}")

    lines = [
        "# Findings — Calibración del gate de régimen",
        "",
        f"_Período de señales: {signal_period[0]} → {signal_period[1]}._",
        "",
        "## Veredicto",
        "",
        f"**{verdict}** — {prose}",
        "",
        "## Resultados por estado (umbrales de producción)",
        "",
    ]

    if by_estado:
        for estado, stats in by_estado.items():
            n = stats.get("n", 0)
            med = stats.get("median_max_fwd_14d")
            w15 = stats.get("win15")
            med_s = f"{med * 100:.1f}" if med is not None else "—"
            w15_s = f"{w15 * 100:.0f}" if w15 is not None else "—"
            lines.append(
                f"- **{estado}** n={n} mediana max_fwd_14d={med_s}% win15={w15_s}%"
            )
    else:
        lines.append("_(sin datos por estado — panel insuficiente)_")

    lines += [
        "",
        "## Grid exploratorio (COTA SUPERIOR — no usar para decisión)",
        "",
    ]
    if grid:
        for row in grid[:5]:
            ov = row["overrides"]
            lines.append(
                f"- BREADTH_ALT={ov['BREADTH_ALT']} OUTPERF_ALT={ov['OUTPERF_ALT']}: "
                f"delta={_fmt_pp(row['delta_pp'])}pp "
                f"(n_alts={row['n_alts']}, n_btc={row['n_btc']})"
            )
    else:
        lines.append("_(grid vacío: sin datos suficientes en ambos estados simultáneamente)_")

    lines += ["", "## Caveats", ""]
    for c in caveats:
        lines.append(f"- {c}")

    out = HERE / "findings.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"escrito {out}")


def main() -> None:
    res = run_study(_DEFAULT_DB, _DEFAULT_BTC_DOM_CSV)
    out_json = HERE / "results.json"
    out_json.write_text(_json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(f"escrito {out_json}")
    write_findings(res)


if __name__ == "__main__":
    main()
