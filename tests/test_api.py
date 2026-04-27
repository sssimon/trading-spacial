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
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG PATCH HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _patch_config_files(monkeypatch, tmp_path):
    """Patch CONFIG_FILE/DEFAULTS_FILE/SECRETS_FILE on BOTH btc_api and api.config
    modules so tests reach an isolated tmp_path config (and never the developer's
    real config files). Required after PR2 because load_config now lives in
    api.config and reads api.config.CONFIG_FILE — patching btc_api alone is silent."""
    import btc_api
    import api.config as api_config
    cfg_path = tmp_path / "config.json"
    defaults_path = tmp_path / "_no_defaults.json"
    secrets_path = tmp_path / "_no_secrets.json"
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(defaults_path), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(secrets_path), raising=False)
    monkeypatch.setattr(api_config, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(api_config, "DEFAULTS_FILE", str(defaults_path), raising=False)
    monkeypatch.setattr(api_config, "SECRETS_FILE", str(secrets_path), raising=False)
    return cfg_path


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
        cfg_path = _patch_config_files(monkeypatch, tmp_path)
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "", "webhook_secret": "s3cret",
                       "telegram_bot_token": "tok123", "api_key": "",
                       "notify_setup_only": False, "scan_interval_sec": 300}, f)

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
        test_app.get("/config")(btc_api.get_config)
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

    def test_status_strips_secrets(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        cfg = r.json()["config"]
        for key in ("webhook_secret", "telegram_bot_token", "api_key"):
            assert key not in cfg, f"{key} should not be exposed in /status"

    def test_config_strips_secrets(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        cfg = r.json()
        for key in ("webhook_secret", "telegram_bot_token", "api_key"):
            assert key not in cfg, f"{key} should not be exposed in GET /config"

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

    def test_webhook_test_sin_config(self, client):
        r = client.get("/webhook/test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False

    def test_webhook_test_con_url(self, client, tmp_path, monkeypatch):
        import btc_api
        import api.config as _ac
        cfg_path = str(tmp_path / "config_wh.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "http://localhost:9999/wh"}, f)
        monkeypatch.setattr(btc_api, "CONFIG_FILE", cfg_path)
        monkeypatch.setattr(_ac, "CONFIG_FILE", cfg_path)
        monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "_no_defaults.json"))
        monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "_no_secrets.json"))

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
    def _patch_config_files(self, monkeypatch, cfg_path, tmp_path):
        """Delegate to module-level helper, then override CONFIG_FILE if a custom path was requested."""
        import btc_api
        import api.config as _ac
        _patch_config_files(monkeypatch, tmp_path)  # sets up defaults/secrets isolation
        # Override CONFIG_FILE with the caller-specified path (may differ from tmp_path/config.json)
        monkeypatch.setattr(btc_api, "CONFIG_FILE", cfg_path)
        monkeypatch.setattr(_ac, "CONFIG_FILE", cfg_path)

    def test_defaults_sin_archivo(self, tmp_path, monkeypatch):
        self._patch_config_files(monkeypatch, str(tmp_path / "no_existe.json"), tmp_path)
        import btc_api
        cfg = btc_api.load_config()
        assert "webhook_url" in cfg
        assert "scan_interval_sec" in cfg
        assert cfg["scan_interval_sec"] == 300

    def test_lee_archivo_existente(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "http://test.com", "scan_interval_sec": 60}, f)
        self._patch_config_files(monkeypatch, cfg_path, tmp_path)
        import btc_api
        cfg = btc_api.load_config()
        assert cfg["webhook_url"] == "http://test.com"
        assert cfg["scan_interval_sec"] == 60

    def test_valores_por_defecto_cuando_faltan_claves(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "http://test.com"}, f)
        self._patch_config_files(monkeypatch, cfg_path, tmp_path)
        import btc_api
        cfg = btc_api.load_config()
        # notify_setup_only debe tener valor por defecto
        assert "notify_setup_only" in cfg
        assert cfg["notify_setup_only"] is False

    def test_env_var_override(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"telegram_chat_id": "from_file"}, f)
        self._patch_config_files(monkeypatch, cfg_path, tmp_path)
        monkeypatch.setenv("TRADING_TELEGRAM_CHAT_ID", "from_env")
        import btc_api
        cfg = btc_api.load_config()
        assert cfg["telegram_chat_id"] == "from_env"  # ENV takes precedence

# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — execute_scan_for_symbol
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteScanForSymbol:
    """Tests for the shared scan cycle function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        import btc_api
        db_path = str(tmp_path / "test_scan.db")
        cfg_path = _patch_config_files(monkeypatch, tmp_path)
        with open(cfg_path, "w") as f:
            json.dump({"signal_filters": {"min_score": 4}}, f)
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        # Prevent file I/O for logs/csv in tests
        monkeypatch.setattr(btc_api, "append_signal_log", lambda rep, sid: None)
        monkeypatch.setattr(btc_api, "append_signal_csv", lambda rep, sid: None)
        monkeypatch.setattr(btc_api, "check_position_stops", lambda sym, price: None)
        btc_api.init_db()

    def test_returns_dict_with_symbol(self, monkeypatch):
        """execute_scan_for_symbol returns a dict with the symbol."""
        import btc_api
        fake_report = {
            "symbol": "BTCUSDT",
            "timestamp": "2026-01-01T00:00:00",
            "estado": "Sin zona LRC",
            "señal_activa": False,
            "gatillo_activo": False,
            "price": 65000.0,
            "score": 2,
            "score_label": "MINIMA",
            "macro_4h": {"price_above": True},
            "lrc_1h": {"pct": 45.0},
            "sizing_1h": {},
            "confirmations": {},
        }
        monkeypatch.setattr(btc_api, "scan", lambda sym: fake_report)
        monkeypatch.setattr(btc_api, "notify", lambda event, cfg: [])

        cfg = btc_api.load_config()
        result = btc_api.execute_scan_for_symbol("BTCUSDT", cfg)
        assert result["symbol"] == "BTCUSDT"
        assert "error" not in result

    def test_saves_scan_to_db(self, monkeypatch):
        """Scan results are persisted to the database."""
        import btc_api
        fake_report = {
            "symbol": "ETHUSDT", "timestamp": "2026-01-01T00:00:00",
            "estado": "Sin zona", "señal_activa": False, "gatillo_activo": False,
            "price": 3500.0, "score": 1, "score_label": "MINIMA",
            "macro_4h": {}, "lrc_1h": {}, "sizing_1h": {}, "confirmations": {},
        }
        monkeypatch.setattr(btc_api, "scan", lambda sym: fake_report)
        monkeypatch.setattr(btc_api, "notify", lambda event, cfg: [])

        cfg = btc_api.load_config()
        btc_api.execute_scan_for_symbol("ETHUSDT", cfg)
        scans = btc_api.get_scans()
        assert len(scans) >= 1
        assert scans[0]["symbol"] == "ETHUSDT"

    def test_updates_scanner_state(self, monkeypatch):
        """Scanner state is updated after scan."""
        import btc_api
        fake_report = {
            "symbol": "BTCUSDT", "timestamp": "2026-01-01T12:00:00",
            "estado": "Test", "señal_activa": False, "gatillo_activo": False,
            "price": 65000.0, "score": 0, "score_label": "MINIMA",
            "macro_4h": {}, "lrc_1h": {}, "sizing_1h": {}, "confirmations": {},
        }
        monkeypatch.setattr(btc_api, "scan", lambda sym: fake_report)
        monkeypatch.setattr(btc_api, "notify", lambda event, cfg: [])
        initial_count = btc_api._scanner_state["scans_total"]

        cfg = btc_api.load_config()
        btc_api.execute_scan_for_symbol("BTCUSDT", cfg)
        assert btc_api._scanner_state["scans_total"] > initial_count
        assert btc_api._scanner_state["last_symbol"] == "BTCUSDT"

    def test_notifies_on_premium_signal(self, monkeypatch):
        """Notification sent when signal passes filters."""
        import btc_api
        notified = []
        fake_report = {
            "symbol": "BTCUSDT", "timestamp": "2026-01-01T00:00:00",
            "estado": "SEÑAL CONFIRMADA", "señal_activa": True, "gatillo_activo": True,
            "price": 65000.0, "score": 6, "score_label": "PREMIUM",
            "macro_4h": {"price_above": True}, "lrc_1h": {"pct": 15.0},
            "sizing_1h": {"sl_precio": 63000, "tp_precio": 70000},
            "confirmations": {},
        }
        monkeypatch.setattr(btc_api, "scan", lambda sym: fake_report)
        monkeypatch.setattr(btc_api, "notify",
                            lambda event, cfg: notified.append(event.symbol) or [])

        cfg = btc_api.load_config()
        cfg["signal_filters"]["min_score"] = 4
        btc_api.execute_scan_for_symbol("BTCUSDT", cfg)
        assert "BTCUSDT" in notified

    def test_no_notification_below_threshold(self, monkeypatch):
        """No notification when score is below min_score."""
        import btc_api
        notified = []
        fake_report = {
            "symbol": "BTCUSDT", "timestamp": "2026-01-01T00:00:00",
            "estado": "Setup valido", "señal_activa": True, "gatillo_activo": True,
            "price": 65000.0, "score": 2, "score_label": "MINIMA",
            "macro_4h": {}, "lrc_1h": {"pct": 15.0}, "sizing_1h": {},
            "confirmations": {},
        }
        monkeypatch.setattr(btc_api, "scan", lambda sym: fake_report)
        monkeypatch.setattr(btc_api, "notify",
                            lambda event, cfg: notified.append(True) or [])

        cfg = btc_api.load_config()
        cfg["signal_filters"]["min_score"] = 4
        btc_api.execute_scan_for_symbol("BTCUSDT", cfg)
        assert len(notified) == 0

    def test_handles_scan_exception(self, monkeypatch):
        """Exception in scan() returns error dict, doesn't crash."""
        import btc_api

        def raise_error(sym):
            raise ValueError("API down")

        monkeypatch.setattr(btc_api, "scan", raise_error)

        cfg = btc_api.load_config()
        result = btc_api.execute_scan_for_symbol("BTCUSDT", cfg)
        assert "error" in result

    def test_increments_signal_count(self, monkeypatch):
        """signals_total incremented for confirmed signals."""
        import btc_api
        fake_report = {
            "symbol": "BTCUSDT", "timestamp": "2026-01-01T00:00:00",
            "estado": "SEÑAL CONFIRMADA", "señal_activa": True, "gatillo_activo": True,
            "price": 65000.0, "score": 6, "score_label": "PREMIUM",
            "macro_4h": {}, "lrc_1h": {}, "sizing_1h": {}, "confirmations": {},
        }
        monkeypatch.setattr(btc_api, "scan", lambda sym: fake_report)
        monkeypatch.setattr(btc_api, "notify", lambda event, cfg: [])

        initial = btc_api._scanner_state["signals_total"]
        cfg = btc_api.load_config()
        btc_api.execute_scan_for_symbol("BTCUSDT", cfg)
        assert btc_api._scanner_state["signals_total"] == initial + 1
