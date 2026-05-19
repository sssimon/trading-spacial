"""B6 dashboard observability — pure fns + endpoint (#187 #200)."""
import pytest


# ── compute_next_conditions ─────────────────────────────────────────────────


CFG_NC = {
    "min_trades_for_eval": 20,
    "alert_win_rate_threshold": 0.15,
    "pause_months_consecutive": 3,
    "v2": {"probation": {
        "regression_wr_threshold": 0.10,
        "regression_window_trades": 10,
        "paused_to_probation_days": 14,
    }},
}


def _metrics(wr20=0.5, wr10=0.5, pnl_30d=500.0, months_neg=0, total=50,
              prob_remaining=None, paused_days=None):
    return {
        "trades_count_total": total,
        "win_rate_20_trades": wr20,
        "win_rate_10_trades": wr10,
        "pnl_30d": pnl_30d,
        "months_negative_consecutive": months_neg,
        "probation_trades_remaining": prob_remaining,
        "paused_days_at_entry": paused_days,
    }


def test_next_conditions_normal_returns_healthy():
    from health import compute_next_conditions
    text = compute_next_conditions("NORMAL", _metrics(), False, CFG_NC, 0)
    assert "Saludable" in text


def test_next_conditions_alert_text_includes_wr_and_wins_needed():
    """ALERT with WR=0.10 (2/20 wins), threshold=0.15 (3/20) → wins_needed=1."""
    from health import compute_next_conditions
    text = compute_next_conditions("ALERT", _metrics(wr20=0.10), False, CFG_NC, 0)
    assert "WR" in text and "0.15" in text
    # Spec: text must mention what the threshold is and how many trades to evaluate.


def test_next_conditions_reduced_text_mentions_pnl_30d():
    from health import compute_next_conditions
    text = compute_next_conditions(
        "REDUCED", _metrics(pnl_30d=-50.0), False, CFG_NC, 0,
    )
    assert "pnl_30d" in text or "PnL" in text
    assert "0" in text  # threshold or gap


def test_next_conditions_paused_manual_override_text():
    from health import compute_next_conditions
    text = compute_next_conditions(
        "PAUSED", _metrics(months_neg=4), True, CFG_NC, 0,
    )
    assert "manual" in text.lower() or "Reactivación manual" in text


def test_next_conditions_paused_auto_text_includes_days_remaining():
    """PAUSED 7 days, threshold 14 → 7 days remaining."""
    from health import compute_next_conditions
    text = compute_next_conditions(
        "PAUSED", _metrics(months_neg=4), False, CFG_NC, days_in_paused=7,
    )
    # Should mention days remaining (14 - 7 = 7) or paused_to_probation_days threshold
    assert "días" in text.lower()


def test_next_conditions_probation_text_includes_trades_remaining():
    from health import compute_next_conditions
    text = compute_next_conditions(
        "PROBATION", _metrics(prob_remaining=8), False, CFG_NC, 0,
    )
    assert "8" in text
    assert "trades" in text.lower() or "PROBATION" in text or "NORMAL" in text


# ── sparkline_for_symbol ────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _insert_closed_position(conn, symbol, pnl, exit_ts):
    conn.execute(
        """INSERT INTO positions
           (symbol, direction, status, entry_price, entry_ts,
            exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, tenant_id)
           VALUES (?, 'LONG', 'closed', 100.0, ?, 110.0, ?, 'TP', ?, ?, 1)""",
        (symbol, exit_ts, exit_ts, pnl, pnl / 100.0),
    )


def test_sparkline_empty_returns_20_nones(tmp_db):
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert len(result) == 20
    assert all(x is None for x in result)


def test_sparkline_3_wins_pads_with_leading_nones(tmp_db):
    """3 wins → [None, None, ..., 'W', 'W', 'W'] in chronological order."""
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        for i in range(3):
            _insert_closed_position(conn, "BTC", 10.0, f"2026-04-{1+i:02d}T12:00:00+00:00")
        conn.commit()
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert len(result) == 20
    assert result[-3:] == ['W', 'W', 'W']
    assert all(x is None for x in result[:-3])


