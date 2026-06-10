"""Reporte de realización de un tenant — read-only sobre el server de producción.

Usage: python -m tools.tenant_realization.report [--tenant 2] [--host atrium-aws]

Fetch: una sola llamada ssh → sqlite3 (mode=ro) → filas JSON de posiciones
cerradas del tenant. Cómputo local puro (testeable offline con fixtures).
Artefacto: data/realizacion/tenant<id>_<fecha>.json (gitignoreado — data
familiar sensible; el TOOL se versiona, la data no).

Métricas honestas:
- pnl_total_usd y retorno sobre capital DESPLEGADO (Σ size de cada trade,
  secuencial) — no sobre el balance nocional de la plataforma.
- Descomposición por exit_reason: MANUAL (criterio del operador, el hallazgo
  q2) vs señal (SL/TP/TIME_LIMIT).
- CI95 del pnl_pct por trade (t aproximada) — el número que decide cuándo
  esto deja de ser ruido.
"""
from __future__ import annotations
import argparse
import json
import math
import pathlib
import subprocess
import time

PROD_DB = "/var/www/trading/signals.db"
OUTPUT_DIR = "data/realizacion"
# Cierre discrecional del operador = conducta atribuible. MANUAL_AGENT (el
# operador confirma un cierre propuesto por el copiloto) es humano-en-el-loop,
# así que cuenta como conducta — NO como señal. (Corrige el bug que clasificaba
# MANUAL_AGENT del lado resultado; ver spec eje-conducta REV 2 §6/§7.)
MANUAL_REASONS = {"MANUAL", "MANUAL_AGENT"}

# BNC-12: el read-model de conducta lee SOLO actos del operador/señal
# (origin IN SIGNAL/OPERATOR). Las filas AUTO_DERIVED (reconstruidas por el
# sistema del trade history de Binance) son OBSERVABILIDAD, nunca conducta — si
# entraran, el sistema "llamaría disciplina a la suerte" (Voronov). El filtro
# vive AQUÍ, en el QUERY, NO en episode.py (que es proyección pura).
# Spec: 2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §2.
_QUERY = (
    "SELECT json_group_array(json_object("
    "'symbol', symbol, 'direction', direction, 'size_usd', size_usd,"
    "'pnl_usd', pnl_usd, 'pnl_pct', pnl_pct, 'exit_reason', exit_reason,"
    "'entry_ts', entry_ts, 'exit_ts', exit_ts, 'scan_id', scan_id)) "
    "FROM positions WHERE tenant_id={tenant} AND status='closed' "
    "AND pnl_usd IS NOT NULL AND origin IN ('SIGNAL', 'OPERATOR')"
)


def fetch_positions(tenant: int, host: str) -> list[dict]:
    """One read-only ssh round-trip → list of closed-position dicts."""
    sql = _QUERY.format(tenant=int(tenant))
    cmd = ["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", host,
           f"sqlite3 'file:{PROD_DB}?mode=ro' \"{sql};\""]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"ssh/sqlite3 fallo: {out.stderr.strip()}")
    return json.loads(out.stdout.strip())


def _mean_std(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / n
    return mu, math.sqrt(var)


def compute_report(positions: list[dict]) -> dict:
    """Pure function: positions → reporte. Testeable offline."""
    n = len(positions)
    pnl_total = sum(p["pnl_usd"] for p in positions)
    deployed = sum(p["size_usd"] or 0.0 for p in positions)
    pcts = [p["pnl_pct"] for p in positions if p["pnl_pct"] is not None]
    mu, sigma = _mean_std(pcts)
    se = sigma / math.sqrt(len(pcts)) if pcts else 0.0
    ci_lo, ci_hi = mu - 1.96 * se, mu + 1.96 * se

    manual = [p for p in positions if p["exit_reason"] in MANUAL_REASONS]
    señal = [p for p in positions if p["exit_reason"] not in MANUAL_REASONS]
    pnl_manual = sum(p["pnl_usd"] for p in manual)
    pnl_señal = sum(p["pnl_usd"] for p in señal)

    semanas: dict[str, dict] = {}
    for p in sorted(positions, key=lambda x: x["exit_ts"] or ""):
        wk = _iso_week(p["exit_ts"])
        s = semanas.setdefault(wk, {"n": 0, "pnl_usd": 0.0})
        s["n"] += 1
        s["pnl_usd"] = round(s["pnl_usd"] + p["pnl_usd"], 2)

    return {
        "n_trades": n,
        "pnl_total_usd": round(pnl_total, 2),
        "capital_desplegado_usd": round(deployed, 2),
        "retorno_sobre_desplegado_pct": round(100 * pnl_total / deployed, 3) if deployed else None,
        "wins": sum(1 for p in positions if p["pnl_usd"] > 0),
        "per_trade_pct": {
            "media": round(mu, 4), "sigma": round(sigma, 4),
            "ci95": [round(ci_lo, 4), round(ci_hi, 4)],
            "significativo": bool(ci_lo > 0 or ci_hi < 0),
        },
        "descomposicion_q2": {
            "manual": {"n": len(manual), "pnl_usd": round(pnl_manual, 2)},
            "señal": {"n": len(señal), "pnl_usd": round(pnl_señal, 2)},
            "fraccion_manual_del_pnl": round(pnl_manual / pnl_total, 3) if pnl_total else None,
        },
        "por_semana_iso": semanas,
    }


def _iso_week(ts: str | None) -> str:
    if not ts:
        return "?"
    import datetime
    d = datetime.date.fromisoformat(ts[:10])
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, default=2)
    ap.add_argument("--host", default="atrium-aws")
    args = ap.parse_args()

    positions = fetch_positions(args.tenant, args.host)
    report = compute_report(positions)
    report["tenant_id"] = args.tenant
    report["generado_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["fuente"] = f"{args.host}:{PROD_DB} (read-only)"

    out = pathlib.Path(OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d", time.gmtime())
    path = out / f"tenant{args.tenant}_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    pt = report["per_trade_pct"]
    q2 = report["descomposicion_q2"]
    print(f"tenant {args.tenant}: {report['n_trades']} trades, "
          f"P&L ${report['pnl_total_usd']} sobre ${report['capital_desplegado_usd']} desplegados "
          f"({report['retorno_sobre_desplegado_pct']}%)")
    print(f"per-trade: {pt['media']}% CI95 {pt['ci95']} -> "
          f"{'SIGNIFICATIVO' if pt['significativo'] else 'aun ruido'}")
    print(f"q2: manual {q2['manual']['n']} trades = ${q2['manual']['pnl_usd']} "
          f"({q2['fraccion_manual_del_pnl']} del total) vs señal {q2['señal']['n']} = ${q2['señal']['pnl_usd']}")
    print(f"artefacto: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
