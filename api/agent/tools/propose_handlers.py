"""Phase 3 propose handlers — the agent emits a *proposal*, never an action.

Every handler in this module:

  1. Validates that the action is GROUNDED — the position belongs to
     the tenant, the symbol is in the curated set, the tune is the
     latest pending one. If anything fails, returns {"error": "..."}
     and never signs a proposal.

  2. Calls api.agent.proposals.sign_proposal(...) to mint a fresh
     proposal_id + HMAC-signed payload. The HMAC uses
     AGENT_PROPOSAL_SECRET (env var separate from JWT_SECRET).

  3. Persists the proposal in agent_side_effects with result=NULL.

  4. Returns a dict back to the model with a `_proposal` envelope. The
     loop in api/agent/loop.py picks this up, emits a `proposal` SSE
     event (so the frontend can render the amber confirm button), and
     hands the tool_result to the model unchanged — the model only
     sees the user-facing summary, never the signed token.

The model NEVER receives nor needs the signed_payload. The frontend
holds it between the proposal event and the confirm POST.

Pre-reg §10.
"""
from __future__ import annotations

import logging
from typing import Any

from db.connection import get_db

from api.agent.proposals import (
    ACTION_APPLY_TUNE,
    ACTION_CLOSE_POSITION,
    ACTION_REACTIVATE_SYMBOL,
    ProposalError,
    persist_proposal,
    sign_proposal,
)

log = logging.getLogger("api.agent.tools.propose_handlers")


def _model_facing(proposal, summary: str) -> dict:
    """Build the shape returned to the model. The signed_payload is
    NOT in here — it travels via the proposal SSE event to the
    frontend, never via the model context. The `_proposal` envelope
    is a marker the loop reads to know it should emit a proposal event."""
    return {
        "_proposal": {
            "proposal_id":    proposal.proposal_id,
            "signed_payload": proposal.signed_payload,
            "action":         proposal.action,
            "args":           proposal.args,
            "expires_at":     proposal.expires_at,
            "summary":        summary,
        },
        # Visible-to-model summary — short, actionable, doesn't include
        # the signed token. The model uses this to explain the proposal
        # back to the user.
        "proposal_id": proposal.proposal_id,
        "expires_in":  300,
        "summary":     summary,
    }


# ── propose_close_position ─────────────────────────────────────────────


def propose_close_position(
    *,
    tenant_id:       int,
    conversation_id: str,
    position_id:     int,
    exit_price:      float,
    rationale:       str,
) -> dict:
    """Verify ownership, sign a close_position proposal, persist."""
    con = get_db()
    try:
        row = con.execute(
            "SELECT symbol, direction, status, entry_price, size_usd "
            "FROM positions WHERE id = ? AND tenant_id = ?",
            (position_id, tenant_id),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return {"error": "not_found"}
    pos = dict(row)
    if pos["status"] != "open":
        return {"error": "position_not_open"}

    try:
        proposal = sign_proposal(
            action=ACTION_CLOSE_POSITION,
            args={"position_id": position_id, "exit_price": float(exit_price)},
            tenant_id=tenant_id,
        )
        persist_proposal(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            proposal=proposal,
        )
    except ProposalError as e:
        log.warning("propose_close_position sign/persist failed: %s", e.reason)
        return {"error": e.reason}

    summary = (
        f"Cerrar posición #{position_id} ({pos['symbol']} {pos['direction']}) "
        f"a ${exit_price}. Rationale: {rationale[:200]}"
    )
    return _model_facing(proposal, summary)


# ── propose_reactivate_symbol ──────────────────────────────────────────


def propose_reactivate_symbol(
    *,
    tenant_id:       int,
    conversation_id: str,
    symbol:          str,
    reason:          str,
) -> dict:
    """Verify the symbol is currently PAUSED, sign + persist."""
    norm = symbol.upper().strip()
    if not norm.endswith("USDT"):
        norm = f"{norm}USDT"
    con = get_db()
    try:
        row = con.execute(
            "SELECT state FROM symbol_health WHERE symbol = ?",
            (norm,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return {"error": "not_found"}
    state = dict(row)["state"]
    if state != "PAUSED":
        return {"error": "symbol_not_paused", "current_state": state}

    try:
        proposal = sign_proposal(
            action=ACTION_REACTIVATE_SYMBOL,
            args={"symbol": norm, "reason": reason[:500]},
            tenant_id=tenant_id,
        )
        persist_proposal(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            proposal=proposal,
        )
    except ProposalError as e:
        log.warning("propose_reactivate_symbol failed: %s", e.reason)
        return {"error": e.reason}

    summary = (
        f"Liberar {norm} de PAUSED → PROBATION. Reason: {reason[:200]}"
    )
    return _model_facing(proposal, summary)


# ── propose_apply_tune ─────────────────────────────────────────────────


def propose_apply_tune(
    *,
    tenant_id:       int,
    conversation_id: str,
    tune_id:         int,
    rationale:       str,
) -> dict:
    """Verify the tune is pending + matches the latest one + sign."""
    try:
        from api.tune import tune_latest  # noqa: PLC0415
        latest = tune_latest()
    except Exception:  # noqa: BLE001
        log.warning("propose_apply_tune: tune_latest failed", exc_info=True)
        return {"error": "tune_unavailable"}
    if not latest:
        return {"error": "no_pending_tune"}
    if int(latest.get("id", 0)) != int(tune_id):
        return {"error": "tune_mismatch", "latest_tune_id": latest.get("id")}
    if latest.get("applied"):
        return {"error": "tune_already_applied"}

    try:
        proposal = sign_proposal(
            action=ACTION_APPLY_TUNE,
            args={"tune_id": int(tune_id)},
            tenant_id=tenant_id,
        )
        persist_proposal(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            proposal=proposal,
        )
    except ProposalError as e:
        log.warning("propose_apply_tune failed: %s", e.reason)
        return {"error": e.reason}

    summary = f"Aplicar auto-tune #{tune_id}. Rationale: {rationale[:200]}"
    return _model_facing(proposal, summary)


# Map exposed to the dispatch surface. Each handler is keyword-only
# with `tenant_id` AND `conversation_id` required — the latter is the
# new wrinkle vs read-only tools: we need it to thread into
# agent_side_effects.conversation_id so the audit row links back.
PROPOSE_HANDLERS = {
    "propose_close_position":    propose_close_position,
    "propose_reactivate_symbol": propose_reactivate_symbol,
    "propose_apply_tune":        propose_apply_tune,
}
