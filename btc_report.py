#!/usr/bin/env python3
"""
BTC Market Intelligence Report
Genera un reporte HTML con métricas clave de trading para BTC/USDT
Fuentes: Binance Futures API, Coinglass (si disponible), Farside (ETF flows)
"""

import os
import requests
import pandas as pd
import json
import base64
import io
import sys
from datetime import datetime, timezone, timedelta
import warnings
warnings.filterwarnings('ignore')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.colors import LinearSegmentedColormap
import numpy as np

SYMBOL = "BTCUSDT"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BTC-Report/1.0)"}
TIMEOUT = 15

# ──────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ──────────────────────────────────────────────────────────────────────────────

def safe_get(url, params=None, headers=None):
    try:
        r = requests.get(url, params=params, headers=headers or HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"[WARN] {url}: {e}", file=sys.stderr)
        return None

def get_ls_ratio_global(period="1h", limit=48):
    """Ratio largo/corto global de cuentas - Binance Futures"""
    r = safe_get(
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        params={"symbol": SYMBOL, "period": period, "limit": limit}
    )
    if r is None: return None
    df = pd.DataFrame(r.json())
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    return df.sort_values("timestamp")

def get_ls_ratio_top(period="1h", limit=48):
    """Ratio largo/corto de top traders - Binance Futures"""
    r = safe_get(
        "https://fapi.binance.com/futures/data/topLongShortAccountRatio",
        params={"symbol": SYMBOL, "period": period, "limit": limit}
    )
    if r is None: return None
    df = pd.DataFrame(r.json())
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    return df.sort_values("timestamp")

def get_taker_ls_ratio(period="1h", limit=48):
    """Ratio buy/sell de takers (proxy de presión de liquidaciones)"""
    r = safe_get(
        "https://fapi.binance.com/futures/data/takerlongshortRatio",
        params={"symbol": SYMBOL, "period": period, "limit": limit}
    )
    if r is None: return None
    df = pd.DataFrame(r.json())
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df["buySellRatio"] = df["buySellRatio"].astype(float)
    df["buyVol"] = df["buyVol"].astype(float)
    df["sellVol"] = df["sellVol"].astype(float)
    return df.sort_values("timestamp")

def get_open_interest_hist(period="1h", limit=48):
    """Historial de Open Interest - Binance Futures"""
    r = safe_get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": SYMBOL, "period": period, "limit": limit}
    )
    if r is None: return None
    df = pd.DataFrame(r.json())
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
    df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
    return df.sort_values("timestamp")

