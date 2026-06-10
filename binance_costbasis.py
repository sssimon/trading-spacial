"""Reconstrucción de cost-basis ACB (Adjusted Cost Base) desde fills de Binance.

Módulo PURO (sin I/O, sin red, sin DB) — testeable con fixtures.

En spot NO existen "posiciones" con precio de entrada: hay un stream de compras
y ventas. El "entry_price" se RECONSTRUYE. Usamos ACB / weighted-average sobre la
posición VIVA (coincide con el "Average Cost" que la app de Binance le muestra al
operador, estable ante ventas parciales):

    qty_viva   = Σ qty_buy − Σ qty_sell
    avg_entry  = costo_remanente / qty_viva

Cada compra suma (quoteQty + comisión-en-quote) al pool y su qty; cada venta
reduce qty al costo PROMEDIO vigente (el promedio NO cambia con ventas). Las
comisiones en el asset base reducen la qty recibida; en BNB/otro asset se ignoran
(best-effort — convertir exige el precio BNB/quote del instante, spec §11 abierta).

`entry_ts_ms` = ts del fill que INICIÓ el holding continuo actual (último cruce de
qty acumulada 0→>0). Resetea en un round-trip completo (vendió-todo-y-recompró) —
y eso es CORRECTO: un recompra es un holding nuevo, su age cuenta desde ahí (F2).

BNC-13: el avg_entry es una RECONSTRUCCIÓN (muta con fills nuevos), válida para
observabilidad/equity/riesgo, NUNCA consumida por el read-model de conducta.

Spec: docs/superpowers/specs/es/2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §3.
"""
from __future__ import annotations

# Umbral de "qty efectivamente cero" para comparaciones de punto flotante.
# Una posición por debajo de esto se considera cerrada (el dust real se filtra
# aguas arriba por minNotional; esto es solo el epsilon numérico de la suma).
_EPS = 1e-12


def reconstruct_acb(fills: list[dict], *, base_asset: str, quote_asset: str) -> dict:
    """fills (crudos de myTrades) → {status, qty_viva, avg_entry, entry_ts_ms}.

    status:
      - 'ok'       : holding vivo; trae qty_viva, avg_entry, entry_ts_ms.
      - 'no_fills' : sin fills (el caller se abstiene → no_reconstruible, F9).
      - 'flat'     : compró y vendió todo (qty_viva ≈ 0); no hay holding.

    El caller decide la qty final desde el balance /account (transfers a Earn
    reducen el balance spot sin ser ventas → qty_viva de fills puede ser ≥ balance;
    el avg_entry — costo por unidad — sigue siendo válido para el balance real).
    """
    if not fills:
        return {"status": "no_fills"}

    ordered = sorted(fills, key=lambda f: (int(f["time"]), int(f.get("id", 0))))
    qty = 0.0            # base mantenido actualmente
    cost = 0.0           # costo (quote) del pool ACB del qty mantenido
    entry_ts_ms = None   # inicio del holding continuo actual

    for f in ordered:
        fqty = float(f["qty"])
        fquote = float(f["quoteQty"])
        comm = float(f.get("commission") or 0.0)
        comm_asset = f.get("commissionAsset")
        is_buy = bool(f["isBuyer"])

        if is_buy:
            received = fqty
            buy_cost = fquote
            if comm_asset == base_asset:
                received -= comm          # fee en base → menos base recibido
            elif comm_asset == quote_asset:
                buy_cost += comm          # fee en quote → más costo
            # else (BNB/otro): ignorado (best-effort, spec §11)
            if qty <= _EPS:
                # abría un holding nuevo (estábamos flat) → nuevo entry_ts + pool limpio
                entry_ts_ms = int(f["time"])
                qty = 0.0
                cost = 0.0
            qty += received
            cost += buy_cost
        else:  # venta
            if qty <= _EPS:
                continue                  # vender sin posición (dato raro) → ignora
            avg = cost / qty
            sell_qty = min(fqty, qty)
            qty -= sell_qty
            cost -= sell_qty * avg        # ACB: remueve al promedio → avg invariante
            if qty <= _EPS:
                qty = 0.0
                cost = 0.0
                entry_ts_ms = None        # holding cerrado

    if qty <= _EPS:
        return {"status": "flat", "qty_viva": 0.0}
    return {
        "status": "ok",
        "qty_viva": qty,
        "avg_entry": cost / qty,
        "entry_ts_ms": entry_ts_ms,
    }
