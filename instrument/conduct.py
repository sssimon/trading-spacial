"""Campos de conducta `i` del instrumento (Fase 1) — puro. Spec §7.

Compara la secuencia realizada contra el plan confirmado. La medición es
INDEPENDIENTE del PnL: mide si honraste la ley que aprobaste, no si ganaste.
SIN score único (no-mezcla de tipos, INV-7). Cada episodio lleva procedencia."""
from __future__ import annotations

from datetime import datetime


def _hours_between(a_iso: str, b_iso: str) -> float:
    a = datetime.fromisoformat(a_iso.replace("Z", "+00:00"))
    b = datetime.fromisoformat(b_iso.replace("Z", "+00:00"))
    return (b - a).total_seconds() / 3600.0


def compute_conduct(plan, events: list[dict], final_state, *, entry_price: float,
                    entry_ts: str, exit_ts: str, procedencia: str) -> dict:
    """Deriva los campos de conducta del episodio cerrado (spec §7)."""
    entry_en_zona = (plan.entry_zone is not None
                     and plan.entry_zone["precio_bajo"] <= entry_price <= plan.entry_zone["precio_alto"])

    sl_widened = any(e["tipo"] == "SL_MOVED" and e["nuevo_sl"] < plan.sl_price
                     for e in events)
    sl_respetado = not sl_widened

    tp1_filled = 0 in final_state.rungs_llenos
    adherencia_be = bool(final_state.be_movido) if tp1_filled else None

    rungs_honrados = len(final_state.rungs_llenos)
    cierre_en_plan = final_state.close_reason != "MANUAL"
    escalono = cierre_en_plan or rungs_honrados > 0
    hold_hours = _hours_between(entry_ts, exit_ts)

    return {
        "entry_en_zona": entry_en_zona,
        "sl_respetado": sl_respetado,
        "adherencia_be": adherencia_be,
        "rungs_honrados": rungs_honrados,
        "escalono": escalono,
        "cierre_en_plan": cierre_en_plan,
        "hold_hours": hold_hours,
        "close_reason": final_state.close_reason,
        "procedencia": procedencia,
    }