def get_klines(interval="1h", limit=168):
    """Velas OHLCV de BTC/USDT - Binance Spot"""
    r = safe_get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit}
    )
    if r is None: return None
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(r.json(), columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"].astype(int), unit="ms", utc=True)
    for c in ["open","high","low","close","volume","quote_vol","taker_buy_base","taker_buy_quote"]:
        df[c] = df[c].astype(float)
    return df.sort_values("open_time")

def get_funding_rate(limit=8):
    """Tasa de financiación actual - Binance Futures"""
    r = safe_get(
        "https://fapi.binance.com/fapi/v1/fundingRate",
        params={"symbol": SYMBOL, "limit": limit}
    )
    if r is None: return None
    df = pd.DataFrame(r.json())
    df["fundingTime"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.sort_values("fundingTime")

def get_btc_price():
    """Precio actual de BTC"""
    r = safe_get("https://api.binance.com/api/v3/ticker/price", params={"symbol": SYMBOL})
    if r is None: return None
    return float(r.json()["price"])

def get_liquidations_coinglass():
    """Intenta obtener liquidaciones de Coinglass (público)"""
    urls_to_try = [
        "https://open-api.coinglass.com/public/v2/liquidation_history",
        "https://open-api.coinglass.com/api/pro/v1/futures/liquidation/chart",
    ]
    for url in urls_to_try:
        r = safe_get(url, params={"symbol": "BTC", "timeType": 0})
        if r and r.status_code == 200:
            try:
                data = r.json()
                if data.get("code") == "0":
                    return data.get("data")
            except:
                pass
    return None

def get_etf_flows():
    """Scraping de flujos ETF BTC desde farside.co.uk"""
    try:
        from bs4 import BeautifulSoup
        r = safe_get("https://farside.co.uk/bitcoin-etf-flow-all-data/")
        if r is None: return None, None
        soup = BeautifulSoup(r.content, "lxml")
        tables = soup.find_all("table")
        if not tables: return None, None
        # La primera tabla grande suele tener los datos de flujo
        for table in tables:
            try:
                df = pd.read_html(str(table))[0]
                if "Total" in df.columns or "Date" in str(df.columns):
                    df.columns = [str(c).strip() for c in df.columns]
                    # Buscar columna de fecha
                    date_col = [c for c in df.columns if "date" in c.lower() or "day" in c.lower()]
                    total_col = [c for c in df.columns if "total" in c.lower()]
                    if date_col and total_col:
                        df = df[[date_col[0], total_col[0]]].dropna()
                        df.columns = ["date", "total"]
                        df["total"] = pd.to_numeric(df["total"].astype(str).str.replace(",","").str.replace("$",""), errors="coerce")
                        df = df.dropna()
                        last7 = df.tail(7)
                        last30 = df.tail(30)
                        return last7, last30
            except:
                continue
        return None, None
    except ImportError:
        return None, None
    except Exception as e:
        print(f"[WARN] ETF flows: {e}", file=sys.stderr)
        return None, None

def estimate_liquidations_from_oi(klines_1h, oi_hist):
    """
    Estima liquidaciones por ventana de tiempo usando cambios en OI + precio.
    Liq. longs ≈ caída precio + caída OI
    Liq. shorts ≈ subida precio + caída OI
    """
    if klines_1h is None or oi_hist is None:
        return None
    df = klines_1h[["open_time","close","volume"]].copy()
    df = df.merge(oi_hist[["timestamp","sumOpenInterestValue"]],
                  left_on="open_time", right_on="timestamp", how="inner")
    df["oi_change"] = df["sumOpenInterestValue"].diff()
    df["price_change"] = df["close"].pct_change()
    # Liq estimadas: OI cae mientras el precio se mueve fuertemente
    df["est_liq_longs"] = np.where(
        (df["price_change"] < -0.005) & (df["oi_change"] < 0),
        abs(df["oi_change"]), 0
    )
    df["est_liq_shorts"] = np.where(
        (df["price_change"] > 0.005) & (df["oi_change"] < 0),
        abs(df["oi_change"]), 0
    )
    df["est_liq_total"] = df["est_liq_longs"] + df["est_liq_shorts"]
    return df

def compute_liq_windows(liq_df):
    """Calcula liquidaciones estimadas por ventana de 1h, 4h, 12h, 24h"""
    now = datetime.now(timezone.utc)
    windows = {"1h": 1, "4h": 4, "12h": 12, "24h": 24}
    result = {}
    for name, hours in windows.items():
        cutoff = now - timedelta(hours=hours)
        subset = liq_df[liq_df["open_time"] >= cutoff]
        result[name] = {
            "longs": subset["est_liq_longs"].sum() / 1e6,
            "shorts": subset["est_liq_shorts"].sum() / 1e6,
            "total": subset["est_liq_total"].sum() / 1e6,
        }
    return result

# ──────────────────────────────────────────────────────────────────────────────
# CHART GENERATION
# ──────────────────────────────────────────────────────────────────────────────

DARK_BG     = "#0d1117"
PANEL_BG    = "#161b22"
GRID_COLOR  = "#21262d"
TEXT_COLOR  = "#e6edf3"
GREEN       = "#3fb950"
RED         = "#f85149"
BLUE        = "#58a6ff"
YELLOW      = "#d29922"
PURPLE      = "#bc8cff"
ORANGE      = "#f0883e"

def setup_dark_ax(ax):
    ax.set_facecolor(PANEL_BG)
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    ax.xaxis.label.set_color(TEXT_COLOR)
    ax.yaxis.label.set_color(TEXT_COLOR)
    ax.title.set_color(TEXT_COLOR)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)
    ax.grid(True, color=GRID_COLOR, linewidth=0.5, alpha=0.8)

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=DARK_BG, edgecolor="none")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return b64

# ── 1. LONG/SHORT RATIO ──────────────────────────────────────────────────────

