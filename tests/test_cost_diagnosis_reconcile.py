from tools.cost_diagnosis.reconcile import reconcile

CORR = [("baseline", 1.0, 1.0), ("daily_basis", 1440.0, 1.0)]


def _trade(tier, pnl, move_pct, baseline_bps, daily_bps):
    return {
        "tier": tier, "pnl_usd": pnl, "observed_move_pct": move_pct,
        "costs": {"baseline": baseline_bps, "daily_basis": daily_bps},
    }


def test_re_anchor_when_a_correction_reconciles():
    # baseline over-charges winners (90bps=0.9% > 0.5% move); daily_basis (8bps) fixes it.
    per_trade = [_trade("major", 10.0, 0.5, 90.0, 8.0) for _ in range(3)]
    branch, winning, results = reconcile(per_trade, CORR)
    assert branch == "RE-ANCHOR"
    assert "daily_basis" in winning
    assert results["baseline"]["winners_exceeded"] == 3
    assert results["daily_basis"]["reconciles"] is True


def test_rebuild_when_none_reconcile():
    # even daily_basis still exceeds the winning move (move 0.05% < 0.08% cost).
    per_trade = [_trade("major", 5.0, 0.05, 90.0, 8.0) for _ in range(3)]
    branch, winning, results = reconcile(per_trade, CORR)
    assert branch == "REBUILD"
    assert winning == []


def test_no_winner_exceeded_but_band_broken_does_not_reconcile():
    # cost (40bps) never exceeds the big move (5%) on winners, but 40 > 30 band (major).
    per_trade = [_trade("major", 10.0, 5.0, 40.0, 40.0) for _ in range(3)]
    branch, winning, results = reconcile(per_trade, CORR)
    assert results["daily_basis"]["reconciles"] is False
    assert branch == "REBUILD"


def test_small_tier_uses_50bps_band():
    # 45bps round-trip: within 50 (small) but would break 30 (major). Move large so cond1 ok.
    per_trade = [_trade("small", 10.0, 5.0, 45.0, 45.0) for _ in range(3)]
    _, winning, results = reconcile(per_trade, CORR)
    assert results["daily_basis"]["reconciles"] is True
    assert "daily_basis" in winning
