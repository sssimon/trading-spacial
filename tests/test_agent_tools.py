"""Phase 1 of epic #400 — read-only tool layer.

Covers:

1. The audit schema is created by init_db (agent_conversations,
   agent_side_effects, agent_quotas).

2. Every per-user-table handler is strictly tenant-scoped. Two tenants
   seeded with positions in the same symbol → tenant 1's tool never
   surfaces tenant 2's rows, regardless of which read-only tool is
   invoked.

3. get_position_detail returns 'not_found' (not 'forbidden', not 500)
   when the id belongs to another tenant — never reveals existence
   cross-tenant. Same wire shape as 'truly absent', closing the IDOR
   side channel.

4. The dispatch surface validates inputs, dispatches by name, and
   surfaces handler errors as JSON content (never raises).

5. Catalog/schema parity (the import-time assert backstops this in
   prod; the test makes the failure mode explicit in CI).

6. Per-surface tool subsets match the matrix in api/agent/tools/registry.py.

Multi-tenant policy: every read-only tool's handler signature has
`tenant_id` as keyword-only required. This is checked statically (a
positional call raises TypeError) — see
test_handlers_signatures_require_tenant_id_keyword_only.
"""
from __future__ import annotations

import inspect
import json

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Same pattern as the other agent tests."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


# ── 1. Audit schema ────────────────────────────────────────────────────


def test_init_db_creates_agent_audit_tables(tmp_db):
    import btc_api
    con = btc_api.get_db()
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('agent_conversations', 'agent_side_effects', 'agent_quotas')"
        ).fetchall()
    finally:
        con.close()
    names = {r[0] for r in rows}
    assert names == {"agent_conversations", "agent_side_effects", "agent_quotas"}, (
        f"Missing agent audit table(s); found {names}"
    )


def test_agent_side_effects_idempotency_key_is_unique(tmp_db):
    """A double-insert with the same idempotency_key must raise IntegrityError.
    This is the property that makes the propose/confirm pattern safe against
    double-click in Phase 3 — pre-reg §10.2."""
    import sqlite3
    import btc_api
    con = btc_api.get_db()
    try:
        con.execute(
            "INSERT INTO agent_side_effects "
            "(tenant_id, conversation_id, ts, action, args_json, "
            " idempotency_key, result, http_status) "
            "VALUES (1, 'c1', '2026-05-19T10:00:00+00:00', 'close_position', "
            "'{}', 'key-a', 'ok', 200)"
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO agent_side_effects "
                "(tenant_id, conversation_id, ts, action, args_json, "
                " idempotency_key, result, http_status) "
                "VALUES (1, 'c1', '2026-05-19T10:00:01+00:00', 'close_position', "
                "'{}', 'key-a', 'ok', 200)"
            )
            con.commit()
    finally:
        con.close()


# ── 2. Tenant isolation per handler ─────────────────────────────────────


def _seed_two_tenant_positions(con):
    """Seed two open + two closed positions per tenant in the same symbol."""
    for tid, base_price in ((1, 50_000.0), (2, 60_000.0)):
        for direction, status, exit_price, exit_ts, pnl in (
            ("LONG", "open",   None,            None,                       None),
            ("LONG", "open",   None,            None,                       None),
            ("LONG", "closed", base_price * 1.02, "2026-05-15T12:00:00+00:00",  base_price * 0.02),
            ("LONG", "closed", base_price * 0.98, "2026-05-15T15:00:00+00:00", -base_price * 0.02),
        ):
            con.execute(
                "INSERT INTO positions "
                "(symbol, direction, status, entry_price, entry_ts, size_usd, "
                " exit_price, exit_ts, pnl_usd, tenant_id) "
                "VALUES ('BTCUSDT', ?, ?, ?, '2026-05-15T10:00:00+00:00', 1000, "
                " ?, ?, ?, ?)",
                (direction, status, base_price, exit_price, exit_ts, pnl, tid),
            )
    con.commit()


def test_get_positions_filters_strictly_by_tenant(tmp_db):
    from api.agent.tools.handlers import get_positions
    import btc_api

    con = btc_api.get_db()
    try:
        _seed_two_tenant_positions(con)
    finally:
        con.close()

    out_a = get_positions(tenant_id=1)
    out_b = get_positions(tenant_id=2)

    assert len(out_a["positions"]) == 2
    assert len(out_b["positions"]) == 2
    assert all(p["entry_price"] == 50_000.0 for p in out_a["positions"])
    assert all(p["entry_price"] == 60_000.0 for p in out_b["positions"])


def test_get_closed_trades_filters_strictly_by_tenant(tmp_db):
    from api.agent.tools.handlers import get_closed_trades
    import btc_api

    con = btc_api.get_db()
    try:
        _seed_two_tenant_positions(con)
    finally:
        con.close()

    a = get_closed_trades(tenant_id=1, window="30d")
    b = get_closed_trades(tenant_id=2, window="30d")

    assert len(a["trades"]) == 2
    assert len(b["trades"]) == 2
    a_pnls = sorted(t["pnl_usd"] for t in a["trades"])
    b_pnls = sorted(t["pnl_usd"] for t in b["trades"])
    # Verify the cross-tenant leak vector is closed: tenant 1's PnLs are
    # scaled to base 50k; tenant 2's to base 60k. Mixing would show up
    # immediately as a leak across the assertion.
    assert all(abs(p) == pytest.approx(1000.0) for p in a_pnls)
    assert all(abs(p) == pytest.approx(1200.0) for p in b_pnls)


