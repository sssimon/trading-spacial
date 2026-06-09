"""Read-model del eje-conducta — v0.1 (spec 2026-06-09-integracion-eje-conducta REV 2).

Proyección PURA, read-only, retrospectiva sobre posiciones CERRADAS. Sin tabla
nueva, sin escritura, sin precio vivo. No toca el trading.

Ley de no-mezcla (INV-1, *no-validación-por-resultado*): ningún resultado
individual valida la decisión individual que lo produjo. Las métricas de FORMA
agregada (las 3 banderas, el aguante) leen R ex-post para CARACTERIZAR el estilo
de conducta — caracterizar la forma de muchos resultados NO es validar un acto
con su resultado, así que es legítimo (REV 2 §2/§4.3).

Todo lo que emite este módulo es de tipo RETROSPECTIVO (INV-7): describe el
pasado, no prescribe. Cero cifras de tipo LEY en v0.1.

Las proyecciones del eje-conducta (§4.2):
- cierre_discrecional   <- exit_reason ∈ {MANUAL, MANUAL_AGENT}
- apertura_discrecional <- scan_id IS NULL (manual, sin señal); None si el fetch no trae scan_id
- size_usd, hold_hours  <- directos
- costo_piso_usd        <- cota INFERIOR de fricción (INV-4); None si el símbolo no tiene tier
"""
from __future__ import annotations

import datetime
import math

from backtest_costs import UnknownSymbolError, load_calibration, tier_for_symbol

# El cierre discrecional del operador. {SL_HIT, TP_HIT} = outcome (mercado),
# {TIME_LIMIT_HIT} = sistema, status='cancelled'/exit_reason NULL = conducta con
# outcome nulo (se trata aparte, no es un cierre discrecional "limpio").
CONDUCT_EXIT_REASONS = {"MANUAL", "MANUAL_AGENT"}
RETRO = "RETROSPECTIVO"


def hold_hours(entry_ts: str | None, exit_ts: str | None) -> float | None:
    """Horas entre dos timestamps ISO. None si falta alguno."""
    if not entry_ts or not exit_ts:
        return None
    a = datetime.datetime.fromisoformat(entry_ts)
    b = datetime.datetime.fromisoformat(exit_ts)
    return (b - a).total_seconds() / 3600.0


def is_cierre_discrecional(exit_reason: str | None) -> bool:
    return exit_reason in CONDUCT_EXIT_REASONS


def costo_piso_usd(
    size_usd: float | None,
    symbol: str | None,
    hold_h: float | None,
    cal=None,
) -> float | None:
    """Cota INFERIOR de fricción de un trade (INV-4). NO es el costo real; es el
    piso (al menos esto). Devuelve None si el símbolo no tiene tier mapeado — NO
    se fabrica (spec §4.2).

    RT floor = stress_mult * (2*half_spread + 2*fee_per_side)  [13/18/30 bps por tier]
    funding add-on = funding_rate_bps_per_8h * floor(hold_hours / 8)  [0 para holds < 8h]
    """
    if size_usd is None or symbol is None:
        return None
    try:
        tier = tier_for_symbol(symbol)
    except UnknownSymbolError:
        return None
    cal = cal or load_calibration()
    tp = cal.tiers[tier]
    rt_bps = tp.stress_mult * (2.0 * tp.half_spread_bps + 2.0 * tp.fee_bps_per_side)
    intervals = max(0, math.floor((hold_h or 0.0) / 8.0))
    funding_bps = tp.funding_rate_bps_per_8h * intervals
    return round(size_usd * (rt_bps + funding_bps) / 1e4, 4)


