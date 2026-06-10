"""Reconciliación SPOT read-only: balances reales de Binance → filas EXTERNAL.

v0.1 es UPDATE-only + reporte (NO inserta): actualiza la `qty` de las filas
EXTERNAL spot registradas (autoridad = Binance, §4.1), adopta `market='SPOT'`
en el primer sync (bootstrap), y reporta holds no-registrados para registro
manual (no auto-crea: entry_price es NOT NULL, sin cost-basis = v0.2).

`closed_pending` (derivado de qty≈0 con credencial ACTIVE) = señal para que el
humano confirme el cierre (CD-5: el sistema no escribe `closed`). El equity se
auto-corrige porque qty→0 (compute_real_equity hace qty×precio).

Spec: docs/superpowers/specs/es/2026-06-10-conexion-binance-solo-lectura-spec.md §4.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from binance_costbasis import reconstruct_acb
from data.providers.binance_account import BinanceRateBanned

_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD")


def _ms_to_iso(ms: int) -> str:
    """epoch ms (del primer fill del holding) → ISO 8601 UTC para entry_ts."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def base_asset(symbol: str) -> str:
    """'BTCUSDT' → 'BTC'. Asume quote en _QUOTES (spot v0.1)."""
    s = symbol.upper()
    for q in _QUOTES:
        if s.endswith(q):
            return s[: -len(q)]
    return s


def reconcile_spot(
    con: sqlite3.Connection, *, tenant_id: int, balances: dict[str, float], dust: float = 1e-6,
) -> dict:
    """Reconcilia las filas EXTERNAL spot del tenant contra los balances reales.

    `balances`: {asset: free+locked} de get_spot_balances(). El caller posee la tx.
    """
    rows = con.execute(
        "SELECT id, symbol, qty FROM positions "
        "WHERE tenant_id=? AND status='open' AND control_domain='EXTERNAL' "
        "AND (market='SPOT' OR market IS NULL)",
        (tenant_id,),
    ).fetchall()

    reconciled: list[str] = []
    closed_pending: list[str] = []
    tracked_assets: set[str] = set()

    for r in rows:
        symbol = r["symbol"]
        asset = base_asset(symbol)
        tracked_assets.add(asset)
        real_qty = float(balances.get(asset, 0.0))
        # market='SPOT' adoptado en el mismo UPDATE (el trigger lo permite: EXTERNAL).
        con.execute(
            "UPDATE positions SET qty=?, market='SPOT' WHERE id=?",
            (real_qty, r["id"]),
        )
        if real_qty <= dust:
            closed_pending.append(symbol)   # señal de cierre observado (derivado)
        else:
            reconciled.append(symbol)

    # Holds reales no-registrados (asset con balance > dust sin fila): se REPORTAN.
    untracked: list[str] = []
    for asset, amount in balances.items():
        if amount > dust and asset not in tracked_assets and asset not in _QUOTES:
            untracked.append(asset + "USDT")

    return {
        "reconciled": reconciled,
        "closed_pending": closed_pending,
        "untracked": untracked,
    }


def _create_auto_derived(
    con: sqlite3.Connection, *, tenant_id: int, symbol: str, qty: float,
    avg_entry: float, entry_ts: str, direction: str = "LONG",
) -> dict | None:
    """INSERT de una fila AUTO_DERIVED (observabilidad). origin='AUTO_DERIVED',
    market='SPOT', control_domain='EXTERNAL', scan_id=NULL. Idempotente por la tupla
    (tenant,symbol,market,direction) EXTERNAL — NO por entry_ts (BNC-5/F4). Si ya
    existe una fila EXTERNAL (OPERATOR o AUTO_DERIVED) para esa tupla → None: NO
    pisa ni re-crea (F4). El caller posee la tx."""
    symbol = symbol.upper()
    direction = direction.upper()
    existing = con.execute(
        "SELECT id FROM positions WHERE tenant_id=? AND symbol=? AND market='SPOT' "
        "AND direction=? AND control_domain='EXTERNAL'",
        (tenant_id, symbol, direction),
    ).fetchone()
    if existing is not None:
        return None
    size_usd = round(qty * avg_entry, 4)
    cur = con.execute(
        """INSERT INTO positions
               (scan_id, symbol, direction, status, entry_price, entry_ts,
                sl_price, tp_price, size_usd, qty, tenant_id, control_domain, market, origin)
           VALUES (NULL, ?, ?, 'open', ?, ?, NULL, NULL, ?, ?, ?, 'EXTERNAL', 'SPOT', 'AUTO_DERIVED')""",
        (symbol, direction, avg_entry, entry_ts, size_usd, qty, tenant_id),
    )
    cur2 = con.execute("SELECT * FROM positions WHERE id=?", (cur.lastrowid,))
    cols = [d[0] for d in cur2.description]
    return dict(zip(cols, cur2.fetchone()))