def chart_ls_ratio(ls_global, ls_top, taker):
    fig, axes = plt.subplots(3, 1, figsize=(12, 7), facecolor=DARK_BG)
    fig.suptitle("Ratio Largo / Corto — BTC/USDT", color=TEXT_COLOR, fontsize=13, fontweight="bold", y=1.01)

    # Panel 1: Global L/S Ratio
    ax1 = axes[0]
    setup_dark_ax(ax1)
    if ls_global is not None:
        t = ls_global["timestamp"]
        ax1.plot(t, ls_global["longShortRatio"], color=BLUE, linewidth=1.5, label="Ratio L/S Global")
        ax1.axhline(1.0, color=YELLOW, linestyle="--", linewidth=0.8, alpha=0.7, label="Neutro (1.0)")
        latest = ls_global["longShortRatio"].iloc[-1]
        long_pct = ls_global["longAccount"].iloc[-1] * 100
        short_pct = ls_global["shortAccount"].iloc[-1] * 100
        ax1.set_title(f"Cuentas Globales  |  Longs: {long_pct:.1f}%  Shorts: {short_pct:.1f}%  Ratio: {latest:.3f}",
                      color=TEXT_COLOR, fontsize=9)
        ax1.fill_between(t, ls_global["longShortRatio"], 1,
                         where=ls_global["longShortRatio"] >= 1, color=GREEN, alpha=0.15)
        ax1.fill_between(t, ls_global["longShortRatio"], 1,
                         where=ls_global["longShortRatio"] < 1, color=RED, alpha=0.15)
    else:
        ax1.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax1.transAxes)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    ax1.tick_params(axis='x', rotation=30, labelsize=7)
    ax1.legend(fontsize=8, facecolor=PANEL_BG, labelcolor=TEXT_COLOR)

    # Panel 2: Top Traders
    ax2 = axes[1]
    setup_dark_ax(ax2)
    if ls_top is not None:
        t = ls_top["timestamp"]
        ax2.plot(t, ls_top["longShortRatio"], color=PURPLE, linewidth=1.5, label="Top Traders")
        ax2.axhline(1.0, color=YELLOW, linestyle="--", linewidth=0.8, alpha=0.7)
        latest = ls_top["longShortRatio"].iloc[-1]
        ax2.set_title(f"Top Traders  |  Ratio actual: {latest:.3f}", color=TEXT_COLOR, fontsize=9)
        ax2.fill_between(t, ls_top["longShortRatio"], 1,
                         where=ls_top["longShortRatio"] >= 1, color=GREEN, alpha=0.15)
        ax2.fill_between(t, ls_top["longShortRatio"], 1,
                         where=ls_top["longShortRatio"] < 1, color=RED, alpha=0.15)
    else:
        ax2.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax2.transAxes)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    ax2.tick_params(axis='x', rotation=30, labelsize=7)
    ax2.legend(fontsize=8, facecolor=PANEL_BG, labelcolor=TEXT_COLOR)

    # Panel 3: Taker Ratio
    ax3 = axes[2]
    setup_dark_ax(ax3)
    if taker is not None:
        t = taker["timestamp"]
        colors = [GREEN if v >= 1 else RED for v in taker["buySellRatio"]]
        ax3.bar(t, taker["buySellRatio"] - 1, bottom=1, color=colors, alpha=0.7,
                width=timedelta(minutes=50), label="Taker Buy/Sell")
        ax3.axhline(1.0, color=YELLOW, linestyle="--", linewidth=0.8, alpha=0.7)
        latest = taker["buySellRatio"].iloc[-1]
        ax3.set_title(f"Taker Buy/Sell Ratio  |  Actual: {latest:.3f}", color=TEXT_COLOR, fontsize=9)
    else:
        ax3.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax3.transAxes)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    ax3.tick_params(axis='x', rotation=30, labelsize=7)

    plt.tight_layout()
    return fig_to_b64(fig)

# ── 2. LIQUIDACIONES ─────────────────────────────────────────────────────────

