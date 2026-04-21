"""Jinja2 template loader + render helper.

Templates are named '<event_type>.<channel>.j2' under notifier/templates/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from notifier.events import _BaseEvent


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


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
