import os
from scanner.baseline.ensemble import BaselineEnsemble
from scanner.baseline import store


def _bars(universe, price):
    return {s: {"open": price, "high": price, "low": price, "close": price} for s in universe}


def test_persist_load_roundtrip(tmp_path):
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=8)
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)
    p = str(tmp_path / "state.json")
    store.persist(e, "2026-07-02T00:00:00Z", path=p)
    e2, gen = store.load(path=p)
    assert gen == "2026-07-02T00:00:00Z"
    assert e2.last_date == "2026-07-02"
    assert e2.snapshot() == e.snapshot()


def test_load_missing_returns_none(tmp_path):
    assert store.load(path=str(tmp_path / "nope.json")) == (None, None)