# ── 3. get_position_detail IDOR contract ────────────────────────────────


def test_get_position_detail_returns_not_found_for_other_tenant(tmp_db):
    """Tenant 1 queries an id that exists but belongs to tenant 2. Result
    must be the same shape as querying an id that does not exist at all —
    never reveals existence."""
    from api.agent.tools.handlers import get_position_detail
    import btc_api

    con = btc_api.get_db()
    try:
        cur = con.execute(
            "INSERT INTO positions "
            "(symbol, direction, status, entry_price, entry_ts, size_usd, tenant_id) "
            "VALUES ('BTCUSDT', 'LONG', 'open', 50000, "
            "'2026-05-15T10:00:00+00:00', 1000, 2)"
        )
        con.commit()
        other_tenant_pos_id = cur.lastrowid
    finally:
        con.close()

    # tenant 1 asks for tenant 2's position id
    out_existing = get_position_detail(tenant_id=1, position_id=other_tenant_pos_id)
    # tenant 1 asks for an id that doesn't exist at all
    out_absent = get_position_detail(tenant_id=1, position_id=999_999)

    assert out_existing == {"error": "not_found"}
    assert out_absent == {"error": "not_found"}
    # Same response shape → indistinguishable to a probing attacker.


def test_get_position_detail_returns_row_for_own_tenant(tmp_db):
    from api.agent.tools.handlers import get_position_detail
    import btc_api

    con = btc_api.get_db()
    try:
        cur = con.execute(
            "INSERT INTO positions "
            "(symbol, direction, status, entry_price, entry_ts, size_usd, tenant_id) "
            "VALUES ('ETHUSDT', 'LONG', 'open', 3000, "
            "'2026-05-15T10:00:00+00:00', 500, 7)"
        )
        con.commit()
        own_pos_id = cur.lastrowid
    finally:
        con.close()

    out = get_position_detail(tenant_id=7, position_id=own_pos_id)
    assert out["id"] == own_pos_id
    assert out["symbol"] == "ETHUSDT"
    assert out["entry_price"] == 3000


# ── 4. Dispatch surface ─────────────────────────────────────────────────


def test_dispatch_unknown_tool_returns_error_json(tmp_db):
    from api.agent.tools.handlers import dispatch_tool
    out = dispatch_tool("nonexistent_tool", {}, tenant_id=1)
    parsed = json.loads(out)
    assert parsed == {"error": "unknown_tool", "name": "nonexistent_tool"}


def test_dispatch_invalid_input_returns_error_json(tmp_db):
    from api.agent.tools.handlers import dispatch_tool
    # get_position_detail requires `position_id: int >= 1`; pass a string.
    out = dispatch_tool("get_position_detail", {"position_id": "abc"}, tenant_id=1)
    parsed = json.loads(out)
    assert parsed["error"] == "invalid_input"
    assert "position_id" in parsed["detail"].lower() or "type" in parsed["detail"].lower()


def test_dispatch_handler_exception_returns_error_json(tmp_db, monkeypatch):
    """If a handler raises (DB unavailable, scanner error, etc), the
    dispatcher must serialize the failure — never propagate. The
    conversation core marks the tool_result as is_error:true so the
    model can self-correct."""
    from api.agent.tools import handlers as h
    from api.agent.tools.handlers import dispatch_tool

    def _boom(**kwargs):
        raise RuntimeError("simulated downstream failure")

    monkeypatch.setitem(h.TOOL_HANDLERS, "get_positions", _boom)
    out = dispatch_tool("get_positions", {}, tenant_id=1)
    parsed = json.loads(out)
    assert parsed["error"] == "handler_error"
    assert "simulated downstream failure" in parsed["detail"]


def test_dispatch_passes_validated_input_to_handler(tmp_db):
    """Round-trip: dispatch_tool deserializes raw_input via Pydantic and
    forwards the validated kwargs to the handler. Verify by inspecting
    what a stub handler receives."""
    from api.agent.tools import handlers as h
    from api.agent.tools.handlers import dispatch_tool

    received = {}

    def _spy(*, tenant_id, **kwargs):
        received["tenant_id"] = tenant_id
        received["kwargs"] = kwargs
        return {"ok": True}

    spy_orig = h.TOOL_HANDLERS["get_recent_signals"]
    try:
        h.TOOL_HANDLERS["get_recent_signals"] = _spy
        dispatch_tool(
            "get_recent_signals",
            {"limit": 5, "since_hours": 12},
            tenant_id=42,
        )
    finally:
        h.TOOL_HANDLERS["get_recent_signals"] = spy_orig

    assert received["tenant_id"] == 42
    assert received["kwargs"] == {"limit": 5, "since_hours": 12}


