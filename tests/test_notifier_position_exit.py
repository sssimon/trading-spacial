"""PositionExitEvent + position_exit.telegram.j2 template tests (#162 PR B)."""


def test_position_exit_event_required_fields():
    from notifier import PositionExitEvent
    ev = PositionExitEvent(symbol="BTC", direction="LONG", exit_reason="TP",
                            entry_price=50_000, exit_price=55_000,
                            pnl_usd=100.0, pnl_pct=10.0)
    assert ev.event_type == "position_exit"
    assert ev.priority == "info"  # winner
    assert "BTC" in ev.dedupe_key
    assert "TP" in ev.dedupe_key


def test_position_exit_loser_has_warning_priority():
    from notifier import PositionExitEvent
    ev = PositionExitEvent(symbol="DOGE", direction="SHORT", exit_reason="SL",
                            entry_price=1.0, exit_price=1.2,
                            pnl_usd=-50.0, pnl_pct=-5.0)
    assert ev.priority == "warning"


def test_position_exit_telegram_template_tp_winner():
    from notifier._templates import render
    from notifier import PositionExitEvent
    ev = PositionExitEvent(symbol="BTC", direction="LONG", exit_reason="TP",
                            entry_price=50_000, exit_price=55_000,
                            pnl_usd=500.0, pnl_pct=10.0)
    msg = render(ev, channel="telegram")
    assert "🎯" in msg
    assert "BTC" in msg
    assert "LONG" in msg
    assert "TP hit" in msg
    assert "+500.00" in msg


def test_position_exit_telegram_template_sl_loser():
    from notifier._templates import render
    from notifier import PositionExitEvent
    ev = PositionExitEvent(symbol="DOGE", direction="SHORT", exit_reason="SL",
                            entry_price=1.0, exit_price=1.2,
                            pnl_usd=-50.0, pnl_pct=-20.0)
    msg = render(ev, channel="telegram")
    assert "🛑" in msg
    assert "SL hit" in msg
    assert "-50.00" in msg


def test_position_exit_telegram_template_time_limit():
    """TIME_LIMIT exit_reason renders the time-limit branch (not the generic else)."""
    from notifier._templates import render
    from notifier import PositionExitEvent
    ev = PositionExitEvent(symbol="ADAUSDT", direction="LONG", exit_reason="TIME_LIMIT",
                            entry_price=0.50, exit_price=0.51,
                            pnl_usd=20.0, pnl_pct=2.0)
    msg = render(ev, channel="telegram")
    assert "⏰" in msg
    assert "Time-limit hit" in msg
    assert "ADAUSDT" in msg
    assert "Position closed" not in msg, (
        "TIME_LIMIT must hit its own branch, not fall through to the else"
    )


def test_position_exit_dedupe_key_includes_exit_price():
    """Two closures on the same symbol with different exit prices must dedupe
    independently (otherwise a second close after re-entry would be suppressed)."""
    from notifier import PositionExitEvent
    e1 = PositionExitEvent(symbol="BTC", direction="LONG", exit_reason="TP",
                            exit_price=55_000, pnl_usd=100, pnl_pct=10)
    e2 = PositionExitEvent(symbol="BTC", direction="LONG", exit_reason="TP",
                            exit_price=56_000, pnl_usd=120, pnl_pct=12)
    assert e1.dedupe_key != e2.dedupe_key
