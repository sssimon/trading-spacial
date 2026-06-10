"""Señal de RIESGO de holding (§7) — observabilidad, NO conducta (BNC-17).

Spec: docs/superpowers/specs/es/2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §7.

Lee los holds EXTERNAL del tenant (OPERATOR — el "rojo" del papá — y AUTO_DERIVED)
y reporta HECHOS del holding: underwater (precio vivo < entry de referencia),
age_days (desde el inicio del holding), sin_stop (sl_price NULL). La bandera
`at_risk = underwater AND age_days >= horizonte AND valuado`.

NUNCA infiere un ACTO: no lee scan_id ni computa apertura_discrecional. Vive en el
plano de observabilidad, ortogonal a la conducta (la ley conducta⊥resultado). Es
el sucesor honesto del "rojo de violación": afirma un hecho de riesgo del holding,
no una decisión deliberada que el sistema no observó.

Sin precio para un símbolo → `valuado=False` (se ABSTIENE); NUNCA asume "sin
riesgo" (F1). El entry de referencia es el `entry_price` almacenado: ACB para
AUTO_DERIVED, valor tecleado para OPERATOR — la señal no distingue (lee el hecho).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Mapping, Optional


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """ISO 8601 → datetime aware (UTC si no trae tz). None si no parsea."""
    if not ts:
        return None
    raw = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_holding_risk(
    con: sqlite3.Connection,
    *,
    tenant_id: int,
    price_lookup: Mapping[str, float],
    now: datetime,
    horizon_days: float = 14.0,
) -> dict:
    """Riesgo de los holds EXTERNAL del tenant. Read-only, on-read (como
    compute_real_equity). Devuelve {holdings, no_valuados, at_risk}.

    `price_lookup`: symbol → precio vivo (vía ticker público, Task 3). Un símbolo
    ausente → el hold se marca valuado=False y entra a `no_valuados` (abstención).
    `now`: datetime aware del momento de la lectura (inyectable para test).
    `horizon_days`: a partir de cuántos días underwater el holding se marca at_risk.
    """
    rows = con.execute(
        "SELECT symbol, qty, entry_price, entry_ts, sl_price, origin FROM positions "
        "WHERE tenant_id = ? AND status = 'open' AND control_domain = 'EXTERNAL' "
        "AND qty IS NOT NULL AND qty > 0 ORDER BY symbol",
        (tenant_id,),
    ).fetchall()

    holdings: list[dict] = []
    no_valuados: list[str] = []
    at_risk: list[str] = []

    for r in rows:
        symbol = r["symbol"]
        entry_price = r["entry_price"]
        sin_stop = r["sl_price"] is None
        entry_dt = _parse_ts(r["entry_ts"])
        age_days = (now - entry_dt).total_seconds() / 86400.0 if entry_dt else None
        price = price_lookup.get(symbol)

        base = {
            "symbol": symbol,
            "qty": float(r["qty"]),
            "entry_price": entry_price,
            "origin": r["origin"],
            "age_days": round(age_days, 2) if age_days is not None else None,
            "sin_stop": sin_stop,
        }

        if price is None:
            # Abstención (F1): sin precio NO se afirma riesgo ni ausencia de riesgo.
            no_valuados.append(symbol)
            holdings.append({**base, "valuado": False, "price": None,
                             "underwater": None, "unrealized_pct": None, "at_risk": None})
            continue

        underwater = bool(entry_price is not None and price < entry_price)
        unrealized_pct = (price - entry_price) / entry_price if entry_price else None
        is_at_risk = bool(underwater and age_days is not None and age_days >= horizon_days)
        if is_at_risk:
            at_risk.append(symbol)
        holdings.append({
            **base,
            "valuado": True,
            "price": float(price),
            "underwater": underwater,
            "unrealized_pct": round(unrealized_pct, 4) if unrealized_pct is not None else None,
            "at_risk": is_at_risk,
        })

    return {"holdings": holdings, "no_valuados": no_valuados, "at_risk": at_risk}
