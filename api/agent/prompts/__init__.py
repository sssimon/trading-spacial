"""Agent system-prompt blocks — Phase 2 of epic #400.

The system prompt is built as a list of typed `text` blocks, every block
marked `cache_control: {type: "ephemeral"}`. Anthropic's prompt-caching
beta keys off the byte-exact prefix; arrangement order matters and is
locked in pre-reg §7:

  1. system.PERSONA_AND_SAFETY  — most stable, lives at the front
  2. tool_docs(registry)         — stable per feature deploy
  3. system.INVARIANTS           — stable per config edit
  4. surfaces.for_surface(name)  — varies per surface

Block 1 is hardcoded in `system.py`. Block 2 is generated from the
tool registry at request time. Block 3 is hardcoded. Block 4 lives in
`surfaces.py`, one micro-prompt per surface.

Anything dynamic (timestamps, UUIDs, conversation-specific context)
goes AFTER the last cache breakpoint, in the `messages` array — see
pre-reg §7.5 silent invalidators.
"""

from api.agent.prompts.system import (  # noqa: F401
    PERSONA_AND_SAFETY,
    INVARIANTS,
    build_tool_docs,
    build_system_blocks,
)
from api.agent.prompts.surfaces import (  # noqa: F401
    SURFACE_PROMPTS,
    for_surface,
)
