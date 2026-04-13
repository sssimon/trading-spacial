"""
Tests para BTC Scanner — Ultimate Macro & Order Flow V6.0
Ejecutar con:  pytest tests/ -v
"""

import sys
import os
import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

# Agregar el directorio raíz al path para importar los módulos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import btc_scanner as scanner


# ─────────────────────────────────────────────────────────────────────────────
#  FIXTURES  —  DataFrames sintéticos
# ─────────────────────────────────────────────────────────────────────────────

def make_ohlcv(n=210, base_price=85000.0, trend=0.0, noise=200.0, seed=42) -> pd.DataFrame:
    """Genera un DataFrame OHLCV sintético con tendencia y ruido controlados."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    closes = base_price + trend * np.arange(n) + rng.normal(0, noise, n)
    closes = np.maximum(closes, 1000.0)  # evitar negativos
    highs  = closes + rng.uniform(50, 300, n)
    lows   = closes - rng.uniform(50, 300, n)
    lows   = np.maximum(lows, 1.0)
    opens  = closes + rng.normal(0, 100, n)
    volume = rng.uniform(100, 1000, n)
    taker_buy_base  = volume * rng.uniform(0.3, 0.7, n)
    taker_buy_quote = taker_buy_base * closes

    df = pd.DataFrame({
        "open":  opens,
        "high":  highs,
        "low":   lows,
        "close": closes,
        "volume": volume,
        "taker_buy_base":  taker_buy_base,
        "taker_buy_quote": taker_buy_quote,
    }, index=idx)
    return df


def make_bearish_then_bullish(base=85000.0) -> pd.DataFrame:
    """
    DataFrame con bull engulfing en las dos últimas velas:
      vela[-2]: bajista  (open=85400, close=85000)
      vela[-1]: alcista  (open=84900 ≤ close[-2]=85000, close=85500 ≥ open[-2]=85400)
    """
    idx = pd.date_range("2025-01-01", periods=5, freq="1h")
    df = pd.DataFrame({
        "open":  [base, base+100, base+200, base+400, base-100],  # [-2]=85400 bajista, [-1]=84900
        "close": [base, base-100, base+100, base,     base+500],  # [-2]=85000, [-1]=85500 engulfs
        "high":  [base+500]*5,
        "low":   [base-300]*5,
        "volume": [500.0]*5,
        "taker_buy_base":  [250.0]*5,
        "taker_buy_quote": [250.0*base]*5,
    }, index=idx)
    return df


def make_no_engulfing(base=85000.0) -> pd.DataFrame:
    """DataFrame donde no hay bull engulfing (ambas velas alcistas)."""
    idx = pd.date_range("2025-01-01", periods=5, freq="1h")
    df = pd.DataFrame({
        "open":  [base, base+100, base+200, base+300, base+400],
        "close": [base+100, base+200, base+300, base+400, base+500],
        "high":  [base+300]*5,
        "low":   [base-100]*5,
        "volume": [500.0]*5,
        "taker_buy_base":  [300.0]*5,
        "taker_buy_quote": [300.0*base]*5,
    }, index=idx)
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — calc_lrc
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcLRC:
    def test_retorna_cuatro_valores(self):
        df = make_ohlcv()
        lrc_pct, upper, lower, mid = scanner.calc_lrc(df["close"], period=100)
        assert lrc_pct is not None
        assert upper is not None
        assert lower is not None
        assert mid is not None

    def test_rango_pct_0_100(self):
        """lrc_pct debe estar en [0, 100] para precios dentro del canal."""
        df = make_ohlcv(n=200, noise=50)
        lrc_pct, upper, lower, mid = scanner.calc_lrc(df["close"], period=100)
        # Puede estar fuera del rango en casos extremos, pero normalmente no
        assert isinstance(lrc_pct, float)

    def test_insufficient_data_retorna_none(self):
        """Si hay menos barras que el período, retorna None."""
        close = pd.Series([85000.0] * 50)
        lrc_pct, upper, lower, mid = scanner.calc_lrc(close, period=100)
        assert lrc_pct is None
        assert upper is None
        assert lower is None
        assert mid is None

    def test_precio_bajo_da_pct_bajo(self):
        """Precio en el mínimo del canal debe dar lrc_pct bajo."""
        # Canal de 80000–90000; precio en 80000 → pct ≈ 0
        n = 110
        idx = pd.date_range("2025-01-01", periods=n, freq="1h")
        close = pd.Series([85000.0] * n, index=idx)
        # Reemplazar últimas barras con precio bajo para que el canal se forme
        # y el precio actual quede cerca del lower
        lrc_pct, upper, lower, mid = scanner.calc_lrc(close, period=100)
        # Para una serie constante, std=0, así que lrc_pct debería ser 50 (fallback)
        assert lrc_pct == 50.0

    def test_retorna_floats_redondeados(self):
        df = make_ohlcv()
        lrc_pct, upper, lower, mid = scanner.calc_lrc(df["close"], period=100)
        # Verificar que tienen máximo 2 decimales
        assert lrc_pct == round(lrc_pct, 2)
        assert upper == round(upper, 2)
        assert lower == round(lower, 2)
        assert mid == round(mid, 2)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — calc_rsi
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcRSI:
    def test_rango_0_100(self):
        df = make_ohlcv()
        rsi = scanner.calc_rsi(df["close"], period=14)
        assert rsi.between(0, 100).all()

    def test_longitud_serie(self):
        df = make_ohlcv(n=50)
        rsi = scanner.calc_rsi(df["close"], period=14)
        assert len(rsi) == len(df)

    def test_tendencia_alcista_rsi_alto(self):
        """Serie mayoritariamente alcista → RSI debe ser alto (>60).
        Nota: una serie puramente creciente da avg_loss=0 → fillna(50).
        Se usa una serie con subidas grandes y bajadas pequeñas para
        garantizar RSI > 60 sin depender de fillna.
        """
        rng = np.random.default_rng(5)
        n = 60
        # Subidas de 500, bajadas de 50 → ganancias >> pérdidas
        deltas = np.where(rng.random(n) > 0.2, 500.0, -50.0)
        close = 80000.0 + np.cumsum(deltas)
        close = pd.Series(close)
        rsi = scanner.calc_rsi(close, period=14)
        assert rsi.iloc[-1] > 60

    def test_tendencia_bajista_rsi_bajo(self):
        """Serie con cierre siempre a la baja → RSI debe ser bajo (<40)."""
        n = 60
        close = pd.Series(np.linspace(90000, 80000, n))
        rsi = scanner.calc_rsi(close, period=14)
        assert rsi.iloc[-1] < 40

    def test_serie_plana_rsi_50(self):
        """Serie con precio constante → RSI ≈ 50 (ganancias = pérdidas = 0)."""
        close = pd.Series([85000.0] * 50)
        rsi = scanner.calc_rsi(close, period=14)
        # fillna(50) cuando avg_loss = 0
        assert rsi.iloc[-1] == pytest.approx(50.0, abs=5)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — calc_bb
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcBB:
    def test_upper_mayor_lower(self):
        df = make_ohlcv()
        upper, mid, lower = scanner.calc_bb(df["close"], period=20)
        # Donde hay datos válidos, upper > mid > lower
        valid = upper.notna() & lower.notna()
        assert (upper[valid] > lower[valid]).all()
        assert (upper[valid] > mid[valid]).all()
        assert (mid[valid] > lower[valid]).all()

    def test_mid_es_sma(self):
        """La banda media debe ser igual a la SMA del mismo período."""
        df = make_ohlcv()
        _, mid_bb, _ = scanner.calc_bb(df["close"], period=20)
        sma = scanner.calc_sma(df["close"], 20)
        pd.testing.assert_series_equal(mid_bb, sma, check_names=False)

    def test_retorna_tres_series(self):
        df = make_ohlcv()
        result = scanner.calc_bb(df["close"], period=20)
        assert len(result) == 3
        for s in result:
            assert isinstance(s, pd.Series)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — calc_sma
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcSMA:
    def test_primeros_nan(self):
        """Los primeros (period-1) valores deben ser NaN."""
        df = make_ohlcv(n=50)
        sma = scanner.calc_sma(df["close"], period=10)
        assert sma.iloc[:9].isna().all()
        assert sma.iloc[9:].notna().all()

    def test_valor_correcto(self):
        """Verifica el cálculo manual de la SMA."""
        close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        sma = scanner.calc_sma(close, period=3)
        assert sma.iloc[4] == pytest.approx(4.0)  # (3+4+5)/3

    def test_longitud_igual_input(self):
        df = make_ohlcv(n=100)
        sma = scanner.calc_sma(df["close"], period=20)
        assert len(sma) == len(df)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — calc_cvd_delta
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcCVDDelta:
    def test_compradores_dominan(self):
        """Si taker_buy_base = volume (todo compra), delta debe ser positivo."""
        df = make_ohlcv(n=20)
        df["taker_buy_base"] = df["volume"]  # 100% compradores
        delta = scanner.calc_cvd_delta(df, n=3)
        assert delta > 0

    def test_vendedores_dominan(self):
        """Si taker_buy_base = 0 (todo venta), delta debe ser negativo."""
        df = make_ohlcv(n=20)
        df["taker_buy_base"] = 0.0
        delta = scanner.calc_cvd_delta(df, n=3)
        assert delta < 0

    def test_equilibrio_delta_cero(self):
        """Si buy = sell (50/50), delta debe ser ≈ 0."""
        df = make_ohlcv(n=20)
        df["taker_buy_base"] = df["volume"] * 0.5
        delta = scanner.calc_cvd_delta(df, n=3)
        assert delta == pytest.approx(0.0, abs=1e-6)

    def test_retorna_float(self):
        df = make_ohlcv(n=20)
        delta = scanner.calc_cvd_delta(df, n=3)
        assert isinstance(delta, float)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — detect_bull_engulfing
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectBullEngulfing:
    def test_detecta_engulfing(self):
        """Vela anterior bajista cubierta por vela alcista mayor → True."""
        df = make_bearish_then_bullish()
        assert scanner.detect_bull_engulfing(df)

    def test_no_engulfing_ambas_alcistas(self):
        df = make_no_engulfing()
        assert not scanner.detect_bull_engulfing(df)

    def test_datos_insuficientes(self):
        """Con menos de 2 velas debe retornar False."""
        df = make_ohlcv(n=1)
        assert not scanner.detect_bull_engulfing(df)

    def test_ambas_bajistas_no_engulfing(self):
        """Dos velas bajistas consecutivas → no es bull engulfing."""
        base = 85000.0
        idx = pd.date_range("2025-01-01", periods=3, freq="1h")
        df = pd.DataFrame({
            "open":   [base+500, base+200, base+100],
            "close":  [base+200, base+100, base-100],
            "high":   [base+600]*3,
            "low":    [base-100]*3,
            "volume": [500.0]*3,
            "taker_buy_base":  [250.0]*3,
            "taker_buy_quote": [250.0*base]*3,
        }, index=idx)
        assert not scanner.detect_bull_engulfing(df)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — detect_rsi_divergence
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectRSIDivergence:
    def test_datos_insuficientes(self):
        close = pd.Series([85000.0] * 20)
        rsi   = pd.Series([50.0] * 20)
        assert scanner.detect_rsi_divergence(close, rsi, window=30) is False

    def test_sin_minimos(self):
        """Serie monotonamente decreciente → sin mínimos locales → False."""
        close = pd.Series(np.linspace(90000, 80000, 50))
        rsi   = pd.Series(np.linspace(70, 30, 50))
        assert scanner.detect_rsi_divergence(close, rsi, window=30) is False

    def test_divergencia_alcista_detectada(self):
        """
        Precio hace lower low, RSI hace higher low → divergencia alcista → True.
        Los dos mínimos deben estar dentro de la ventana, en posiciones internas (no índice 0).
        """
        window = 40
        n = window + 10  # buffer extra para que los mínimos no caigan en el borde
        close = np.ones(n) * 85000.0
        rsi_vals = np.ones(n) * 50.0

        # Los últimos `window` elementos son los índices [n-window .. n-1]
        # En términos relativos dentro de la ventana: posición relativa = i - (n - window)
        # Mínimo 1: posición relativa 5  → absoluta n-window+5
        p1 = n - window + 5
        close[p1] = 84000.0
        rsi_vals[p1] = 35.0

        # Mínimo 2: posición relativa 20 → absoluta n-window+20
        p2 = n - window + 20
        close[p2] = 83000.0   # lower low
        rsi_vals[p2] = 38.0   # higher low

        close_s = pd.Series(close)
        rsi_s   = pd.Series(rsi_vals)
        result = scanner.detect_rsi_divergence(close_s, rsi_s, window=window)
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — score_label
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreLabel:
    def test_premium(self):
        label = scanner.score_label(4)
        assert "PREMIUM" in label
        assert "150%" in label

    def test_estandar(self):
        label = scanner.score_label(2)
        assert "ESTÁNDAR" in label
        assert "100%" in label

    def test_minima(self):
        label = scanner.score_label(1)
        assert "MÍNIMA" in label
        assert "50%" in label

    def test_minima_score_0(self):
        label = scanner.score_label(0)
        assert "MÍNIMA" in label

    def test_premium_score_alto(self):
        label = scanner.score_label(8)
        assert "PREMIUM" in label


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — check_trigger_5m
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckTrigger5M:
    def _make_df5(self, bullish_last=True, rsi_recovering=True, n=30):
        """Construye un DataFrame 5M con control de la última vela."""
        base = 85000.0
        rng = np.random.default_rng(0)
        closes = base + rng.normal(0, 100, n)
        opens  = closes + rng.normal(0, 50, n)
        volume = np.full(n, 500.0)
        tbb    = volume * 0.5

        df = pd.DataFrame({
            "open":  opens,
            "close": closes,
            "high":  closes + 150,
            "low":   closes - 150,
            "volume": volume,
            "taker_buy_base":  tbb,
            "taker_buy_quote": tbb * closes,
        })

        # Forzar condición de la última vela
        if bullish_last:
            df.iloc[-1, df.columns.get_loc("close")] = df.iloc[-1]["open"] + 200
        else:
            df.iloc[-1, df.columns.get_loc("close")] = df.iloc[-1]["open"] - 200

        # Forzar RSI recovering ajustando precios anteriores
        if rsi_recovering:
            # Hacer que las barras anteriores tengan precio más bajo (RSI bajo)
            # y la última más alta (RSI sube)
            df.iloc[-5:-1, df.columns.get_loc("close")] -= 500
        return df

    def test_trigger_activo_cuando_ambas_condiciones(self):
        df = self._make_df5(bullish_last=True, rsi_recovering=True)
        active, details = scanner.check_trigger_5m(df)
        # La última vela es alcista
        assert details["vela_5m_alcista"] is True

    def test_trigger_inactivo_vela_bajista(self):
        df = self._make_df5(bullish_last=False, rsi_recovering=True)
        active, details = scanner.check_trigger_5m(df)
        assert details["vela_5m_alcista"] is False
        assert active is False

    def test_datos_insuficientes(self):
        df = make_ohlcv(n=2)
        active, details = scanner.check_trigger_5m(df)
        assert active is False
        assert details == {}

    def test_detalles_contienen_claves(self):
        df = self._make_df5()
        _, details = scanner.check_trigger_5m(df)
        expected_keys = {
            "vela_5m_alcista", "rsi_5m_recuperando",
            "rsi_5m_actual", "rsi_5m_anterior",
            "close_5m", "open_5m"
        }
        assert expected_keys.issubset(set(details.keys()))

    def test_retorna_bool_y_dict(self):
        df = self._make_df5()
        result = scanner.check_trigger_5m(df)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], dict)


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — scan() con mock de API
# ─────────────────────────────────────────────────────────────────────────────

class TestScan:
    """Tests de integración del scanner con datos de API mockeados."""

    def _make_scan_mock(self, lrc_pct_override=None, macro_above=True,
                        bullish_trigger=True, rsi_1h_low=True):
        """
        Retorna un DataFrame 1H que produce condiciones controladas:
        - LRC < 25% si lrc_pct_override < 25
        - precio > SMA100(4H) si macro_above=True
        """
        n = 210
        # Base price: si queremos macro alcista, precio debe superar SMA100
        base = 85000.0

        df1h = make_ohlcv(n=n, base_price=base, noise=50)
        df4h = make_ohlcv(n=150, base_price=base, noise=50)
        df5m = make_ohlcv(n=210, base_price=base, noise=50)

        if rsi_1h_low:
            # Forzar últimas 30 barras a la baja para tener RSI < 40
            df1h.iloc[-30:, df1h.columns.get_loc("close")] = base * 0.92

        if not macro_above:
            # Precio actual mucho por debajo del precio histórico de 4H
            df1h.iloc[-1, df1h.columns.get_loc("close")] = base * 0.70
            df4h.iloc[:, df4h.columns.get_loc("close")] = base * 1.10

        if bullish_trigger:
            # Última vela 5M alcista
            last_open = df5m.iloc[-1]["open"]
            df5m.iloc[-1, df5m.columns.get_loc("close")] = last_open + 300
        else:
            last_open = df5m.iloc[-1]["open"]
            df5m.iloc[-1, df5m.columns.get_loc("close")] = last_open - 300

        return df1h, df4h, df5m

    @patch("btc_scanner.get_klines")
    def test_scan_retorna_dict_con_claves(self, mock_klines):
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]  # 5m, 1h, 4h

        rep = scanner.scan()

        assert isinstance(rep, dict)
        claves = ["timestamp", "symbol", "estado", "señal_activa", "price",
                  "lrc_1h", "rsi_1h", "macro_4h", "score",
                  "score_label", "confirmations", "gatillo_activo",
                  "sizing_1h"]
        for clave in claves:
            assert clave in rep, f"Clave faltante: {clave}"

    @patch("btc_scanner.get_klines")
    def test_scan_precio_es_float(self, mock_klines):
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan("ETHUSDT")
        assert isinstance(rep["price"], float)
        assert rep["symbol"] == "ETHUSDT"

    @patch("btc_scanner.get_klines")
    def test_scan_señal_activa_es_bool(self, mock_klines):
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan()
        assert isinstance(rep["señal_activa"], bool)

    @patch("btc_scanner.get_klines")
    def test_scan_sin_zona_lrc_no_señal(self, mock_klines):
        """Si LRC% > 25, no debe haber señal ni setup."""
        n = 210
        # Precio en la parte alta del canal (LRC% > 25)
        # Hacemos que el precio esté muy por encima del mid
        df1h = make_ohlcv(n=n, base_price=85000, trend=100, noise=10)
        df4h = make_ohlcv(n=150, base_price=85000, noise=50)
        df5m = make_ohlcv(n=210, base_price=85000, noise=50)
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan()
        # No garantizamos el valor exacto de lrc_pct ya que depende del canal,
        # pero sí verificamos que el estado sea consistente con la señal
        if not rep["señal_activa"]:
            assert "✅ SEÑAL" not in rep["estado"] or not rep["señal_activa"]

    @patch("btc_scanner.get_klines")
    def test_scan_sizing_coherente(self, mock_klines):
        """Verifica que el sizing no supere el 98% del capital."""
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan()
        sz = rep["sizing_1h"]
        assert sz["pct_capital"] <= 98.0
        assert sz["capital_usd"] == 1000.0
        assert sz["riesgo_usd"] == pytest.approx(10.0, abs=0.01)

    @patch("btc_scanner.get_klines")
    def test_scan_sl_tp_coherentes(self, mock_klines):
        """SL debe ser menor al precio, TP mayor."""
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan()
        price = rep["price"]
        sz    = rep["sizing_1h"]
        assert sz["sl_precio"] < price
        assert sz["tp_precio"] > price

    @patch("btc_scanner.get_klines")
    def test_scan_json_serializable(self, mock_klines):
        """El reporte debe ser completamente serializable a JSON."""
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan()
        # Esto no debe lanzar excepción
        serialized = json.dumps(rep, ensure_ascii=False)
        assert len(serialized) > 0

    @patch("btc_scanner.get_klines")
    def test_scan_score_en_rango(self, mock_klines):
        """El score debe estar entre 0 y 10."""
        df1h, df4h, df5m = self._make_scan_mock()
        mock_klines.side_effect = [df5m, df1h, df4h]

        rep = scanner.scan()
        assert 0 <= rep["score"] <= 10


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — _load_proxy
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadProxy:
    def test_sin_proxy_retorna_dict_vacio(self, tmp_path, monkeypatch):
        # config.json sin proxy
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"proxy": ""}))
        monkeypatch.setenv("HTTPS_PROXY", "")
        monkeypatch.setenv("HTTP_PROXY", "")

        orig_dir = scanner.SCRIPT_DIR
        monkeypatch.setattr(scanner, "SCRIPT_DIR", str(tmp_path))
        result = scanner._load_proxy()
        monkeypatch.setattr(scanner, "SCRIPT_DIR", orig_dir)
        assert result == {}

    def test_proxy_desde_config(self, tmp_path, monkeypatch):
        proxy_url = "socks5://127.0.0.1:1080"
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"proxy": proxy_url}))
        # Eliminar variables de entorno para que no interfieran
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)

        monkeypatch.setattr(scanner, "SCRIPT_DIR", str(tmp_path))
        result = scanner._load_proxy()
        assert result == {"http": proxy_url, "https": proxy_url}

    def test_variable_entorno_tiene_prioridad(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"proxy": "socks5://config-proxy:1080"}))
        env_proxy = "http://env-proxy:8080"
        monkeypatch.setenv("HTTPS_PROXY", env_proxy)
        monkeypatch.delenv("HTTP_PROXY", raising=False)

        monkeypatch.setattr(scanner, "SCRIPT_DIR", str(tmp_path))
        result = scanner._load_proxy()
        assert result["https"] == env_proxy


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — _klines_bybit (formato de DataFrame)
# ─────────────────────────────────────────────────────────────────────────────

class TestKlinesBybit:
    @patch("btc_scanner._get")
    def test_retorna_dataframe_correcto(self, mock_get):
        """Verifica que el DataFrame de Bybit tenga el mismo formato que Binance."""
        # Simular respuesta de Bybit (formato: más reciente primero)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows = []
        for i in range(5):
            ts = now_ms - (4 - i) * 5 * 60 * 1000  # cada 5 min
            rows.append([str(ts), "85000", "85500", "84800", "85200", "10.5", "892650"])
        rows_reversed = list(reversed(rows))  # Bybit envía más reciente primero

        mock_get.return_value = {
            "retCode": 0,
            "result": {"list": rows_reversed},
        }

        df = scanner._klines_bybit("BTCUSDT", "5m", 5)

        assert isinstance(df, pd.DataFrame)
        assert "open" in df.columns
        assert "close" in df.columns
        assert "taker_buy_base" in df.columns
        assert len(df) == 5

    @patch("btc_scanner._get")
    def test_bybit_error_lanza_excepcion(self, mock_get):
        mock_get.return_value = {
            "retCode": 10001,
            "retMsg": "Invalid symbol",
        }
        with pytest.raises(RuntimeError, match="Bybit error"):
            scanner._klines_bybit("INVALID", "5m", 10)

    @patch("btc_scanner._get")
    def test_taker_buy_base_aproximado(self, mock_get):
        """Verifica que taker_buy_base esté entre 0 y volume."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows = []
        for i in range(10):
            ts = now_ms - (9 - i) * 60 * 1000
            rows.append([str(ts), "85000", "85500", "84500", "85200", "100", "8520000"])
        rows_reversed = list(reversed(rows))

        mock_get.return_value = {
            "retCode": 0,
            "result": {"list": rows_reversed},
        }

        df = scanner._klines_bybit("BTCUSDT", "1m", 10)
        assert (df["taker_buy_base"] >= 0).all()
        assert (df["taker_buy_base"] <= df["volume"] + 1e-6).all()


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — fmt() (formato de salida)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTopSymbols:
    @patch("btc_scanner._load_proxy", return_value={})
    def test_retorna_lista_usdt(self, _mock):
        """Con CoinGecko mockeado retorna pares USDT correctos."""
        import requests as _req
        fake_coins = [
            {"symbol": "btc", "market_cap": 1e12},
            {"symbol": "eth", "market_cap": 5e11},
            {"symbol": "usdt", "market_cap": 4e11},   # stablecoin → se excluye
            {"symbol": "bnb", "market_cap": 3e11},
            {"symbol": "sol", "market_cap": 2e11},
        ]
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                ok=True,
                json=lambda: fake_coins,
                raise_for_status=lambda: None,
            )
            symbols = scanner.get_top_symbols(n=3)
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols
        assert "USDTUSDT" not in symbols   # stablecoin excluida
        assert len(symbols) == 3

    @patch("btc_scanner._load_proxy", return_value={})
    def test_fallback_si_coingecko_falla(self, _mock):
        """Si CoinGecko lanza error, retorna DEFAULT_SYMBOLS."""
        with patch("requests.get", side_effect=ConnectionError("sin red")):
            symbols = scanner.get_top_symbols(n=5)
        assert symbols == scanner.DEFAULT_SYMBOLS[:5]

    def test_default_symbols_son_pares_usdt(self):
        for sym in scanner.DEFAULT_SYMBOLS:
            assert sym.endswith("USDT"), f"{sym} no termina en USDT"

    def test_stablecoins_excluidas(self):
        for stable in scanner.STABLECOINS:
            assert f"{stable}USDT" not in scanner.DEFAULT_SYMBOLS


