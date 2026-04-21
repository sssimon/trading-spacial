"""Template loader renders per (event_type, channel) combination.
Renders must match the current Telegram message format for backward compat
(the snapshot test in Task 9 will enforce byte-level parity for signals)."""


def test_render_signal_telegram_includes_symbol_score_direction():
    from notifier._templates import render
    from notifier import SignalEvent
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    msg = render(ev, channel="telegram")
    assert "BTCUSDT" in msg
    assert "6" in msg
    assert "LONG" in msg


def test_render_health_telegram_flags_transition():
    from notifier._templates import render
    from notifier import HealthEvent
    ev = HealthEvent(symbol="JUPUSDT", from_state="REDUCED", to_state="PAUSED",
                      reason="3mo_consec_neg", metrics={"pnl_30d": -500})
    msg = render(ev, channel="telegram")
    assert "JUPUSDT" in msg
    assert "PAUSED" in msg
    assert "3mo_consec_neg" in msg


def test_render_infra_telegram_critical():
    from notifier._templates import render
    from notifier import InfraEvent
    ev = InfraEvent(component="scanner", severity="critical", message="died")
    msg = render(ev, channel="telegram")
    assert "scanner" in msg
    assert "critical" in msg.lower()
    assert "died" in msg


def test_render_system_telegram():
    from notifier._templates import render
    from notifier import SystemEvent
    ev = SystemEvent(kind="startup", message="API online")
    msg = render(ev, channel="telegram")
    assert "startup" in msg
    assert "API online" in msg


def test_unknown_template_raises():
    import pytest
    from notifier._templates import render
    from notifier import SignalEvent
    ev = SignalEvent(symbol="X", score=1, direction="LONG",
                     entry=1.0, sl=1.0, tp=1.0)
    with pytest.raises(FileNotFoundError):
        render(ev, channel="sms")  # no template for sms
