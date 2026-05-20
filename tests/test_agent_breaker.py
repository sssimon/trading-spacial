"""Phase 5 of epic #400 — global circuit breaker.

Covers:
  - Explicit flag: cfg.agent.breaker_open=true → tripped
  - Automatic trip: 24h global spend >= cap → tripped
  - Negative / zero cap is interpreted as "no spend allowed" → tripped
  - DB failure during spend lookup → NOT tripped (fail-open, logged)
  - get_agent_status reflects the breaker reason
  - The status endpoint never leaks the cap value or the env-var name
  - /agent/turn 503s when the breaker is tripped (path identical to
    agent_disabled, but reason enum differs)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _seed_spend(amount_usd: float, *, hours_ago: float = 1):
    """Insert one agent_conversations row with the given cost_usd and
    a ts the given hours in the past."""
    import btc_api
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    con = btc_api.get_db()
    try:
        con.execute(
            "INSERT INTO agent_conversations "
            "(tenant_id, surface, conversation_id, ts, role, model, "
            " input_tokens, output_tokens, cache_read_input_tokens, "
            " cache_creation_input_tokens, latency_ms, cost_usd) "
            "VALUES (1, 'dock', 'c1', ?, 'assistant', 'claude-sonnet-4-6', "
            "        100, 50, 0, 0, 1000, ?)",
            (ts, amount_usd),
        )
        con.commit()
    finally:
        con.close()


# ── Explicit trip ──────────────────────────────────────────────────


def test_explicit_flag_trips_breaker(tmp_db):
    from api.agent.circuit_breaker import is_breaker_tripped

    cfg = {"agent": {"enabled": True, "breaker_open": True}}
    assert is_breaker_tripped(cfg) is True


def test_explicit_flag_false_does_not_trip(tmp_db):
    from api.agent.circuit_breaker import is_breaker_tripped

    cfg = {"agent": {"enabled": True, "breaker_open": False}}
    assert is_breaker_tripped(cfg) is False


def test_zero_cap_is_treated_as_explicit_trip(tmp_db):
    """An operator who sets global_daily_usd_cap=0 means 'allow no
    spend' — the breaker fires before any turn runs."""
    from api.agent.circuit_breaker import is_breaker_tripped

    cfg = {"agent": {"enabled": True, "global_daily_usd_cap": 0}}
    assert is_breaker_tripped(cfg) is True


# ── Automatic spend trip ───────────────────────────────────────────


def test_automatic_trip_when_24h_spend_exceeds_cap(tmp_db):
    """Seed >= $5 in the last 24h → breaker trips when default cap is
    in effect."""
    _seed_spend(3.00, hours_ago=2)
    _seed_spend(2.50, hours_ago=20)
    # Total in 24h window = $5.50; default cap is $5.00
    from api.agent.circuit_breaker import is_breaker_tripped

    cfg = {"agent": {"enabled": True}}  # no cap → DEFAULT_GLOBAL_DAILY_USD_CAP
    assert is_breaker_tripped(cfg) is True


def test_automatic_trip_respects_explicit_cap(tmp_db):
    """A higher operator-set cap ($10) leaves the $5.50 spend below
    threshold; breaker stays closed."""
    _seed_spend(3.00, hours_ago=2)
    _seed_spend(2.50, hours_ago=20)
    from api.agent.circuit_breaker import is_breaker_tripped

    cfg = {"agent": {"enabled": True, "global_daily_usd_cap": 10.00}}
    assert is_breaker_tripped(cfg) is False


def test_spend_older_than_24h_does_not_count(tmp_db):
    """A $100 spike 25h ago must NOT trip — the window is rolling 24h."""
    _seed_spend(100.00, hours_ago=25)
    from api.agent.circuit_breaker import is_breaker_tripped

    cfg = {"agent": {"enabled": True}}
    assert is_breaker_tripped(cfg) is False


def test_db_failure_fails_open(tmp_db, monkeypatch):
    """The breaker logic must NOT cut everyone off on a DB hiccup —
    it logs and returns False. We don't seed any spend; instead we
    monkeypatch the spend helper to raise."""
    from api.agent import circuit_breaker as cb

    def _boom():
        raise RuntimeError("simulated db down")

    monkeypatch.setattr(cb, "_global_spend_last_24h", _boom)
    cfg = {"agent": {"enabled": True}}
    assert cb.is_breaker_tripped(cfg) is False


