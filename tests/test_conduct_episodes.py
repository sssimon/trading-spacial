"""Tests de la tabla conduct_episodes + helpers (instrumento, Fase 1). Spec §8."""
import sqlite3

from db.conduct_episodes import db_put_episode, db_get_episodes


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE conduct_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER, symbol TEXT NOT NULL, tenant_id INTEGER,
            entry_ts TEXT NOT NULL, exit_ts TEXT,
            procedencia TEXT NOT NULL,
            entry_en_zona INTEGER, sl_respetado INTEGER, adherencia_be INTEGER,
            rungs_honrados INTEGER, cierre_en_plan INTEGER, hold_hours REAL,
            close_reason TEXT, plan_json TEXT, reproduced INTEGER NOT NULL,
            created_ts TEXT NOT NULL
        )""")
    return con


def _conduct():
    return {"entry_en_zona": True, "sl_respetado": True, "adherencia_be": None,
            "rungs_honrados": 2, "escalono": True, "cierre_en_plan": True,
            "hold_hours": 48.0, "close_reason": "SL_HIT", "procedencia": "observado"}


def test_put_y_get_roundtrip():
    con = _con()
    db_put_episode(con, position_id=7, symbol="BTCUSDT", tenant_id=2,
                   entry_ts="2026-01-01T00:00:00+00:00", exit_ts="2026-01-03T00:00:00+00:00",
                   conduct=_conduct(), plan_json="{}", reproduced=True,
                   created_ts="2026-06-12T00:00:00+00:00")
    rows = db_get_episodes(con, tenant_id=2)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["rungs_honrados"] == 2
    assert rows[0]["reproduced"] == 1


def test_adherencia_be_none_se_guarda_como_null():
    con = _con()
    db_put_episode(con, position_id=7, symbol="ETHUSDT", tenant_id=2,
                   entry_ts="2026-01-01T00:00:00+00:00", exit_ts=None,
                   conduct=_conduct(), plan_json="{}", reproduced=False,
                   created_ts="2026-06-12T00:00:00+00:00")
    rows = db_get_episodes(con, tenant_id=2)
    assert rows[0]["adherencia_be"] is None
    assert rows[0]["reproduced"] == 0


def test_get_filtra_por_tenant():
    con = _con()
    for t in (2, 3):
        db_put_episode(con, position_id=None, symbol="ADAUSDT", tenant_id=t,
                       entry_ts="2026-01-01T00:00:00+00:00", exit_ts=None,
                       conduct=_conduct(), plan_json="{}", reproduced=True,
                       created_ts="2026-06-12T00:00:00+00:00")
    assert len(db_get_episodes(con, tenant_id=2)) == 1
