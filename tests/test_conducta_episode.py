"""Tests offline del read-model del eje-conducta (v0.1, cómputo puro).

Ver docs/superpowers/specs/es/2026-06-09-integracion-eje-conducta-spec.md (REV 2).
"""
from __future__ import annotations

from tools.conducta.episode import (
    compute_flags,
    conducta_report,
    costo_piso_usd,
    hold_hours,
    is_cierre_discrecional,
    loss_budget,
    project_conduct,
)

_OMIT = object()


def _pos(
    pnl_usd=10.0,
    pnl_pct=1.0,
    reason="MANUAL",
    size=1000.0,
    symbol="BTCUSDT",
    entry="2026-06-01T00:00:00",
    exit="2026-06-01T05:00:00",
    scan_id=_OMIT,
):
    p = {
        "symbol": symbol, "direction": "SHORT", "size_usd": size,
        "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_reason": reason,
        "entry_ts": entry, "exit_ts": exit,
    }
    if scan_id is not _OMIT:
        p["scan_id"] = scan_id
    return p


# ---------- costo-piso (INV-4) — anclado a la calibración v3 real ----------

def test_costo_piso_por_tier():
    # RT floor = 2*half_spread + 2*fee_per_side -> major 13 / mid 18 / small 30 bps
    assert costo_piso_usd(1000.0, "BTCUSDT", 5.0) == 1.30   # major
    assert costo_piso_usd(1000.0, "ADAUSDT", 5.0) == 1.80   # mid
    assert costo_piso_usd(1000.0, "PENDLEUSDT", 5.0) == 3.00  # small


def test_costo_piso_funding_addon_para_holds_largos():
    # hold 20h -> floor(20/8)=2 intervalos; major funding 1 bps/8h -> (13+2)=15 bps
    assert costo_piso_usd(1000.0, "BTCUSDT", 20.0) == 1.50


def test_costo_piso_simbolo_sin_tier_es_none_no_se_fabrica():
    assert costo_piso_usd(1000.0, "FOOUSDT", 5.0) is None


def test_costo_piso_size_none():
    assert costo_piso_usd(None, "BTCUSDT", 5.0) is None


# ---------- helpers de proyección ----------

def test_hold_hours():
    assert hold_hours("2026-06-01T00:00:00", "2026-06-01T05:00:00") == 5.0
    assert hold_hours(None, "2026-06-01T05:00:00") is None


def test_cierre_discrecional_incluye_manual_agent():
    assert is_cierre_discrecional("MANUAL") is True
    assert is_cierre_discrecional("MANUAL_AGENT") is True
    assert is_cierre_discrecional("SL_HIT") is False
    assert is_cierre_discrecional("TP_HIT") is False
    assert is_cierre_discrecional("TIME_LIMIT_HIT") is False


def test_project_conduct_proyecciones():
    p = _pos(reason="MANUAL_AGENT", size=500.0, symbol="ETHUSDT",
             entry="2026-06-01T00:00:00", exit="2026-06-01T05:00:00", scan_id=None)
    c = project_conduct(p)
    assert c["cierre_discrecional"] is True
    assert c["apertura_discrecional"] is True   # scan_id NULL = apertura manual
    assert c["size_usd"] == 500.0
    assert c["hold_hours"] == 5.0
    assert c["costo_piso_usd"] == 0.65          # ETH major 13 bps sobre 500
    assert c["tipo"] == "RETROSPECTIVO"


def test_project_conduct_apertura_desconocida_si_no_hay_scan_id():
    # el fetch actual NO trae scan_id -> apertura_discrecional desconocida (None),
    # NO se confunde con "manual"
    assert project_conduct(_pos())["apertura_discrecional"] is None


def test_project_conduct_apertura_desde_senal():
    assert project_conduct(_pos(scan_id=42))["apertura_discrecional"] is False


# ---------- las 3 banderas de conducta ----------

def test_bandera_revenge_marcada():
    poss = [
        _pos(pnl_usd=-5, entry="2026-06-01T00:00:00", exit="2026-06-01T02:00:00"),  # perdedor A
        _pos(pnl_usd=3,  entry="2026-06-01T03:00:00", exit="2026-06-01T06:00:00"),  # abre 1h tras A -> revenge
        _pos(pnl_usd=-5, entry="2026-06-02T00:00:00", exit="2026-06-02T02:00:00"),  # perdedor
        _pos(pnl_usd=-5, entry="2026-06-03T00:00:00", exit="2026-06-03T02:00:00"),  # perdedor
        _pos(pnl_usd=-5, entry="2026-06-04T00:00:00", exit="2026-06-04T02:00:00"),  # perdedor
        _pos(pnl_usd=-5, entry="2026-06-05T00:00:00", exit="2026-06-05T02:00:00"),  # perdedor (5 total)
    ]
    f = compute_flags(poss)["revenge_trade"]
    assert f["estado"] == "marcada"
    assert f["marcada"] == 1


