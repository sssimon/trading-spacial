"""Arnés de falsación de la columna del instrumento (Fase 1) — read-only.

Re-juega la máquina de estados sobre las posiciones REALES ya cerradas del
operador y confirma que el plan derivado reproduce el envelope (entry/exit/sl/tp).
Si no reproduce, la máquina está mal (el refutador de Voronov). HONESTO sobre la
resolución: sin libro de fills histórico, falsa el ENVELOPE, no la secuencia
completa (spec §6). reproduce_position es PURO (testeable sin red/DB); la red
(klines históricas) y la lectura/escritura DB viven en main(), I/O deliberado.

Uso: python -m tools.lifecycle_falsifier   (network-marked; corre a propósito)
"""
from __future__ import annotations

import json
import logging

from instrument.plan import derive_plan
from instrument.lifecycle import LifecycleState, step
from instrument.conduct import compute_conduct

log = logging.getLogger("tools.lifecycle_falsifier")

_TP_TOL_PCT = 0.005   # 0.5%: cercanía exit↔rung/SL para considerar "en plan"


def _close(a: float, b: float) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= _TP_TOL_PCT


def reproduce_position(pos: dict, zonas: list[dict]) -> dict:
    """PURO. Dado una posición cerrada real + las zonas de D.1 al entry, deriva
    el plan, sintetiza la secuencia de evento del envelope, la re-juega y computa
    conducta. Devuelve {reproduced: bool, conduct: dict, plan_json: str}."""
    entry = float(pos["entry_price"])
    exit_price = float(pos["exit_price"]) if pos.get("exit_price") is not None else None
    plan = derive_plan(zonas, entry)
    procedencia = "observado"   # F1: posiciones reales del enlace Binance

    events: list[dict] = [{"tipo": "PLAN_CONFIRMED", "procedencia": procedencia}]
    reproduced = False

    if exit_price is not None and _close(exit_price, plan.sl_price):
        events.append({"tipo": "STOP_HIT", "procedencia": procedencia})
        reproduced = True
    else:
        hit_idx = None
        for i, r in enumerate(plan.rungs):
            if exit_price is not None and (exit_price >= r.tp_price or _close(exit_price, r.tp_price)):
                hit_idx = i
        if hit_idx is not None:
            for i in range(hit_idx + 1):
                events.append({"tipo": "RUNG_FILLED", "order_id": f"r{i}",
                               "rung_index": i, "procedencia": procedencia})
            events.append({"tipo": "STOP_HIT", "procedencia": procedencia})
            reproduced = True
        else:
            events.append({"tipo": "MANUAL_EXIT", "procedencia": procedencia})
            reproduced = False

    state = LifecycleState(plan_id=int(pos.get("id") or 0))
    for e in events:
        state = step(state, e, plan)

    conduct = compute_conduct(
        plan, events, state, entry_price=entry,
        entry_ts=pos["entry_ts"], exit_ts=pos.get("exit_ts") or pos["entry_ts"],
        procedencia=procedencia,
    )
    return {"reproduced": reproduced, "conduct": conduct,
            "plan_json": json.dumps({"sl_price": plan.sl_price,
                                     "rungs": [r.tp_price for r in plan.rungs],
                                     "runner_frac": plan.runner_frac})}


# ── I/O (network + DB) — cubierto sólo por smoke test -m network ────────────

import time  # noqa: E402  (deliberado: imports de red/DB fuera del módulo puro)
from datetime import datetime, timezone

from data.providers.binance import BinanceAdapter
from screener.sr_levels import detect_levels, LOOKBACK_DAYS
from db.conduct_episodes import db_put_episode
from db.transaction import snapshot_connection, transaction


def _bars_as_of(symbol: str, entry_ts: str) -> list[dict]:
    """Velas diarias hasta entry_ts (reconstruye D.1 al momento de la entrada)."""
    end = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    end_ms = int(end.timestamp() * 1000)
    start_ms = end_ms - LOOKBACK_DAYS * 86_400_000
    bars = BinanceAdapter().fetch_klines(symbol, "1d", start_ms, end_ms)
    return [{"high": b.high, "low": b.low} for b in bars]


def _closed_positions(tenant_id: int) -> list[dict]:
    import sqlite3
    with snapshot_connection() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT id, symbol, entry_price, entry_ts, exit_price, exit_ts,
                      exit_reason, tenant_id
               FROM positions WHERE status='closed' AND tenant_id=?
               ORDER BY entry_ts""", (tenant_id,)).fetchall()
    return [dict(r) for r in rows]


def main(tenant_id: int = 2) -> int:
    logging.basicConfig(level=logging.INFO)
    now = datetime.now(timezone.utc).isoformat()
    positions = _closed_positions(tenant_id)

    # Regeneración idempotente: el ledger es una FOTO de la conducta histórica;
    # re-correr el arnés borra y reescribe las filas del tenant (snapshot
    # semantics, mismo principio que observed_orders).
    with transaction() as con:
        con.execute("DELETE FROM conduct_episodes WHERE tenant_id=?", (tenant_id,))

    reproduced = insuficiente = 0
    for pos in positions:
        try:
            zonas = detect_levels(_bars_as_of(pos["symbol"], pos["entry_ts"]))
        except Exception as e:  # noqa: BLE001 — fallo de red/símbolo = datos insuficientes
            log.warning("FALSIFIER_SKIP symbol=%s causa=%s", pos["symbol"], e)
            insuficiente += 1
            continue
        res = reproduce_position(pos, zonas)
        with transaction() as con:
            db_put_episode(con, position_id=pos["id"], symbol=pos["symbol"],
                           tenant_id=tenant_id, entry_ts=pos["entry_ts"],
                           exit_ts=pos.get("exit_ts"), conduct=res["conduct"],
                           plan_json=res["plan_json"], reproduced=res["reproduced"],
                           created_ts=now)
        reproduced += int(res["reproduced"])
        insuficiente += int(not res["reproduced"])
    print(f"conduct_episodes: {len(positions)} posiciones · "
          f"{reproduced} reproducidas · {insuficiente} fuera de plan/datos insuficientes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