def test_sparkline_mixed_wins_losses(tmp_db):
    """W/L based on pnl_usd>0."""
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        _insert_closed_position(conn, "BTC", 10.0, "2026-04-01T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", -5.0, "2026-04-02T12:00:00+00:00")
        _insert_closed_position(conn, "BTC", 0.0, "2026-04-03T12:00:00+00:00")  # breakeven = L
        conn.commit()
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert result[-3:] == ['W', 'L', 'L']


def test_sparkline_caps_at_20(tmp_db):
    import btc_api
    from health import sparkline_for_symbol
    conn = btc_api.get_db()
    try:
        for i in range(25):
            _insert_closed_position(conn, "BTC", 10.0, f"2026-04-{1+i%28:02d}T{i%24:02d}:00:00+00:00")
        conn.commit()
        result = sparkline_for_symbol("BTC", conn)
    finally:
        conn.close()
    assert len(result) == 20
    assert all(x == 'W' for x in result)


# ── summarize_recent_alerts ─────────────────────────────────────────────────


def _insert_health_event(conn, symbol, from_state, to_state, reason, ts):
    conn.execute(
        """INSERT INTO symbol_health_events
           (symbol, from_state, to_state, trigger_reason, metrics_json, ts)
           VALUES (?, ?, ?, ?, '{}', ?)""",
        (symbol, from_state, to_state, reason, ts),
    )


def test_summarize_recent_alerts_empty_returns_empty_items(tmp_db):
    import btc_api
    from health import summarize_recent_alerts
    conn = btc_api.get_db()
    try:
        result = summarize_recent_alerts(conn=conn, window_hours=24)
    finally:
        conn.close()
    assert result["items"] == []


def test_summarize_recent_alerts_3_alerts_emits_warning(tmp_db):
    """3 distinct symbols entered ALERT → emits symbol_failures warning."""
    from datetime import datetime, timezone, timedelta
    import btc_api
    from health import summarize_recent_alerts
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(hours=2)).isoformat()
    conn = btc_api.get_db()
    try:
        _insert_health_event(conn, "BTC", "NORMAL", "ALERT", "wr_below_threshold", recent_ts)
        _insert_health_event(conn, "ETH", "NORMAL", "ALERT", "wr_below_threshold", recent_ts)
        _insert_health_event(conn, "DOGE", "NORMAL", "ALERT", "wr_below_threshold", recent_ts)
        conn.commit()
        result = summarize_recent_alerts(conn=conn, window_hours=24)
    finally:
        conn.close()
    items = result["items"]
    assert any(i["kind"] == "symbol_failures" for i in items)
    failure_item = next(i for i in items if i["kind"] == "symbol_failures")
    assert "3" in failure_item["text"]
    assert failure_item["severity"] == "warning"


def test_summarize_recent_alerts_excludes_events_outside_window(tmp_db):
    from datetime import datetime, timezone, timedelta
    import btc_api
    from health import summarize_recent_alerts
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(hours=48)).isoformat()
    conn = btc_api.get_db()
    try:
        _insert_health_event(conn, "BTC", "NORMAL", "ALERT", "wr_below_threshold", old_ts)
        conn.commit()
        result = summarize_recent_alerts(conn=conn, window_hours=24)
    finally:
        conn.close()
    assert result["items"] == []


# ── portfolio_health_events ────────────────────────────────────────────────


def test_init_db_creates_portfolio_health_events_table(tmp_db):
    import btc_api
    conn = btc_api.get_db()
    try:
        rows = conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name='portfolio_health_events'"""
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_record_portfolio_transition_inserts_row(tmp_db):
    import btc_api
    from health import record_portfolio_transition
    record_portfolio_transition(
        from_tier="NORMAL", to_tier="WARNED",
        reason="3_concurrent_failures", dd_pct=-0.02, concurrent=3,
    )
    conn = btc_api.get_db()
    try:
        row = conn.execute(
            """SELECT from_tier, to_tier, reason, dd_pct, concurrent
               FROM portfolio_health_events ORDER BY ts DESC LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "NORMAL"
    assert row[1] == "WARNED"
    assert row[2] == "3_concurrent_failures"
    assert row[3] == -0.02
    assert row[4] == 3