# ── 5. Multi-tenant policy: handlers signature ──────────────────────────


def test_handlers_signatures_require_tenant_id_keyword_only():
    """Static check: every handler's `tenant_id` parameter must be
    keyword-only AND have no default value. This makes positional misuse
    fail loudly and makes "forgot to pass tenant_id" a TypeError instead
    of a silent cross-tenant read.

    Pre-reg §5.1 hard requirement.
    """
    from api.agent.tools.handlers import TOOL_HANDLERS

    for name, fn in TOOL_HANDLERS.items():
        sig = inspect.signature(fn)
        tparam = sig.parameters.get("tenant_id")
        assert tparam is not None, f"{name}: missing tenant_id parameter"
        assert tparam.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name}: tenant_id must be KEYWORD_ONLY (got {tparam.kind})"
        )
        assert tparam.default is inspect.Parameter.empty, (
            f"{name}: tenant_id must NOT have a default value "
            f"(would allow silent omission)"
        )


def test_tenant_id_is_not_an_input_field_on_any_schema():
    """Multi-tenant policy: tenant_id is server-bound, never model-supplied.
    Any schema that exposes tenant_id is a regression."""
    from api.agent.tools.schemas import TOOL_INPUT_SCHEMAS

    for name, schema in TOOL_INPUT_SCHEMAS.items():
        fields = schema.model_fields.keys()
        assert "tenant_id" not in fields, (
            f"{name}: schema must NOT expose tenant_id as an input field "
            f"(it leaks the multi-tenant boundary to the model)"
        )
        assert "user_id" not in fields, (
            f"{name}: schema must NOT expose user_id as an input field"
        )


# ── 5b. Error-shape consistency across handlers (PR #403 review issue 1)


def test_get_portfolio_overview_surfaces_error_when_dashboard_fails(tmp_db, monkeypatch):
    """When get_dashboard_state raises, the handler must return a
    closed-shape error (consistent with get_kill_switch_state), NOT a
    partial result with null equity. Returning nulls would be ambiguous
    to the model — could read as "user has zero equity" vs. "we couldn't
    read it"."""
    from api.agent.tools import handlers as h

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated dashboard failure")

    # Patch the local reference inside the handler module.
    import health
    monkeypatch.setattr(health, "get_dashboard_state", _boom)
    out = h.get_portfolio_overview(tenant_id=1)
    assert out == {"error": "dashboard_unavailable"}


# ── 6. Registry consistency + per-surface subsets ───────────────────────


def test_tool_registry_matches_input_schemas():
    from api.agent.tools.registry import TOOL_CATALOG
    from api.agent.tools.schemas import TOOL_INPUT_SCHEMAS
    catalog_names = {t.name for t in TOOL_CATALOG}
    schema_names = set(TOOL_INPUT_SCHEMAS.keys())
    assert catalog_names == schema_names


def test_tool_registry_matches_handlers():
    from api.agent.tools.registry import TOOL_CATALOG
    from api.agent.tools.handlers import TOOL_HANDLERS
    catalog_names = {t.name for t in TOOL_CATALOG}
    handler_names = set(TOOL_HANDLERS.keys())
    assert catalog_names == handler_names


def test_tools_for_surface_dock_has_full_breadth():
    from api.agent.tools.registry import tools_for_surface
    tools = {t.name for t in tools_for_surface("dock")}
    # The Dock is the main floating chat; it gets the broadest tool set.
    # If you tighten the Dock subset in the future, update this assertion
    # deliberately — don't just delete the test.
    assert "get_portfolio_overview" in tools
    assert "get_positions" in tools
    assert "get_kill_switch_state" in tools
    assert "get_symbols_with_signals" in tools


def test_tools_for_surface_symbol_detail_excludes_kill_switch_and_tune():
    from api.agent.tools.registry import tools_for_surface
    tools = {t.name for t in tools_for_surface("symbol_detail")}
    # SymbolDetail is scoped to one symbol; kill-switch + tune live in
    # their dedicated views.
    assert "get_kill_switch_state" not in tools
    assert "get_tune_proposal" not in tools
    assert "get_symbol_setup" in tools


def test_tools_for_surface_autotune_excludes_position_writes():
    """Sanity: the autotune surface gets get_tune_proposal +
    get_closed_trades for context, but should never expose tools that
    would let the model read raw position state. (Phase 3 will add
    propose_apply_tune in a separate side-effects module — not a
    read-only tool, so it doesn't live in this catalog.)"""
    from api.agent.tools.registry import tools_for_surface
    tools = {t.name for t in tools_for_surface("autotune")}
    assert "get_tune_proposal" in tools
    assert "get_closed_trades" in tools
    assert "get_positions" not in tools
    assert "get_position_detail" not in tools


def test_tools_for_surface_unknown_surface_returns_empty():
    from api.agent.tools.registry import tools_for_surface
    assert tools_for_surface("nonexistent_surface") == tuple()
