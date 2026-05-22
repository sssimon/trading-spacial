"""Jinja2 template loader + render helper.

Templates are named '<event_type>.<channel>.j2' under notifier/templates/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from notifier.events import _BaseEvent


def fmt_price(value: Any) -> str:
    """Format a crypto price with adaptive precision so we never silently
    drop sub-cent information on small-value tokens.

    Why this exists: the old `"%.2f"|format(entry)` in the j2 templates
    truncated entries / SL / TP to 2 decimals. For a token at $0.001234,
    that turned into $0.00 — losing the entire signal. Even for $5.4203
    RUNE, the SL/TP precision was getting chopped to 2dp ($5.42), which
    is the difference between a real entry and a missed one.

    Tiers (chosen for the curated 10 symbols' price range; survives
    smaller tokens too):

      ≥ 100   → 2 decimals, thousand separators (BTC 80,000.50)
      ≥ 1     → up to 4 decimals, trailing zeros stripped (RUNE 5.4203)
      ≥ 0.01  → up to 6 decimals (DOGE 0.15234)
      < 0.01  → up to 8 decimals (sub-cent tokens)

    None / non-numeric input returns "—" (jinja's StrictUndefined would
    normally raise, but a filter that swallows a missing field
    cleanly is safer than 500-ing a notification render).
    """
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    if f == 0:
        return "0"
    sign = "-" if f < 0 else ""
    abs_f = abs(f)
    if abs_f >= 100:
        return f"{sign}{abs_f:,.2f}"
    if abs_f >= 1:
        return f"{sign}{abs_f:.4f}".rstrip("0").rstrip(".")
    if abs_f >= 0.01:
        return f"{sign}{abs_f:.6f}".rstrip("0").rstrip(".")
    return f"{sign}{abs_f:.8f}".rstrip("0").rstrip(".")


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["fmt_price"] = fmt_price


def render(event: _BaseEvent, channel: str) -> str:
    """Render an event through the appropriate <event_type>.<channel>.j2 template."""
    template_name = f"{event.event_type}.{channel}.j2"
    template_path = _TEMPLATE_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(
            f"No template for event_type={event.event_type!r} channel={channel!r} "
            f"(looked for {template_name})"
        )
    template = _env.get_template(template_name)
    return template.render(**event.to_dict()).strip()
