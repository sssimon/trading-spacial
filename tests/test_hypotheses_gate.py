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
    lock_hypothesis(hid, today=_T())
    with pytest.raises(HypothesisLockError, match="re-lock|not 'draft'"):
        lock_hypothesis(hid, today=_T())