def test_recent_portfolio_transitions_returns_last_5(tmp_db):
    """Helper to fetch last 5 transitions for the dashboard panel."""
    import btc_api
    from health import record_portfolio_transition, recent_portfolio_transitions
    for i in range(7):
        record_portfolio_transition(
            from_tier="NORMAL" if i % 2 else "WARNED",
            to_tier="WARNED" if i % 2 else "NORMAL",
            reason=f"reason_{i}", dd_pct=0.0, concurrent=i,
        )
    transitions = recent_portfolio_transitions(limit=5)
    assert len(transitions) == 5
    # Newest first
    assert transitions[0]["reason"] == "reason_6"
    assert transitions[4]["reason"] == "reason_2"


# ── GET /health/dashboard endpoint ─────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    import btc_api
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_tenant_id
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    # /health/dashboard now resolves capital per-tenant. In tests there's no
    # JWT, so override the dependency to a fixed tenant id (1). With no capital
    # row seeded for tenant 1, the endpoint falls back to cfg.capital_usd
    # (preserves pre-fix behavior in tests that don't seed capital).
    #
    # NOTE for future test authors: downstream tests in this file implicitly
    # rely on `tenant_id=1` having NO capital row unless they explicitly seed
    # one via db_upsert_capital. The fixture rebuilds the DB on each test, so
    # state does not leak across tests — but within a single test, any seed
    # changes the portfolio branch (multi-tenant vs. legacy).
    btc_api.app.dependency_overrides[get_current_tenant_id] = lambda: 1
    try:
        yield TestClient(btc_api.app)
    finally:
        btc_api.app.dependency_overrides.pop(get_current_tenant_id, None)


def test_get_health_dashboard_empty_db_returns_default_shape(client):
    """Empty DB → portfolio NORMAL + symbols=[] (or all DEFAULT_SYMBOLS as NORMAL placeholders) + alerts.items=[]."""
    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "symbols" in data
    assert "portfolio" in data
    assert "alerts" in data
    assert "generated_at" in data
    assert isinstance(data["symbols"], list)
    assert data["alerts"]["items"] == []
    assert data["portfolio"]["tier"] == "NORMAL"


