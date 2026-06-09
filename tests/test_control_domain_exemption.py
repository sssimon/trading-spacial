"""Exención sistemática de posiciones EXTERNAL (control_domain) — fundación 2/2.

CD-1: ningún actuador del sistema ni su matemática de riesgo/cooldown toca una
posición EXTERNAL. Verifica los 6 consumidores de `status='open'` que Adrian
enumeró + el rechazo de DELETE.

Spec: docs/superpowers/specs/es/2026-06-09-posiciones-externas-control-domain-spec.md (REV 2 §4).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_and_cfg(tmp_path, monkeypatch):
    """Fresh DB wired into every connection source + overridable config."""
    db_path = str(tmp_path / "cd_exempt.db")
    import db.connection as dbconn
    import btc_api
    import api.positions as _pos_mod

    monkeypatch.setattr(dbconn, "DB_FILE", db_path)
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    cfg_holder: dict = {"cfg": {}}
    monkeypatch.setattr(_pos_mod, "load_config", lambda: cfg_holder["cfg"])
    from strategy import _validators
    monkeypatch.setattr(_validators, "_validator_warned", set())

    def set_cfg(cfg: dict):
        cfg_holder["cfg"] = cfg

    return db_path, set_cfg


def _insert(con, *, symbol="BTCUSDT", direction="LONG", status="open",
            control_domain="INTERNAL", entry_price=65000.0, entry_ts=None,
            sl_price=None, tp_price=None, qty=1.0, tenant_id=2,
            exit_ts=None, pnl_usd=None):
    if entry_ts is None:
        entry_ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    cur = con.execute(
        """INSERT INTO positions
               (symbol, direction, status, entry_price, entry_ts, sl_price,
                tp_price, qty, tenant_id, control_domain, exit_ts, pnl_usd)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, direction, status, entry_price, entry_ts, sl_price,
         tp_price, qty, tenant_id, control_domain, exit_ts, pnl_usd),
    )
    return cur.lastrowid


# ─────────────────────────────────────────────────────────────────────────────
# 1. check_position_stops — el landmine: NO auto-cierra una EXTERNAL
# ─────────────────────────────────────────────────────────────────────────────


def test_check_position_stops_skips_external(db_and_cfg):
    """Una EXTERNAL muy vencida NO se auto-cierra; una INTERNAL gemela SÍ."""
    import btc_api
    from db.transaction import transaction

    _, set_cfg = db_and_cfg
    set_cfg({"symbol_overrides": {"BTCUSDT": {"time_limit_hours": 5}}})

    old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    with transaction() as con:
        ext_id = _insert(con, control_domain="EXTERNAL", entry_ts=old)
        int_id = _insert(con, control_domain="INTERNAL", entry_ts=old)

    btc_api.check_position_stops("BTCUSDT", 65500.0)

    with transaction() as con:
        ext = con.execute("SELECT status FROM positions WHERE id=?", (ext_id,)).fetchone()[0]
        intl = con.execute("SELECT status FROM positions WHERE id=?", (int_id,)).fetchone()[0]
    assert ext == "open", "EXTERNAL no debe auto-cerrarse por TIME_LIMIT"
    assert intl == "closed", "la INTERNAL gemela SÍ debe cerrarse (control: el filtro discrimina)"


# ─────────────────────────────────────────────────────────────────────────────
# 2. db_last_exit_ts — el cooldown ignora cierres EXTERNAL
# ─────────────────────────────────────────────────────────────────────────────


def test_db_last_exit_ts_ignores_external(db_and_cfg):
    """Un cierre EXTERNAL no debe contar como último exit del símbolo (cooldown)."""
    from db.transaction import transaction
    from db.positions import db_last_exit_ts

    with transaction() as con:
        _insert(con, status="closed", control_domain="EXTERNAL",
                exit_ts=datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat(), pnl_usd=-10.0)
        result = db_last_exit_ts(con, "BTCUSDT")
    assert result is None, "un cierre EXTERNAL no debe fijar el cooldown system-wide"


# ─────────────────────────────────────────────────────────────────────────────
# 3. kill_switch_v2_shadow — MTM/riesgo excluye EXTERNAL (open + closed)
# ─────────────────────────────────────────────────────────────────────────────


