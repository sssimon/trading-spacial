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
    row = db_upsert_user_preferences(
        tenant_id,
        symbol_filter=body.symbol_filter,
        min_score=body.min_score,
        notify_channels=body.notify_channels,
    )
    return {"ok": True, "preferences": row}
