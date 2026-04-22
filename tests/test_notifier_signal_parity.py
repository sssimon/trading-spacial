"""Lock the current Telegram signal message format (#162 PR A snapshot).

When Task 10 migrates btc_api call sites to notifier.notify(SignalEvent(...)),
the rendered message must match what build_telegram_message used to produce.
If this test fails, either:
  (a) the SignalEvent→telegram template drifted — fix the template, or
  (b) the legacy build_telegram_message evolved — sync the template.
"""
from notifier import SignalEvent
from notifier._templates import render


def test_signal_telegram_message_contains_required_tokens():
    """Loose contract: message must mention symbol, score, direction, entry, sl, tp."""
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    msg = render(ev, channel="telegram")
    assert "BTCUSDT" in msg
    assert "6" in msg
    assert "LONG" in msg
    assert "50000" in msg or "50,000" in msg or "50000.00" in msg
    assert "49000" in msg or "49,000" in msg or "49000.00" in msg
    assert "55000" in msg or "55,000" in msg or "55000.00" in msg


def test_signal_template_stable_for_fixed_input():
    """Guard against accidental template edits that change the output.
    If you intentionally change the format, update EXPECTED below."""
    ev = SignalEvent(symbol="BTCUSDT", score=6, direction="LONG",
                      entry=50_000.0, sl=49_000.0, tp=55_000.0)
    got = render(ev, channel="telegram")
    expected = (
        "*Signal* `BTCUSDT`\n"
        "Score: *6* (LONG)\n"
        "Entry: `50000.00` | SL: `49000.00` | TP: `55000.00`"
    )
    assert got == expected, f"template drift detected:\nexpected:\n{expected!r}\ngot:\n{got!r}"
