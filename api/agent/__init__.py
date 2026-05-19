"""Agent / copiloto package.

Owns the new /agent/* surface introduced by the production-grade rewrite.

Pre-registro: docs/superpowers/specs/es/2026-05-19-trading-copilot-production-grade-pre-reg.md
Epic: #400.

Phase 0 (this commit) introduces the package + GET /agent/status only.
Phase 1 adds the read-only tool layer + audit tables.
Phase 2 delivers the conversation core (SSE streaming) that replaces
/agent/chat. Phase 3 adds the propose/confirm side-effect tools.
"""
