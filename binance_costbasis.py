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
SESGO ACOTADO del fee-BNB ignorado: subestima el costo en <= ~0.075% del notional
comprado; el avg_entry queda marginalmente bajo el real -> la bandera underwater de
§7 solo da falso-negativo en la banda ~0.075% del break-even (no silencia un holding
meaningfully-underwater). Cuantificar/convertir el fee BNB = slice posterior.

`entry_ts_ms` = ts del fill que INICIÓ el holding continuo actual (último cruce de
qty acumulada 0→>0). Resetea en un round-trip completo (vendió-todo-y-recompró) —
y eso es CORRECTO: un recompra es un holding nuevo, su age cuenta desde ahí (F2).

BNC-13: el avg_entry es una RECONSTRUCCIÓN (muta con fills nuevos), válida para
observabilidad/equity/riesgo, NUNCA consumida por el read-model de conducta.

Spec: docs/superpowers/specs/es/2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §3.
"""
from __future__ import annotations

# Piso ABSOLUTO del umbral de "qty efectivamente cero". El umbral real es RELATIVO
# a la escala de qty del símbolo (ver reconstruct_acb): un eps absoluto rompe con
# memecoins de qty ~1e9 (su error de redondeo flotante ~2e-7 es MUY mayor que 1e-12)
# → tras vender-todo, qty no convergería a flat → holding fantasma con avg_entry
# corrupto (Halberg, Task3+4 review). Este piso solo aplica a qtys pequeñas (BTC).
_EPS_FLOOR = 1e-12


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

    # Umbral de "flat" RELATIVO a la escala de qty del símbolo (1e-9 de la mayor qty
    # negociada), con piso absoluto. Absorbe el residuo de redondeo flotante tras
    # vender-todo (un memecoin de qty ~1e9 acumula ~2e-7, muy sobre 1e-12) SIN
    # aplanar holdings reales (1e-9 de la qty es económicamente nulo).
    eps = max(_EPS_FLOOR, max(float(f["qty"]) for f in fills) * 1e-9)

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
            if qty <= eps:
                # abría un holding nuevo (estábamos flat) → nuevo entry_ts + pool limpio
                entry_ts_ms = int(f["time"])
                qty = 0.0
                cost = 0.0
            qty += received
            cost += buy_cost
        else:  # venta
            if qty <= eps:
                continue                  # vender sin posición (dato raro) → ignora
            avg = cost / qty
            sell_qty = min(fqty, qty)
            qty -= sell_qty
            cost -= sell_qty * avg        # ACB: remueve al promedio → avg invariante
            if qty <= eps:
                qty = 0.0
                cost = 0.0
                entry_ts_ms = None        # holding cerrado

    if qty <= eps:
        return {"status": "flat", "qty_viva": 0.0}
    return {
        "status": "ok",
        "qty_viva": qty,
        "avg_entry": cost / qty,
        "entry_ts_ms": entry_ts_ms,
    }