def chart_liquidaciones(liq_windows, liq_df):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), facecolor=DARK_BG)
    fig.suptitle("Liquidaciones Estimadas — BTC/USDT", color=TEXT_COLOR, fontsize=13, fontweight="bold")

    # Panel izquierdo: barras por ventana de tiempo
    ax1 = axes[0]
    setup_dark_ax(ax1)
    if liq_windows:
        labels = list(liq_windows.keys())
        longs_vals = [liq_windows[k]["longs"] for k in labels]
        shorts_vals = [liq_windows[k]["shorts"] for k in labels]
        x = np.arange(len(labels))
        w = 0.35
        ax1.bar(x - w/2, longs_vals, w, label="Longs liquidados", color=RED, alpha=0.85)
        ax1.bar(x + w/2, shorts_vals, w, label="Shorts liquidados", color=GREEN, alpha=0.85)
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, color=TEXT_COLOR)
        ax1.set_ylabel("Millones USD", color=TEXT_COLOR)
        ax1.set_title("Liquidaciones por Ventana de Tiempo\n(estimado via cambios OI)", color=TEXT_COLOR, fontsize=9)
        ax1.legend(fontsize=8, facecolor=PANEL_BG, labelcolor=TEXT_COLOR)
        # Totals on top
        for i, k in enumerate(labels):
            total = liq_windows[k]["total"]
            ax1.text(i, max(longs_vals[i], shorts_vals[i]) + 0.1, f"${total:.1f}M",
                     ha="center", color=TEXT_COLOR, fontsize=7)
    else:
        ax1.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax1.transAxes)

    # Panel derecho: serie temporal
    ax2 = axes[1]
    setup_dark_ax(ax2)
    if liq_df is not None:
        last48 = liq_df.tail(48)
        t = last48["open_time"]
        ax2.bar(t, last48["est_liq_longs"] / 1e6, color=RED, alpha=0.75,
                label="Liq. Longs est.", width=timedelta(minutes=50))
        ax2.bar(t, last48["est_liq_shorts"] / 1e6, bottom=0, color=GREEN, alpha=0.75,
                label="Liq. Shorts est.", width=timedelta(minutes=50))
        ax2.set_title("Serie Temporal — Liquidaciones Estimadas (48h)", color=TEXT_COLOR, fontsize=9)
        ax2.set_ylabel("Millones USD", color=TEXT_COLOR)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
        ax2.tick_params(axis='x', rotation=35, labelsize=7)
        ax2.legend(fontsize=8, facecolor=PANEL_BG, labelcolor=TEXT_COLOR)
    else:
        ax2.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax2.transAxes)

    plt.tight_layout()
    return fig_to_b64(fig)

# ── 3. ETF FLOWS ─────────────────────────────────────────────────────────────

def chart_etf_flows(df7, df30, funding_df):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), facecolor=DARK_BG)
    fig.suptitle("Flujos ETF BTC + Funding Rate", color=TEXT_COLOR, fontsize=13, fontweight="bold")

    ax1 = axes[0]
    setup_dark_ax(ax1)
    if df30 is not None and len(df30) > 0:
        colors = [GREEN if v >= 0 else RED for v in df30["total"]]
        ax1.bar(range(len(df30)), df30["total"], color=colors, alpha=0.85)
        ax1.axhline(0, color=YELLOW, linewidth=0.8, linestyle="--")
        ax1.set_title(f"Flujos ETF BTC — últimos {len(df30)} días (M USD)", color=TEXT_COLOR, fontsize=9)
        ax1.set_ylabel("Flujo (M USD)", color=TEXT_COLOR)
        # Etiquetas cada 5 días
        ticks = range(0, len(df30), 5)
        ax1.set_xticks(list(ticks))
        ax1.set_xticklabels([str(df30["date"].iloc[i]) for i in ticks if i < len(df30)],
                             rotation=40, fontsize=7, color=TEXT_COLOR)
        total_net = df30["total"].sum()
        ax1.text(0.02, 0.95, f"Neto 30d: ${total_net:.0f}M", transform=ax1.transAxes,
                 color=GREEN if total_net >= 0 else RED, fontsize=9, va="top")
    else:
        ax1.text(0.5, 0.5, "Datos ETF no disponibles\n(farside.co.uk)", ha="center", va="center",
                 color=TEXT_COLOR, fontsize=10, transform=ax1.transAxes)
        ax1.set_title("Flujos ETF BTC", color=TEXT_COLOR, fontsize=9)

    ax2 = axes[1]
    setup_dark_ax(ax2)
    if funding_df is not None:
        t = funding_df["fundingTime"]
        rates = funding_df["fundingRate"] * 100
        bar_colors = [GREEN if v >= 0 else RED for v in rates]
        ax2.bar(t, rates, color=bar_colors, alpha=0.85, width=timedelta(hours=7))
        ax2.axhline(0, color=YELLOW, linewidth=0.8, linestyle="--")
        current = rates.iloc[-1]
        annualized = current * 3 * 365
        ax2.set_title(f"Funding Rate — Actual: {current:.4f}%  |  Anualizado: {annualized:.1f}%",
                      color=TEXT_COLOR, fontsize=9)
        ax2.set_ylabel("Funding Rate (%)", color=TEXT_COLOR)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
        ax2.tick_params(axis='x', rotation=35, labelsize=7)
    else:
        ax2.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax2.transAxes)

    plt.tight_layout()
    return fig_to_b64(fig)

