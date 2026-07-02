from scanner.baseline.ladder import ladder_return, TPS, FRACS, DISASTER, HORIZON


def test_constants_frozen():
    assert TPS == [0.15, 0.30, 0.50, 0.90]
    assert FRACS == [0.25, 0.25, 0.20, 0.15]
    assert DISASTER == -0.50
    assert HORIZON == 30


def test_all_targets_hit_plus_runner():
    # hi_max alcanza el +90% => vende las 4 fracciones; runner (0.15 restante) al close +100%
    r = ladder_return(entry=100.0, hi_max=200.0, lo_min=95.0, close_last=200.0)
    realized = 0.25*0.15 + 0.25*0.30 + 0.20*0.50 + 0.15*0.90
    runner = (1.0 - 0.85) * (200.0 - 100.0) / 100.0
    assert abs(r - (realized + runner)) < 1e-9


def test_disaster_floor_when_no_target():
    # nunca toca +15% y el low perfora -50% => piso -0.50
    r = ladder_return(entry=100.0, hi_max=110.0, lo_min=40.0, close_last=45.0)
    assert r == DISASTER


def test_no_target_no_disaster_is_runner_close():
    # no toca ningún target ni el piso => runner completo al close
    r = ladder_return(entry=100.0, hi_max=110.0, lo_min=90.0, close_last=105.0)
    assert abs(r - 0.05) < 1e-9


def test_guards_return_none():
    assert ladder_return(0.0, 1.0, 1.0, 1.0) is None
    assert ladder_return(100.0, 100.0, 100.0, None) is None


def test_live_accumulation_equals_oneshot():
    # contrato escalera VIVA ↔ un-tiro: acumular hi_max/lo_min día a día sobre la
    # ventana da EXACTAMENTE lo mismo que ladder_return sobre los extremos completos
    highs = [100 + i for i in range(HORIZON)]      # sube a 129
    lows = [90 - (i % 5) for i in range(HORIZON)]
    close_last, entry = 125.0, 100.0
    oneshot = ladder_return(entry, max(highs), min(lows), close_last)
    hi_max, lo_min = highs[0], lows[0]
    for i in range(1, HORIZON):                    # acumulación como PaperPortfolio
        hi_max = max(hi_max, highs[i]); lo_min = min(lo_min, lows[i])
    assert ladder_return(entry, hi_max, lo_min, close_last) == oneshot
