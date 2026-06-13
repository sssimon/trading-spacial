"""Arnés del simulador determinista F2 (instrumento) — refutador, read-only.

Corre simulate_plan sobre las posiciones REALES cerradas (filtro BNC-12) con
frames diarios, y verifica PARIDAD contra el envelope real. Reporta tres cubos:
simuladas, paridad, divergencias, no_aplica. SIN PnL, SIN tabla nueva.
check_parity es PURO; la red (frames) y la lectura DB viven en main(). Spec §5.

Uso: python -m tools.plan_simulator   (network-marked; corre a propósito)
"""
from __future__ import annotations

import logging
from datetime import datetime

from instrument.plan import derive_plan
from instrument.simulate import simulate_plan
from screener.sr_levels import detect_levels
from tools.lifecycle_falsifier import _bars_as_of, _closed_positions

log = logging.getLogger("tools.plan_simulator")


def check_parity(sim_state, pos: dict, plan) -> dict:
    """PURO. Compara el cierre del sim con el real. Spec §5.
    - real SL ↔ sim SL_HIT/BE_HIT → paridad.
    - real TP ↔ sim tocó ≥1 rung → paridad.
    - real MANUAL/TIME_LIMIT (fuera de plan) → parity=None (NO aplica al refutador:
      el operador salió fuera de plan, no es una refutación de la máquina).
    - resto → divergencia."""
    real = (pos.get("exit_reason") or "").upper()
    sim = sim_state.close_reason or ""
    real_sl = "SL" in real
    real_tp = "TP" in real
    if not real_sl and not real_tp:
        return {"parity": None, "motivo": f"real fuera de plan ({real or '?'})"}
    sim_sl = sim in ("SL_HIT", "BE_HIT")
    sim_toco_rung = bool(sim_state.rungs_llenos)
    if real_sl and sim_sl:
        return {"parity": True, "motivo": "ambos SL"}
    if real_tp and sim_toco_rung:
        return {"parity": True, "motivo": "ambos tocaron TP"}
    return {"parity": False, "motivo": f"real={real} sim={sim or '?'}"}


def _forward_candles(symbol: str, entry_ts: str, exit_ts: str | None) -> list[dict]:
    """Velas diarias en [entry, exit], orden ascendente. I/O (red)."""
    from backtest import get_cached_data
    start = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    df = get_cached_data(symbol, "1d", start)
    if df.empty:
        return []
    idx = df.index
    # get_cached_data hoy devuelve índice naive; la rama tz es red de seguridad si eso cambia.
    if getattr(idx, "tz", None) is not None:
        df = df.set_index(idx.tz_convert("UTC").tz_localize(None))
    df = df[df.index >= start.replace(tzinfo=None)]
    if exit_ts:
        hi = datetime.fromisoformat(exit_ts.replace("Z", "+00:00")).replace(tzinfo=None)
        df = df[df.index <= hi]
    return [{"open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close)} for r in df.itertuples()]


def main(tenant_id: int = 2) -> int:
    logging.basicConfig(level=logging.INFO)
    positions = _closed_positions(tenant_id)
    simuladas = parity = diverg = no_aplica = 0
    divergencias: list[dict] = []
    for pos in positions:
        try:
            zonas = detect_levels(_bars_as_of(pos["symbol"], pos["entry_ts"]))
            candles = _forward_candles(pos["symbol"], pos["entry_ts"], pos.get("exit_ts"))
        except Exception as e:  # noqa: BLE001 — fallo de red/símbolo = se omite
            log.warning("SIM_SKIP symbol=%s causa=%s", pos["symbol"], e)
            continue
        if not candles:
            continue
        simuladas += 1
        plan = derive_plan(zonas, float(pos["entry_price"]))
        _, st = simulate_plan(plan, candles)
        res = check_parity(st, pos, plan)
        if res["parity"] is True:
            parity += 1
        elif res["parity"] is False:
            diverg += 1
            divergencias.append({"symbol": pos["symbol"], "id": pos["id"], **res})
        else:
            no_aplica += 1
    print(f"plan_simulator: {simuladas} simuladas · {parity} paridad · "
          f"{diverg} divergencias · {no_aplica} fuera de plan")
    for d in divergencias:
        print(f"  DIVERGENCIA {d['symbol']}#{d['id']}: {d['motivo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