def test_malformed_cap_falls_back_to_default(tmp_db):
    """An operator who typo'd the cap (string 'asdf', None) must not
    crash — fall back to the safer default."""
    from api.agent.circuit_breaker import is_breaker_tripped, DEFAULT_GLOBAL_DAILY_USD_CAP

    _seed_spend(DEFAULT_GLOBAL_DAILY_USD_CAP + 0.01, hours_ago=1)
    cfg = {"agent": {"enabled": True, "global_daily_usd_cap": "asdf"}}
    assert is_breaker_tripped(cfg) is True  # fall back to $5, exceeded


# ── Integration with /agent/status ─────────────────────────────────


def test_status_returns_breaker_open_when_tripped(tmp_db, monkeypatch):
    """get_agent_status surfaces the breaker as its own closed-enum
    reason, distinct from agent_disabled."""
    from api.agent.config import get_agent_status

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-test")
    cfg = {"agent": {"enabled": True, "breaker_open": True}}
    s = get_agent_status(cfg)
    assert s.enabled is False
    assert s.reason == "breaker_open"


def test_status_disabled_wins_over_breaker(tmp_db, monkeypatch):
    """Precedence: agent_disabled has higher precedence than
    breaker_open because the feature being off is a stronger signal
    than the rate-limit. The wire response should reflect that."""
    from api.agent.config import get_agent_status

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-test")
    cfg = {"agent": {"enabled": False, "breaker_open": True}}
    s = get_agent_status(cfg)
    assert s.reason == "agent_disabled"


def test_status_reason_does_not_leak_threshold(tmp_db, monkeypatch):
    """Belt-and-suspenders: even when the breaker IS tripped, the wire
    reason is just 'breaker_open' — never a numeric cap, never the env
    var name. Pre-reg §13.5."""
    from api.agent.config import get_agent_status

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-test")
    _seed_spend(99.99, hours_ago=1)
    cfg = {"agent": {"enabled": True, "global_daily_usd_cap": 1.00}}
    s = get_agent_status(cfg)
    assert s.reason == "breaker_open"
    # Wire fields are .enabled and .reason. Neither should contain
    # any sensitive substring.
    payload = f"{s.enabled} {s.reason}"
    for forbidden in ("99.99", "1.00", "global_daily_usd_cap", "ANTHROPIC_API_KEY"):
        assert forbidden not in payload


# ── current_global_spend_24h (read-only accessor) ─────────────────


def test_current_global_spend_24h_sums_window(tmp_db):
    from api.agent.circuit_breaker import current_global_spend_24h

    _seed_spend(1.23, hours_ago=1)
    _seed_spend(0.77, hours_ago=10)
    _seed_spend(99.0, hours_ago=30)  # outside window
    total = current_global_spend_24h()
    assert abs(total - 2.00) < 1e-9


def test_breaker_spend_excludes_non_assistant_rows(tmp_db):
    """PR #408 review pickup: the breaker's spend query filters to
    role='assistant' so a future phase that writes cost_usd on other
    role rows (e.g. 'partial' for cancellations) doesn't accidentally
    contribute to the breaker. Today, error rows have cost_usd IS NULL
    (which COALESCEs to 0), but we want the filter explicit before that
    invariant ever changes."""
    import btc_api
    from datetime import datetime, timedelta, timezone
    from api.agent.circuit_breaker import current_global_spend_24h

    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    con = btc_api.get_db()
    try:
        # An assistant row with $1.00 cost (counts).
        con.execute(
            "INSERT INTO agent_conversations "
            "(tenant_id, surface, conversation_id, ts, role, model, "
            " input_tokens, output_tokens, cache_read_input_tokens, "
            " cache_creation_input_tokens, latency_ms, cost_usd) "
            "VALUES (1, 'dock', 'c1', ?, 'assistant', 'claude-sonnet-4-6', "
            "        100, 50, 0, 0, 1000, 1.00)",
            (ts,),
        )
        # An error row WITH a non-NULL cost_usd of $99 — hypothetical
        # future schema where cancelled turns carry a partial cost.
        # Today's audit code writes cost_usd=NULL for errors, but the
        # filter must hold even if that changes.
        con.execute(
            "INSERT INTO agent_conversations "
            "(tenant_id, surface, conversation_id, ts, role, model, "
            " input_tokens, output_tokens, cache_read_input_tokens, "
            " cache_creation_input_tokens, latency_ms, cost_usd) "
            "VALUES (1, 'dock', 'c2', ?, 'error', 'claude-sonnet-4-6', "
            "        0, 0, 0, 0, 100, 99.00)",
            (ts,),
        )
        con.commit()
    finally:
        con.close()

    total = current_global_spend_24h()
    # Only the assistant row counts; the error row's $99 is excluded.
    assert abs(total - 1.00) < 1e-9
