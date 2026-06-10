"""Señal de RIESGO de holding (§7) — observabilidad, NO conducta (BNC-17).

Spec: 2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §7 (Task 6).

Lee holds EXTERNAL (OPERATOR + AUTO_DERIVED) y reporta HECHOS del holding
(underwater, age, sin_stop) — NUNCA infiere un acto (no toca scan_id /
apertura_discrecional). Si falta precio → `no_valuado` (se ABSTIENE), nunca
asume "sin riesgo" (F1). Es el sucesor honesto del "rojo de violación".
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "risk.db"
    os.environ["MIGRATE_QTY_ALLOW_BULK_QUARANTINE"] = "1"
    try:
        import btc_api
        original = btc_api.DB_FILE
        btc_api.DB_FILE = str(db_path)
        try:
            from db.schema import init_db
            init_db()
            con = sqlite3.connect(str(db_path))
            con.row_factory = sqlite3.Row
            return con
        finally:
            btc_api.DB_FILE = original
    finally:
        os.environ.pop("MIGRATE_QTY_ALLOW_BULK_QUARANTINE", None)


def _ins(con, *, symbol, origin, entry_price, entry_ts, qty=1.0, sl_price=None, market=None):
    con.execute(
        "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,entry_ts,"
        "sl_price,qty,tenant_id,control_domain,market,origin) VALUES "
        "(NULL,?,'LONG','open',?,?,?,?,2,'EXTERNAL',?,?)",
        (symbol, entry_price, entry_ts, sl_price, qty, market, origin),
    )


_NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _risk(con, price_lookup, horizon_days=14.0):
    from api.holding_risk import compute_holding_risk
    return compute_holding_risk(
        con, tenant_id=2, price_lookup=price_lookup, now=_NOW, horizon_days=horizon_days
    )


def test_underwater_past_horizon_flags_risk(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="BTCUSDT", origin="OPERATOR",
             entry_price=100.0, entry_ts="2026-06-01T00:00:00+00:00")  # 30 días
        con.commit()
        out = _risk(con, {"BTCUSDT": 90.0})  # underwater (90<100)
    finally:
        con.close()
    h = {x["symbol"]: x for x in out["holdings"]}["BTCUSDT"]
    assert h["underwater"] is True
    assert h["sin_stop"] is True
    assert h["age_days"] >= 29
    assert h["at_risk"] is True
    assert "BTCUSDT" in out["at_risk"]


def test_in_profit_not_at_risk(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="BTCUSDT", origin="OPERATOR",
             entry_price=100.0, entry_ts="2026-06-01T00:00:00+00:00")
        con.commit()
        out = _risk(con, {"BTCUSDT": 110.0})  # en ganancia
    finally:
        con.close()
    h = {x["symbol"]: x for x in out["holdings"]}["BTCUSDT"]
    assert h["underwater"] is False
    assert h["at_risk"] is False
    assert out["at_risk"] == []


def test_underwater_but_recent_not_at_risk(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="BTCUSDT", origin="OPERATOR",
             entry_price=100.0, entry_ts="2026-06-28T00:00:00+00:00")  # 3 días
        con.commit()
        out = _risk(con, {"BTCUSDT": 90.0}, horizon_days=14.0)
    finally:
        con.close()
    h = {x["symbol"]: x for x in out["holdings"]}["BTCUSDT"]
    assert h["underwater"] is True
    assert h["at_risk"] is False  # underwater pero no past-horizon


def test_missing_price_abstains_not_no_risk(tmp_path):
    """Sin precio → no_valuado (abstención), NUNCA 'sin riesgo' (F1)."""
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="PEPEUSDT", origin="AUTO_DERIVED", market="SPOT",
             entry_price=0.00001, entry_ts="2026-06-01T00:00:00+00:00")
        con.commit()
        out = _risk(con, {})  # sin precio para PEPEUSDT
    finally:
        con.close()
    assert "PEPEUSDT" in out["no_valuados"]
    # NO debe aparecer en at_risk ni afirmarse underwater/no-underwater
    assert "PEPEUSDT" not in out["at_risk"]
    h = {x["symbol"]: x for x in out["holdings"]}["PEPEUSDT"]
    assert h["valuado"] is False
    assert h.get("underwater") is None
    assert h.get("at_risk") is None


def test_reads_both_operator_and_auto_derived(tmp_path):
    """La señal lee TODAS las EXTERNAL: OPERATOR (el rojo del papá) + AUTO_DERIVED."""
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="BTCUSDT", origin="OPERATOR",
             entry_price=100.0, entry_ts="2026-06-01T00:00:00+00:00")
        _ins(con, symbol="BNBUSDT", origin="AUTO_DERIVED", market="SPOT",
             entry_price=600.0, entry_ts="2026-06-01T00:00:00+00:00")
        con.commit()
        out = _risk(con, {"BTCUSDT": 90.0, "BNBUSDT": 500.0})
    finally:
        con.close()
    syms = {x["symbol"] for x in out["holdings"]}
    assert syms == {"BTCUSDT", "BNBUSDT"}
    assert set(out["at_risk"]) == {"BTCUSDT", "BNBUSDT"}  # ambos underwater + viejos


def test_signal_never_infers_an_act(tmp_path):
    """BNC-17: la señal reporta hechos, NO actos. No expone apertura_discrecional."""
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="BTCUSDT", origin="AUTO_DERIVED", market="SPOT",
             entry_price=100.0, entry_ts="2026-06-01T00:00:00+00:00")
        con.commit()
        out = _risk(con, {"BTCUSDT": 90.0})
    finally:
        con.close()
    h = {x["symbol"]: x for x in out["holdings"]}["BTCUSDT"]
    assert "apertura_discrecional" not in h
    assert "scan_id" not in h


def test_with_stop_not_sin_stop(tmp_path):
    con = _fresh_db(tmp_path)
    try:
        _ins(con, symbol="BTCUSDT", origin="OPERATOR", entry_price=100.0,
             entry_ts="2026-06-01T00:00:00+00:00", sl_price=95.0)
        con.commit()
        out = _risk(con, {"BTCUSDT": 90.0})
    finally:
        con.close()
    h = {x["symbol"]: x for x in out["holdings"]}["BTCUSDT"]
    assert h["sin_stop"] is False
