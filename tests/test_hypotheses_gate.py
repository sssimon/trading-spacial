import json
import sqlite3
from datetime import datetime, timezone

import pytest

from db.transaction import transaction


def _T():
    """Fixed 'today' before the deflation decay date — keeps the floor active."""
    return datetime(2026, 6, 1, tzinfo=timezone.utc)


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


# ---------------------------------------------------------------------------
# Task 2: lock_hypothesis — provenance (4a) + complete-claim (4e) + seal
# ---------------------------------------------------------------------------

from db.trials import claim_trial, finalize_trial


def _draft(**overrides):
    """A complete, lockable draft. Override single fields per test."""
    from db.hypotheses import claim_hypothesis
    kw = dict(
        strategy_config={"atr_sl_mult": 1.0}, symbols=["BTCUSDT"],
        window_label="2025-04-30..2026-04-30", metric="net_pnl",
        threshold=0.0, direction=">", deflated_metric="sharpe_deflated",
        deflated_threshold=0.95, cand_sharpe=3.0, cand_n_returns=200,
        cand_skew=0.0, cand_kurt_raw=3.0, source_note="t",
    )
    kw.update(overrides)
    return claim_hypothesis(**kw)


def _register_matching_ok_trial(strategy_config):
    """Register an exploratory ok trial whose combo == strategy_config so the
    candidate's config_hash has provenance, plus enough sibling trials that N
    is meaningful."""
    tid = claim_trial(source="grid_search_tf", combo=strategy_config,
                      symbol="BTCUSDT", window_label="w")
    finalize_trial(tid, status="ok", metrics={"sharpe_ratio": 3.0})
    for s in (0.5, 1.0, 1.5, 2.0):
        t = claim_trial(source="grid_search_tf", combo={"atr_sl_mult": s},
                        symbol="BTCUSDT", window_label="w")
        finalize_trial(t, status="ok", metrics={"sharpe_ratio": s})


def test_lock_refuses_without_provenance(hyp_db):
    """4a: config_hash must match an ok exploratory trial."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _draft(strategy_config={"atr_sl_mult": 9.9})  # no matching trial
    with pytest.raises(HypothesisLockError, match="provenance"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_incomplete_claim(hyp_db):
    """4e: deflated_threshold missing -> refuse. Build a draft then NULL it."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET deflated_threshold=NULL WHERE id=?", (hid,))
    with pytest.raises(HypothesisLockError, match="claim"):
        lock_hypothesis(hid, today=_T())


def test_lock_succeeds_and_seals(hyp_db):
    from db.hypotheses import lock_hypothesis
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=?, drift_check_ref=? WHERE id=?",
                    (_attest(), _attest(), hid))
    lock_hypothesis(hid, today=_T())
    r = _all_hyps()[0]
    assert r["status"] == "locked"
    assert r["locked_ts"]
    assert r["seal"]                         # sealed
    assert r["n_at_lock"] is not None        # N captured
    from db.hypotheses import _compute_seal
    assert r["seal"] == _compute_seal(r)


def test_lock_refuses_relock(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=?, drift_check_ref=? WHERE id=?",
                    (_attest(), _attest(), hid))
    lock_hypothesis(hid, today=_T())
    with pytest.raises(HypothesisLockError, match="re-lock|not 'draft'"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_when_deflated_below_threshold(hyp_db):
    """4b: a weak candidate (low Sharpe vs best-of-N) fails the deflation gate."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    # cand_sharpe barely positive, large selection population -> low DSR
    hid = _draft(strategy_config=cfg, cand_sharpe=0.05, cand_n_returns=30,
                 deflated_threshold=0.95)
    with pytest.raises(HypothesisLockError, match="deflat"):
        lock_hypothesis(hid, today=_T())


def test_lock_captures_n_at_lock_on_success(hyp_db):
    from db.hypotheses import lock_hypothesis
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)            # 5 distinct ok trials registered
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=?, drift_check_ref=? WHERE id=?",
                    (_attest(), _attest(), hid))
    lock_hypothesis(hid, today=_T())
    r = _all_hyps()[0]
    assert r["status"] == "locked"
    assert r["n_at_lock"] >= 50               # floor active before decay date


def test_lock_refuses_when_dsr_undefined(hyp_db):
    """4b fail-closed: n_returns < 2 makes the PSR/DSR undefined -> refuse, even
    with a high Sharpe."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg, cand_sharpe=5.0, cand_n_returns=1,
                 deflated_threshold=0.50)
    with pytest.raises(HypothesisLockError, match="deflation|degenerate|undefined"):
        lock_hypothesis(hid, today=_T())


# ---------------------------------------------------------------------------
# Task 4: lock_hypothesis — walk-forward (4c) + drift (4d) attested refs
# ---------------------------------------------------------------------------

def _attest(verdict="pass"):
    return json.dumps({"ref": "data/retune/x/report.md", "verdict": verdict,
                       "ts": "2026-05-30T00:00:00+00:00"})


def _lockable(**overrides):
    """A draft that passes provenance + deflation, with both attested refs set
    to 'pass'. Override to break one ref."""
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)
    wf = overrides.get("walkforward_ref", _attest())
    dr = overrides.get("drift_check_ref", _attest())
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=?, drift_check_ref=? WHERE id=?",
                    (wf, dr, hid))
    return hid


