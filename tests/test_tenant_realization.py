"""Tests offline del monitor de realización per-tenant (cómputo puro)."""
from __future__ import annotations

from tools.tenant_realization.report import compute_report


def _pos(pnl_usd, pnl_pct, reason="TIME_LIMIT_HIT", size=1000.0, exit_ts="2026-06-01T12:00:00"):
    return {"symbol": "X", "direction": "SHORT", "size_usd": size,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_reason": reason,
            "entry_ts": "2026-06-01T00:00:00", "exit_ts": exit_ts}


def test_totales_y_retorno_sobre_desplegado():
    r = compute_report([_pos(10.0, 1.0), _pos(-5.0, -0.5)])
    assert r["n_trades"] == 2
    assert r["pnl_total_usd"] == 5.0
    assert r["capital_desplegado_usd"] == 2000.0
    assert r["retorno_sobre_desplegado_pct"] == 0.25
    assert r["wins"] == 1


def test_descomposicion_q2_manual_vs_senal():
    r = compute_report([
        _pos(80.0, 8.0, reason="MANUAL"),
        _pos(10.0, 1.0, reason="TP_HIT"),
        _pos(10.0, 1.0, reason="SL_HIT"),
    ])
    q2 = r["descomposicion_q2"]
    assert q2["manual"]["n"] == 1 and q2["manual"]["pnl_usd"] == 80.0
    assert q2["señal"]["n"] == 2 and q2["señal"]["pnl_usd"] == 20.0
    assert q2["fraccion_manual_del_pnl"] == 0.8


def test_manual_agent_cuenta_como_conducta():
    # MANUAL_AGENT (operador confirma cierre propuesto por copiloto) es conducta,
    # no señal (fix spec eje-conducta REV 2).
    r = compute_report([
        _pos(80.0, 8.0, reason="MANUAL"),
        _pos(20.0, 2.0, reason="MANUAL_AGENT"),
        _pos(10.0, 1.0, reason="SL_HIT"),
    ])
    q2 = r["descomposicion_q2"]
    assert q2["manual"]["n"] == 2 and q2["manual"]["pnl_usd"] == 100.0
    assert q2["señal"]["n"] == 1 and q2["señal"]["pnl_usd"] == 10.0


def test_ci_incluye_cero_es_ruido():
    # media positiva pero n chico y sigma grande -> no significativo
    r = compute_report([_pos(5, 2.0), _pos(-4, -1.5), _pos(3, 1.0)])
    assert r["per_trade_pct"]["significativo"] is False
    lo, hi = r["per_trade_pct"]["ci95"]
    assert lo < 0 < hi


def test_ci_significativo_cuando_consistente():
    # 40 trades todos +1% con variacion minima -> CI lo > 0
    poss = [_pos(10, 1.0 + 0.01 * (i % 3)) for i in range(40)]
    r = compute_report(poss)
    assert r["per_trade_pct"]["significativo"] is True
    assert r["per_trade_pct"]["ci95"][0] > 0


def test_agrupacion_semanal_iso():
    r = compute_report([
        _pos(1.0, 0.1, exit_ts="2026-05-21T10:00:00"),  # W21
        _pos(2.0, 0.2, exit_ts="2026-05-30T10:00:00"),  # W22
        _pos(3.0, 0.3, exit_ts="2026-06-01T10:00:00"),  # W23
    ])
    assert set(r["por_semana_iso"]) == {"2026-W21", "2026-W22", "2026-W23"}
    assert r["por_semana_iso"]["2026-W23"]["pnl_usd"] == 3.0


def test_pnl_cero_no_divide():
    r = compute_report([_pos(0.0, 0.0)])
    assert r["descomposicion_q2"]["fraccion_manual_del_pnl"] is None
