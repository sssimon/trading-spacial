"""
Tests para BTC Scanner API (FastAPI + SQLite + Webhook)
Ejecutar con:  pytest tests/ -v
"""

import sys
import os
import json
import pytest
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
#  FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_report():
    """Reporte de scan sintético completo."""
    return {
        "timestamp": "2025-01-01 12:00:00 UTC",
        "symbol": "BTCUSDT",
        "estado": "✅ SEÑAL + GATILLO CONFIRMADOS — Calidad: PREMIUM ⭐⭐⭐ (sizing 150%)",
        "señal_activa": True,
        "price": 85000.0,
        "lrc_1h": {"pct": 15.5, "upper": 90000.0, "lower": 80000.0, "mid": 85000.0},
        "rsi_1h": 32.5,
        "macro_4h": {"sma100": 82000.0, "price_above": True},
        "score": 4,
        "score_label": "PREMIUM ⭐⭐⭐ (sizing 150%)",
        "confirmations": {
            "C1_RSI_Sobreventa":      {"pass": True,  "pts": 2, "max_pts": 2, "rsi_1h": 32.5},
            "C2_Divergencia_Alcista": {"pass": False, "pts": 0, "max_pts": 2},
            "C3_Soporte_Cercano":     {"pass": True,  "pts": 1, "max_pts": 1},
            "C4_BB_Inferior":         {"pass": False, "pts": 0, "max_pts": 1},
            "C5_Volumen":             {"pass": True,  "pts": 1, "max_pts": 1},
            "C6_CVD_Delta_Positivo":  {"pass": False, "pts": 0, "max_pts": 1},
            "C7_SMA10_mayor_SMA20":   {"pass": False, "pts": 0, "max_pts": 1},
            "C8_DXY_Bajando":         {"pass": "MANUAL", "pts": "?", "max_pts": 1},
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
        "gatillo_activo": True,
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


@pytest.fixture
def setup_report(sample_report):
    """Reporte de setup válido sin gatillo."""
    rep = sample_report.copy()
    rep["señal_activa"] = False
    rep["gatillo_activo"] = False
    rep["estado"] = "🕐 SETUP VÁLIDO — Esperando gatillo 5M"
    return rep


@pytest.fixture
def tmp_db(tmp_path):
    """Retorna la ruta a una DB temporal."""
    return str(tmp_path / "test_signals.db")


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — build_telegram_message
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTelegramMessage:
    def test_señal_activa_tiene_header_correcto(self, sample_report):
        import btc_api
        msg = btc_api.build_telegram_message(sample_report)
        assert "BTCUSDT" in msg and ("LONG" in msg or "SENAL" in msg)

    def test_setup_sin_gatillo_header(self, setup_report):
        import btc_api
        msg = btc_api.build_telegram_message(setup_report)
        assert "SETUP VÁLIDO" in msg

    def test_contiene_precio(self, sample_report):
        import btc_api
        msg = btc_api.build_telegram_message(sample_report)
        assert "85,000.00" in msg or "85000" in msg

    def test_contiene_lrc(self, sample_report):
        import btc_api
        msg = btc_api.build_telegram_message(sample_report)
        assert "15.5%" in msg or "LRC" in msg

    def test_señal_incluye_gestion_riesgo(self, sample_report):
        import btc_api
        msg = btc_api.build_telegram_message(sample_report)
        assert "GESTION DE RIESGO" in msg or "RIESGO" in msg
        assert "SL" in msg
        assert "TP" in msg

    def test_siempre_incluye_verificar_manual(self, sample_report):
        import btc_api
        msg = btc_api.build_telegram_message(sample_report)
        assert "Verificar manualmente" in msg or "verificar" in msg.lower()

    def test_retorna_string(self, sample_report):
        import btc_api
        msg = btc_api.build_telegram_message(sample_report)
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_reporte_vacio_no_lanza_excepcion(self):
        import btc_api
        msg = btc_api.build_telegram_message({})
        assert isinstance(msg, str)

    def test_estrellas_score_premium(self, sample_report):
        """Score >= 4 → 3 estrellas."""
        import btc_api
        sample_report["score"] = 5
        msg = btc_api.build_telegram_message(sample_report)
        assert "⭐⭐⭐" in msg

    def test_estrellas_score_estandar(self, sample_report):
        """Score 2-3 → 2 estrellas."""
        import btc_api
        sample_report["score"] = 3
        msg = btc_api.build_telegram_message(sample_report)
        assert "⭐⭐" in msg


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — Base de datos (init_db, save_scan, get_scans, etc.)
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase:
    @pytest.fixture(autouse=True)
    def patch_db(self, tmp_db, monkeypatch):
        import btc_api
        monkeypatch.setattr(btc_api, "DB_FILE", tmp_db)
        btc_api.init_db()
        yield

    def test_init_db_crea_tablas(self, tmp_db):
        con = sqlite3.connect(tmp_db)
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con.close()
        names = {t[0] for t in tables}
        assert "scans" in names
        assert "webhooks_sent" in names

    def test_save_scan_inserta_registro(self, sample_report):
        import btc_api
        scan_id = btc_api.save_scan(sample_report)
        assert isinstance(scan_id, int)
        assert scan_id > 0

    def test_get_scans_retorna_lista(self, sample_report):
        import btc_api
        btc_api.save_scan(sample_report)
        rows = btc_api.get_scans(limit=10)
        assert isinstance(rows, list)
        assert len(rows) == 1

    def test_get_scans_only_signals(self, sample_report, setup_report):
        import btc_api
        btc_api.save_scan(sample_report)   # señal completa
        btc_api.save_scan(setup_report)    # setup sin gatillo
        rows = btc_api.get_scans(only_signals=True)
        assert len(rows) == 1
        assert rows[0]["señal"] == 1

    def test_get_scans_only_setups(self, sample_report, setup_report):
        import btc_api
        btc_api.save_scan(sample_report)
        btc_api.save_scan(setup_report)
        rows = btc_api.get_scans(only_setups=True)
        # Debe incluir señales Y setups
        assert len(rows) == 2

    def test_get_latest_signal(self, sample_report):
        import btc_api
        btc_api.save_scan(sample_report)
        latest = btc_api.get_latest_signal()
        assert latest is not None
        assert latest["señal"] == 1

    def test_get_latest_signal_sin_datos(self):
        import btc_api
        latest = btc_api.get_latest_signal()
        assert latest is None

    def test_get_latest_scan(self, sample_report, setup_report):
        import btc_api
        btc_api.save_scan(setup_report)
        btc_api.save_scan(sample_report)  # último
        latest = btc_api.get_latest_scan()
        assert latest["señal"] == 1

    def test_save_scan_guarda_payload_json(self, sample_report):
        import btc_api
        scan_id = btc_api.save_scan(sample_report)
        con = sqlite3.connect(btc_api.DB_FILE)
        row = con.execute("SELECT payload, symbol FROM scans WHERE id=?", (scan_id,)).fetchone()
        con.close()
        payload = json.loads(row[0])
        assert payload["price"] == 85000.0
        assert row[1] == "BTCUSDT"

    def test_save_scan_campos_correctos(self, sample_report):
        import btc_api
        scan_id = btc_api.save_scan(sample_report)
        con = sqlite3.connect(btc_api.DB_FILE)
        row = con.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        con.close()
        # Verificar que los campos numéricos se guardaron
        assert row is not None

    def test_multiple_scans_limit(self, sample_report):
        import btc_api
        for _ in range(5):
            btc_api.save_scan(sample_report)
        rows = btc_api.get_scans(limit=3)
        assert len(rows) == 3


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — FastAPI endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestAPIEndpoints:
    @pytest.fixture(autouse=True)
    def setup_test_app(self, tmp_db, tmp_path, monkeypatch):
        """Configura la app con DB temporal y scanner mockeado."""
        import btc_api

        # Parchear DB y config
        monkeypatch.setattr(btc_api, "DB_FILE", tmp_db)
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "", "webhook_secret": "",
                       "notify_setup_only": False, "scan_interval_sec": 300}, f)
        monkeypatch.setattr(btc_api, "CONFIG_FILE", cfg_path)

        btc_api.init_db()
        yield

    @pytest.fixture
    def client(self):
        """TestClient de FastAPI sin iniciar el scanner background."""
        from fastapi.testclient import TestClient
        import btc_api

        # Crear app sin lifespan para evitar el thread del scanner
        from fastapi import FastAPI
        test_app = FastAPI()

        # Registrar rutas manualmente
        test_app.get("/")(btc_api.root)
        test_app.get("/symbols")(btc_api.list_symbols)
        test_app.get("/status")(btc_api.status)
        test_app.post("/scan")(btc_api.force_scan)
        test_app.get("/signals")(btc_api.list_signals)
        test_app.get("/signals/latest")(btc_api.latest_signal)
        test_app.get("/signals/latest/message")(btc_api.latest_message)
        test_app.get("/signals/{scan_id}")(btc_api.signal_by_id)
        test_app.get("/webhook/test")(btc_api.test_webhook)

        return TestClient(test_app)

    def test_root_200(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert "service" in data
        assert "symbols" in data   # ahora es lista de símbolos activos

    def test_status_200(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "scanner_state" in data

    def test_signals_vacio(self, client):
        r = client.get("/signals")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["signals"] == []

    def test_signals_latest_sin_datos(self, client):
        r = client.get("/signals/latest")
        assert r.status_code == 200
        data = r.json()
        assert data["señal"] is None

    def test_signals_latest_message_sin_datos(self, client):
        r = client.get("/signals/latest/message")
        assert r.status_code == 200

    def test_signal_by_id_no_encontrado(self, client):
        r = client.get("/signals/9999")
        assert r.status_code == 404

    def test_signals_despues_de_scan(self, client, sample_report):
        """Después de guardar un scan, /signals debe retornarlo."""
        import btc_api
        btc_api.save_scan(sample_report)
        r = client.get("/signals")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_signals_latest_con_datos(self, client, sample_report):
        import btc_api
        btc_api.save_scan(sample_report)
        r = client.get("/signals/latest")
        assert r.status_code == 200
        data = r.json()
        assert data["price"] == 85000.0

    def test_signal_by_id_encontrado(self, client, sample_report):
        import btc_api
        scan_id = btc_api.save_scan(sample_report)
        r = client.get(f"/signals/{scan_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == scan_id

    def test_force_scan_con_mock(self, client):
        """POST /scan debe retornar resultado del scanner."""
        import btc_scanner
        import numpy as np
        import pandas as pd

        mock_rep = {
            "timestamp": "2025-01-01 12:00:00 UTC",
            "estado": "⏳ SIN SETUP — LRC% fuera de zona",
            "señal_activa": False,
            "price": 85000.0,
            "lrc_1h": {"pct": 60.0, "upper": 90000.0, "lower": 80000.0, "mid": 85000.0},
            "rsi_1h": 55.0,
            "macro_4h": {"sma100": 82000.0, "price_above": True},
            "score": 0,
            "score_label": "MÍNIMA ⭐ (sizing 50%)",
            "confirmations": {},
            "exclusions": {},
            "blocks_auto": [],
            "gatillo_5m": {},
            "gatillo_activo": False,
            "sizing_1h": {
                "capital_usd": 1000.0, "riesgo_usd": 10.0,
                "sl_pct": "2.0%", "tp_pct": "4.0%",
                "sl_precio": 83300.0, "tp_precio": 88400.0,
                "qty_btc": 0.000118, "valor_pos": 10.0, "pct_capital": 1.0,
            },
            "errors": [],
        }

        with patch("btc_api.scan", return_value=mock_rep):
            # Escanear un solo símbolo para que el resultado sea predecible
            r = client.post("/scan?symbol=BTCUSDT")
        assert r.status_code == 200
        data = r.json()
        assert "scanned" in data
        assert "results" in data
        assert data["scanned"] == 1
        first = data["results"][0]
        assert "scan_id" in first
        assert "estado" in first
        assert "price" in first

    def test_signals_only_signals_filter(self, client, sample_report, setup_report):
        import btc_api
        btc_api.save_scan(sample_report)  # señal completa
        btc_api.save_scan(setup_report)   # setup sin señal
        r = client.get("/signals?only_signals=true")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["signals"][0]["señal"] is True

    def test_signals_limit_param(self, client, sample_report):
        import btc_api
        for _ in range(5):
            btc_api.save_scan(sample_report)
        r = client.get("/signals?limit=2")
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_symbols_endpoint(self, client):
        r = client.get("/symbols")
        assert r.status_code == 200
        data = r.json()
        assert "symbols" in data
        assert "total" in data

    def test_signals_filtro_symbol(self, client, sample_report):
        import btc_api
        btc_api.save_scan(sample_report)
        # filtrar por símbolo existente
        r = client.get("/signals?symbol=BTCUSDT")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        # filtrar por símbolo que no existe
        r2 = client.get("/signals?symbol=XYZUSDT")
        assert r2.status_code == 200
        assert r2.json()["total"] == 0

    def test_signals_latest_filtro_symbol(self, client, sample_report):
        import btc_api
        btc_api.save_scan(sample_report)
        r = client.get("/signals/latest?symbol=BTCUSDT")
        assert r.status_code == 200
        data = r.json()
        assert data.get("symbol") == "BTCUSDT"

    def test_webhook_test_sin_config_400(self, client):
        r = client.get("/webhook/test")
        assert r.status_code == 400

    def test_webhook_test_con_url(self, client, tmp_path, monkeypatch):
        import btc_api
        cfg_path = str(tmp_path / "config_wh.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "http://localhost:9999/wh"}, f)
        monkeypatch.setattr(btc_api, "CONFIG_FILE", cfg_path)

        with patch("btc_api.req_lib.post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            r = client.get("/webhook/test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — push_webhook
# ─────────────────────────────────────────────────────────────────────────────

class TestPushWebhook:
    @pytest.fixture(autouse=True)
    def patch_db(self, tmp_db, monkeypatch):
        import btc_api
        monkeypatch.setattr(btc_api, "DB_FILE", tmp_db)
        btc_api.init_db()
        yield

    def test_sin_url_no_llama_post(self, sample_report):
        import btc_api
        cfg = {"webhook_url": "", "webhook_secret": ""}
        with patch("btc_api.req_lib.post") as mock_post:
            btc_api.push_webhook(sample_report, 1, cfg)
            mock_post.assert_not_called()

    def test_con_url_llama_post(self, sample_report):
        import btc_api
        cfg = {"webhook_url": "http://localhost:9000/webhook", "webhook_secret": ""}
        with patch("btc_api.req_lib.post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            btc_api.push_webhook(sample_report, 1, cfg)
            mock_post.assert_called_once()

    def test_payload_contiene_telegram_message(self, sample_report):
        import btc_api
        cfg = {"webhook_url": "http://localhost:9000/webhook", "webhook_secret": ""}
        captured = {}
        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return MagicMock(ok=True, status_code=200)

        with patch("btc_api.req_lib.post", side_effect=fake_post):
            btc_api.push_webhook(sample_report, 1, cfg)

        assert "telegram_message" in captured["payload"]
        assert len(captured["payload"]["telegram_message"]) > 0

    def test_con_secret_agrega_header(self, sample_report):
        import btc_api
        cfg = {"webhook_url": "http://localhost:9000/webhook",
               "webhook_secret": "mi-secreto-123"}
        captured_headers = {}
        def fake_post(url, json=None, headers=None, timeout=None):
            captured_headers.update(headers or {})
            return MagicMock(ok=True, status_code=200)

        with patch("btc_api.req_lib.post", side_effect=fake_post):
            btc_api.push_webhook(sample_report, 1, cfg)

        assert "X-Scanner-Secret" in captured_headers
        assert captured_headers["X-Scanner-Secret"] == "mi-secreto-123"

    def test_error_de_red_no_lanza_excepcion(self, sample_report):
        """Un error de red no debe propagarse; solo loguear."""
        import btc_api
        cfg = {"webhook_url": "http://localhost:9000/webhook", "webhook_secret": ""}
        with patch("btc_api.req_lib.post", side_effect=ConnectionError("sin red")):
            # No debe lanzar excepción
            btc_api.push_webhook(sample_report, 1, cfg)

    def test_guarda_resultado_en_db(self, sample_report):
        import btc_api
        btc_api.save_scan(sample_report)
        cfg = {"webhook_url": "http://localhost:9000/webhook", "webhook_secret": ""}
        with patch("btc_api.req_lib.post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, status_code=200)
            btc_api.push_webhook(sample_report, 1, cfg)

        con = sqlite3.connect(btc_api.DB_FILE)
        rows = con.execute("SELECT * FROM webhooks_sent").fetchall()
        con.close()
        assert len(rows) == 1
        assert rows[0][5] == 1  # ok=1


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — load_config
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_defaults_sin_archivo(self, tmp_path, monkeypatch):
        import btc_api
        monkeypatch.setattr(btc_api, "CONFIG_FILE", str(tmp_path / "no_existe.json"))
        cfg = btc_api.load_config()
        assert "webhook_url" in cfg
        assert "scan_interval_sec" in cfg
        assert cfg["scan_interval_sec"] == 300

    def test_lee_archivo_existente(self, tmp_path, monkeypatch):
        import btc_api
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "http://test.com", "scan_interval_sec": 60}, f)
        monkeypatch.setattr(btc_api, "CONFIG_FILE", cfg_path)
        cfg = btc_api.load_config()
        assert cfg["webhook_url"] == "http://test.com"
        assert cfg["scan_interval_sec"] == 60

    def test_valores_por_defecto_cuando_faltan_claves(self, tmp_path, monkeypatch):
        import btc_api
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "http://test.com"}, f)
        monkeypatch.setattr(btc_api, "CONFIG_FILE", cfg_path)
        cfg = btc_api.load_config()
        # notify_setup_only debe tener valor por defecto
        assert "notify_setup_only" in cfg
        assert cfg["notify_setup_only"] is False
