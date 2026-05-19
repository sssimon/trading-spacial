"""Agent tool layer — Phase 1 of epic #400.

This package owns the read-only tools the copilot can call. The pattern,
locked in pre-reg §5:

  - schemas.py  — Pydantic models for every tool's input.
  - handlers.py — implementations. Every handler is keyword-only and
                  REQUIRES `tenant_id: int`. Server-side wiring binds
                  tenant_id from the JWT before dispatching; the model
                  never sees nor supplies it.
  - registry.py — catalog mapping tool name → (schema, handler, doc,
                  surface allowlist). The Phase 2 conversation core
                  reads this to build the per-turn tool list.

Propose / side-effect tools (close_position, reactivate_symbol,
apply_tune) land in Phase 3 as a separate sub-module so the security
boundary is visually obvious.
"""
