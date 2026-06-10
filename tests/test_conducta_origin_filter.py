"""BNC-12: el read-model de CONDUCTA excluye AUTO_DERIVED; el equity NO.

Spec: 2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §2 (Task 2).

El filtro `origin IN ('SIGNAL','OPERATOR')` vive en el QUERY del fetcher
(report.py:_QUERY + get_closed_trades vía db_get_positions(origin_in=...)),
NO en episode.py (proyección pura). El equity (compute_real_equity) NO se
filtra — AUTO_DERIVED es observabilidad y SÍ cuenta para el valor real.

OPERATOR es EXTERNAL pero SÍ entra a conducta (acto deliberado del operador);
por eso el filtro es por `origin`, no por `control_domain`.
"""
from __future__ import annotations

import json
import os
import sqlite3


def _fresh_db(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "conducta.db"
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


def _insert_closed(con, *, symbol, origin, control_domain, scan_id, market=None):
    con.execute(
        "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,entry_ts,"
        "exit_price,exit_ts,exit_reason,pnl_usd,pnl_pct,size_usd,qty,tenant_id,"
        "control_domain,market,origin) VALUES "
        "(?,?,'LONG','closed',100,'2026-06-01T00:00:00+00:00',110,'2026-06-02T00:00:00+00:00',"
        "'MANUAL',10,10,100,1.0,2,?,?,?)",
        (scan_id, symbol, control_domain, market, origin),
    )


def _insert_open_external(con, *, symbol, origin, market):
    con.execute(
        "INSERT INTO positions (scan_id,symbol,direction,status,entry_price,entry_ts,"
        "qty,tenant_id,control_domain,market,origin) VALUES "
        "(NULL,?,'LONG','open',100,'2026-06-01T00:00:00+00:00',2.0,2,'EXTERNAL',?,?)",
        (symbol, market, origin),
    )


def test_conducta_query_excludes_auto_derived(tmp_path):
    """report.py:_QUERY trae SIGNAL/OPERATOR pero NUNCA AUTO_DERIVED."""
    from tools.tenant_realization.report import _QUERY
    con = _fresh_db(tmp_path)
    try:
        _insert_closed(con, symbol="BTCUSDT", origin="SIGNAL", control_domain="INTERNAL", scan_id=5)
        _insert_closed(con, symbol="ETHUSDT", origin="OPERATOR", control_domain="EXTERNAL", scan_id=None)
        _insert_closed(con, symbol="BNBUSDT", origin="AUTO_DERIVED", control_domain="EXTERNAL", scan_id=None, market="SPOT")
        con.commit()
        raw = con.execute(_QUERY.format(tenant=2)).fetchone()[0]
        symbols = {p["symbol"] for p in json.loads(raw)}
    finally:
        con.close()
    assert "BTCUSDT" in symbols, "SIGNAL debe entrar a conducta"
    assert "ETHUSDT" in symbols, "OPERATOR (EXTERNAL manual) debe entrar a conducta"
    assert "BNBUSDT" not in symbols, "AUTO_DERIVED NO debe entrar a conducta (BNC-12)"


def test_db_get_positions_origin_in_excludes_auto_derived(tmp_path):
    """db_get_positions(origin_in=('SIGNAL','OPERATOR')) excluye AUTO_DERIVED."""
    from db.positions import db_get_positions
    con = _fresh_db(tmp_path)
    try:
        _insert_closed(con, symbol="BTCUSDT", origin="SIGNAL", control_domain="INTERNAL", scan_id=5)
        _insert_closed(con, symbol="BNBUSDT", origin="AUTO_DERIVED", control_domain="EXTERNAL", scan_id=None, market="SPOT")
        con.commit()
        rows = db_get_positions(con, status="closed", tenant_id=2, origin_in=("SIGNAL", "OPERATOR"))
        symbols = {r["symbol"] for r in rows}
    finally:
        con.close()
    assert symbols == {"BTCUSDT"}, f"esperado solo BTCUSDT (SIGNAL), vivo {symbols}"


def test_db_get_positions_no_origin_filter_returns_all(tmp_path):
    """Sin origin_in (default None), db_get_positions NO filtra — compat legacy."""
    from db.positions import db_get_positions
    con = _fresh_db(tmp_path)
    try:
        _insert_closed(con, symbol="BTCUSDT", origin="SIGNAL", control_domain="INTERNAL", scan_id=5)
        _insert_closed(con, symbol="BNBUSDT", origin="AUTO_DERIVED", control_domain="EXTERNAL", scan_id=None, market="SPOT")
        con.commit()
        rows = db_get_positions(con, status="closed", tenant_id=2)
        symbols = {r["symbol"] for r in rows}
    finally:
        con.close()
    assert symbols == {"BTCUSDT", "BNBUSDT"}, "sin filtro debe traer todas (compat)"


def test_get_closed_trades_excludes_auto_derived(tmp_path, monkeypatch):
    """El contexto de trades cerrados del copiloto excluye AUTO_DERIVED."""
    import btc_api
    monkeypatch.setattr(btc_api, "DB_FILE", str(tmp_path / "conducta.db"), raising=False)
    con = _fresh_db(tmp_path)
    try:
        _insert_closed(con, symbol="BTCUSDT", origin="SIGNAL", control_domain="INTERNAL", scan_id=5)
        _insert_closed(con, symbol="BNBUSDT", origin="AUTO_DERIVED", control_domain="EXTERNAL", scan_id=None, market="SPOT")
        con.commit()
    finally:
        con.close()
    from api.agent.tools.handlers import get_closed_trades
    out = get_closed_trades(tenant_id=2, window="all")
    symbols = {t["symbol"] for t in out["trades"]}
    assert "BTCUSDT" in symbols
    assert "BNBUSDT" not in symbols, "el copiloto NO debe ver AUTO_DERIVED como trade cerrado (BNC-12)"


def test_equity_includes_auto_derived(tmp_path):
    """compute_real_equity SÍ incluye AUTO_DERIVED — observabilidad, NO filtra origin (BNC-11)."""
    from api.equity import compute_real_equity
    con = _fresh_db(tmp_path)
    try:
        _insert_open_external(con, symbol="BNBUSDT", origin="AUTO_DERIVED", market="SPOT")
        con.commit()
        eq = compute_real_equity(con, tenant_id=2, price_lookup={"BNBUSDT": 600.0})
    finally:
        con.close()
    held = {h["symbol"] for h in eq["holds"]}
    assert "BNBUSDT" in held, "el equity debe incluir el hold AUTO_DERIVED (observabilidad, BNC-11)"
    assert eq["holds_value_usd"] == 1200.0  # 2.0 * 600