def test_shadow_load_open_excludes_external(db_and_cfg):
    from db.transaction import transaction
    from strategy.kill_switch_v2_shadow import _load_open_positions

    with transaction() as con:
        _insert(con, status="open", control_domain="EXTERNAL", tenant_id=2)
        _insert(con, status="open", control_domain="INTERNAL", tenant_id=2)
        loaded = _load_open_positions(con, tenant_id=2)
    assert len(loaded) == 1, "el MTM del kill-switch no debe ver la EXTERNAL"


def test_shadow_load_closed_excludes_external(db_and_cfg):
    from db.transaction import transaction
    from strategy.kill_switch_v2_shadow import _load_closed_trades

    with transaction() as con:
        _insert(con, status="closed", control_domain="EXTERNAL", tenant_id=2,
                exit_ts="2026-06-01T00:00:00+00:00", pnl_usd=-5.0)
        _insert(con, status="closed", control_domain="INTERNAL", tenant_id=2,
                exit_ts="2026-06-02T00:00:00+00:00", pnl_usd=3.0)
        loaded = _load_closed_trades(con, tenant_id=2)
    assert len(loaded) == 1, "los baselines del kill-switch no deben incluir cierres EXTERNAL"


# ─────────────────────────────────────────────────────────────────────────────
# 4. db_get_positions — filtro opcional control_domain (lo usa el agente)
# ─────────────────────────────────────────────────────────────────────────────


def test_db_get_positions_control_domain_filter(db_and_cfg):
    from db.transaction import transaction
    from db.positions import db_get_positions

    with transaction() as con:
        _insert(con, status="open", control_domain="EXTERNAL", tenant_id=2)
        _insert(con, status="open", control_domain="INTERNAL", tenant_id=2)
        internal_only = db_get_positions(con, status="open", tenant_id=2, control_domain="INTERNAL")
        all_rows = db_get_positions(con, status="open", tenant_id=2)
    assert len(internal_only) == 1, "el filtro INTERNAL debe excluir EXTERNAL"
    assert len(all_rows) == 2, "sin filtro, el listado general (la vista) ve ambas"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Agente — get_positions no presenta EXTERNAL como gobernable
# ─────────────────────────────────────────────────────────────────────────────


def test_agent_get_positions_excludes_external(db_and_cfg):
    from db.transaction import transaction
    from api.agent.tools.handlers import get_positions

    with transaction() as con:
        _insert(con, status="open", control_domain="EXTERNAL", tenant_id=2)
        _insert(con, status="open", control_domain="INTERNAL", tenant_id=2)

    result = get_positions(tenant_id=2)
    assert len(result["positions"]) == 1, "el copiloto no debe ver la EXTERNAL como posición suya"


# ─────────────────────────────────────────────────────────────────────────────
# 6. DELETE rechaza EXTERNAL (no se cancela lo que corrió y tiene P&L)
# ─────────────────────────────────────────────────────────────────────────────


def test_delete_position_rejects_external(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import db.connection as dbconn
    import btc_api

    db_path = str(tmp_path / "del.db")
    monkeypatch.setattr(dbconn, "DB_FILE", db_path)
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    from db.transaction import transaction
    with transaction() as con:
        ext_id = _insert(con, status="open", control_domain="EXTERNAL", tenant_id=99)

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "test-key"}))
    import api.config as _ac
    for mod, attr in ((btc_api, "CONFIG_FILE"), (_ac, "CONFIG_FILE"),
                      (btc_api, "DEFAULTS_FILE"), (_ac, "DEFAULTS_FILE"),
                      (btc_api, "SECRETS_FILE"), (_ac, "SECRETS_FILE")):
        val = str(cfg_path) if "CONFIG" in attr else str(tmp_path / f"no_{attr}.json")
        monkeypatch.setattr(mod, attr, val, raising=False)

    from btc_api import app
    client = TestClient(app)
    r = client.delete(f"/positions/{ext_id}", headers={"X-API-Key": "test-key"})
    assert r.status_code == 409, f"DELETE de EXTERNAL debe ser 409; got {r.status_code} {r.text}"
