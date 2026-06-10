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


def autocreate_spot_holdings(
    con: sqlite3.Connection, *, tenant_id: int, client, balances: dict[str, float],
    dry_run: bool = False,
) -> dict:
    """Descubre holds spot reales y auto-crea filas AUTO_DERIVED (observabilidad).

    Para cada asset con balance>0 (excluye quotes y Earn LD*) SIN fila EXTERNAL:
    busca el par con trades entre las 4 quotes, reconstruye el ACB, valida
    minNotional (no dust), y crea la fila con el BALANCE como qty (NO qty_viva —
    transfers a Earn reducen el balance spot sin ser ventas, Adrian #3) y el ACB
    como entry de referencia. Abstención: no_reconstruible (sin trades, F9), flat
    (vendido), dust (<minNotional), ingest_incompleto (ban → no persiste ACB
    truncado, F8). NO pisa filas OPERATOR del papá (F4). El caller posee la tx.

    Spec: 2026-06-10-binance-v02-autocreacion-observabilidad-spec.md §4, §5.
    """
    existing = {
        r["symbol"] for r in con.execute(
            "SELECT symbol FROM positions WHERE tenant_id=? AND control_domain='EXTERNAL' "
            "AND status='open'",
            (tenant_id,),
        ).fetchall()
    }
    created: list[str] = []
    abstained: dict[str, str] = {}

    for asset, amount in balances.items():
        if amount <= 0 or asset in _QUOTES or asset.startswith("LD"):
            continue  # quote / Earn (LD*) / sin balance → no candidato (Earn diferido)
        fills = None
        pair = None
        quote = None
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
        if pair in existing:
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
        if dry_run:
            created.append(pair)   # would-create: reporta sin INSERT
            continue
        row = _create_auto_derived(
            con, tenant_id=tenant_id, symbol=pair, qty=amount,
            avg_entry=acb["avg_entry"], entry_ts=_ms_to_iso(acb["entry_ts_ms"]),
        )
        if row is None:
            abstained[pair] = "ya_existe"
        else:
            created.append(pair)

    return {"created": created, "abstained": abstained}
