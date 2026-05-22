"""Signal precision regression — entry/SL/TP must keep crypto-grade
precision through scanner → DB → template rendering.

Surfaced when papá saw signal prices truncated to 2 decimals on
trading.sdar.dev: `round(price, 2)` in btc_scanner.py was collapsing
sub-cent SL/TP precision (catastrophic for small-value tokens, lossy
for everything ≥ $1).
"""
from __future__ import annotations

import json

import pytest

from notifier._templates import fmt_price


# ---------------------------------------------------------------------------
# fmt_price filter — adaptive precision
# ---------------------------------------------------------------------------


class TestFmtPriceTiers:
    """The four tiers cover every crypto price range we care about
    without dropping precision or showing 8-decimal trailing zeros."""

    def test_large_token_uses_2dp_with_thousands_separator(self):
        # BTC tier — humans want "80,000.50", not "80000.5".
        assert fmt_price(80000.5) == "80,000.50"
        assert fmt_price(80000.50123) == "80,000.50"  # 2dp here is fine

    def test_mid_token_strips_trailing_zeros_up_to_4dp(self):
        # RUNE ~$5.4203 — 4dp is enough, but no trailing zeros.
        assert fmt_price(5.4203) == "5.4203"
        assert fmt_price(5.42) == "5.42"
        assert fmt_price(5.4) == "5.4"
        assert fmt_price(5.0) == "5"

    def test_sub_dollar_token_uses_up_to_6dp(self):
        # DOGE / XLM tier — half-cent precision matters.
        assert fmt_price(0.15234) == "0.15234"
        assert fmt_price(0.5) == "0.5"
        assert fmt_price(0.05) == "0.05"

    def test_micro_token_uses_up_to_8dp(self):
        # SHIB / sub-cent tokens — collapsing to 2dp would have
        # shown "$0.00" pre-fix. This is the catastrophic case.
        assert fmt_price(0.00012345) == "0.00012345"
        assert fmt_price(0.001) == "0.001"
        assert fmt_price(0.00000001) == "0.00000001"


class TestFmtPriceEdgeCases:
    def test_zero_is_zero(self):
        assert fmt_price(0) == "0"
        assert fmt_price(0.0) == "0"

    def test_negative_value(self):
        assert fmt_price(-5.4203) == "-5.4203"
        assert fmt_price(-80000.5) == "-80,000.50"

    def test_none_returns_em_dash(self):
        # Filter MUST NOT raise on missing fields — a 500 in a
        # notification render is worse than a "—" in the message.
        assert fmt_price(None) == "—"

    def test_non_numeric_returns_em_dash(self):
        assert fmt_price("not a number") == "—"
        assert fmt_price({}) == "—"

    def test_accepts_int_input(self):
        # Some upstream values arrive as Python ints (e.g. score-tier
        # USD thresholds). Must format the same as floats.
        assert fmt_price(80000) == "80,000.00"


# ---------------------------------------------------------------------------
# Signal templates render with full precision (no silent truncation)
# ---------------------------------------------------------------------------


def _render(event, channel):
    from notifier._templates import render
    return render(event, channel)


def _signal_event(entry, sl, tp):
    from notifier.events import SignalEvent
    return SignalEvent(
        symbol="RUNEUSDT", score=5, direction="LONG",
        entry=entry, sl=sl, tp=tp,
        health_state="NORMAL",
    )


class TestSignalTemplates:
    def test_telegram_renders_full_precision_entry_sl_tp(self):
        msg = _render(_signal_event(5.4203, 5.10, 6.0), "telegram")
        # 5.4203 must NOT be "5.42" — that's the pre-fix bug.
        assert "5.4203" in msg
        assert "5.42" not in msg.replace("5.4203", "")
        assert "5.1" in msg  # SL — trailing zero stripped from 5.10
        assert "6" in msg

    def test_webhook_emits_json_with_raw_precision(self):
        msg = _render(_signal_event(5.4203, 5.10, 6.0), "webhook")
        # Parse the rendered JSON — it must be valid + numeric.
        parsed = json.loads(msg)
        assert parsed["entry"] == 5.4203
        assert parsed["sl"] == 5.10
        assert parsed["tp"] == 6.0

    def test_telegram_small_token_no_zero_collapse(self):
        # The catastrophic pre-fix case: a $0.00012345 token rendered
        # as "$0.00". Now must preserve the value end-to-end.
        msg = _render(_signal_event(0.00012345, 0.00012000, 0.00013000), "telegram")
        assert "0.00012345" in msg

    def test_webhook_small_token_json_is_lossless(self):
        msg = _render(_signal_event(0.00012345, 0.00012, 0.00013), "webhook")
        parsed = json.loads(msg)
        assert parsed["entry"] == pytest.approx(0.00012345)


# ---------------------------------------------------------------------------
# Position exit templates likewise preserve precision
# ---------------------------------------------------------------------------


def _exit_event(entry_price, exit_price, pnl_usd=10.0, pnl_pct=2.5):
    from notifier.events import PositionExitEvent
    return PositionExitEvent(
        symbol="RUNEUSDT", direction="LONG", exit_reason="TP",
        entry_price=entry_price, exit_price=exit_price,
        pnl_usd=pnl_usd, pnl_pct=pnl_pct,
    )


class TestPositionExitTemplates:
    def test_telegram_keeps_entry_exit_precision(self):
        msg = _render(_exit_event(5.4203, 5.6789), "telegram")
        assert "5.4203" in msg
        assert "5.6789" in msg

    def test_webhook_emits_lossless_json(self):
        msg = _render(_exit_event(5.4203, 5.6789), "webhook")
        parsed = json.loads(msg)
        assert parsed["entry_price"] == 5.4203
        assert parsed["exit_price"] == 5.6789
        # pnl_usd / pnl_pct remain at 2dp — those are USD amounts and
        # percentages, where 2dp is the correct convention.
        assert parsed["pnl_usd"] == 10.0
        assert parsed["pnl_pct"] == 2.5


# ---------------------------------------------------------------------------
# Scanner no longer rounds entry / SL / TP to 2dp
# ---------------------------------------------------------------------------


class TestScannerPriceRoundingTier:
    """Statically verify btc_scanner.py uses round(_, 8) for every
    place that builds the signal-payload price/sl/tp. The actual
    end-to-end signal-generation flow is exercised by the integration
    tests in test_scanner.py — here we just pin the source invariant."""

    def test_btc_scanner_uses_8dp_for_price_rounding(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "btc_scanner.py"
        text = src.read_text(encoding="utf-8")
        # No `round(price, 2)` or `round(price + sl_dist, 2)` left for
        # the signal-payload prices. The diagnostic indicators (SMA,
        # BB) still use round(_, 2) — those are NOT in the signal
        # payload that drives the user's order.
        assert "round(price, 2)" not in text, (
            "btc_scanner.py still rounds signal price to 2dp; "
            "use round(_, 8) for crypto precision."
        )
        assert "round(price + sl_dist, 2)" not in text
        assert "round(price - sl_dist, 2)" not in text
        assert "round(price + tp_dist, 2)" not in text
        assert "round(price - tp_dist, 2)" not in text