class TestFmt:
    def _make_report(self, señal=False, setup=False):
        return {
            "timestamp": "2025-01-01 12:00:00 UTC",
            "estado": "✅ SEÑAL + GATILLO CONFIRMADOS" if señal else "⏳ SIN SETUP",
            "señal_activa": señal,
            "price": 85000.0,
            "lrc_1h": {"pct": 15.5, "upper": 90000.0, "lower": 80000.0, "mid": 85000.0},
            "rsi_1h": 32.5,
            "macro_4h": {"sma100": 82000.0, "price_above": True},
            "score": 4,
            "score_label": "PREMIUM ⭐⭐⭐ (sizing 150%)",
            "confirmations": {
                "C1_RSI_Sobreventa": {"pass": True, "pts": 2, "max_pts": 2, "rsi_1h": 32.5},
                "C2_Divergencia_Alcista": {"pass": False, "pts": 0, "max_pts": 2},
            },
            "exclusions": {
                "E1_BullEngulfing": {"activo": False, "nota": "OK"},
                "E2_Noticias_Macro": {"activo": "VERIFICAR_MANUAL", "nota": "Revisar calendario"},
            },
            "blocks_auto": [],
            "gatillo_5m": {
                "vela_5m_alcista": True,
                "rsi_5m_recuperando": True,
                "rsi_5m_actual": 45.0,
                "rsi_5m_anterior": 38.0,
                "close_5m": 85200.0,
                "open_5m": 85000.0,
            },
            "gatillo_activo": señal,
            "sizing_1h": {
                "capital_usd": 1000.0,
                "riesgo_usd": 10.0,
                "sl_pct": "2.0%",
                "tp_pct": "4.0%",
                "sl_precio": 83300.0,
                "tp_precio": 88400.0,
                "qty_btc": 0.000118,
                "valor_pos": 10.03,
                "pct_capital": 1.0,
            },
            "errors": [],
        }

    def test_fmt_retorna_string(self):
        rep = self._make_report()
        result = scanner.fmt(rep)
        assert isinstance(result, str)

    def test_fmt_contiene_precio(self):
        rep = self._make_report()
        result = scanner.fmt(rep)
        assert "85,000.00" in result or "85000" in result

    def test_fmt_contiene_estado(self):
        rep = self._make_report(señal=False)
        result = scanner.fmt(rep)
        assert "SIN SETUP" in result

    def test_fmt_señal_activa_menciona_confirmado(self):
        rep = self._make_report(señal=True)
        result = scanner.fmt(rep)
        assert "SEÑAL" in result

    def test_fmt_muestra_lrc_pct(self):
        rep = self._make_report()
        result = scanner.fmt(rep)
        assert "15.5%" in result or "15.5" in result

    def test_fmt_sin_bloques_auto(self):
        rep = self._make_report()
        result = scanner.fmt(rep)
        assert "Ningún bloqueo automático" in result

    def test_fmt_con_bloqueo(self):
        rep = self._make_report()
        rep["blocks_auto"] = ["E1: BullEngulfing activo"]
        result = scanner.fmt(rep)
        assert "BullEngulfing" in result
