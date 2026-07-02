from scanner.baseline.ensemble import PaperPortfolio, seed_pick, M
from scanner.baseline.ladder import HORIZON


def _bars(universe, price):
    return {s: {"open": price, "high": price, "low": price, "close": price} for s in universe}


def test_seed_pick_reproducible_and_seeded():
    uni = [f"S{i}" for i in range(50)]
    assert seed_pick(uni, "2026-07-02", 3, M) == seed_pick(uni, "2026-07-02", 3, M)
    # semillas distintas -> selección distinta (con universo grande)
    assert seed_pick(uni, "2026-07-02", 3, M) != seed_pick(uni, "2026-07-02", 7, M)


def test_flat_market_returns_zero_pnl():
    # precio plano 30+ días => cada posición realiza runner 0% => cap vuelve a 1.0
    uni = [f"S{i}" for i in range(50)]
    p = PaperPortfolio()
    for d in range(HORIZON + 2):
        p.advance_day(f"2026-07-{d+1:02d}", _bars(uni, 100.0), uni, seed=1)
    assert abs(p.cap - 1.0) < 1e-6
    assert p.open_pos == [] or all(pp["bars_left"] > 0 for pp in p.open_pos)


def test_frozen_tier_blocks_new_entries():
    # forzar drawdown fuerte -> el kill-switch (agresivo) congela -> no abre nuevas
    uni = [f"S{i}" for i in range(50)]
    p = PaperPortfolio()
    p.cap = 1.0
    p.eq = [1.0, 1.0, 1.0]
    # inyectar una caída del 20% respecto al pico rodante
    p.cap = 0.80
    p.advance_day("2026-08-01", _bars(uni, 100.0), uni, seed=1)
    # con cap 0.80 vs pico ~1.0 => dd -20% <= frozen (~-10.5%) => FROZEN => sin nuevas
    assert len(p.open_pos) == 0
