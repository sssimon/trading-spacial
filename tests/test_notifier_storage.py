"""Storage of outbound notifications — insert, list unread, mark-read."""
import pytest
from db.transaction import transaction


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Isolated signals.db pointing at tmp path."""
    import btc_api

    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    # reset any thread-local connection if present
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    # create all tables on fresh db
    btc_api.init_db()
    yield db_path


def test_record_delivery_inserts_row(tmp_db):
    from notifier._storage import record_delivery
    with transaction() as con:
        record_delivery(
            con,
            event_type="signal", event_key="signal:BTCUSDT",
            priority="info",
            payload={"symbol": "BTCUSDT", "score": 6},
            channels_sent=["telegram"],
            delivery_status="ok",
        )
    from notifier._storage import list_unread
    with transaction() as con:
        rows = list_unread(con, limit=10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "signal"
    assert rows[0]["delivery_status"] == "ok"


def test_list_unread_filters_read(tmp_db):
    from notifier._storage import record_delivery, list_unread, mark_read
    with transaction() as con:
        record_delivery(con, "signal", "signal:BTCUSDT", "info",
                        {"symbol": "BTCUSDT"}, ["telegram"], "ok")
    with transaction() as con:
        (row_id,) = (r["id"] for r in list_unread(con, limit=10))
    with transaction() as con:
        mark_read(con, row_id)
    with transaction() as con:
        assert list_unread(con, limit=10) == []


def test_list_unread_ordered_by_sent_at_desc(tmp_db):
    from notifier._storage import record_delivery, list_unread
    with transaction() as con:
        record_delivery(con, "signal", "signal:A", "info", {"s": "A"}, ["telegram"], "ok")
    with transaction() as con:
        record_delivery(con, "signal", "signal:B", "info", {"s": "B"}, ["telegram"], "ok")
    with transaction() as con:
        rows = list_unread(con, limit=10)
    assert rows[0]["event_key"] == "signal:B"
    assert rows[1]["event_key"] == "signal:A"
