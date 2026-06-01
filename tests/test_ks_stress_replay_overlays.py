from tools.ks_stress_replay.overlays import NoneOverlay


def test_none_overlay_always_takes_full_size():
    ov = NoneOverlay()
    assert ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00") == (False, 1.0)
    # record_close is a no-op and must not raise
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -50.0, "SL")


from tools.ks_stress_replay.overlays import V1Overlay, V2Overlay


def _v1_cfg():
    # KillSwitchSimulator reads cfg["kill_switch"]; defaults are fine for a
    # fresh symbol (no closed trades => NORMAL => full size).
    return {"kill_switch": {}}


def test_v1_overlay_normal_tier_full_size():
    ov = V1Overlay(_v1_cfg())
    skip, factor = ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00")
    assert skip is False and factor == 1.0
    # feeding a close must not raise (exit_ts is ISO; now derived internally)
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -10.0, "SL")


def test_v2_overlay_fresh_symbol_full_size_and_slider_injected():
    ov = V2Overlay({}, slider=50.0, capital_base=1000.0)
    skip, factor = ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00")
    # Fresh portfolio (no closed trades): DD=0, tier NORMAL => full size.
    assert skip is False and factor == 1.0
    # slider was injected into the cfg the simulator built
    assert ov.sim.cfg_eff["kill_switch"]["v2"]["aggressiveness"] == 50.0


def test_v2_overlay_record_close_accumulates_portfolio_dd():
    ov = V2Overlay({}, slider=50.0, capital_base=1000.0)
    # A large loss should push the simulator's internal portfolio DD negative.
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -200.0, "SL")
    assert ov.sim._current_portfolio_dd() < 0.0
