from tools.ks_stress_replay.overlays import NoneOverlay


def test_none_overlay_always_takes_full_size():
    ov = NoneOverlay()
    assert ov.decide("BTCUSDT", "2022-05-10T00:00:00+00:00") == (False, 1.0)
    # record_close is a no-op and must not raise
    ov.record_close("BTCUSDT", "2022-05-11T00:00:00+00:00", -50.0, "SL")
