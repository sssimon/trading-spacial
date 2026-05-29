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


def test_finalize_trial_ok_records_metrics_and_sharpe(trials_db):
    from db.trials import claim_trial, finalize_trial

    tid = claim_trial(source="auto_tune", combo={"x": 1}, symbol="ETHUSDT")
    finalize_trial(tid, status="ok", metrics={"sharpe_ratio": 1.42, "net_pnl": 100})

    row = _all_trials()[0]
    assert row["status"] == "ok"
    assert row["finalized_ts"]
    assert row["sharpe"] == pytest.approx(1.42)
    assert json.loads(row["metrics_json"])["net_pnl"] == 100
    assert row["error"] is None


def test_finalize_trial_failed_records_error(trials_db):
    from db.trials import claim_trial, finalize_trial

    tid = claim_trial(source="auto_tune", combo={"x": 1})
    finalize_trial(tid, status="failed", error="No data")

    row = _all_trials()[0]
    assert row["status"] == "failed"
    assert row["error"] == "No data"
    assert row["sharpe"] is None


def test_crashed_trial_leaves_pending_row(trials_db):
    """A claim with no finalize (process died) must remain countable."""
    from db.trials import claim_trial

    claim_trial(source="grid_search_tf", combo={"x": 1})
    row = _all_trials()[0]
    assert row["status"] == "pending"
    assert row["finalized_ts"] is None


def test_finalize_invalid_status_raises(trials_db):
    from db.trials import claim_trial, finalize_trial

    tid = claim_trial(source="auto_tune", combo={"x": 1})
    with pytest.raises(ValueError, match="ok.*failed"):
        finalize_trial(tid, status="done")