def plan_spot_autocreate(*, client, balances: dict[str, float], existing_symbols: set) -> dict:
    """FASE I/O (sin DB, SIN writer-lock): descubre holds, baja myTrades, reconstruye
    ACB, valida minNotional. Devuelve {plan, abstained}, donde plan =
    [{symbol, qty, avg_entry, entry_ts}].

    TODO el I/O de red vive AQUÍ, FUERA de cualquier transacción — Halberg
    (revisión holística): sostener el writer-lock (BEGIN IMMEDIATE) durante las
    llamadas a Binance reproduce el incidente de contención del login. El plan se
    aplica después en una tx CORTA (apply_spot_autocreate).

    Para cada asset con balance>0 (excluye quotes y Earn LD*) SIN fila EXTERNAL:
    usa el BALANCE como qty (NO qty_viva — transfers a Earn, Adrian #3) y el ACB
    como entry. Abstención: no_reconstruible (F9), flat, dust (<minNotional),
    ingest_incompleto (ban → F8). NO planifica símbolos ya-existentes (F4).
    Spec §4, §5.
    """
    plan: list[dict] = []
    abstained: dict[str, str] = {}
    for asset, amount in balances.items():
        if amount <= 0 or asset in _QUOTES or asset.startswith("LD"):
            continue  # quote / Earn (LD*) / sin balance → no candidato (Earn diferido)
        fills = pair = quote = None
        try:
            for q in _QUOTES:
                cand = asset + q
                f = client.get_my_trades(cand)
                if f:
                    fills, pair, quote = f, cand, q
                    break
        except BinanceRateBanned:
            abstained[asset] = "ingest_incompleto"   # F8: no persiste ACB truncado
            continue
        if not fills:
            abstained[asset] = "no_reconstruible"     # F9: sin trades en ninguna quote
            continue
        if pair in existing_symbols:
            abstained[pair] = "ya_existe"             # F4: no pisa OPERATOR/existente
            continue
        acb = reconstruct_acb(fills, base_asset=asset, quote_asset=quote)
        if acb["status"] != "ok":
            abstained[pair] = acb["status"]           # flat / no_fills
            continue
        # minNotional (no dust) — best-effort: solo descarta si SE PUEDE valuar.
        price = client.get_ticker_prices([pair]).get(pair)
        min_notional = (client.get_exchange_filters([pair]).get(pair) or {}).get("min_notional")
        if price is not None and min_notional is not None and amount * price < min_notional:
            abstained[pair] = "dust"
            continue
        plan.append({
            "symbol": pair, "qty": amount, "avg_entry": acb["avg_entry"],
            "entry_ts": _ms_to_iso(acb["entry_ts_ms"]),
        })
    return {"plan": plan, "abstained": abstained}


def apply_spot_autocreate(con: sqlite3.Connection, *, tenant_id: int, plan: list[dict]) -> list[str]:
    """FASE WRITE (tx CORTA del caller, sin I/O): aplica el plan = solo INSERTs.
    Re-chequea idempotencia (F4: no pisa OPERATOR). Devuelve los símbolos creados."""
    created: list[str] = []
    for item in plan:
        row = _create_auto_derived(
            con, tenant_id=tenant_id, symbol=item["symbol"], qty=item["qty"],
            avg_entry=item["avg_entry"], entry_ts=item["entry_ts"],
        )
        if row is not None:
            created.append(item["symbol"])
    return created


def autocreate_spot_holdings(
    con: sqlite3.Connection, *, tenant_id: int, client, balances: dict[str, float],
    dry_run: bool = False,
) -> dict:
    """Conveniencia: plan (I/O) + apply (writes) en una pasada sobre `con`.

    Apto para uso/test donde la contención del writer-lock NO es preocupación (DB
    en memoria o ventana sin tráfico). En PROD, el orquestador (sync_tenant) usa
    `plan_spot_autocreate` FUERA de la tx + `apply_spot_autocreate` DENTRO de una tx
    corta, para no sostener el lock durante la red (Halberg). El caller posee la tx.
    """
    existing = {
        r["symbol"] for r in con.execute(
            "SELECT symbol FROM positions WHERE tenant_id=? AND control_domain='EXTERNAL' "
            "AND status='open'",
            (tenant_id,),
        ).fetchall()
    }
    result = plan_spot_autocreate(client=client, balances=balances, existing_symbols=existing)
    if dry_run:
        return {"created": [it["symbol"] for it in result["plan"]], "abstained": result["abstained"]}
    created = apply_spot_autocreate(con, tenant_id=tenant_id, plan=result["plan"])
    return {"created": created, "abstained": result["abstained"]}