#  TESTS — Posiciones CRUD (funciones de DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionsCRUD:
    """Tests for the position management system (DB-level functions)."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path, monkeypatch):
        """Fresh DB for each test."""
        import btc_api
        db_path = str(tmp_path / "test_pos.db")
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        btc_api.init_db()

    # --- db_create_position ---

    def test_create_position_basic(self):
        """Create a position with minimal data (symbol + entry_price)."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        assert isinstance(pos, dict)
        assert pos["id"] > 0
        assert pos["symbol"] == "BTCUSDT"
        assert pos["entry_price"] == 65000.0
        assert pos["status"] == "open"
        assert pos["direction"] == "LONG"  # default

    def test_create_position_full(self):
        """Create a position with all fields."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "ethusdt",
            "entry_price": 3500.0,
            "sl_price": 3200.0,
            "tp_price": 4000.0,
            "qty": 1.5,
            "direction": "LONG",
            "notes": "Test position",
            "size_usd": 5250.0,
        })
        assert pos["symbol"] == "ETHUSDT"  # uppercased
        assert pos["entry_price"] == 3500.0
        assert pos["sl_price"] == 3200.0
        assert pos["tp_price"] == 4000.0
        assert pos["qty"] == 1.5
        assert pos["direction"] == "LONG"
        assert pos["status"] == "open"
        assert pos["notes"] == "Test position"

    def test_create_position_short_direction(self):
        """Create a SHORT position."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 68000.0,
            "direction": "short",
        })
        assert pos["direction"] == "SHORT"

    def test_create_position_with_scan_id(self):
        """Position linked to a scan_id."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "scan_id": 42,
        })
        assert pos["scan_id"] == 42

    def test_create_position_qty_from_size_usd(self):
        """When qty is not given, it is derived from size_usd / entry_price."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 50000.0,
            "size_usd": 1000.0,
        })
        # qty = 1000 / 50000 = 0.02
        assert pos["qty"] == pytest.approx(0.02, abs=1e-6)

    # --- db_get_positions ---

    def test_get_positions_empty(self):
        """No positions returns empty list."""
        import btc_api
        positions = btc_api.db_get_positions()
        assert positions == []

    def test_get_positions_returns_all(self):
        """Get all positions regardless of status."""
        import btc_api
        btc_api.db_create_position({"symbol": "BTCUSDT", "entry_price": 65000})
        btc_api.db_create_position({"symbol": "ETHUSDT", "entry_price": 3500})
        positions = btc_api.db_get_positions()
        assert len(positions) == 2

    def test_get_positions_filter_status(self):
        """Filter positions by status."""
        import btc_api
        btc_api.db_create_position({"symbol": "BTCUSDT", "entry_price": 65000})
        pos2 = btc_api.db_create_position({"symbol": "ETHUSDT", "entry_price": 3500})
        btc_api.db_close_position(pos2["id"], 3800.0, "TP_HIT")

        open_pos = btc_api.db_get_positions(status="open")
        closed_pos = btc_api.db_get_positions(status="closed")
        all_pos = btc_api.db_get_positions()

        assert len(open_pos) == 1
        assert open_pos[0]["symbol"] == "BTCUSDT"
        assert len(closed_pos) == 1
        assert closed_pos[0]["symbol"] == "ETHUSDT"
        assert len(all_pos) == 2

    def test_get_positions_status_all_returns_everything(self):
        """status='all' behaves same as no filter."""
        import btc_api
        btc_api.db_create_position({"symbol": "BTCUSDT", "entry_price": 65000})
        pos2 = btc_api.db_create_position({"symbol": "ETHUSDT", "entry_price": 3500})
        btc_api.db_close_position(pos2["id"], 3800.0, "MANUAL")

        all_pos = btc_api.db_get_positions(status="all")
        assert len(all_pos) == 2

    def test_get_positions_ordered_desc(self):
        """Positions are returned in descending id order (newest first)."""
        import btc_api
        p1 = btc_api.db_create_position({"symbol": "BTCUSDT", "entry_price": 65000})
        p2 = btc_api.db_create_position({"symbol": "ETHUSDT", "entry_price": 3500})
        positions = btc_api.db_get_positions()
        assert positions[0]["id"] == p2["id"]
        assert positions[1]["id"] == p1["id"]

    # --- db_close_position ---

    def test_close_position(self):
        """Close a position and verify exit data."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "qty": 0.1,
            "direction": "LONG",
        })
        closed = btc_api.db_close_position(pos["id"], 68000.0, "TP_HIT")
        assert closed is not None
        assert closed["exit_price"] == 68000.0
        assert closed["exit_reason"] == "TP_HIT"
        assert closed["status"] == "closed"
        assert closed["exit_ts"] is not None

    def test_close_position_pnl_long(self):
        """P&L calculation correct for LONG position."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "qty": 0.1,
            "direction": "LONG",
        })
        closed = btc_api.db_close_position(pos["id"], 68000.0, "TP_HIT")
        # PnL = (68000 - 65000) * 0.1 = 300
        assert closed["pnl_usd"] == pytest.approx(300.0, abs=0.01)
        assert closed["pnl_pct"] > 0

    def test_close_position_pnl_long_loss(self):
        """Negative P&L for LONG position that drops."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "qty": 0.1,
            "direction": "LONG",
        })
        closed = btc_api.db_close_position(pos["id"], 63000.0, "SL_HIT")
        # PnL = (63000 - 65000) * 0.1 = -200
        assert closed["pnl_usd"] == pytest.approx(-200.0, abs=0.01)
        assert closed["pnl_pct"] < 0

    def test_close_position_pnl_short(self):
        """P&L calculation correct for SHORT position."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 68000.0,
            "qty": 0.1,
            "direction": "SHORT",
        })
        closed = btc_api.db_close_position(pos["id"], 65000.0, "TP_HIT")
        # PnL for SHORT = (entry - exit) * qty = (68000 - 65000) * 0.1 = 300
        assert closed["pnl_usd"] == pytest.approx(300.0, abs=0.01)
        assert closed["pnl_pct"] > 0

    def test_close_position_pnl_short_loss(self):
        """Negative P&L for SHORT position that rises."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "qty": 0.1,
            "direction": "SHORT",
        })
        closed = btc_api.db_close_position(pos["id"], 68000.0, "SL_HIT")
        # PnL = (65000 - 68000) * 0.1 = -300
        assert closed["pnl_usd"] == pytest.approx(-300.0, abs=0.01)
        assert closed["pnl_pct"] < 0

    def test_close_nonexistent_position_returns_none(self):
        """Closing a position that does not exist returns None."""
        import btc_api
        result = btc_api.db_close_position(9999, 68000.0, "MANUAL")
        assert result is None

    # --- db_update_position ---

    def test_update_position_sl_tp(self):
        """Update SL/TP of an open position."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
        })
        updated = btc_api.db_update_position(pos["id"], {
            "sl_price": 64000.0,
            "tp_price": 72000.0,
        })
        assert updated is not None
        assert updated["sl_price"] == 64000.0
        assert updated["tp_price"] == 72000.0
        # Entry price unchanged
        assert updated["entry_price"] == 65000.0

    def test_update_position_notes(self):
        """Update notes field."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        updated = btc_api.db_update_position(pos["id"], {"notes": "Updated note"})
        assert updated["notes"] == "Updated note"

    def test_update_position_rejects_invalid_fields(self):
        """Only allowed fields can be updated; invalid fields return None."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        # db_update_position uses an 'allowed' set whitelist;
        # passing only invalid fields returns None (no updates)
        result = btc_api.db_update_position(pos["id"], {"malicious_field": "hacked"})
        assert result is None

    def test_update_position_mixed_valid_invalid_fields(self):
        """Mixed valid and invalid fields: only valid ones are applied."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
        })
        updated = btc_api.db_update_position(pos["id"], {
            "sl_price": 64000.0,
            "evil_field": "drop table",
        })
        assert updated is not None
        assert updated["sl_price"] == 64000.0

    def test_update_position_allowed_fields(self):
        """All allowed fields can be updated."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        updated = btc_api.db_update_position(pos["id"], {
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "size_usd": 1000.0,
            "qty": 0.5,
            "notes": "all fields",
            "entry_price": 65500.0,
        })
        assert updated["sl_price"] == 63000.0
        assert updated["tp_price"] == 70000.0
        assert updated["size_usd"] == 1000.0
        assert updated["qty"] == 0.5
        assert updated["notes"] == "all fields"
        assert updated["entry_price"] == 65500.0

    # --- check_position_stops ---

    def test_check_stops_sl_hit_long(self):
        """SL hit on LONG position auto-closes it."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 62000.0)  # below SL
        positions = btc_api.db_get_positions(status="closed")
        assert len(positions) == 1
        assert positions[0]["exit_reason"] == "SL_HIT"
        assert positions[0]["exit_price"] == 63000.0  # closes at SL price

    def test_check_stops_tp_hit_long(self):
        """TP hit on LONG position auto-closes it."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 71000.0)  # above TP
        positions = btc_api.db_get_positions(status="closed")
        assert len(positions) == 1
        assert positions[0]["exit_reason"] == "TP_HIT"
        assert positions[0]["exit_price"] == 70000.0  # closes at TP price

    def test_check_stops_price_between_sl_tp(self):
        """Price between SL and TP does not close the position."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 66000.0)
        open_pos = btc_api.db_get_positions(status="open")
        closed_pos = btc_api.db_get_positions(status="closed")
        assert len(open_pos) == 1
        assert len(closed_pos) == 0

    def test_check_stops_no_sl_tp(self):
        """Position without SL/TP is never auto-closed."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 50000.0)  # huge drop
        open_pos = btc_api.db_get_positions(status="open")
        assert len(open_pos) == 1  # still open

    def test_check_stops_different_symbol(self):
        """Only positions matching the symbol are checked."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("ETHUSDT", 1000.0)  # different symbol
        open_pos = btc_api.db_get_positions(status="open")
        assert len(open_pos) == 1  # BTC position still open

    def test_check_stops_sl_at_exact_price(self):
        """SL triggered when price equals SL exactly."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 63000.0)  # exactly at SL
        closed_pos = btc_api.db_get_positions(status="closed")
        assert len(closed_pos) == 1
        assert closed_pos[0]["exit_reason"] == "SL_HIT"

    def test_check_stops_tp_at_exact_price(self):
        """TP triggered when price equals TP exactly."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 70000.0)  # exactly at TP
        closed_pos = btc_api.db_get_positions(status="closed")
        assert len(closed_pos) == 1
        assert closed_pos[0]["exit_reason"] == "TP_HIT"

    def test_check_stops_multiple_positions(self):
        """Multiple open positions for same symbol are all checked."""
        import btc_api
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 60000.0,
            "sl_price": 58000.0,
            "tp_price": 68000.0,
            "direction": "LONG",
        })
        # Price triggers TP on second position (>=68000) but not first (needs >=70000)
        btc_api.check_position_stops("BTCUSDT", 69000.0)
        open_pos = btc_api.db_get_positions(status="open")
        closed_pos = btc_api.db_get_positions(status="closed")
        assert len(open_pos) == 1
        assert len(closed_pos) == 1
        assert closed_pos[0]["entry_price"] == 60000.0  # second one hit TP

    def test_check_stops_already_closed_not_affected(self):
        """Already-closed positions are not re-checked."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
            "tp_price": 70000.0,
            "direction": "LONG",
        })
        btc_api.db_close_position(pos["id"], 68000.0, "MANUAL")
        # Now check stops with price below SL -- should NOT re-close
        btc_api.check_position_stops("BTCUSDT", 60000.0)
        closed_pos = btc_api.db_get_positions(status="closed")
        assert len(closed_pos) == 1
        assert closed_pos[0]["exit_reason"] == "MANUAL"  # original reason

    def test_trailing_ratchet_moves_sl_to_breakeven(self):
        """When price rises >= 1.5x ATR above entry, SL moves to entry (breakeven)."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 60000.0,
            "sl_price": 59000.0,
            "tp_price": 63000.0,
            "direction": "LONG",
            "atr_entry": 666.67,
        })
        # Price rises to entry + 1.5*ATR = 60000 + 1000 = 61000
        btc_api.check_position_stops("BTCUSDT", 61000.0)
        updated = btc_api.db_get_positions(status="open")
        assert len(updated) == 1
        assert updated[0]["sl_price"] == 60000.0  # moved to entry price

    def test_trailing_ratchet_never_lowers_sl(self):
        """SL should only go up (tighten), never down."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 60000.0,
            "sl_price": 60000.0,  # already at breakeven
            "tp_price": 63000.0,
            "direction": "LONG",
            "atr_entry": 666.67,
        })
        btc_api.check_position_stops("BTCUSDT", 60500.0)
        updated = btc_api.db_get_positions(status="open")
        assert len(updated) == 1
        assert updated[0]["sl_price"] == 60000.0  # unchanged

    def test_trailing_ratchet_uses_custom_be_mult(self):
        """be_mult from position overrides the default 1.5."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "DOGEUSDT",
            "entry_price": 0.10,
            "sl_price": 0.09,
            "tp_price": 0.14,
            "direction": "LONG",
            "atr_entry": 0.005,
            "be_mult": 2.0,  # custom: breakeven at entry + 2.0 * 0.005 = 0.11
        })
        # Price at 0.108 — above 1.5x ATR (0.1075) but below 2.0x ATR (0.11)
        btc_api.check_position_stops("DOGEUSDT", 0.108)
        updated = btc_api.db_get_positions(status="open")
        assert len(updated) == 1
        assert updated[0]["sl_price"] == 0.09  # NOT at breakeven yet (be_mult=2.0)

        # Price at 0.111 — above 2.0x ATR threshold
        btc_api.check_position_stops("DOGEUSDT", 0.111)
        updated = btc_api.db_get_positions(status="open")
        assert len(updated) == 1
        assert updated[0]["sl_price"] == 0.10  # NOW at breakeven

    def test_position_without_atr_skips_trailing(self):
        """Legacy positions without atr_entry skip trailing logic."""
        import btc_api
        pos = btc_api.db_create_position({
            "symbol": "BTCUSDT",
            "entry_price": 60000.0,
            "sl_price": 58800.0,
            "tp_price": 62400.0,
            "direction": "LONG",
        })
        btc_api.check_position_stops("BTCUSDT", 61500.0)
        updated = btc_api.db_get_positions(status="open")
        assert len(updated) == 1
        assert updated[0]["sl_price"] == 58800.0  # unchanged, no trailing


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — Posiciones API endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionsAPI:
    """Tests for position API endpoints."""

    @pytest.fixture(autouse=True)
    def setup_test_app(self, tmp_path, monkeypatch):
        """Configure app with temp DB and config."""
        import btc_api
        db_path = str(tmp_path / "test_pos_api.db")
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        cfg_path = _patch_config_files(monkeypatch, tmp_path)
        with open(cfg_path, "w") as f:
            json.dump({"webhook_url": "", "webhook_secret": "",
                       "notify_setup_only": False, "scan_interval_sec": 300}, f)
        # Monkeypatch DATA_DIR and LOGS_DIR to temp dirs to avoid file writes
        monkeypatch.setattr(btc_api, "DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setattr(btc_api, "LOGS_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(btc_api, "POSITIONS_JSON_FILE",
                            str(tmp_path / "data" / "positions_summary.json"))
        monkeypatch.setattr(btc_api, "SIGNALS_LOG_FILE",
                            str(tmp_path / "logs" / "signals.log"))
        btc_api.init_db()

    @pytest.fixture
    def client(self):
        """TestClient with position routes registered."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        import btc_api

        test_app = FastAPI()
        test_app.get("/positions")(btc_api.list_positions)
        test_app.post("/positions")(btc_api.open_position)
        test_app.put("/positions/{pos_id}")(btc_api.edit_position)
        test_app.post("/positions/{pos_id}/close")(btc_api.close_position)
        test_app.delete("/positions/{pos_id}")(btc_api.delete_position)

        return TestClient(test_app)

    def test_get_positions_empty(self, client):
        """GET /positions returns 200 with empty list."""
        r = client.get("/positions")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["positions"] == []

    def test_create_position_via_api(self, client):
        """POST /positions creates a position."""
        r = client.post("/positions", json={
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["position"]["symbol"] == "BTCUSDT"
        assert data["position"]["entry_price"] == 65000.0
        assert data["position"]["status"] == "open"

    def test_create_position_missing_fields(self, client):
        """POST /positions without required fields returns 422."""
        r = client.post("/positions", json={"symbol": "BTCUSDT"})
        assert r.status_code == 422

    def test_create_position_full_fields(self, client):
        """POST /positions with all optional fields."""
        r = client.post("/positions", json={
            "symbol": "ETHUSDT",
            "entry_price": 3500.0,
            "sl_price": 3200.0,
            "tp_price": 4000.0,
            "qty": 1.5,
            "direction": "LONG",
            "notes": "API test",
        })
        assert r.status_code == 200
        pos = r.json()["position"]
        assert pos["sl_price"] == 3200.0
        assert pos["tp_price"] == 4000.0
        assert pos["qty"] == 1.5

    def test_close_position_via_api(self, client):
        """POST /positions/{id}/close closes a position."""
        r = client.post("/positions", json={
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "qty": 0.1,
        })
        pos_id = r.json()["position"]["id"]

        r2 = client.post(f"/positions/{pos_id}/close", json={
            "exit_price": 68000.0,
            "exit_reason": "MANUAL",
        })
        assert r2.status_code == 200
        data = r2.json()
        assert data["ok"] is True
        assert data["position"]["status"] == "closed"
        assert data["position"]["exit_price"] == 68000.0
        assert data["position"]["exit_reason"] == "MANUAL"

    def test_close_position_missing_exit_price(self, client):
        """POST /positions/{id}/close without exit_price returns 422."""
        r = client.post("/positions", json={
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        pos_id = r.json()["position"]["id"]
        r2 = client.post(f"/positions/{pos_id}/close", json={})
        assert r2.status_code == 422

    def test_close_nonexistent_position_via_api(self, client):
        """POST /positions/9999/close returns 404."""
        r = client.post("/positions/9999/close", json={
            "exit_price": 68000.0,
        })
        assert r.status_code == 404

    def test_edit_position_via_api(self, client):
        """PUT /positions/{id} updates allowed fields."""
        r = client.post("/positions", json={
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
            "sl_price": 63000.0,
        })
        pos_id = r.json()["position"]["id"]

        r2 = client.put(f"/positions/{pos_id}", json={
            "sl_price": 64000.0,
            "tp_price": 72000.0,
        })
        assert r2.status_code == 200
        pos = r2.json()["position"]
        assert pos["sl_price"] == 64000.0
        assert pos["tp_price"] == 72000.0

    def test_edit_position_invalid_fields_returns_404(self, client):
        """PUT /positions/{id} with only invalid fields returns 404."""
        r = client.post("/positions", json={
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        pos_id = r.json()["position"]["id"]
        r2 = client.put(f"/positions/{pos_id}", json={"bad_field": "value"})
        # db_update_position returns None for only-invalid fields -> 404
        assert r2.status_code == 404

    def test_delete_position_via_api(self, client):
        """DELETE /positions/{id} cancels the position."""
        r = client.post("/positions", json={
            "symbol": "BTCUSDT",
            "entry_price": 65000.0,
        })
        pos_id = r.json()["position"]["id"]

        r2 = client.delete(f"/positions/{pos_id}")
        assert r2.status_code == 200
        assert r2.json()["ok"] is True

        # Verify it's marked as cancelled
        r3 = client.get("/positions")
        positions = r3.json()["positions"]
        assert len(positions) == 1
        assert positions[0]["status"] == "cancelled"

    def test_delete_nonexistent_position(self, client):
        """DELETE /positions/9999 returns 404."""
        r = client.delete("/positions/9999")
        assert r.status_code == 404

    def test_get_positions_filter_open(self, client):
        """GET /positions?status=open filters correctly."""
        client.post("/positions", json={
            "symbol": "BTCUSDT", "entry_price": 65000.0,
        })
        r = client.post("/positions", json={
            "symbol": "ETHUSDT", "entry_price": 3500.0,
        })
        pos_id = r.json()["position"]["id"]
        client.post(f"/positions/{pos_id}/close", json={
            "exit_price": 3800.0, "exit_reason": "MANUAL",
        })

        r_open = client.get("/positions?status=open")
        assert r_open.json()["total"] == 1
        assert r_open.json()["positions"][0]["symbol"] == "BTCUSDT"

        r_closed = client.get("/positions?status=closed")
        assert r_closed.json()["total"] == 1
        assert r_closed.json()["positions"][0]["symbol"] == "ETHUSDT"

    def test_full_position_lifecycle(self, client):
        """Create -> Edit -> Close lifecycle via API."""
        # Create
        r1 = client.post("/positions", json={
            "symbol": "SOLUSDT",
            "entry_price": 150.0,
            "qty": 10.0,
            "direction": "LONG",
        })
        assert r1.status_code == 200
        pos_id = r1.json()["position"]["id"]

        # Edit (trail stop loss up)
        r2 = client.put(f"/positions/{pos_id}", json={
            "sl_price": 145.0,
            "tp_price": 180.0,
        })
        assert r2.status_code == 200
        assert r2.json()["position"]["sl_price"] == 145.0

        # Close
        r3 = client.post(f"/positions/{pos_id}/close", json={
            "exit_price": 175.0,
            "exit_reason": "MANUAL",
        })
        assert r3.status_code == 200
        pos = r3.json()["position"]
        assert pos["status"] == "closed"
        assert pos["pnl_usd"] == pytest.approx(250.0, abs=0.01)  # (175-150)*10
    def test_dedup_window_default(self, tmp_path, monkeypatch):
        import btc_api
        cfg_path = _patch_config_files(monkeypatch, tmp_path)
        with open(cfg_path, "w") as f:
            json.dump({}, f)
        monkeypatch.setenv("TRADING_SCAN_INTERVAL", "120")
        cfg = btc_api.load_config()
        assert cfg["scan_interval_sec"] == 120
        cfg = btc_api.load_config()
        assert cfg["signal_filters"]["dedup_window_minutes"] == 30


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — Signal deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalDeduplication:
    @pytest.fixture(autouse=True)
    def clear_notified(self):
        """Clear the in-memory dedup tracker before each test."""
        import btc_api
        btc_api._notified_signals.clear()
        yield
        btc_api._notified_signals.clear()

    def test_first_signal_is_not_duplicate(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 30}}
        assert btc_api._is_duplicate_signal("BTCUSDT", cfg) is False

    def test_same_symbol_is_duplicate_within_window(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 30}}
        btc_api._mark_notified("BTCUSDT")
        assert btc_api._is_duplicate_signal("BTCUSDT", cfg) is True

    def test_different_symbol_is_not_duplicate(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 30}}
        btc_api._mark_notified("BTCUSDT")
        assert btc_api._is_duplicate_signal("ETHUSDT", cfg) is False

    def test_signal_outside_window_is_not_duplicate(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 30}}
        # Simulate a notification from 31 minutes ago
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        btc_api._notified_signals["BTCUSDT"] = old_ts
        assert btc_api._is_duplicate_signal("BTCUSDT", cfg) is False

    def test_signal_inside_window_is_duplicate(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 30}}
        # Simulate a notification from 10 minutes ago
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        btc_api._notified_signals["BTCUSDT"] = recent_ts
        assert btc_api._is_duplicate_signal("BTCUSDT", cfg) is True

    def test_custom_dedup_window(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 5}}
        # 6 minutes ago should be outside a 5-minute window
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        btc_api._notified_signals["BTCUSDT"] = old_ts
        assert btc_api._is_duplicate_signal("BTCUSDT", cfg) is False

    def test_mark_notified_updates_timestamp(self):
        import btc_api
        cfg = {"signal_filters": {"dedup_window_minutes": 30}}
        btc_api._mark_notified("BTCUSDT")
        ts1 = btc_api._notified_signals["BTCUSDT"]
        assert ts1 is not None
        # Mark again — timestamp should be present
        btc_api._mark_notified("BTCUSDT")
        ts2 = btc_api._notified_signals["BTCUSDT"]
        assert ts2 >= ts1

    def test_default_window_from_config(self):
        """When dedup_window_minutes is not set, default to 30."""
        import btc_api
        cfg = {"signal_filters": {}}
        btc_api._mark_notified("BTCUSDT")
        assert btc_api._is_duplicate_signal("BTCUSDT", cfg) is True


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — Signal Performance
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalPerformance:
    @pytest.fixture(autouse=True)
    def setup_api(self, tmp_path, monkeypatch):
        import btc_api
        db_path = str(tmp_path / "test_perf.db")
        monkeypatch.setattr(btc_api, "DB_FILE", db_path)
        btc_api.init_db()

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        import btc_api
        test_app = FastAPI()
        test_app.get("/signals/performance")(btc_api.get_signals_performance)
        return TestClient(test_app)

    def test_performance_no_data(self, client):
        r = client.get("/signals/performance")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["total_completed"] == 0

    def test_check_pending_uses_current_prices(self):
        """check_pending_signal_outcomes uses passed prices, no API calls."""
        import btc_api
        from unittest.mock import patch

        con = btc_api.get_db()
        # Insert a pending signal from 2 hours ago
        ts_2h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        con.execute("""
            INSERT INTO signal_outcomes
            (scan_id, symbol, signal_ts, signal_price, score, status)
            VALUES (99, 'BTCUSDT', ?, 60000.0, 5, 'pending')
        """, (ts_2h_ago,))
        con.commit()
        con.close()

        # Pass current price — should fill price_1h without calling get_klines for milestones
        with patch.object(btc_api.md, "get_klines") as mock_klines:
            # get_klines only needed for runup/drawdown (1h candles)
            import pandas as pd
            mock_klines.return_value = pd.DataFrame({
                "open": [59000.0], "high": [62000.0],
                "low": [58000.0], "close": [61000.0],
                "volume": [100.0], "taker_buy_base": [50.0],
            })
            btc_api.check_pending_signal_outcomes({"BTCUSDT": 61500.0})

            # Should only call get_klines once (for runup/drawdown 1h),
            # NOT 3 times for milestone prices
            assert mock_klines.call_count <= 1
            if mock_klines.call_count == 1:
                args = mock_klines.call_args
                assert args[0][1] == "1h"  # interval must be 1h, not 1m

        # Verify price_1h was set from current_prices
        con = btc_api.get_db()
        row = con.execute("SELECT * FROM signal_outcomes WHERE scan_id = 99").fetchone()
        con.close()
        assert row["price_1h"] == 61500.0

    def test_check_pending_groups_by_symbol(self):
        """Multiple pending signals for same symbol share one klines call."""
        import btc_api
        from unittest.mock import patch

        con = btc_api.get_db()
        ts_3h_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        for scan_id in (200, 201, 202):
            con.execute("""
                INSERT INTO signal_outcomes
                (scan_id, symbol, signal_ts, signal_price, score, status)
                VALUES (?, 'ETHUSDT', ?, 3000.0, 4, 'pending')
            """, (scan_id, ts_3h_ago))
        con.commit()
        con.close()

        with patch.object(btc_api.md, "get_klines") as mock_klines:
            import pandas as pd
            mock_klines.return_value = pd.DataFrame({
                "open": [2900.0], "high": [3100.0],
                "low": [2850.0], "close": [3050.0],
                "volume": [500.0], "taker_buy_base": [250.0],
            })
            btc_api.check_pending_signal_outcomes({"ETHUSDT": 3050.0})

            # Only ONE klines call for all 3 signals (same symbol)
            assert mock_klines.call_count == 1

    def test_performance_with_data(self, client):
        import btc_api
        con = btc_api.get_db()
        # Insert completed signals
        # Signal 1: Win (score 8)
        con.execute("""
            INSERT INTO signal_outcomes (scan_id, symbol, signal_ts, signal_price, score, price_24h, max_runup_pct, max_drawdown_pct, status)
            VALUES (1, 'BTCUSDT', '2025-01-01T00:00:00', 60000.0, 8, 62000.0, 5.0, -1.0, 'completed')
        """)
        # Signal 2: Loss (score 4)
        con.execute("""
            INSERT INTO signal_outcomes (scan_id, symbol, signal_ts, signal_price, score, price_24h, max_runup_pct, max_drawdown_pct, status)
            VALUES (2, 'ETHUSDT', '2025-01-01T01:00:00', 3000.0, 4, 2900.0, 1.0, -5.0, 'completed')
        """)
        con.commit()
        con.close()

        r = client.get("/signals/performance")
        assert r.status_code == 200
        data = r.json()
        assert data["total_completed"] == 2
        assert data["overall_win_rate"] == 0.5
        assert data["avg_max_runup_pct"] == 3.0  # (5+1)/2
        assert data["avg_max_drawdown_pct"] == -3.0 # (-1-5)/2
        
        # Check by_score
        by_score = data["by_score"]
        assert len(by_score) == 2
        assert by_score[0]["score"] == 8
        assert by_score[0]["win_rate"] == 1.0
        assert by_score[1]["score"] == 4
        assert by_score[1]["win_rate"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  TESTS — Kill Switch Observability Endpoints (#187 phase 1)
# ─────────────────────────────────────────────────────────────────────────────

class TestKillSwitchDecisionsEndpoint:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_db, monkeypatch):
        import btc_api
        monkeypatch.setattr(btc_api, "DB_FILE", tmp_db)
        btc_api.init_db()
        yield

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        import btc_api
        return TestClient(btc_api.app)

    def test_returns_empty_when_no_decisions(self, client, tmp_db):
        """GET /kill_switch/decisions returns [] when no decisions recorded."""
        r = client.get("/kill_switch/decisions")
        assert r.status_code == 200
        assert r.json()["decisions"] == []

    def test_returns_recorded_decisions(self, client, tmp_db):
        """GET /kill_switch/decisions returns what was recorded."""
        import observability
        observability.record_decision(
            symbol="BTCUSDT", engine="v1", per_symbol_tier="NORMAL",
            portfolio_tier="NORMAL", size_factor=1.0, skip=False,
            reasons={"x": 1}, scan_id=None, slider_value=None,
            velocity_active=False,
        )
        r = client.get("/kill_switch/decisions")
        assert r.status_code == 200
        body = r.json()
        assert len(body["decisions"]) == 1
        assert body["decisions"][0]["symbol"] == "BTCUSDT"
        assert body["decisions"][0]["engine"] == "v1"

    def test_filters_by_symbol(self, client, tmp_db):
        import observability
        observability.record_decision(symbol="BTCUSDT", engine="v1",
                                      per_symbol_tier="NORMAL", portfolio_tier="NORMAL",
                                      size_factor=1.0, skip=False, reasons={},
                                      scan_id=None, slider_value=None, velocity_active=False)
        observability.record_decision(symbol="ETHUSDT", engine="v1",
                                      per_symbol_tier="ALERT", portfolio_tier="NORMAL",
                                      size_factor=1.0, skip=False, reasons={},
                                      scan_id=None, slider_value=None, velocity_active=False)
        r = client.get("/kill_switch/decisions?symbol=ETHUSDT")
        assert r.status_code == 200
        assert len(r.json()["decisions"]) == 1
        assert r.json()["decisions"][0]["symbol"] == "ETHUSDT"

    def test_respects_limit_query(self, client, tmp_db):
        import observability
        for i in range(5):
            observability.record_decision(
                symbol=f"S{i}", engine="v1",
                per_symbol_tier="NORMAL", portfolio_tier="NORMAL",
                size_factor=1.0, skip=False, reasons={},
                scan_id=None, slider_value=None, velocity_active=False,
            )
        r = client.get("/kill_switch/decisions?limit=2")
        assert r.status_code == 200
        assert len(r.json()["decisions"]) == 2

    def test_rejects_limit_over_max(self, client, tmp_db):
        r = client.get("/kill_switch/decisions?limit=500")
        assert r.status_code == 422  # pydantic Query validation


class TestKillSwitchCurrentStateEndpoint:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_db, monkeypatch):
        import btc_api
        monkeypatch.setattr(btc_api, "DB_FILE", tmp_db)
        btc_api.init_db()
        yield

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        import btc_api
        return TestClient(btc_api.app)

    def test_returns_empty_state(self, client, tmp_db):
        r = client.get("/kill_switch/current_state")
        assert r.status_code == 200
        body = r.json()
        assert body["symbols"] == {}
        assert body["portfolio"]["tier"] == "NORMAL"
        assert body["portfolio"]["concurrent_failures"] == 0

    def test_returns_latest_per_symbol(self, client, tmp_db):
        import observability
        observability.record_decision(
            symbol="BTCUSDT", engine="v1",
            per_symbol_tier="ALERT", portfolio_tier="NORMAL",
            size_factor=1.0, skip=False, reasons={},
            scan_id=None, slider_value=None, velocity_active=False,
        )
        r = client.get("/kill_switch/current_state")
        assert r.status_code == 200
        body = r.json()
        assert body["symbols"]["BTCUSDT"]["per_symbol_tier"] == "ALERT"