def project_conduct(position: dict, cal=None) -> dict:
    """Aísla la parte-conducta de un episodio cerrado (proyecciones, no campos crudos)."""
    hh = hold_hours(position.get("entry_ts"), position.get("exit_ts"))
    # Distinguir "scan_id ausente del fetch" (desconocido -> None) de "scan_id NULL
    # en la fila" (apertura manual sin señal -> True).
    if "scan_id" not in position:
        apertura_discrecional = None
    else:
        apertura_discrecional = position["scan_id"] is None
    return {
        "cierre_discrecional": is_cierre_discrecional(position.get("exit_reason")),
        "apertura_discrecional": apertura_discrecional,
        "size_usd": position.get("size_usd"),
        "hold_hours": round(hh, 3) if hh is not None else None,
        "costo_piso_usd": costo_piso_usd(
            position.get("size_usd"), position.get("symbol"), hh, cal
        ),
        "tipo": RETRO,
    }


def _percentile(xs: list[float], p: float) -> float | None:
    """Percentil con interpolación lineal. None si la lista está vacía."""
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def _flag(count: int, *, suppress: bool) -> dict:
    """Estado de una bandera de conducta. Suprime (datos_insuficientes) cuando la
    sub-cohorte no alcanza para juzgar — no se inventa."""
    if suppress:
        return {"estado": "datos_insuficientes", "marcada": None, "tipo": RETRO}
    return {"estado": "marcada" if count > 0 else "limpia", "marcada": count, "tipo": RETRO}


def compute_flags(positions: list[dict], window: int = 30) -> dict:
    """Las 3 banderas de conducta (RETROSPECTIVO), sobre los últimos `window`
    trades por orden de entrada. Supresión por sub-cohorte (spec / panel REV 3):

    - revenge_trade: abrir < 4h después de cerrar un perdedor. Suprime si < 5 perdedores.
    - oversizing_tras_ganar: size > 1.5x mediana, justo tras un ganador. Suprime si ventana < 10.
    - aguantar_perdedores: hold de un perdedor > p75 de holds de ganadores. Suprime si < 8 ganadores o < 5 perdedores.
    """
    ordered = sorted([p for p in positions if p.get("entry_ts")], key=lambda x: x["entry_ts"])
    n = len(ordered)
    start = max(0, n - window)
    win = ordered[start:]
    losers = [p for p in win if (p.get("pnl_usd") or 0) < 0]
    winners = [p for p in win if (p.get("pnl_usd") or 0) > 0]
    sizes = [p["size_usd"] for p in win if p.get("size_usd") is not None]
    med = _percentile(sizes, 0.5)

    # #1 revenge-trade
    loser_exits = [p["exit_ts"] for p in ordered if (p.get("pnl_usd") or 0) < 0 and p.get("exit_ts")]
    revenge = 0
    for p in win:
        e = p.get("entry_ts")
        if not e:
            continue
        for xt in loser_exits:
            gap = hold_hours(xt, e)
            if gap is not None and 0 <= gap < 4:
                revenge += 1
                break
    f1 = _flag(revenge, suppress=len(losers) < 5)

    # #2 oversizing tras ganar
    oversizing = 0
    for i in range(start, n):
        if i == 0:
            continue
        prev = ordered[i - 1]
        cur = ordered[i]
        if (
            (prev.get("pnl_usd") or 0) > 0
            and cur.get("size_usd") is not None
            and med
            and cur["size_usd"] > 1.5 * med
        ):
            oversizing += 1
    f2 = _flag(oversizing, suppress=len(win) < 10)

    # #3 aguantar perdedores
    winner_holds = [
        h for h in (hold_hours(p.get("entry_ts"), p.get("exit_ts")) for p in winners) if h is not None
    ]
    p75 = _percentile(winner_holds, 0.75)
    holding = 0
    if p75 is not None:
        for p in losers:
            h = hold_hours(p.get("entry_ts"), p.get("exit_ts"))
            if h is not None and h > p75:
                holding += 1
    f3 = _flag(holding, suppress=(len(winners) < 8 or len(losers) < 5))

    return {
        "ventana": len(win),
        "revenge_trade": f1,
        "oversizing_tras_ganar": f2,
        "aguantar_perdedores": f3,
    }


