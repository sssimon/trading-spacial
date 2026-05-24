"""HMAC-signed proposal envelopes — Phase 3 of epic #400.

Pre-reg §10. The contract:

  1. A propose_* tool (api/agent/tools/proposals.py) verifies that the
     action it proposes is grounded — the position belongs to the
     tenant, the symbol exists, the tune is pending. Then it calls
     `sign_proposal(...)` and `persist_proposal(...)`. The tool's
     return value to the model is `{proposal_id, expires_in, ...}`;
     the signed payload travels OUT-OF-MODEL via an SSE proposal
     event so the model never sees nor handles the signature.

  2. The frontend renders an amber confirm button referencing the
     proposal_id. Click → POST /agent/proposals/{proposal_id}/confirm
     with the signed payload in the body. The server:

       - re-verifies the HMAC (defense in depth — the row in
         agent_side_effects is the source of truth, but verifying the
         token catches a row that was tampered with via raw SQL).
       - checks expiry (TTL = 5 min).
       - checks tenant_id matches the JWT-resolved tenant_id.
       - checks idempotency_key (= proposal_id) hasn't been consumed.
       - re-checks current state (TOCTOU: position still open, symbol
         still PAUSED, tune still pending).
       - invokes the downstream handler with the user's cookie.
       - persists the result to agent_side_effects.

  3. Idempotency: the UNIQUE constraint on agent_side_effects.idempotency_key
     makes a double-click safe at the DB layer. The application-level
     check also short-circuits — same proposal confirmed twice returns
     the first result, never re-executes the side effect.

Secrets:
  - AGENT_PROPOSAL_SECRET (env var) — used for HMAC. Separate from
    JWT_SECRET so rotating one doesn't invalidate the other (PR #404
    review issue 1 generalized).

Envelope versioning (v field):
  - The canonical payload includes "v": 1. verify_proposal rejects any
    other value with reason="unsupported_version".
  - ADDING A FIELD TO THE ENVELOPE IS A BREAKING CHANGE. Bumping the
    schema means: (a) bump v to 2 in _canonical_payload, (b) update
    verify_proposal to accept both 1 and 2 during a migration window,
    (c) write a backfill / sunset plan for in-flight v=1 proposals
    (worst case: their TTL of 5 min is the natural drain window).
  - Adding a field without bumping v will silently invalidate every
    proposal-in-flight: the new sign produces a different MAC than the
    old verify can reconstruct, and the user sees an opaque
    signature_mismatch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from db.transaction import transaction

log = logging.getLogger("api.agent.proposals")


# 5-minute TTL — pre-reg §10.1. Long enough for the user to read the
# proposal and decide; short enough that a stale tab can't replay a
# proposal an hour later.
PROPOSAL_TTL_SECONDS = 300


# Closed enum for `action`. Every propose_* tool must populate one of
# these; the confirm endpoint dispatches downstream based on the value.
ACTION_CLOSE_POSITION     = "close_position"
ACTION_REACTIVATE_SYMBOL  = "reactivate_symbol"
ACTION_APPLY_TUNE         = "apply_tune"

_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    ACTION_CLOSE_POSITION,
    ACTION_REACTIVATE_SYMBOL,
    ACTION_APPLY_TUNE,
})


# Result enum on agent_side_effects.result:
#   NULL     — pending (proposal emitted, not yet confirmed)
#   "ok"     — confirmed + downstream handler succeeded
#   "error"  — confirmed but downstream handler failed
#   "conflict"— confirmed but TOCTOU re-check failed (state drifted)
#   "expired"— confirm arrived after TTL


class ProposalError(Exception):
    """Raised when sign/verify/persist hits an invariant. Caught at the
    request boundary and translated to a closed-enum HTTP detail."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class SignedProposal:
    """The full envelope returned by sign_proposal. The frontend gets
    the `proposal_id` (visible) AND the `signed_payload` (opaque token
    it sends back on confirm). Persisted row in agent_side_effects
    only records proposal_id + args + expires_at; the signature is
    derived deterministically from the secret on verify, so we don't
    persist it."""
    proposal_id:    str
    signed_payload: str
    action:         str
    args:           dict
    tenant_id:      int
    expires_at:     str   # ISO 8601


