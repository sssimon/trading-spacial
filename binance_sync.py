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


_SL_TYPES = ("STOP_LOSS", "STOP_LOSS_LIMIT")
_TP_STOP_TYPES = ("TAKE_PROFIT", "TAKE_PROFIT_LIMIT")


def classify_open_orders(orders: list[dict], holdings_qty: dict[str, float]) -> list[dict]:
    """Función PURA (sin red, sin DB): órdenes crudas de get_open_orders() →
    [{symbol, kind, price, qty, pct_holding, order_id, oco_group}].

    Mapeo (solo side=SELL — un BUY es entrada pendiente, no protección):
    STOP_LOSS* → SL (stopPrice); TAKE_PROFIT* → TP (stopPrice);
    LIMIT_MAKER (pata alta de OCO) y LIMIT venta → TP (price).
    Patas OCO comparten orderListId → oco_group (orderListId=-1 → None).
    qty = origQty - executedQty (lo vivo). pct_holding = qty / holding del
    base asset (`holdings_qty` = balances {asset: free+locked}); sin holding
    conocido → None (se abstiene); orden > holding → pct real >1 SIN clamp
    (hecho observado, no se maquilla).

    Spec: docs/superpowers/specs/es/2026-06-11-binance-v03-sl-tp-observados-spec.md §3.
    """
    out: list[dict] = []
    for o in orders:
        if o.get("side") != "SELL":
            continue
        otype = o.get("type")
        if otype in _SL_TYPES:
            kind, price = "SL", float(o["stopPrice"])
        elif otype in _TP_STOP_TYPES:
            kind, price = "TP", float(o["stopPrice"])
        elif otype in ("LIMIT_MAKER", "LIMIT"):
            kind, price = "TP", float(o["price"])
        else:
            continue  # tipo desconocido/futuro: no se clasifica, no se inventa
        qty = float(o["origQty"]) - float(o.get("executedQty", 0) or 0)
        if qty <= 0 or price <= 0:
            continue
        symbol = o["symbol"].upper()
        held = holdings_qty.get(base_asset(symbol))
        pct = (qty / held) if held else None
        olist = int(o.get("orderListId", -1))
        out.append({
            "symbol": symbol, "kind": kind, "price": price, "qty": qty,
            "pct_holding": pct, "order_id": int(o["orderId"]),
            "oco_group": olist if olist != -1 else None,
        })
    return out


def apply_observed_orders(
    con: sqlite3.Connection, *, tenant_id: int, classified: list[dict], observed_at: str,
) -> dict:
    """FASE WRITE (tx CORTA del caller, sin I/O): snapshot fuente-de-verdad.

    (a) DELETE + reinserta observed_orders del tenant (sin estado incremental).
    (b) Resumen en cada fila EXTERNAL open: sl_price/tp_price = la orden de
        mayor qty de su kind; sin orden de ese kind → NULL (decisión Samuel
        2026-06-11: sin orden abierta = sin protección real — el dashboard
        nunca muestra protección ficticia). Aplica a OPERATOR y AUTO_DERIVED
        por igual; filas INTERNAL intocables (su SL/TP es del camino de
        control check_position_stops).

    Punto ciego cross-quote: el match es por símbolo exacto. Un SL colocado
    bajo otra quote (p.ej. BTCUSDC) NO se refleja en la fila BTCUSDT. Hoy
    autocreate nombra las filas `asset+USDT`, así que el comportamiento es
    consistente; pero si un usuario opera BTCUSDC manualmente, ese SL/TP
    queda invisible para esta función.

    Spec: docs/superpowers/specs/es/2026-06-11-binance-v03-sl-tp-observados-spec.md §5.
    """
    con.execute("DELETE FROM observed_orders WHERE tenant_id=?", (tenant_id,))
    for c in classified:
        con.execute(
            """INSERT INTO observed_orders
                   (tenant_id, symbol, kind, price, qty, pct_holding,
                    order_id, oco_group, observed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tenant_id, c["symbol"], c["kind"], c["price"], c["qty"],
             c["pct_holding"], c["order_id"], c["oco_group"], observed_at),
        )
    # Mejor orden por (symbol, kind) = la de mayor qty.
    best: dict[str, dict[str, dict]] = {}
    for c in classified:
        slot = best.setdefault(c["symbol"], {})
        cur = slot.get(c["kind"])
        if cur is None or c["qty"] > cur["qty"]:
            slot[c["kind"]] = c
    rows = con.execute(
        "SELECT id, symbol FROM positions "
        "WHERE tenant_id=? AND status='open' AND control_domain='EXTERNAL'",
        (tenant_id,),
    ).fetchall()
    summarized: list[str] = []
    for r in rows:
        slot = best.get(r["symbol"], {})
        sl, tp = slot.get("SL"), slot.get("TP")
        con.execute(
            "UPDATE positions SET sl_price=?, tp_price=? WHERE id=?",
            (sl["price"] if sl else None, tp["price"] if tp else None, r["id"]),
        )
        summarized.append(r["symbol"])
    return {"observed": len(classified), "summarized": summarized}


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