def _iso_week(ts: str | None) -> str:
    if not ts:
        return "?"
    d = datetime.date.fromisoformat(ts[:10])
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def loss_budget(positions: list[dict], cap_usd: float | None = None, iso_week: str | None = None) -> dict:
    """Presupuesto de pérdida REALIZADA semanal (τ_b, control de ruina).

    perdida_realizada = Σ de pérdidas (pnl_usd < 0) de la semana ISO, número
    positivo. Los ganadores NO restan (es un control de ruina, no un neto). Si
    `cap_usd` es None: estado 'sin_tope' (solo muestra). El tope lo fija Samuel,
    NO se deriva del capital desplegado.
    """
    weekly: dict[str, float] = {}
    for p in positions:
        wk = _iso_week(p.get("exit_ts"))
        loss = -p["pnl_usd"] if (p.get("pnl_usd") or 0) < 0 else 0.0
        weekly[wk] = round(weekly.get(wk, 0.0) + loss, 2)
    if iso_week is None:
        iso_week = max((w for w in weekly if w != "?"), default=None)
    perdida = weekly.get(iso_week, 0.0)
    out = {"semana_iso": iso_week, "perdida_realizada_usd": perdida, "tipo": RETRO}
    if cap_usd is None:
        out.update({"estado": "sin_tope", "tope_usd": None, "restante_usd": None})
    else:
        out.update({
            "tope_usd": cap_usd,
            "restante_usd": round(cap_usd - perdida, 2),
            "estado": "excedido" if perdida > cap_usd else "dentro",
        })
    return out


def conducta_report(positions: list[dict], cap_usd: float | None = None, cal=None) -> dict:
    """Read-model v0.1: costo-piso agregado + banderas + presupuesto. RETROSPECTIVO.
    No persiste nada. `costo_piso_total_usd` es una COTA INFERIOR ('al menos $X');
    `n_simbolos_sin_tier` cuenta los trades cuyo piso no se pudo computar."""
    cal = cal or load_calibration()
    costo_total = 0.0
    n_sin_tier = 0
    for p in positions:
        c = costo_piso_usd(
            p.get("size_usd"), p.get("symbol"),
            hold_hours(p.get("entry_ts"), p.get("exit_ts")), cal,
        )
        if c is None:
            n_sin_tier += 1
        else:
            costo_total += c
    return {
        "costo_piso_total_usd": round(costo_total, 2),
        "costo_piso_nota": "cota inferior: al menos esto en fricción",
        "n_simbolos_sin_tier": n_sin_tier,
        "banderas": compute_flags(positions),
        "presupuesto_perdida": loss_budget(positions, cap_usd),
        "tipo": RETRO,
    }


def main() -> int:
    """CLI read-only del eje-conducta del papá. Reusa el fetch del monitor
    (ssh → sqlite mode=ro). Corre del lado de Samuel (necesita el server).

    Usage: python -m tools.conducta.episode [--tenant 2] [--host atrium-aws] [--cap 500]
    """
    import argparse
    import json

    from tools.tenant_realization.report import fetch_positions

    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, default=2)
    ap.add_argument("--host", default="atrium-aws")
    ap.add_argument("--cap", type=float, default=None, help="tope de pérdida realizada semanal (USD)")
    args = ap.parse_args()

    positions = fetch_positions(args.tenant, args.host)
    report = conducta_report(positions, cap_usd=args.cap)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    b = report["presupuesto_perdida"]
    print(
        f"\ntenant {args.tenant}: costo-piso (al menos) ${report['costo_piso_total_usd']}"
        f"{f' [{report['n_simbolos_sin_tier']} trades sin tier]' if report['n_simbolos_sin_tier'] else ''}"
    )
    print(f"presupuesto {b['semana_iso']}: perdiste ${b['perdida_realizada_usd']} → {b['estado']}"
          + (f" (queda ${b['restante_usd']})" if b["restante_usd"] is not None else ""))
    for nombre in ("revenge_trade", "oversizing_tras_ganar", "aguantar_perdedores"):
        f = report["banderas"][nombre]
        print(f"bandera {nombre}: {f['estado']}" + (f" ({f['marcada']})" if f["marcada"] else ""))
    print("\n[RETROSPECTIVO] te muestra lo que ya pasó. No predice. No aconseja sizing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
