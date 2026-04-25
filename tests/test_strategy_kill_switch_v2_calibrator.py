"""Tests for strategy.kill_switch_v2_calibrator — auto-calibrator daemon (#187 #214 B4b.1)."""
import pytest


# ── B4b.1: schema smoke test ────────────────────────────────────────────────


def test_init_db_creates_kill_switch_recommendations_table(tmp_path, monkeypatch):
    """init_db must create kill_switch_recommendations with the expected columns."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()

    conn = btc_api.get_db()
    try:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(kill_switch_recommendations)"
        ).fetchall()]
    finally:
        conn.close()

    expected = {
        "id", "ts", "triggered_by", "slider_value",
        "projected_pnl", "projected_dd", "status",
        "applied_ts", "applied_by", "report_json",
    }
    assert expected.issubset(set(cols))
