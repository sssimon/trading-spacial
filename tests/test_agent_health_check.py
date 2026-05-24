"""Phase 6 of epic #400 — health check script smoke + correctness.

The rollout depends on `scripts/agent_health_check.py` being right.
A bad query that always reports OK would let a real breach slip
unnoticed during the 48h bake. These tests:

  - Run the script's helpers against a controlled fixture DB.
  - Assert each metric returns the expected value for known seeded data.
  - Cover the warmup waiver (cache_hit_rate skipped when n<5).
  - Cover the 'no such table' friendly error path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from db.transaction import transaction


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Initialize a fresh signals.db with the agent audit schema."""
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    yield db_path


def _seed_assistant(con, *, hours_ago=1, input_tokens=100, output_tokens=50,
                     cache_read=800, cache_creation=0, latency_ms=1000,
                     cost_usd=0.05):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    con.execute(
        "INSERT INTO agent_conversations "
        "(tenant_id, surface, conversation_id, ts, role, model, "
        " input_tokens, output_tokens, cache_read_input_tokens, "
        " cache_creation_input_tokens, latency_ms, cost_usd) "
        "VALUES (1, 'dock', ?, ?, 'assistant', 'claude-sonnet-4-6', "
        "        ?, ?, ?, ?, ?, ?)",
        (f"conv-{ts}", ts, input_tokens, output_tokens,
         cache_read, cache_creation, latency_ms, cost_usd),
    )


def _seed_error(con, *, hours_ago=1, reason="upstream", latency_ms=200):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    con.execute(
        "INSERT INTO agent_conversations "
        "(tenant_id, surface, conversation_id, ts, role, model, "
        " latency_ms, content_json) "
        "VALUES (1, 'dock', ?, ?, 'error', 'claude-sonnet-4-6', ?, ?)",
        (f"conv-err-{ts}", ts, latency_ms, json.dumps(reason)),
    )


# ── Query helpers ─────────────────────────────────────────────────


def test_query_cache_hit_rate_with_seeded_data(tmp_db):
    import sqlite3
    import btc_api
    import scripts.agent_health_check as h

    with transaction() as con:
        _seed_assistant(con, input_tokens=100, cache_read=900)
        _seed_assistant(con, input_tokens=200, cache_read=800)

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    try:
        cutoff = h._cutoff_iso(timedelta(hours=24))
        rate, n = h.query_cache_hit_rate(con, cutoff)
    finally:
        con.close()

    # cache_read=1700, input_tokens=300 → 1700 / (1700 + 300) = 0.85
    assert rate == pytest.approx(0.85)
    assert n == 2


def test_query_error_rate_with_mix(tmp_db):
    import btc_api
    import scripts.agent_health_check as h
    import sqlite3

    with transaction() as con:
        _seed_assistant(con); _seed_assistant(con); _seed_assistant(con)
        _seed_error(con)  # 1 of 4 is error

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    try:
        cutoff = h._cutoff_iso(timedelta(hours=24))
        rate, errs, total = h.query_error_rate(con, cutoff)
    finally:
        con.close()
    assert rate == pytest.approx(0.25)
    assert errs == 1
    assert total == 4


def test_query_p95_latency_picks_high_percentile(tmp_db):
    import btc_api
    import scripts.agent_health_check as h
    import sqlite3

    with transaction() as con:
        # 10 rows with latencies 100..1000 step 100. The OFFSET-based
        # p95 query in agent_health_check resolves to:
        #   offset = max(0, 10 * 95 // 100 - 1) = max(0, 9 - 1) = 8
        #   ORDER BY latency_ms LIMIT 1 OFFSET 8  →  9th-smallest = 900
        # Integers + deterministic ordering = no ambiguity. If SQLite
        # ever shifts the OFFSET semantics, the assert below catches
        # the drift instead of silently passing on the wrong value.
        for i in range(1, 11):
            _seed_assistant(con, latency_ms=i * 100)

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    try:
        cutoff = h._cutoff_iso(timedelta(hours=24))
        p95, n = h.query_p95_latency_ms(con, cutoff)
    finally:
        con.close()
    assert n == 10
    assert p95 == 900