# ── 4. VOLUME HEATMAP ────────────────────────────────────────────────────────

def chart_volume_heatmap(klines_1h):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), facecolor=DARK_BG)
    fig.suptitle("Mapa de Calor de Volumen — BTC/USDT", color=TEXT_COLOR, fontsize=13, fontweight="bold")

    ax1 = axes[0]
    setup_dark_ax(ax1)
    if klines_1h is not None:
        df = klines_1h.copy().tail(7 * 24)  # última semana
        df["hour"] = df["open_time"].dt.hour
        df["day_name"] = df["open_time"].dt.strftime("%a %d/%m")
        df["day"] = df["open_time"].dt.date
        days = sorted(df["day"].unique())[-7:]

        matrix = np.zeros((24, len(days)))
        day_labels = []
        for j, day in enumerate(days):
            day_labels.append(str(day))
            day_data = df[df["day"] == day]
            for _, row in day_data.iterrows():
                matrix[int(row["hour"]), j] = row["volume"]

        # Normalizar por columna
        col_max = matrix.max(axis=0, keepdims=True)
        col_max[col_max == 0] = 1
        matrix_norm = matrix / col_max

        cmap = LinearSegmentedColormap.from_list("vol", [DARK_BG, "#1e3a5f", BLUE, "#00d4ff"])
        im = ax1.imshow(matrix_norm, aspect="auto", cmap=cmap, vmin=0, vmax=1, origin="upper")
        ax1.set_xticks(range(len(days)))
        ax1.set_xticklabels(day_labels, rotation=40, ha="right", fontsize=7, color=TEXT_COLOR)
        ax1.set_yticks(range(0, 24, 2))
        ax1.set_yticklabels([f"{h:02d}:00" for h in range(0, 24, 2)], fontsize=7, color=TEXT_COLOR)
        ax1.set_title("Heatmap Vol. — por Hora y Día (últimos 7 días)", color=TEXT_COLOR, fontsize=9)
        ax1.set_ylabel("Hora UTC", color=TEXT_COLOR)
        plt.colorbar(im, ax=ax1, label="Vol. relativo",
                     fraction=0.03).ax.yaxis.set_tick_params(color=TEXT_COLOR, labelcolor=TEXT_COLOR)
    else:
        ax1.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax1.transAxes)

    # Panel derecho: volumen por hora del día (media histórica)
    ax2 = axes[1]
    setup_dark_ax(ax2)
    if klines_1h is not None:
        df = klines_1h.copy()
        df["hour"] = df["open_time"].dt.hour
        avg_vol = df.groupby("hour")["volume"].mean()
        bar_colors = [BLUE if v < avg_vol.mean() else ORANGE for v in avg_vol]
        ax2.bar(avg_vol.index, avg_vol.values, color=bar_colors, alpha=0.85)
        ax2.axhline(avg_vol.mean(), color=YELLOW, linewidth=1, linestyle="--", label="Promedio")
        ax2.set_title("Volumen Promedio por Hora UTC (7 días)", color=TEXT_COLOR, fontsize=9)
        ax2.set_xlabel("Hora UTC", color=TEXT_COLOR)
        ax2.set_ylabel("BTC", color=TEXT_COLOR)
        ax2.set_xticks(range(0, 24, 2))
        ax2.set_xticklabels([f"{h:02d}h" for h in range(0, 24, 2)], fontsize=7, color=TEXT_COLOR)
        ax2.legend(fontsize=8, facecolor=PANEL_BG, labelcolor=TEXT_COLOR)
    else:
        ax2.text(0.5, 0.5, "Datos no disponibles", ha="center", va="center",
                 color=TEXT_COLOR, transform=ax2.transAxes)

    plt.tight_layout()
    return fig_to_b64(fig)

