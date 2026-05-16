"""Capital API — per-user capital state (GET + PUT).

B.5 follow-up B for Epic B #253. Single row per tenant; PUT is upsert.

Pre-reg: docs/superpowers/plans/2026-05-16-multi-tenant-b5-capital-prefs-pre-reg.md
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_api_key
from auth.dependencies import get_current_tenant_id
from db.capital import db_get_capital, db_upsert_capital

log = logging.getLogger("api.capital")

router = APIRouter(prefix="/capital", tags=["capital"])


class CapitalPutBody(BaseModel):
    balance: float = Field(..., ge=0, description="Current notional capital in USD")
    peak_balance: Optional[float] = Field(None, ge=0)
    max_drawdown_pct: Optional[float] = Field(None)


@router.get("", summary="Get current capital state for current tenant")
def get_capital(tenant_id: int = Depends(get_current_tenant_id)):
    row = db_get_capital(tenant_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Capital not initialized for this tenant. "
                "Use PUT /capital to set initial state."
            ),
        )
    return row


@router.put(
    "",
    summary="Upsert capital state for current tenant",
    dependencies=[Depends(verify_api_key)],
)
def put_capital(
    body: CapitalPutBody,
    tenant_id: int = Depends(get_current_tenant_id),
):
    row = db_upsert_capital(
        tenant_id,
        balance=body.balance,
        peak_balance=body.peak_balance,
        max_drawdown_pct=body.max_drawdown_pct,
    )
    return {"ok": True, "capital": row}
