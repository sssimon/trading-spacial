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
    import selection_provenance
    monkeypatch.setattr(selection_provenance, "_cache", None)
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


def test_with_write_retry_succeeds_after_transient_lock(monkeypatch):
    import db.trials
    monkeypatch.setattr(db.trials.time, "sleep", lambda s: None)  # skip real backoff
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert db.trials._with_write_retry("test", flaky) == "ok"
    assert calls["n"] == 2


def test_with_write_retry_exhaustion_raises(monkeypatch):
    import db.trials
    monkeypatch.setattr(db.trials.time, "sleep", lambda s: None)

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        db.trials._with_write_retry("test", always_locked)


def test_with_write_retry_non_lock_error_propagates_immediately(monkeypatch):
    import db.trials
    monkeypatch.setattr(db.trials.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: trials")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db.trials._with_write_retry("test", boom)
    assert calls["n"] == 1  # no retry on non-lock errors


def _read_trials_direct(db_path):
    """Read the trials table via a direct connection to `db_path`, independent
    of any monkeypatched module globals (used after installing patches that
    would break the shared transaction() path)."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM trials ORDER BY id"
        ).fetchall()]
    finally:
        con.close()


def test_ensure_trials_schema_idempotent(trials_db, monkeypatch):
    """_ensure_trials_schema must be safe to call repeatedly:
    (a) no exception on a second call,
    (b) the `trials` table + any prior rows survive,
    (c) once `_schema_ensured` is True the second call is a no-op — it issues
        no DDL (proven by monkeypatching `transaction` to blow up and confirming
        it is NOT called while the flag is already set).
    """
    import db.trials

    # First call runs the schema DDL (fixture reset _schema_ensured=False).
    db.trials._ensure_trials_schema()
    assert db.trials._schema_ensured is True

    # Insert a row so we can prove idempotency preserves prior data.
    tid = db.trials.claim_trial(source="grid_search_tf", combo={"x": 1})
    rows_before = _read_trials_direct(trials_db)
    assert len(rows_before) == 1
    assert rows_before[0]["id"] == tid

    # (c) With _schema_ensured already True, a second call must short-circuit
    # BEFORE touching the DB. Make transaction() + WAL re-set explode if reached.
    def exploding_transaction(*a, **k):
        raise AssertionError(
            "_ensure_trials_schema opened a transaction when _schema_ensured "
            "was already True — second call was NOT a no-op"
        )

    def exploding_wal(*a, **k):
        raise AssertionError("WAL re-set on no-op second call")

    monkeypatch.setattr(db.trials, "transaction", exploding_transaction)
    monkeypatch.setattr(
        db.trials, "_set_wal_mode_idempotent_with_retry", exploding_wal,
    )

    # (a) No exception even though transaction/WAL would blow up if reached.
    db.trials._ensure_trials_schema()

    # (b) Table + prior row still intact — read via a direct connection so the
    # exploding monkeypatches above don't interfere.
    rows_after = _read_trials_direct(trials_db)
    assert len(rows_after) == 1
    assert rows_after[0]["id"] == tid
    assert rows_after[0]["source"] == "grid_search_tf"


def test_ensure_trials_schema_creates_table_when_flag_reset(trials_db, monkeypatch):
    """Belt-and-suspenders for the idempotent CREATE: resetting _schema_ensured
    and calling again (e.g. a fresh process) re-issues CREATE TABLE IF NOT
    EXISTS without error and without dropping existing rows."""
    import db.trials

    db.trials._ensure_trials_schema()
    tid = db.trials.claim_trial(source="auto_tune", combo={"y": 2})

    # Simulate a fresh process: flag reset, but the table already exists on disk.
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    db.trials._ensure_trials_schema()  # CREATE TABLE IF NOT EXISTS → no-op DDL

    rows = _all_trials()
    assert len(rows) == 1
    assert rows[0]["id"] == tid


from datetime import datetime, timezone


def test_selection_population_stats_dedups_distinct_configs(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats

    # Two DISTINCT exploratory configs + one duplicate of the first.
    t1 = claim_trial(source="auto_tune", combo={"a": 1}, window_label="W")
    finalize_trial(t1, status="ok", metrics={"sharpe_ratio": 1.0})
    t2 = claim_trial(source="auto_tune", combo={"a": 2}, window_label="W")
    finalize_trial(t2, status="ok", metrics={"sharpe_ratio": 2.0})
    t3 = claim_trial(source="auto_tune", combo={"a": 1}, window_label="W")  # dup of t1
    finalize_trial(t3, status="ok", metrics={"sharpe_ratio": 1.0})

    stats = selection_population_stats()
    assert stats["n_registered"] == 2          # dup collapsed
    assert stats["sigma_sr_trials"] == pytest.approx(0.5, abs=1e-9)  # stdev of [1.0, 2.0] (population)


def test_selection_population_stats_excludes_confirmatory_and_null_sharpe(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats

    a = claim_trial(source="auto_tune", combo={"a": 1})
    finalize_trial(a, status="ok", metrics={"sharpe_ratio": 1.0})
    b = claim_trial(source="signal_calibration", combo={"a": 2}, study_type="confirmatory")
    finalize_trial(b, status="ok", metrics={"sharpe_ratio": 9.0})  # confirmatory -> excluded
    c = claim_trial(source="auto_tune", combo={"a": 3})            # pending, NULL sharpe -> excluded
    d = claim_trial(source="auto_tune", combo={"a": 4})
    finalize_trial(d, status="failed", error="x")                  # failed, NULL sharpe -> excluded

    stats = selection_population_stats()
    assert stats["n_registered"] == 1
    assert stats["sigma_sr_trials"] is None  # stdev of a single value is undefined


def test_n_effective_floor_active_then_decayed(trials_db):
    from db.trials import n_effective

    decay = datetime(2026, 11, 29, tzinfo=timezone.utc)
    # Before decay: floor wins when registry is small.
    before = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert n_effective(n_registered=10, today=before, decay_date=decay) == 50
    assert n_effective(n_registered=80, today=before, decay_date=decay) == 80  # registry wins
    # On/after decay: floor is 0.
    after = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert n_effective(n_registered=10, today=after, decay_date=decay) == 10
    assert n_effective(n_registered=0, today=after, decay_date=decay) == 0


def test_new_db_has_provenance_columns(trials_db):
    import db.trials
    db.trials._ensure_trials_schema()
    with transaction() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(trials)")}
    assert "cost_model" in cols and "selection_fingerprint" in cols


def test_migration_adds_columns_and_backfills_v2(trials_db, monkeypatch):
    import db.trials, selection_provenance
    selection_provenance._clear_cache()
    with transaction() as con:
        con.execute(
            "CREATE TABLE trials (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "claimed_ts TEXT NOT NULL, finalized_ts TEXT, source TEXT NOT NULL, "
            "study_type TEXT NOT NULL DEFAULT 'exploratory', symbol TEXT, "
            "combo_json TEXT NOT NULL, window_label TEXT, "
            "status TEXT NOT NULL DEFAULT 'pending', sharpe REAL, "
            "metrics_json TEXT, error TEXT)")
        con.execute("INSERT INTO trials (claimed_ts, source, combo_json, status, sharpe) "
                    "VALUES ('2026-05-01T00:00:00+00:00', 'auto_tune', '{\"a\":1}', 'ok', 1.2)")
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    db.trials._ensure_trials_schema()
    with transaction() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(trials)")}
        row = dict(con.execute("SELECT cost_model, selection_fingerprint FROM trials").fetchone())
    assert "selection_fingerprint" in cols
    assert row["cost_model"] == "v2"
    assert row["selection_fingerprint"] == selection_provenance.fingerprint_for_v2_sibling()


def test_migration_idempotent(trials_db, monkeypatch):
    import db.trials
    db.trials._ensure_trials_schema()
    monkeypatch.setattr(db.trials, "_schema_ensured", False)
    db.trials._ensure_trials_schema()
    with transaction() as con:
        names = [r["name"] for r in con.execute("PRAGMA table_info(trials)")]
    assert names.count("selection_fingerprint") == 1


def test_claim_trial_auto_stamps_fingerprint(trials_db):
    import db.trials, selection_provenance
    selection_provenance._clear_cache()
    db.trials.claim_trial(source="auto_tune", combo={"a": 1}, window_label="w")
    fp, _ = selection_provenance.selection_fingerprint()
    with transaction() as con:
        row = dict(con.execute("SELECT cost_model, selection_fingerprint FROM trials").fetchone())
    assert row["cost_model"] == "v3"
    assert row["selection_fingerprint"] == fp


def test_population_stats_filters_by_fingerprint(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats
    ids = [claim_trial(source="auto_tune", combo={"a": i}, window_label="w") for i in range(5)]
    for i, tid in enumerate(ids):
        finalize_trial(tid, status="ok", metrics={"sharpe": 1.0 + i})
    with transaction() as con:
        for tid in ids[:3]:
            con.execute("UPDATE trials SET selection_fingerprint='AAA' WHERE id=?", (tid,))
        for tid in ids[3:]:
            con.execute("UPDATE trials SET selection_fingerprint='BBB' WHERE id=?", (tid,))
    a = selection_population_stats(selection_fingerprint="AAA")
    b = selection_population_stats(selection_fingerprint="BBB")
    assert a["n_registered"] == 3
    assert b["n_registered"] == 2


def test_population_stats_none_pools_all_legacy(trials_db):
    from db.trials import claim_trial, finalize_trial, selection_population_stats
    for i in range(4):
        tid = claim_trial(source="auto_tune", combo={"a": i}, window_label="w")
        finalize_trial(tid, status="ok", metrics={"sharpe": 1.0 + i})
    assert selection_population_stats()["n_registered"] == 4