# ── 5. MAPA DE LIQUIDACIONES ─────────────────────────────────────────────────

def chart_liq_map(btc_price, oi_value):
    """
    Mapa de liquidaciones estimado por niveles de precio.
    Calcula dónde ocurrirían liquidaciones masivas según precio actual y
    distribución típica de leverage (10x, 20x, 50x, 100x).
    """
    fig, ax = plt.subplots(figsize=(12, 5.5), facecolor=DARK_BG)
    setup_dark_ax(ax)
    fig.suptitle("Mapa de Liquidaciones Estimado — BTC/USDT Binance Futures",
                 color=TEXT_COLOR, fontsize=13, fontweight="bold")

    if btc_price is None:
        ax.text(0.5, 0.5, "Precio BTC no disponible", ha="center", va="center",
                color=TEXT_COLOR, transform=ax.transAxes)
        plt.tight_layout()
        return fig_to_b64(fig)

    price = float(btc_price)

    # Niveles de liquidación por leverage (liquidación = precio_entrada / (1 ± 1/leverage))
    leverages = [5, 10, 20, 25, 50, 100]
    # Distribución de tamaño estimada (más concentración en 10x-20x)
    liq_weights = {5: 0.08, 10: 0.25, 20: 0.30, 25: 0.15, 50: 0.15, 100: 0.07}

    # OI estimado disponible
    oi_est = oi_value if oi_value else price * 50000  # estimado si no hay dato

    # Rangos de precio desde -30% hasta +30%
    pct_range = np.linspace(-0.30, 0.30, 500)
    price_levels = price * (1 + pct_range)

    liq_longs = np.zeros_like(price_levels)
    liq_shorts = np.zeros_like(price_levels)

    for lev, weight in liq_weights.items():
        # Longs se liquidan hacia abajo
        liq_price_long = price * (1 - 1/lev)
        sigma = price * (0.01 / lev)
        liq_longs += weight * np.exp(-0.5 * ((price_levels - liq_price_long) / sigma) ** 2)

        # Shorts se liquidan hacia arriba
        liq_price_short = price * (1 + 1/lev)
        liq_shorts += weight * np.exp(-0.5 * ((price_levels - liq_price_short) / sigma) ** 2)

    # Normalizar
    max_val = max(liq_longs.max(), liq_shorts.max())
    if max_val > 0:
        liq_longs /= max_val
        liq_shorts /= max_val

    # Multiplicar por OI estimado (en millones)
    scale = (oi_est / 1e9) * 500  # escalar a millones USD

    ax.barh(price_levels, -liq_longs * scale, color=RED, alpha=0.6, label="Zona Liq. Longs", height=price*0.001)
    ax.barh(price_levels, liq_shorts * scale, color=GREEN, alpha=0.6, label="Zona Liq. Shorts", height=price*0.001)
    ax.axhline(price, color=YELLOW, linewidth=1.5, linestyle="--", label=f"Precio Actual ${price:,.0f}")
    ax.axvline(0, color=GRID_COLOR, linewidth=0.8)

    # Marcadores de niveles clave
    for lev in [10, 20, 50]:
        lp_down = price * (1 - 1/lev)
        lp_up = price * (1 + 1/lev)
        ax.axhline(lp_down, color=RED, linewidth=0.6, linestyle=":", alpha=0.6)
        ax.axhline(lp_up, color=GREEN, linewidth=0.6, linestyle=":", alpha=0.6)
        ax.text(ax.get_xlim()[0] if ax.get_xlim()[0] < 0 else -0.1,
                lp_down, f"  {lev}x Long liq", color=RED, fontsize=6.5, va="center")
        ax.text(0.1, lp_up, f"  {lev}x Short liq", color=GREEN, fontsize=6.5, va="center")

    ax.set_xlabel("Intensidad Relativa de Liquidaciones (M USD estimado)", color=TEXT_COLOR)
    ax.set_ylabel("Precio BTC (USD)", color=TEXT_COLOR)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=9, facecolor=PANEL_BG, labelcolor=TEXT_COLOR, loc="lower right")
    ax.set_title(
        f"Distribución estimada de liquidaciones por nivel de precio\n"
        f"Precio actual: ${price:,.0f}  |  Leverage estimado: 5x–100x",
        color=TEXT_COLOR, fontsize=9
    )

    plt.tight_layout()
    return fig_to_b64(fig)

