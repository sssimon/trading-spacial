"""B.7 IDOR test suite — validates tenant isolation across enforced endpoints.

Pre-reg: docs/superpowers/specs/es/2026-05-16-multi-tenant-threat-model.md

Strategy:
- TestClient operates as synthetic test user (id=99 per auth.middleware._synthetic_test_user;
  updated from 0 → 99 by #446 Task 6 fix — PositionClosure USER mode requires
  caller_tenant_id > 0 to match production semantics)
- Cross-tenant data is seeded directly via DB (user_id=999 = "other user")
- Verify TestClient (acting as user 99) cannot see / mutate user 999's data
- Verify tampering vectors (query/header/body manipulation) are silently dropped

Coverage:
- positions (5 endpoints): list, open, edit, close, delete
- notifications (3): list, read-single, read-all
- signals (1): /signals/performance
- capital (2): GET, PUT
- preferences (2): GET, PUT
Total: 13 endpoints × cross-tenant + tampering scenarios.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


OTHER_USER_ID = 999  # synthetic "other user" — not the TestClient's identity (99)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with fresh DB + api_key='test-key' configured."""
    import btc_api
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    btc_api.init_db()

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "test-key"}))
    monkeypatch.setattr(btc_api, "CONFIG_FILE", str(cfg_path), raising=False)
    import api.config as _ac
    monkeypatch.setattr(_ac, "CONFIG_FILE", str(cfg_path), raising=False)
    monkeypatch.setattr(btc_api, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(_ac, "DEFAULTS_FILE", str(tmp_path / "no_def.json"), raising=False)
    monkeypatch.setattr(btc_api, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    monkeypatch.setattr(_ac, "SECRETS_FILE", str(tmp_path / "no_sec.json"), raising=False)
    return TestClient(btc_api.app)


def _seed_other_user_position(symbol: str = "BTCUSDT") -> int:
    """Insert position owned by OTHER_USER_ID. Returns pos_id.

    D Task 17: routes through `_build_open_request` + `db_create_position_sql`
    (the new birth path). `qty` derived from size_usd/entry to mirror what the
    legacy 5-deep fallback used to compute (now an explicit Pydantic field).
    """
    from api.positions_birth import _build_open_request
    from db.positions import db_create_position_sql
    from db.transaction import transaction
    entry, size = 80000.0, 500.0
    validated = _build_open_request(
        {"symbol": symbol, "entry_price": entry, "size_usd": size,
         "qty": size / entry, "direction": "LONG"},
        tenant_id=OTHER_USER_ID, idempotency_key=None,
    )
    with transaction() as con:
        pos = db_create_position_sql(con, validated)
    return pos["id"]


def _seed_own_position(symbol: str = "ETHUSDT") -> int:
    """Insert position owned by TestClient (user_id=99). D Task 17 birth path."""
    from api.positions_birth import _build_open_request
    from db.positions import db_create_position_sql
    from db.transaction import transaction
    entry, size = 2300.0, 300.0
    validated = _build_open_request(
        {"symbol": symbol, "entry_price": entry, "size_usd": size,
         "qty": size / entry, "direction": "LONG"},
        tenant_id=99, idempotency_key=None,
    )
    with transaction() as con:
        pos = db_create_position_sql(con, validated)
    return pos["id"]


# ---------------------------------------------------------------------------
# Positions endpoints (5) — cross-tenant isolation
# ---------------------------------------------------------------------------


class TestPositionsIDOR:
    def test_list_excludes_other_user_positions(self, client):
        _seed_other_user_position("BTCUSDT")
        _seed_own_position("ETHUSDT")
        resp = client.get("/positions")
        assert resp.status_code == 200
        symbols = [p["symbol"] for p in resp.json()["positions"]]
        assert "ETHUSDT" in symbols
        assert "BTCUSDT" not in symbols  # other user's invisible

    def test_list_query_tenant_id_tampering_ignored(self, client):
        """?tenant_id=999 in URL must NOT override JWT user_id."""
        other_pos = _seed_other_user_position("BTCUSDT")
        resp = client.get(f"/positions?tenant_id={OTHER_USER_ID}")
        assert resp.status_code == 200
        # Should NOT see other user's position even when asked via query
        positions = resp.json()["positions"]
        assert all(p["symbol"] != "BTCUSDT" for p in positions)

    def test_get_returns_only_own_position_count(self, client):
        _seed_other_user_position()
        _seed_other_user_position("ETHUSDT")
        _seed_own_position()
        resp = client.get("/positions")
        assert resp.json()["total"] == 1

    def test_edit_other_users_position_returns_404(self, client):
        other_id = _seed_other_user_position()
        resp = client.put(
            f"/positions/{other_id}",
            json={"sl_price": 79000},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_close_other_users_position_returns_404(self, client):
        other_id = _seed_other_user_position()
        resp = client.post(
            f"/positions/{other_id}/close",
            json={"exit_price": 81000},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_delete_other_users_position_returns_404(self, client):
        other_id = _seed_other_user_position()
        resp = client.delete(
            f"/positions/{other_id}",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_open_position_body_tenant_id_tampering_dropped(self, client):
        """POST body with tenant_id field must be rejected at the boundary.

        Pre-D behavior (B.5): tampered tenant_id in body was silently dropped;
        endpoint returned 200, position was stored under JWT user.

        D (#473) behavior — patology closed at the boundary: the Pydantic
        OpenPositionRequest uses `extra='forbid'`, so any unknown field
        (including tenant_id) is rejected with 422. The tampering attempt
        never reaches the storage layer at all; the row is never created.
        This is structurally stricter than silent-drop."""
        resp = client.post(
            "/positions",
            json={
                "symbol": "BTCUSDT", "entry_price": 80000,
                "size_usd": 500, "qty": 0.00625, "direction": "LONG",
                "tenant_id": OTHER_USER_ID,  # tampering attempt
            },
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 422
        # Verify the tampered tenant_id field is explicitly named in the rejection.
        # Shape: {"detail": {"error": "BodyValidationError",
        #                    "message": ..., "detail": [{loc: ["tenant_id"], ...}]}}
        body = resp.json()
        outer = body.get("detail")
        inner_detail = outer.get("detail", []) if isinstance(outer, dict) else (outer or [])
        assert any(
            "tenant_id" in str(e.get("loc", ()))
            for e in (inner_detail if isinstance(inner_detail, list) else [])
        ), f"expected tenant_id rejection in 422 detail; got {body!r}"
        # No row should have been created under either tenant.
        from db.positions import db_get_positions
        from db.transaction import transaction
        with transaction() as con:
            own_positions = db_get_positions(con, tenant_id=99)
            other_positions = db_get_positions(con, tenant_id=OTHER_USER_ID)
        assert len(own_positions) == 0
        assert len(other_positions) == 0

    def test_x_user_id_header_tampering_ignored(self, client):
        """Custom X-User-Id header must NOT override JWT-derived user."""
        _seed_other_user_position("BTCUSDT")
        _seed_own_position("ETHUSDT")
        resp = client.get(
            "/positions",
            headers={"X-User-Id": str(OTHER_USER_ID)},
        )
        # Sees only own data — header is ignored
        symbols = [p["symbol"] for p in resp.json()["positions"]]
        assert "ETHUSDT" in symbols
        assert "BTCUSDT" not in symbols


# ---------------------------------------------------------------------------
# Notifications endpoints (3) — cross-tenant isolation
# ---------------------------------------------------------------------------


class TestNotificationsIDOR:
    def _seed_notif(self, tenant_id: int, event_key: str = "test:1"):
        from notifier._storage import record_delivery
        from db.transaction import transaction
        with transaction() as con:
            return record_delivery(
                con,
                event_type="signal", event_key=event_key, priority="info",
                payload={"x": 1}, channels_sent=["telegram"], delivery_status="ok",
                tenant_id=tenant_id,
            )

    def test_list_excludes_other_user_notifications(self, client):
        self._seed_notif(OTHER_USER_ID, "other:1")
        self._seed_notif(99, "own:1")
        # GET /notifications uses verify_api_key — pass header
        resp = client.get("/notifications", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 200
        keys = [n["event_key"] for n in resp.json()["notifications"]]
        assert "own:1" in keys
        assert "other:1" not in keys

    def test_mark_read_other_user_notif_returns_404(self, client):
        other_id = self._seed_notif(OTHER_USER_ID)
        resp = client.post(
            f"/notifications/{other_id}/read",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 404

    def test_read_all_does_not_affect_other_user(self, client):
        # Seed unread for both
        self._seed_notif(OTHER_USER_ID, "other:1")
        self._seed_notif(OTHER_USER_ID, "other:2")
        own_id = self._seed_notif(99, "own:1")

        resp = client.post(
            "/notifications/read-all",
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["marked"] == 1  # only own queue affected

        # Verify other user's notifications still unread
        from notifier._storage import list_unread
        from db.transaction import transaction
        with transaction() as con:
            other_still_unread = list_unread(con, tenant_id=OTHER_USER_ID)
        assert len(other_still_unread) == 2


# ---------------------------------------------------------------------------
# Signals/performance endpoint — tenant isolation
# ---------------------------------------------------------------------------


class TestSignalsPerformanceIDOR:
    def _seed_signal_outcome(self, tenant_id: int, score: int, price_24h_higher: bool):
        from db.transaction import transaction
        with transaction() as con:
            signal_price = 100.0
            price_24h = 110.0 if price_24h_higher else 95.0
            con.execute(
                """INSERT INTO signal_outcomes
                   (scan_id, symbol, signal_ts, signal_price, score, macro_ok,
                    price_24h, max_runup_pct, max_drawdown_pct, status, tenant_id)
                   VALUES (NULL, ?, ?, ?, ?, 1, ?, 5.0, -2.0, 'completed', ?)""",
                (f"SYM{tenant_id}", f"2026-01-{tenant_id:02d}T00:00:00Z",
                 signal_price, score, price_24h, tenant_id),
            )

    def test_performance_excludes_other_user_outcomes(self, client):
        # Other user has 10 winning outcomes — would skew win_rate dramatically
        for i in range(10):
            self._seed_signal_outcome(OTHER_USER_ID, score=5, price_24h_higher=True)
        # Own user has 1 losing outcome
        self._seed_signal_outcome(99, score=3, price_24h_higher=False)

        resp = client.get("/signals/performance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_completed"] == 1  # only own data
        assert body["overall_win_rate"] == 0.0  # 0/1 — confirms isolation


# ---------------------------------------------------------------------------
# Capital endpoint — tenant isolation
# ---------------------------------------------------------------------------


class TestCapitalIDOR:
    def test_get_other_user_capital_invisible(self, client):
        """GET /capital with other user's data seeded must 404 for current user."""
        from db.capital import db_upsert_capital
        from db.transaction import transaction
        with transaction() as con:
            db_upsert_capital(con, OTHER_USER_ID, balance=999999.0)
        resp = client.get("/capital")
        assert resp.status_code == 404  # current user (id=99) has no capital

    def test_put_does_not_overwrite_other_user_capital(self, client):
        from db.capital import db_upsert_capital, db_get_capital
        from db.transaction import transaction
        with transaction() as con:
            db_upsert_capital(con, OTHER_USER_ID, balance=999999.0)
        # Current user PUTs their own
        resp = client.put(
            "/capital",
            json={"balance": 100.0},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        # Other user's capital unchanged
        with transaction() as con:
            other = db_get_capital(con, OTHER_USER_ID)
        assert other["balance"] == 999999.0


# ---------------------------------------------------------------------------
# User_preferences endpoint — tenant isolation
# ---------------------------------------------------------------------------


class TestPreferencesIDOR:
    def test_get_other_user_prefs_invisible(self, client):
        """GET /preferences returns DEFAULTS (not other user's prefs)."""
        from db.transaction import transaction
        from db.user_preferences import db_upsert_user_preferences
        with transaction() as con:
            db_upsert_user_preferences(
                con,
                tenant_id=OTHER_USER_ID, symbol_filter=["XAUTUSDT"], min_score=8,
                notify_channels={"telegram_chat_id": "OTHER"},
            )
        resp = client.get("/preferences")
        assert resp.status_code == 200
        body = resp.json()
        # Defaults returned — NOT other user's settings
        assert body["symbol_filter"] is None
        assert body["min_score"] == 4
        assert body["notify_channels"] is None

    def test_put_does_not_overwrite_other_user_prefs(self, client):
        from db.transaction import transaction
        from db.user_preferences import db_upsert_user_preferences, db_get_user_preferences
        with transaction() as con:
            db_upsert_user_preferences(con, tenant_id=OTHER_USER_ID, min_score=8)
        # Current user PUTs their own
        resp = client.put(
            "/preferences",
            json={"min_score": 3},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 200
        # Other user's prefs unchanged
        with transaction() as con:
            other = db_get_user_preferences(con, OTHER_USER_ID)
        assert other["min_score"] == 8


# ---------------------------------------------------------------------------
# Meta-test — verify Depends pattern wired on per-user endpoints
# ---------------------------------------------------------------------------


class TestEnforcementWiringMeta:
    """Inspects route handlers to confirm get_current_tenant_id is wired.

    This is the regression net for §4.2 middleware bypass: ensures every
    new per-user endpoint uses the dependency. If someone adds a new
    endpoint and forgets the Depends, this test fails.
    """

    def test_known_per_user_endpoints_use_dependency(self):
        """Inspect FastAPI route dependencies for each per-user endpoint."""
        import inspect
        from auth.dependencies import get_current_tenant_id

        # Each entry: (module_path, handler_name)
        per_user_handlers = [
            ("api.positions", "list_positions"),
            ("api.positions", "open_position"),
            ("api.positions", "edit_position"),
            ("api.positions", "close_position"),
            ("api.positions", "delete_position"),
            ("api.notifications", "get_notifications"),
            ("api.notifications", "post_notification_read"),
            ("api.notifications", "post_notifications_read_all"),
            ("api.signals", "get_signals_performance"),
            ("api.capital", "get_capital"),
            ("api.capital", "put_capital"),
            ("api.user_preferences", "get_preferences"),
            ("api.user_preferences", "put_preferences"),
        ]
        for module_path, name in per_user_handlers:
            import importlib
            mod = importlib.import_module(module_path)
            handler = getattr(mod, name)
            sig = inspect.signature(handler)
            # Look for a tenant_id parameter with Depends(get_current_tenant_id)
            tenant_param = sig.parameters.get("tenant_id")
            assert tenant_param is not None, (
                f"{module_path}.{name} missing tenant_id parameter — "
                f"per-user endpoint must use Depends(get_current_tenant_id)"
            )
            default = tenant_param.default
            # Default should be a Depends instance — check the dependency function
            from fastapi.params import Depends as FastAPIDepends
            assert isinstance(default, FastAPIDepends), (
                f"{module_path}.{name}::tenant_id must use Depends(...) — got {default!r}"
            )
            assert default.dependency is get_current_tenant_id, (
                f"{module_path}.{name}::tenant_id Depends must use get_current_tenant_id"
            )
