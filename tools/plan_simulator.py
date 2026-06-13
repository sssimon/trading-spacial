"""Arnés del simulador determinista F2 (instrumento) — refutador, read-only.

Corre simulate_plan sobre las posiciones REALES cerradas (filtro BNC-12) con
frames diarios, y verifica PARIDAD contra el envelope real. Reporta tres cubos:
máquina legal, paridad, divergencias. SIN PnL, SIN tabla nueva. check_parity es
PURO; la red (frames) y la lectura DB viven en main(). Spec §5.

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
    """PURO. ¿El cierre del sim corresponde direccionalmente al cierre real?
    real SL ↔ sim SL_HIT/BE_HIT; real TP ↔ sim tocó al menos un rung. Spec §5."""
    real = (pos.get("exit_reason") or "").upper()
    sim = sim_state.close_reason or ""
    real_sl = "SL" in real
    real_tp = "TP" in real
    sim_sl = sim in ("SL_HIT", "BE_HIT")
    sim_toco_rung = bool(sim_state.rungs_llenos)
    if real_sl and sim_sl:
        return {"parity": True, "motivo": "ambos SL"}
    if real_tp and sim_toco_rung:
        return {"parity": True, "motivo": "ambos tocaron TP"}
    return {"parity": False, "motivo": f"real={real or '?'} sim={sim or '?'}"}


def _forward_candles(symbol: str, entry_ts: str, exit_ts: str | None) -> list[dict]:
    """Velas diarias en [entry, exit], orden ascendente. I/O (red)."""
    from backtest import get_cached_data
    start = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    df = get_cached_data(symbol, "1d", start)
    if df.empty:
        return []
    idx = df.index
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
    legal = parity = diverg = 0
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
        plan = derive_plan(zonas, float(pos["entry_price"]))
        _, st = simulate_plan(plan, candles)
        legal += int(st.fase == "CLOSED")
        res = check_parity(st, pos, plan)
        if res["parity"]:
            parity += 1
        else:
            diverg += 1
            divergencias.append({"symbol": pos["symbol"], "id": pos["id"], **res})
    print(f"plan_simulator: {len(positions)} posiciones · {legal} máquina-legal · "
          f"{parity} paridad · {diverg} divergencias")
    for d in divergencias:
        print(f"  DIVERGENCIA {d['symbol']}#{d['id']}: {d['motivo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