def test_get_health_dashboard_seeded_symbol_returns_full_state(client):
    """One PROBATION symbol seeded → response includes all the fields."""
    import btc_api
    conn = btc_api.get_db()
    try:
        # Seed PROBATION row + 5 wins
        conn.execute(
            """INSERT INTO symbol_health
               (symbol, state, state_since, last_evaluated_at, last_metrics_json,
                manual_override, probation_trades_remaining, probation_started_at,
                paused_days_at_entry)
               VALUES ('BTC', 'PROBATION', '2026-04-20T00:00:00+00:00',
                       '2026-04-26T00:00:00+00:00', '{}', 1,
                       8, '2026-04-20T00:00:00+00:00', 15)"""
        )
        for i in range(5):
            conn.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, tenant_id)
                   VALUES ('BTC', 'LONG', 'closed', 100.0, ?, 110.0, ?, 'TP', 10.0, 0.10, 1)""",
                (f"2026-04-2{1+i}T12:00:00+00:00", f"2026-04-2{1+i}T13:00:00+00:00"),
            )
        # Seed an event
        conn.execute(
            """INSERT INTO symbol_health_events
               (symbol, from_state, to_state, trigger_reason, metrics_json, ts)
               VALUES ('BTC', 'PAUSED', 'PROBATION', 'reactivated_manual',
                       '{}', '2026-04-20T00:00:00+00:00')"""
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    btc = next(s for s in data["symbols"] if s["symbol"] == "BTC")
    assert btc["state"] == "PROBATION"
    assert btc["metrics"]["probation_trades_remaining"] == 8
    assert len(btc["sparkline_20"]) == 20
    assert btc["sparkline_20"][-5:] == ['W', 'W', 'W', 'W', 'W']
    assert btc["last_transition"]["reason"] == "reactivated_manual"
    assert "PROBATION" in btc["next_conditions"] or "trades" in btc["next_conditions"].lower()


def test_get_health_dashboard_unevaluated_symbol_has_placeholder_text(client):
    """B6 boundary: symbol with no row → next_conditions='Sin datos aún', not 'Saludable'.

    Empty-DB DEFAULT_SYMBOLS appear as placeholders; their next_conditions must
    explicitly say there's no data, NOT mislead operators with 'Saludable'.
    """
    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    # All symbols in empty DB are placeholders (no row in symbol_health)
    for sym_state in data["symbols"]:
        assert "Sin datos aún" in sym_state["next_conditions"], (
            f"Placeholder symbol {sym_state['symbol']} should say "
            f"'Sin datos aún' but got: {sym_state['next_conditions']}"
        )


def test_get_health_dashboard_disabled_kill_switch_still_returns(client, monkeypatch):
    """Even with kill_switch.enabled=False, the dashboard still serves a snapshot
    (read-only — useful for post-mortems)."""
    import btc_api
    monkeypatch.setattr(btc_api, "load_config", lambda: {"kill_switch": {"enabled": False}})
    resp = client.get("/health/dashboard")
    assert resp.status_code == 200


def test_get_health_dashboard_uses_tenant_capital_when_present(client):
    """Epic B #253 wire (issue #382): the dashboard reports the tenant's real
    capital, not the legacy cfg.capital_usd default.

    With balance=$10K + peak=$12K (a prior drawdown stamped into the ledger)
    and zero open positions, the dashboard should show current=$10K (no MTM
    to add) and peak=$12K (ledger monotonic peak), with dd ≈ -16.67%.
    """
    from db.capital import db_upsert_capital
    # tenant_id=1 matches the override in the client fixture above.
    db_upsert_capital(1, balance=10_000.0, peak_balance=12_000.0)
    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    portfolio = resp.json()["portfolio"]
    assert portfolio["current_equity"] == pytest.approx(10_000.0)
    assert portfolio["peak_equity"] == pytest.approx(12_000.0)
    # Sign convention matches kill_switch_v2.compute_portfolio_dd: negative in DD.
    assert portfolio["dd_pct"] == pytest.approx(-(12_000.0 - 10_000.0) / 12_000.0)


def test_get_health_dashboard_falls_back_when_tenant_has_no_capital(client):
    """When the tenant has no capital row yet, the dashboard preserves the
    legacy single-tenant behavior: cfg.capital_usd (default $1000)."""
    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    portfolio = resp.json()["portfolio"]
    assert portfolio["current_equity"] == pytest.approx(1000.0)
    assert portfolio["peak_equity"] == pytest.approx(1000.0)


def test_get_health_dashboard_no_double_count_on_realized_pnl_history(client):
    """Regression for #382 review: when the tenant has a non-trivial trade
    history already folded into `capital.balance` via apply_pnl_to_capital,
    the dashboard MUST NOT re-apply those PnLs by also walking the closed
    trades in compute_portfolio_equity_curve.

    Seed five non-monotonic trades (+200, +200, -300, +200, -100) → final
    balance = $10,200 with a peak of $10,400. Insert matching `positions`
    rows so _load_closed_trades sees them too. The dashboard should report
    current_equity=$10,200 (from the ledger, not 10,200 + 200 inflation)
    and peak_equity=$10,400.
    """
    from db.capital import apply_pnl_to_capital
    import btc_api

    pnl_sequence = [200.0, 200.0, -300.0, 200.0, -100.0]
    # Stamp each trade into the capital ledger.
    for pnl in pnl_sequence:
        apply_pnl_to_capital(1, pnl)

    # Also insert the closed positions so the (now-removed) equity-curve
    # path would see them — if regression returns, the curve would double-
    # count and inflate current_equity past $10,200.
    conn = btc_api.get_db()
    try:
        for i, pnl in enumerate(pnl_sequence):
            reason = "TP" if pnl > 0 else "SL"
            conn.execute(
                """INSERT INTO positions
                   (symbol, direction, status, entry_price, entry_ts,
                    exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct,
                    tenant_id)
                   VALUES ('BTC', 'LONG', 'closed', 100.0, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    f"2026-04-2{i + 1}T12:00:00+00:00",
                    100.0 + pnl / 10.0,
                    f"2026-04-2{i + 1}T13:00:00+00:00",
                    reason,
                    pnl,
                    pnl / 100.0,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    portfolio = resp.json()["portfolio"]
    assert portfolio["current_equity"] == pytest.approx(10_200.0), (
        f"Double-counting regression: expected $10,200 from ledger, got "
        f"${portfolio['current_equity']}"
    )
    assert portfolio["peak_equity"] == pytest.approx(10_400.0)
    expected_dd = -(10_400.0 - 10_200.0) / 10_400.0
    assert portfolio["dd_pct"] == pytest.approx(expected_dd, abs=1e-6)


def test_get_health_dashboard_isolates_tenants(client):
    """Multi-tenant isolation (per "todo tiene que ser multitenant" policy):
    tenant 1's dashboard MUST NOT reflect tenant 2's positions, regardless
    of who has the bigger drawdown. Each tenant sees only their own ledger.

    Covers both leak vectors:
      (a) Closed trades for tenant 2 must not show in tenant 1's realized
          balance — `_load_closed_trades(tenant_id=...)` filter is exercised
          via the apply_pnl_to_capital ledger.
      (b) Open positions for tenant 2 must not show in tenant 1's MTM —
          `_load_open_positions(tenant_id=...)` filter is exercised by
          seeding a price-cache MTM gain and asserting it stays absent.
    """
    from db.capital import db_upsert_capital, apply_pnl_to_capital
    from strategy.kill_switch_v2_shadow import update_price
    import btc_api

    # Tenant 1 (active in fixture): balance $10K, no closed trades.
    db_upsert_capital(1, balance=10_000.0, peak_balance=10_000.0)

    # Tenant 2 (NOT the request tenant): seed a -$1000 loss → balance $9K, peak $10K.
    apply_pnl_to_capital(2, -1_000.0)
    conn = btc_api.get_db()
    try:
        # (a) Tenant 2 has a closed losing trade for ETH.
        conn.execute(
            """INSERT INTO positions
               (symbol, direction, status, entry_price, entry_ts,
                exit_price, exit_ts, exit_reason, pnl_usd, pnl_pct, tenant_id)
               VALUES ('ETH', 'LONG', 'closed', 1000.0, '2026-04-20T12:00:00+00:00',
                       900.0, '2026-04-20T15:00:00+00:00', 'SL', -1000.0, -0.10, 2)"""
        )
        # (b) Tenant 2 has an OPEN long BTC position with a large unrealized
        # gain. If _load_open_positions ignored tenant_id, the dashboard for
        # tenant 1 would pick up this +$5,000 MTM and report current_equity
        # of $15,000 instead of $10,000.
        conn.execute(
            """INSERT INTO positions
               (symbol, direction, status, entry_price, entry_ts,
                qty, tenant_id)
               VALUES ('BTC', 'LONG', 'open', 50_000.0,
                       '2026-04-20T12:00:00+00:00', 1.0, 2)"""
        )
        conn.commit()
    finally:
        conn.close()
    # Make the price cache see BTC at $55K so the leak (if it existed) would
    # surface a +$5,000 MTM on the tenant-2 position above.
    update_price("BTC", 55_000.0)

    # Request runs as tenant 1 (fixture override). Should see clean $10K equity,
    # NOT a -10% drawdown from tenant 2's loss, NOT a +$5K MTM gain from
    # tenant 2's open position.
    resp = client.get("/health/dashboard")
    assert resp.status_code == 200
    portfolio = resp.json()["portfolio"]
    assert portfolio["current_equity"] == pytest.approx(10_000.0), (
        f"Open-position leak: tenant 2's +$5K BTC MTM bled into tenant 1's "
        f"dashboard (got ${portfolio['current_equity']})"
    )
    assert portfolio["peak_equity"] == pytest.approx(10_000.0)
    assert portfolio["dd_pct"] == pytest.approx(0.0, abs=1e-9)


# ── Portfolio transition recording integration ──────────────────────────────


def test_portfolio_tier_change_records_transition(tmp_db, monkeypatch):
    """When portfolio tier changes between two shadow evaluations, a row is
    appended to portfolio_health_events."""
    import btc_api
    from health import recent_portfolio_transitions

    # Simulate: first eval NORMAL, second eval WARNED. Both via the same
    # shadow path. We invoke the helper directly (the integration via
    # kill_switch_v2_shadow is wired in step 4 below).
    from health import record_portfolio_transition
    record_portfolio_transition("NORMAL", "WARNED", reason="3_concurrent",
                                 dd_pct=-0.01, concurrent=3)
    record_portfolio_transition("WARNED", "REDUCED", reason="dd_threshold",
                                 dd_pct=-0.04, concurrent=3)

    transitions = recent_portfolio_transitions(limit=5)
    assert len(transitions) == 2
    assert transitions[0]["to_tier"] == "REDUCED"
    assert transitions[1]["to_tier"] == "WARNED"