def _secret() -> bytes:
    """Read AGENT_PROPOSAL_SECRET from env. Distinct from JWT_SECRET so
    rotating one doesn't tumble proposals-in-flight (PR #404 review)."""
    raw = (os.environ.get("AGENT_PROPOSAL_SECRET") or "").strip()
    if not raw:
        # Same reason as the agent_disabled enum — we don't leak the
        # env var name to the wire. The endpoint translates this to a
        # 503 with detail="agent_disabled".
        raise ProposalError(
            "agent_disabled",
            "AGENT_PROPOSAL_SECRET not configured",
        )
    return raw.encode("utf-8")


def _new_proposal_id() -> str:
    """URL-safe 16-byte token, prefixed for log-grep friendliness."""
    return "prop_" + secrets.token_urlsafe(16)


def _canonical_payload(
    *,
    proposal_id: str,
    action:      str,
    args:        dict,
    tenant_id:   int,
    expires_at:  str,
) -> bytes:
    """Deterministic serialization of the payload — same bytes on sign
    and on verify, so the HMAC stays stable. sort_keys + ensure_ascii
    + tightly-defined separators are the same invariants we enforce on
    the prompt cache prefix in api/agent/prompts (pre-reg §7.5)."""
    doc = {
        "v":         1,                # version for forward compat
        "proposal_id": proposal_id,
        "action":    action,
        "args":      args,
        "tenant_id": tenant_id,
        "expires_at": expires_at,
    }
    return json.dumps(
        doc,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_proposal(
    *,
    action:    str,
    args:      dict,
    tenant_id: int,
) -> SignedProposal:
    """Emit a new signed proposal. Caller MUST have already verified
    that the action is grounded (position belongs to tenant, etc.).
    """
    if action not in _ALLOWED_ACTIONS:
        raise ProposalError(
            "invalid_action",
            f"action {action!r} not in allowed set",
        )
    proposal_id = _new_proposal_id()
    expires_dt = datetime.now(timezone.utc) + timedelta(seconds=PROPOSAL_TTL_SECONDS)
    expires_at = expires_dt.isoformat()

    payload_bytes = _canonical_payload(
        proposal_id=proposal_id,
        action=action,
        args=args,
        tenant_id=tenant_id,
        expires_at=expires_at,
    )
    mac = hmac.new(_secret(), payload_bytes, hashlib.sha256).hexdigest()
    # Frontend treats `signed_payload` as an opaque blob; the server
    # parses it back into the same components on verify. Format:
    # base64-free, dot-separated: "<hex-mac>.<base64url-payload>" — but
    # we keep the payload visible in dev logs by using urlsafe base64.
    import base64
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signed_payload = f"{mac}.{payload_b64}"
    return SignedProposal(
        proposal_id=proposal_id,
        signed_payload=signed_payload,
        action=action,
        args=args,
        tenant_id=tenant_id,
        expires_at=expires_at,
    )


def verify_proposal(signed_payload: str) -> dict:
    """Verify the HMAC + parse the payload. Raises ProposalError on
    any failure. Does NOT check expiry, ownership, or idempotency —
    those are checked at the confirm endpoint with the DB row in hand."""
    if not isinstance(signed_payload, str) or "." not in signed_payload:
        raise ProposalError("invalid_payload", "missing separator")
    try:
        mac_hex, payload_b64 = signed_payload.split(".", 1)
    except ValueError:
        raise ProposalError("invalid_payload", "split failed")
    import base64
    # Re-pad the base64 (we stripped = on sign).
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
    except Exception as e:  # noqa: BLE001
        raise ProposalError("invalid_payload", f"base64 decode failed: {e}")
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        raise ProposalError("invalid_payload", f"json decode failed: {e}")
    if not isinstance(payload, dict):
        raise ProposalError("invalid_payload", "payload not an object")
    # Envelope schema gate. We currently only know v=1; reject anything
    # else loudly with a closed-enum reason so an attacker (or a future
    # rollback that left a v=2 signer running against a v=1 verifier)
    # gets a distinguishable error from "tampered MAC". See top-of-module
    # docstring for the v contract.
    if int(payload.get("v", 0)) != 1:
        raise ProposalError(
            "unsupported_version",
            f"envelope v={payload.get('v')!r} not supported",
        )
    # Re-canonicalize and compare MAC. Using compare_digest avoids
    # timing-side-channel.
    try:
        proposal_id = str(payload["proposal_id"])
        action      = str(payload["action"])
        args        = dict(payload["args"])
        tenant_id   = int(payload["tenant_id"])
        expires_at  = str(payload["expires_at"])
    except (KeyError, ValueError, TypeError) as e:
        raise ProposalError("invalid_payload", f"missing field: {e}")
    expected_bytes = _canonical_payload(
        proposal_id=proposal_id,
        action=action,
        args=args,
        tenant_id=tenant_id,
        expires_at=expires_at,
    )
    expected_mac = hmac.new(_secret(), expected_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_mac, mac_hex):
        raise ProposalError("signature_mismatch", "HMAC does not match")
    return payload


def persist_proposal(
    *,
    tenant_id:        int,
    conversation_id:  str,
    proposal:         SignedProposal,
) -> None:
    """Insert the proposal into agent_side_effects with result=NULL.
    The UNIQUE constraint on idempotency_key (= proposal_id) makes
    a re-insert with the same id raise IntegrityError — which we
    treat as "already persisted, no-op" (defensive; same proposal
    can't naturally be re-signed because proposal_id is a fresh
    token_urlsafe each time).
    """
    import sqlite3
    try:
        with transaction() as con:
            con.execute(
                """INSERT INTO agent_side_effects
                   (tenant_id, conversation_id, ts, action, args_json,
                    idempotency_key, result, http_status, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
                (
                    tenant_id,
                    conversation_id,
                    datetime.now(timezone.utc).isoformat(),
                    proposal.action,
                    json.dumps(proposal.args, sort_keys=True),
                    proposal.proposal_id,
                    proposal.expires_at,
                ),
            )
    except sqlite3.IntegrityError:
        log.warning(
            "persist_proposal: idempotency_key collision for %s — ignoring",
            proposal.proposal_id,
        )


def load_proposal_row(proposal_id: str) -> Optional[dict]:
    """Fetch the row from agent_side_effects. Returns None if absent.
    Caller compares result column to decide pending vs consumed."""
    with transaction() as con:
        row = con.execute(
            """SELECT id, tenant_id, conversation_id, ts, action, args_json,
                      idempotency_key, result, http_status, expires_at
               FROM agent_side_effects
               WHERE idempotency_key = ?""",
            (proposal_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_proposal_result(
    *,
    proposal_id: str,
    result:      str,
    http_status: Optional[int] = None,
) -> None:
    """Update the result column. result ∈ ok / error / state_drift /
    role_required / expired.

    Best-effort: a DB write that fails AFTER the downstream action
    succeeded (e.g. the position closed but we can't record the
    audit/idempotency state) is logged at WARN and swallowed. We do NOT
    raise because the action ALREADY happened — propagating an exception
    here turns a successful close into a 500 response, making the user
    think nothing happened when in fact everything happened except the
    bookkeeping.

    PR #406 review issue 4 follow-up: if reconciliation matters more
    than a missing audit row, the operator can run a one-off script to
    cross-check positions.exit_reason='MANUAL_AGENT' against
    agent_side_effects rows with result IS NULL — they should match.
    """
    try:
        with transaction() as con:
            con.execute(
                "UPDATE agent_side_effects "
                "SET result = ?, http_status = ? "
                "WHERE idempotency_key = ?",
                (result, http_status, proposal_id),
            )
    except Exception:  # noqa: BLE001
        log.warning(
            "mark_proposal_result failed for proposal_id=%s — audit drift; "
            "the downstream action already ran but this row stays NULL. "
            "Reconcile via agent_side_effects + positions cross-check.",
            proposal_id, exc_info=True,
        )


def is_expired(proposal_row: dict) -> bool:
    """Compare expires_at to now. Robust to legacy rows where
    expires_at is NULL (treated as expired so a stale row pre-Phase 3
    can't be confirmed)."""
    iso = proposal_row.get("expires_at")
    if not iso:
        return True
    try:
        expires_dt = datetime.fromisoformat(iso)
    except ValueError:
        return True
    return datetime.now(timezone.utc) > expires_dt
