import json
import sqlite3

import pytest

from db.transaction import transaction


@pytest.fixture
def hyp_db(tmp_path, monkeypatch):
    """Throwaway signals.db; reset both modules' once-per-process schema flags."""
    db_path = tmp_path / "signals_test.db"
    monkeypatch.setattr("btc_api.DB_FILE", str(db_path))
    import db.hypotheses
    import db.trials
    monkeypatch.setattr(db.hypotheses, "_schema_ensured", False)
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    return db_path


def _all_hyps():
    with transaction() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM hypotheses ORDER BY id"
        ).fetchall()]


def test_claim_hypothesis_inserts_draft_row(hyp_db):
    from db.hypotheses import claim_hypothesis

    hid = claim_hypothesis(
        strategy_config={"atr_sl_mult": 1.0},
        symbols=["BTCUSDT"],
        window_label="2025-04-30..2026-04-30",
        metric="net_pnl",
        threshold=0.0,
        direction=">",
        deflated_metric="sharpe_deflated",
        deflated_threshold=0.95,
        cand_sharpe=1.4,
        cand_n_returns=120,
        cand_skew=0.1,
        cand_kurt_raw=3.5,
        source_note="from grid_search_tf sweep 2026-05",
    )
    rows = _all_hyps()
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == hid
    assert r["status"] == "draft"
    assert r["locked_ts"] is None
    assert r["fired_ts"] is None
    assert r["fire_authorized_ts"] is None
    assert r["config_hash"]                      # computed at draft
    assert json.loads(r["strategy_config_json"])["atr_sl_mult"] == 1.0
    assert json.loads(r["symbols_json"]) == ["BTCUSDT"]
    assert r["deflated_threshold"] == pytest.approx(0.95)
    assert r["created_ts"]