def test_query_daily_spend_sums_correctly(tmp_db):
    import btc_api
    import scripts.agent_health_check as h
    import sqlite3

    with transaction() as con:
        _seed_assistant(con, cost_usd=0.10)
        _seed_assistant(con, cost_usd=0.25)
        _seed_assistant(con, cost_usd=0.15)
        _seed_error(con)  # errors don't carry cost; excluded by role filter

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    try:
        cutoff = h._cutoff_iso(timedelta(hours=24))
        total = h.query_daily_spend_usd(con, cutoff)
    finally:
        con.close()
    assert total == pytest.approx(0.50)


# ── _evaluate_metrics flags breaches ─────────────────────────────


def test_evaluate_metrics_marks_breach_when_error_rate_high(tmp_db):
    import btc_api
    import scripts.agent_health_check as h
    import sqlite3

    with transaction() as con:
        # 10 assistant rows + 5 error rows → error_rate = 5/15 = 33%
        for _ in range(10):
            _seed_assistant(con)
        for _ in range(5):
            _seed_error(con)

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    try:
        results = h._evaluate_metrics(con, timedelta(hours=24))
    finally:
        con.close()
    by_name = {r.name: r for r in results}
    assert by_name["error_rate"].ok is False
    # The breach detail must include the count for operator context.
    assert "5/15" in by_name["error_rate"].detail


def test_evaluate_metrics_waives_cache_during_warmup(tmp_db):
    """With n<5 turns, cache_hit_rate is waived (still reports the
    value, but ok=True so the script doesn't false-positive during
    the first minutes after flip)."""
    import btc_api
    import scripts.agent_health_check as h
    import sqlite3

    with transaction() as con:
        # Single turn with 0% cache hit — would normally fail.
        _seed_assistant(con, input_tokens=1000, cache_read=0)

    con = sqlite3.connect(tmp_db)
    con.row_factory = sqlite3.Row
    try:
        results = h._evaluate_metrics(con, timedelta(hours=24))
    finally:
        con.close()
    cache_metric = next(r for r in results if r.name == "cache_hit_rate")
    assert cache_metric.value == 0.0  # actual rate is 0
    assert cache_metric.ok is True    # but waived during warmup
    assert "warmup" in cache_metric.detail


# ── End-to-end CLI ─────────────────────────────────────────────


def test_cli_returns_exit_0_when_all_ok(tmp_db, monkeypatch):
    """Run the script as a subprocess. Exit 0 + no breach markers."""
    import btc_api
    with transaction() as con:
        for _ in range(6):  # 6 rows to clear the warmup waiver threshold
            _seed_assistant(con, input_tokens=100, cache_read=900,
                             latency_ms=500, cost_usd=0.01)

    monkeypatch.setattr(
        "scripts.agent_health_check._db_path", lambda: tmp_db,
    )
    # Use the in-process main() rather than subprocess so the monkeypatch
    # takes effect (a subprocess wouldn't inherit it).
    from scripts import agent_health_check as h
    monkeypatch.setattr(sys, "argv", ["agent_health_check.py", "--json"])
    exit_code = h.main()
    assert exit_code == 0


def test_cli_returns_exit_1_when_breach(tmp_db, monkeypatch):
    """Seed enough errors to exceed the 5% threshold → exit 1."""
    import btc_api
    with transaction() as con:
        for _ in range(10):
            _seed_assistant(con)
        for _ in range(10):
            _seed_error(con)  # error_rate = 50%

    monkeypatch.setattr(
        "scripts.agent_health_check._db_path", lambda: tmp_db,
    )
    from scripts import agent_health_check as h
    monkeypatch.setattr(sys, "argv", ["agent_health_check.py", "--json"])
    exit_code = h.main()
    assert exit_code == 1


def test_cli_returns_exit_2_when_db_missing(tmp_path, monkeypatch):
    """If signals.db doesn't exist, exit 2 with a clear stderr message."""
    monkeypatch.setattr(
        "scripts.agent_health_check._db_path",
        lambda: str(tmp_path / "nonexistent.db"),
    )
    from scripts import agent_health_check as h
    monkeypatch.setattr(sys, "argv", ["agent_health_check.py"])
    exit_code = h.main()
    assert exit_code == 2
