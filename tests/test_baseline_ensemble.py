from scanner.baseline.ensemble import BaselineEnsemble


def _bars(universe, price):
    return {s: {"open": price, "high": price, "low": price, "close": price} for s in universe}


def test_snapshot_shape_no_picks():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=10)
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)
    snap = e.snapshot()
    assert set(snap) == {"mediana", "banda_p10", "banda_p90", "n_seeds", "tier_mediana", "last_date"}
    assert snap["n_seeds"] == 10
    assert snap["last_date"] == "2026-07-02"
    # anti-veredicto: el snapshot NO expone picks/símbolos
    import json
    assert "symbol" not in json.dumps(snap).lower()


def test_advance_day_idempotent_by_date():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=5)
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)
    snap1 = e.snapshot()
    e.advance_day("2026-07-02", _bars(uni, 100.0), uni)  # misma fecha => no-op
    assert e.snapshot() == snap1


def test_band_ordering():
    uni = [f"S{i}" for i in range(60)]
    e = BaselineEnsemble(n_seeds=20)
    for d in range(3):
        e.advance_day(f"2026-07-{d+2:02d}", _bars(uni, 100.0), uni)
    snap = e.snapshot()
    assert snap["banda_p10"] <= snap["mediana"] <= snap["banda_p90"]
