"""Reconstrucción de cost-basis ACB desde fills de Binance (módulo puro).

Spec: 2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §3 (Task 4).

En spot NO hay "posiciones" con entry: hay un stream de compras/ventas. El
"precio de entrada" se RECONSTRUYE con ACB (weighted-average sobre la posición
viva), que coincide con el "Average Cost" que muestra la app de Binance.

`entry_ts` = inicio del holding CONTINUO actual (último cruce 0→>0); resetea en
un round-trip completo — y eso es CORRECTO (un recompra es un holding nuevo, F2).
"""
from __future__ import annotations

import pytest


def _fill(*, id, time, is_buyer, qty, quote_qty, commission=0.0, commission_asset="USDT"):
    return {
        "id": id, "time": time, "isBuyer": is_buyer,
        "qty": str(qty), "quoteQty": str(quote_qty),
        "commission": str(commission), "commissionAsset": commission_asset,
    }


def _acb(fills):
    from binance_costbasis import reconstruct_acb
    return reconstruct_acb(fills, base_asset="BTC", quote_asset="USDT")


def test_acb_simple_buys_weighted_average():
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100),
        _fill(id=2, time=2000, is_buyer=True, qty=1, quote_qty=200),
    ]
    out = _acb(fills)
    assert out["status"] == "ok"
    assert out["qty_viva"] == pytest.approx(2.0)
    assert out["avg_entry"] == pytest.approx(150.0)  # (100+200)/2


def test_acb_sell_preserves_average():
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=2, quote_qty=200),   # avg 100
        _fill(id=2, time=2000, is_buyer=False, qty=1, quote_qty=150),  # vende 1 @150
    ]
    out = _acb(fills)
    assert out["qty_viva"] == pytest.approx(1.0)
    assert out["avg_entry"] == pytest.approx(100.0)  # la venta NO mueve el avg


def test_acb_commission_in_quote_adds_to_cost():
    fills = [_fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100,
                   commission=0.1, commission_asset="USDT")]
    out = _acb(fills)
    assert out["avg_entry"] == pytest.approx(100.1)


def test_acb_commission_in_base_reduces_received_qty():
    fills = [_fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100,
                   commission=0.001, commission_asset="BTC")]
    out = _acb(fills)
    assert out["qty_viva"] == pytest.approx(0.999)
    assert out["avg_entry"] == pytest.approx(100.0 / 0.999)


def test_acb_commission_in_bnb_ignored_best_effort():
    fills = [_fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100,
                   commission=0.05, commission_asset="BNB")]
    out = _acb(fills)
    assert out["qty_viva"] == pytest.approx(1.0)
    assert out["avg_entry"] == pytest.approx(100.0)  # fee BNB ignorado (best-effort §11)


def test_acb_round_trip_resets_entry_ts():
    """vendió-todo-y-recompró → entry_ts = la recompra (F2: holding nuevo)."""
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100),
        _fill(id=2, time=2000, is_buyer=False, qty=1, quote_qty=120),  # cierra
        _fill(id=3, time=3000, is_buyer=True, qty=1, quote_qty=130),   # reabre
    ]
    out = _acb(fills)
    assert out["status"] == "ok"
    assert out["qty_viva"] == pytest.approx(1.0)
    assert out["avg_entry"] == pytest.approx(130.0)  # solo la recompra
    assert out["entry_ts_ms"] == 3000


def test_acb_continuous_entry_ts_is_first_buy():
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100),
        _fill(id=2, time=2000, is_buyer=True, qty=1, quote_qty=200),
    ]
    out = _acb(fills)
    assert out["entry_ts_ms"] == 1000  # holding continuo: el primer fill


def test_acb_no_fills_abstains():
    out = _acb([])
    assert out["status"] == "no_fills"


def test_acb_flat_when_sold_out():
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100),
        _fill(id=2, time=2000, is_buyer=False, qty=1, quote_qty=120),
    ]
    out = _acb(fills)
    assert out["status"] == "flat"
    assert out["qty_viva"] == pytest.approx(0.0)


def test_acb_large_qty_memecoin_flat_not_phantom():
    """Halberg #6: un memecoin de qty ~1e9 vendido-todo debe dar 'flat', NO un
    fantasma 'ok' con avg_entry corrupto. El eps RELATIVO absorbe el residuo
    flotante (~2e-7) que un eps absoluto 1e-12 dejaría como holding fantasma."""
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=1_000_000_000, quote_qty=10000),
        _fill(id=2, time=2000, is_buyer=False, qty=1_000_000_000, quote_qty=11000),
    ]
    out = _acb(fills)
    assert out["status"] == "flat", f"esperado flat, vivo {out}"
    assert out["qty_viva"] == pytest.approx(0.0)


def test_acb_two_consecutive_base_fee_buys_weighting():
    """Adrian #1: dos compras con fee-en-base consecutivas → ponderación correcta
    del avg sobre qty mermada por el fee."""
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100, commission=0.001, commission_asset="BTC"),
        _fill(id=2, time=2000, is_buyer=True, qty=1, quote_qty=200, commission=0.001, commission_asset="BTC"),
    ]
    out = _acb(fills)
    assert out["qty_viva"] == pytest.approx(1.998)        # (1-0.001)*2
    assert out["avg_entry"] == pytest.approx(300.0 / 1.998)


def test_acb_partial_sell_keeps_entry_ts():
    """Adrian #6 (invariante F2): una venta PARCIAL NO resetea entry_ts."""
    fills = [
        _fill(id=1, time=1000, is_buyer=True, qty=2, quote_qty=200),
        _fill(id=2, time=2000, is_buyer=False, qty=1, quote_qty=150),  # venta parcial
    ]
    out = _acb(fills)
    assert out["qty_viva"] == pytest.approx(1.0)
    assert out["entry_ts_ms"] == 1000, "una venta parcial no debe resetear entry_ts"


def test_acb_orders_unsorted_fills_by_time():
    """Fills desordenados se ordenan por time antes de reconstruir."""
    fills = [
        _fill(id=2, time=2000, is_buyer=True, qty=1, quote_qty=200),
        _fill(id=1, time=1000, is_buyer=True, qty=1, quote_qty=100),
    ]
    out = _acb(fills)
    assert out["entry_ts_ms"] == 1000  # el de time menor abre el holding
    assert out["avg_entry"] == pytest.approx(150.0)
