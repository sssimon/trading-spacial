import json
import sqlite3

import pytest

from db.transaction import transaction


@pytest.fixture
def trials_db(tmp_path, monkeypatch):
    """Point the DB layer at a throwaway file and reset the module's
    once-per-process schema flag so each test starts clean."""
    db_path = tmp_path / "signals_test.db"
    monkeypatch.setattr("btc_api.DB_FILE", str(db_path))
    import db.trials
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    return db_path


def _all_trials():
    with transaction() as con:
        return [dict(r._mapping) for r in con.execute(
            "SELECT * FROM trials ORDER BY id"
        ).fetchall()]


def test_claim_trial_inserts_pending_row(trials_db):
    from db.trials import claim_trial

    tid = claim_trial(
        source="grid_search_tf",
        symbol="BTCUSDT",
        combo={"atr_sl_mult": 1.0, "atr_tp_mult": 3.0},
        window_label="2022-01-01..2022-04-01",
    )

    rows = _all_trials()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == tid
    assert row["status"] == "pending"
    assert row["source"] == "grid_search_tf"
    assert row["symbol"] == "BTCUSDT"
    assert row["study_type"] == "exploratory"
    assert row["finalized_ts"] is None
    assert json.loads(row["combo_json"])["atr_sl_mult"] == 1.0
    assert row["claimed_ts"]  # non-empty ISO timestamp


def test_claim_trial_study_type_can_be_confirmatory(trials_db):
    from db.trials import claim_trial

    claim_trial(
        source="signal_calibration_sweep",
        combo={"vol_target": 0.30},
        study_type="confirmatory",
    )
    assert _all_trials()[0]["study_type"] == "confirmatory"
