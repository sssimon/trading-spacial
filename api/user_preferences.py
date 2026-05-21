"""User preferences API — per-user filter + notification config (GET + PUT).

B.5 follow-up B for Epic B #253. Single row per tenant; PUT is upsert.
GET returns sensible defaults if not set (unlike capital which 404s).

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b5-capital-prefs-pre-reg.md
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import verify_api_key
from auth.dependencies import get_current_tenant_id
from db.user_preferences import (
    db_get_user_preferences,
    db_upsert_user_preferences,
)

log = logging.getLogger("api.user_preferences")

router = APIRouter(prefix="/preferences", tags=["preferences"])


# Marker used to redact the secret portion of a telegram_bot_token in API
# responses. The `put_preferences` handler also uses this marker to detect
# "user submitted the masked value verbatim (didn't retype)" → preserve
# existing DB token rather than overwrite. Both call sites MUST use this
# constant — string drift would silently break preserve-detection.
_MASK_MARKER = "****"

# Default min_score matches schema default + current global default in code.
_DEFAULT_MIN_SCORE = 4


class PreferencesPutBody(BaseModel):
    symbol_filter: Optional[list[str]] = Field(
        None, description="Whitelist of symbols (None = no filter)"
    )
    min_score: Optional[int] = Field(None, ge=0, le=9)
    notify_channels: Optional[dict[str, Any]] = Field(
        None, description='e.g. {"telegram_chat_id": "...", "email": "..."}'
    )


def _mask_token(token: str) -> str:
    """Mask a Telegram bot token, preserving first 10 + last 4 chars.

    Real Telegram tokens have shape `<bot_id>:<35-char-secret>` (~46 chars
    total). Output: first 10 chars + "****" + last 4 chars.

    Defensive: `len < 10` returns "" instead of partial mask. Real tokens
    are never shorter than 10 chars; this guard only fires for garbage
    (empty, corrupt, manually-truncated). Returning "" makes the masked
    field indistinguishable from "not configured" in those cases — accept-
    able because the path is not reachable in prod with valid creds.

    Spec ref: docs/superpowers/specs/es/2026-05-21-telegram-per-user-
    config-pre-reg.md §Security note.
    """
    if not token or len(token) < 10:
        return ""
    return f"{token[:10]}{_MASK_MARKER}{token[-4:]}"


@router.get("", summary="Get preferences for current tenant (defaults if unset)")
def get_preferences(tenant_id: int = Depends(get_current_tenant_id)):
    row = db_get_user_preferences(tenant_id)
    if row is None:
        # Return sensible defaults — per pre-reg §3.2
        return {
            "tenant_id": tenant_id,
            "symbol_filter": None,
            "min_score": _DEFAULT_MIN_SCORE,
            "notify_channels": None,
        }
    # Mask telegram_bot_token in the response to reduce XSS blast radius.
    # See spec §Security note.
    nc = row.get("notify_channels") or None
    if nc and nc.get("telegram_bot_token"):
        nc = {**nc, "telegram_bot_token": _mask_token(nc["telegram_bot_token"])}
        row = {**row, "notify_channels": nc}
    return row


@router.put(
    "",
    summary="Upsert preferences for current tenant",
    dependencies=[Depends(verify_api_key)],
)
def put_preferences(
    body: PreferencesPutBody,
    tenant_id: int = Depends(get_current_tenant_id),
):
    # If the submitted telegram_bot_token contains the mask marker '****',
    # the user did NOT retype it — preserve the existing DB value. This
    # supports the UX pattern "pre-fill masked value, only update if user
    # types something new". See spec §Security note.
    notify_channels = body.notify_channels
    if notify_channels and _MASK_MARKER in (notify_channels.get("telegram_bot_token") or ""):
        existing = db_get_user_preferences(tenant_id)
        existing_token = (
            (existing or {}).get("notify_channels") or {}
        ).get("telegram_bot_token", "")
        notify_channels = {**notify_channels, "telegram_bot_token": existing_token}

    row = db_upsert_user_preferences(
        tenant_id,
        symbol_filter=body.symbol_filter,
        min_score=body.min_score,
        notify_channels=notify_channels,
    )
    # Mask the bot_token in the response for consistency with GET.
    if row and (row.get("notify_channels") or {}).get("telegram_bot_token"):
        nc = row["notify_channels"]
        masked_nc = {**nc, "telegram_bot_token": _mask_token(nc["telegram_bot_token"])}
        row = {**row, "notify_channels": masked_nc}
    return {"ok": True, "preferences": row}