# ──────────────────────────────────────────────────────────────────────────────
# HTML REPORT GENERATOR
# ──────────────────────────────────────────────────────────────────────────────

def generate_html_report(charts, summary):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    liq_windows = summary.get("liq_windows", {})
    price = summary.get("price")
    funding = summary.get("funding")
    oi = summary.get("oi")

    def liq_row(label, data):
        if not data: return ""
        color_l = "var(--red)" if data["longs"] > 0 else "var(--text)"
        color_s = "var(--green)" if data["shorts"] > 0 else "var(--text)"
        return f"""
        <tr>
          <td>{label}</td>
          <td style='color:{color_l}'>${data['longs']:.2f}M</td>
          <td style='color:{color_s}'>${data['shorts']:.2f}M</td>
          <td>${data['total']:.2f}M</td>
        </tr>"""

    liq_rows = ""
    for w in ["1h", "4h", "12h", "24h"]:
        if w in liq_windows:
            liq_rows += liq_row(w, liq_windows[w])

    price_str = f"${price:,.2f}" if price else "N/D"
    funding_str = f"{funding * 100:.4f}%" if funding is not None else "N/D"
    oi_str = f"${oi/1e9:.2f}B" if oi else "N/D"

    charts_html = ""
    for title, b64 in charts:
        charts_html += f"""
        <div class='chart-card'>
          <h2>{title}</h2>
          <img src='data:image/png;base64,{b64}' alt='{title}'/>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BTC Trading Report — {now_str}</title>
<style>
  :root {{
    --bg: #0d1117; --panel: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --red: #f85149; --blue: #58a6ff;
    --yellow: #d29922; --purple: #bc8cff; --orange: #f0883e;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 24px; }}
  h1 {{ font-size: 1.6rem; font-weight: 700; color: var(--blue); margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .metric-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 18px; }}
  .metric-card .label {{ color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }}
  .metric-card .value {{ font-size: 1.3rem; font-weight: 700; }}
  .liq-table-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 28px; }}
  .liq-table-card h2 {{ font-size: 1rem; margin-bottom: 12px; color: var(--text); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 8px 14px; text-align: right; font-size: 0.85rem; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); text-transform: uppercase; font-size: 0.72rem; letter-spacing: .05em; }}
  td:first-child, th:first-child {{ text-align: left; }}
  .chart-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 24px; }}
  .chart-card h2 {{ font-size: 1rem; margin-bottom: 12px; color: var(--text); }}
  .chart-card img {{ width: 100%; border-radius: 6px; }}
  .note {{ color: var(--muted); font-size: 0.75rem; margin-top: 20px; border-top: 1px solid var(--border); padding-top: 12px; }}
</style>
</head>
<body>
<h1>📊 BTC Market Intelligence Report</h1>
<p class="subtitle">Generado: {now_str} &nbsp;·&nbsp; Fuente: Binance Futures API &nbsp;·&nbsp; BTC/USDT</p>

<div class="summary-grid">
  <div class="metric-card">
    <div class="label">Precio BTC</div>
    <div class="value" style="color:var(--blue)">{price_str}</div>
  </div>
  <div class="metric-card">
    <div class="label">Open Interest</div>
    <div class="value">{oi_str}</div>
  </div>
  <div class="metric-card">
    <div class="label">Funding Rate</div>
    <div class="value" style="color:{'var(--green)' if funding and funding >= 0 else 'var(--red)'}">{funding_str}</div>
  </div>
  <div class="metric-card">
    <div class="label">Liq. 24h (est.)</div>
    <div class="value" style="color:var(--orange)">${liq_windows.get('24h', {}).get('total', 0):.1f}M</div>
  </div>
</div>

<div class="liq-table-card">
  <h2>💥 Liquidaciones Estimadas por Ventana de Tiempo</h2>
  <table>
    <thead><tr><th>Ventana</th><th>Longs Liq.</th><th>Shorts Liq.</th><th>Total</th></tr></thead>
    <tbody>{liq_rows if liq_rows else "<tr><td colspan='4' style='color:var(--muted);text-align:center'>Calculando estimados via cambios OI...</td></tr>"}</tbody>
  </table>
  <p style="color:var(--muted);font-size:0.72rem;margin-top:8px">⚠ Estimado via variación de Open Interest + dirección de precio. No son datos directos de liquidaciones.</p>
</div>

{charts_html}

<p class="note">
  Datos obtenidos de Binance Futures REST API (sin clave API).
  Liquidaciones estimadas mediante cambios en Open Interest + movimiento de precio (sin acceso directo a feed de liquidaciones).
  Los flujos ETF provienen de farside.co.uk cuando disponibles.
  Este reporte es solo informativo — no constituye asesoría financiera.
</p>
</body>
</html>"""

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print("🔄 Obteniendo datos del mercado BTC...", flush=True)

    btc_price    = get_btc_price()
    ls_global    = get_ls_ratio_global(period="1h", limit=48)
    ls_top       = get_ls_ratio_top(period="1h", limit=48)
    taker        = get_taker_ls_ratio(period="1h", limit=48)
    oi_hist      = get_open_interest_hist(period="1h", limit=48)
    klines_1h    = get_klines(interval="1h", limit=168)
    funding_df   = get_funding_rate(limit=8)
    etf_7, etf_30 = get_etf_flows()

    print(f"  ✅ Precio BTC: ${btc_price:,.2f}" if btc_price else "  ⚠️  Precio BTC: no disponible")
    print(f"  ✅ L/S Global: {len(ls_global)} registros" if ls_global is not None else "  ⚠️  L/S Global: no disponible")
    print(f"  ✅ Klines 1h: {len(klines_1h)} velas" if klines_1h is not None else "  ⚠️  Klines: no disponible")
    print(f"  ✅ OI History: {len(oi_hist)} registros" if oi_hist is not None else "  ⚠️  OI Hist: no disponible")
    print(f"  {'✅' if etf_7 is not None else '⚠️ '} ETF Flows: {'disponible' if etf_7 is not None else 'no disponible (farside.co.uk)'}")

    # Calcular liquidaciones estimadas
    liq_df = estimate_liquidations_from_oi(klines_1h, oi_hist)
    liq_windows = compute_liq_windows(liq_df) if liq_df is not None else {}

    # OI actual
    oi_current = None
    if oi_hist is not None and len(oi_hist) > 0:
        oi_current = oi_hist["sumOpenInterestValue"].iloc[-1]

    # Funding actual
    funding_current = None
    if funding_df is not None and len(funding_df) > 0:
        funding_current = funding_df["fundingRate"].iloc[-1]

    summary = {
        "price": btc_price,
        "oi": oi_current,
        "funding": funding_current,
        "liq_windows": liq_windows,
    }

    print("\n📊 Generando gráficos...", flush=True)

    charts = []

    # 1. Long/Short Ratio
    print("  → Ratio L/S...")
    b64 = chart_ls_ratio(ls_global, ls_top, taker)
    charts.append(("1. Ratio Largo / Corto", b64))

    # 2. Liquidaciones
    print("  → Liquidaciones...")
    b64 = chart_liquidaciones(liq_windows, liq_df)
    charts.append(("2. Liquidaciones (1h / 4h / 12h / 24h)", b64))

    # 3. ETF Flows + Funding
    print("  → Flujos ETF + Funding Rate...")
    b64 = chart_etf_flows(etf_7, etf_30, funding_df)
    charts.append(("3. Flujos ETF BTC + Funding Rate", b64))

    # 4. Volume Heatmap
    print("  → Mapa de calor de volumen...")
    b64 = chart_volume_heatmap(klines_1h)
    charts.append(("4. Mapa de Calor de Volumen", b64))

    # 5. Liquidation Map
    print("  → Mapa de liquidaciones...")
    b64 = chart_liq_map(btc_price, oi_current)
    charts.append(("5. Mapa de Liquidaciones Binance BTC/USDT", b64))

    print("\n📝 Generando reporte HTML...", flush=True)
    html = generate_html_report(charts, summary)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.environ.get("BTC_REPORT_DIR", script_dir)
    out_path = os.path.join(out_dir, f"BTC_Report_{timestamp}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Reporte guardado en: {out_path}")
    return out_path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BTC Market Intelligence Report")
    parser.add_argument("--output-dir", "-o", default=None,
                        help="Directorio de salida (default: directorio del script)")
    args = parser.parse_args()
    if args.output_dir:
        os.environ["BTC_REPORT_DIR"] = args.output_dir
    main()