def test_lock_refuses_failed_walkforward(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _lockable(walkforward_ref=_attest(verdict="fail"))
    with pytest.raises(HypothesisLockError, match="walk.?forward"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_failed_drift(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _lockable(drift_check_ref=_attest(verdict="fail"))
    with pytest.raises(HypothesisLockError, match="drift"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_missing_refs(hyp_db):
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)  # refs never set -> NULL
    with pytest.raises(HypothesisLockError, match="walk.?forward|drift"):
        lock_hypothesis(hid, today=_T())


def test_lock_succeeds_with_passing_refs(hyp_db):
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    assert _all_hyps()[0]["status"] == "locked"


def test_lock_refuses_non_dict_json_ref(hyp_db):
    """A valid-but-non-dict JSON ref must raise HypothesisLockError, not leak an
    AttributeError past the gate."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    hid = _lockable(walkforward_ref=json.dumps("pass"))  # a JSON string, not an object
    with pytest.raises(HypothesisLockError, match="walk.?forward"):
        lock_hypothesis(hid, today=_T())


def test_lock_refuses_when_only_drift_ref_missing(hyp_db):
    """Independent coverage: walk-forward passes, drift_check_ref NULL -> refuse on drift."""
    from db.hypotheses import lock_hypothesis, HypothesisLockError
    cfg = {"atr_sl_mult": 1.0}
    _register_matching_ok_trial(cfg)
    hid = _draft(strategy_config=cfg, cand_sharpe=4.0, cand_n_returns=500,
                 deflated_threshold=0.50)
    with transaction() as con:
        con.execute("UPDATE hypotheses SET walkforward_ref=? WHERE id=?", (_attest(), hid))
        # drift_check_ref left NULL
    with pytest.raises(HypothesisLockError, match="drift"):
        lock_hypothesis(hid, today=_T())


# ---------------------------------------------------------------------------
# Task 5: Immutability — frozen-field trigger + seal recompute
# ---------------------------------------------------------------------------

def _now_str():
    from db.hypotheses import _now
    return _now()


def test_locked_row_frozen_field_update_is_aborted_by_trigger(hyp_db):
    """Direct UPDATE of a frozen field on a locked row must RAISE (DB trigger).
    SQLite RAISE(ABORT, ...) surfaces as sqlite3.IntegrityError in Python."""
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    with pytest.raises(sqlite3.IntegrityError):
        with transaction() as con:
            con.execute("UPDATE hypotheses SET threshold=999 WHERE id=?", (hid,))


def test_locked_row_lifecycle_field_update_is_allowed(hyp_db):
    """Lifecycle columns (fire_authorized_ts, fired_ts, status, verdict,
    realized_metric) must remain writable after lock — the trigger guards only
    frozen fields."""
    from db.hypotheses import lock_hypothesis
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    with transaction() as con:                       # must not raise
        con.execute("UPDATE hypotheses SET fire_authorized_ts=? WHERE id=?",
                    (_now_str(), hid))
    assert _all_hyps()[0]["fire_authorized_ts"]


def test_seal_detects_tamper_via_recompute(hyp_db):
    """If a frozen field is changed by a path that bypasses the trigger, the
    recomputed seal no longer matches the stored seal."""
    from db.hypotheses import lock_hypothesis, _compute_seal
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    row = _all_hyps()[0]
    tampered = dict(row)
    tampered["threshold"] = 999
    assert _compute_seal(tampered) != row["seal"]


def test_trigger_guards_exactly_frozen_fields_plus_seal(hyp_db):
    """The trigger's hard-coded column list must stay in sync with _FROZEN_FIELDS
    (+ seal). SQLite triggers can't iterate a Python tuple, so this test is the
    only thing preventing silent divergence between the seal and the trigger."""
    import re
    from db.hypotheses import _FROZEN_FIELDS
    # ensure the schema/trigger exist
    from db.hypotheses import _ensure_schema
    _ensure_schema()
    with transaction() as con:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='hypotheses_frozen_after_lock'"
        ).fetchone()
    assert row is not None, "trigger hypotheses_frozen_after_lock does not exist"
    found = set(re.findall(r"NEW\.(\w+)\s+IS NOT", row["sql"]))
    expected = set(_FROZEN_FIELDS) | {"seal"}
    assert found == expected, (
        f"trigger guards {found - expected} extra and misses {expected - found}")


# ---------------------------------------------------------------------------
# Task 6: authorize_fire — cooldown gate + deliberate act
# ---------------------------------------------------------------------------

from datetime import timedelta


def test_authorize_fire_refuses_before_cooldown(hyp_db):
    from db.hypotheses import lock_hypothesis, authorize_fire, FireAuthorizationError
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    locked_at = datetime.fromisoformat(_all_hyps()[0]["locked_ts"])
    with pytest.raises(FireAuthorizationError, match="cooldown"):
        authorize_fire(hid, now=locked_at + timedelta(hours=1))


def test_authorize_fire_refuses_unlocked(hyp_db):
    from db.hypotheses import authorize_fire, FireAuthorizationError
    hid = _draft(strategy_config={"atr_sl_mult": 1.0})  # still draft
    with pytest.raises(FireAuthorizationError, match="locked"):
        authorize_fire(hid, now=_T())


def test_authorize_fire_sets_timestamp_after_cooldown(hyp_db):
    from db.hypotheses import lock_hypothesis, authorize_fire
    hid = _lockable()
    lock_hypothesis(hid, today=_T())
    locked_at = datetime.fromisoformat(_all_hyps()[0]["locked_ts"])
    authorize_fire(hid, now=locked_at + timedelta(hours=25))
    assert _all_hyps()[0]["fire_authorized_ts"]