def test_bandera_revenge_suprimida_con_pocos_perdedores():
    poss = [
        _pos(pnl_usd=-5, entry="2026-06-01T00:00:00", exit="2026-06-01T02:00:00"),
        _pos(pnl_usd=3,  entry="2026-06-01T03:00:00", exit="2026-06-01T06:00:00"),
    ]
    assert compute_flags(poss)["revenge_trade"]["estado"] == "datos_insuficientes"


def test_bandera_oversizing_marcada():
    poss = []
    for i in range(9):
        poss.append(_pos(pnl_usd=(5 if i % 2 == 0 else -5), size=1000.0,
                         entry=f"2026-06-{i + 1:02d}T00:00:00", exit=f"2026-06-{i + 1:02d}T05:00:00"))
    # #10 entra tras un ganador (i=8 -> par -> ganador) con size 3x la mediana (1000)
    poss.append(_pos(pnl_usd=1, size=3000.0,
                     entry="2026-06-10T00:00:00", exit="2026-06-10T05:00:00"))
    f = compute_flags(poss)["oversizing_tras_ganar"]
    assert f["estado"] == "marcada"
    assert f["marcada"] == 1


def test_bandera_oversizing_suprimida_ventana_chica():
    poss = [_pos(pnl_usd=5, size=1000.0, entry="2026-06-01T00:00:00", exit="2026-06-01T05:00:00"),
            _pos(pnl_usd=1, size=3000.0, entry="2026-06-02T00:00:00", exit="2026-06-02T05:00:00")]
    assert compute_flags(poss)["oversizing_tras_ganar"]["estado"] == "datos_insuficientes"


def test_bandera_aguantar_perdedores_marcada():
    poss = []
    for i in range(8):  # 8 ganadores, hold corto (2h)
        poss.append(_pos(pnl_usd=5, entry=f"2026-06-{i + 1:02d}T00:00:00", exit=f"2026-06-{i + 1:02d}T02:00:00"))
    for i in range(4):  # 4 perdedores cortos
        poss.append(_pos(pnl_usd=-5, entry=f"2026-06-{i + 10:02d}T00:00:00", exit=f"2026-06-{i + 10:02d}T02:00:00"))
    # 1 perdedor aguantado 50h (>> p75 de ganadores = 2h)
    poss.append(_pos(pnl_usd=-5, entry="2026-06-20T00:00:00", exit="2026-06-22T02:00:00"))
    f = compute_flags(poss)["aguantar_perdedores"]
    assert f["estado"] == "marcada"
    assert f["marcada"] == 1


def test_bandera_aguantar_suprimida_sin_suficientes_ganadores():
    poss = [_pos(pnl_usd=-5, entry="2026-06-01T00:00:00", exit="2026-06-05T00:00:00"),
            _pos(pnl_usd=5, entry="2026-06-02T00:00:00", exit="2026-06-02T02:00:00")]
    assert compute_flags(poss)["aguantar_perdedores"]["estado"] == "datos_insuficientes"


# ---------- presupuesto de pérdida (τ_b) ----------

def test_presupuesto_sin_tope():
    b = loss_budget([_pos(pnl_usd=-30, exit="2026-06-03T05:00:00")], cap_usd=None)
    assert b["estado"] == "sin_tope"
    assert b["perdida_realizada_usd"] == 30.0
    assert b["restante_usd"] is None


def test_presupuesto_dentro():
    poss = [
        _pos(pnl_usd=-40, exit="2026-06-03T05:00:00"),
        _pos(pnl_usd=-10, exit="2026-06-03T09:00:00"),
        _pos(pnl_usd=100, exit="2026-06-03T12:00:00"),  # ganador no cuenta para pérdida
    ]
    b = loss_budget(poss, cap_usd=100.0)
    assert b["perdida_realizada_usd"] == 50.0
    assert b["estado"] == "dentro"
    assert b["restante_usd"] == 50.0


def test_presupuesto_excedido():
    poss = [_pos(pnl_usd=-40, exit="2026-06-03T05:00:00"),
            _pos(pnl_usd=-10, exit="2026-06-03T09:00:00")]
    b = loss_budget(poss, cap_usd=30.0)
    assert b["estado"] == "excedido"
    assert b["restante_usd"] == -20.0


# ---------- read-model integrado ----------

def test_conducta_report_integra_y_es_retrospectivo():
    poss = [_pos(pnl_usd=-30, size=1000.0, symbol="BTCUSDT",
                 entry="2026-06-03T00:00:00", exit="2026-06-03T05:00:00")]  # hold 5h, funding 0
    r = conducta_report(poss, cap_usd=100.0)
    assert r["tipo"] == "RETROSPECTIVO"
    assert r["costo_piso_total_usd"] == 1.30
    assert r["n_simbolos_sin_tier"] == 0
    assert r["presupuesto_perdida"]["estado"] == "dentro"
    assert set(r["banderas"]) == {"ventana", "revenge_trade", "oversizing_tras_ganar", "aguantar_perdedores"}


def test_conducta_report_cuenta_simbolos_sin_tier():
    poss = [_pos(symbol="FOOUSDT"), _pos(symbol="BTCUSDT")]
    r = conducta_report(poss)
    assert r["n_simbolos_sin_tier"] == 1
