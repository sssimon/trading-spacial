"""GET /notifications, POST /notifications/{id}/read, POST /notifications/read-all."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import btc_api
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(btc_api, "DB_FILE", db_path)
    if hasattr(btc_api, "_db_conn"):
        delattr(btc_api, "_db_conn")
    btc_api.init_db()
    return TestClient(btc_api.app)


def _seed(n_unread=3, n_read=2):
    """Insert notifications directly via the storage helper."""
    from notifier._storage import record_delivery, mark_read
    ids = []
    for i in range(n_unread):
        ids.append(record_delivery(
            event_type="signal", event_key=f"signal:SYM{i}", priority="info",
            payload={"symbol": f"SYM{i}"}, channels_sent=["telegram"],
            delivery_status="ok",
        ))
    for i in range(n_read):
        nid = record_delivery(
            event_type="health", event_key=f"health:R{i}", priority="warning",
            payload={"symbol": f"R{i}"}, channels_sent=["telegram"],
            delivery_status="ok",
        )
        mark_read(nid)
    return ids


def test_get_notifications_empty(client):
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert resp.json() == {"notifications": []}


def test_get_notifications_only_unread_by_default(client):
    _seed(n_unread=3, n_read=2)
    resp = client.get("/notifications")
    assert resp.status_code == 200
    rows = resp.json()["notifications"]
    assert len(rows) == 3
    assert all(r["read_at"] is None for r in rows)


def test_get_notifications_include_read_when_unread_false(client):
    _seed(n_unread=2, n_read=2)
    resp = client.get("/notifications?unread=false")
    assert resp.status_code == 200
    rows = resp.json()["notifications"]
    assert len(rows) == 4


def test_get_notifications_respects_limit(client):
    _seed(n_unread=10, n_read=0)
    resp = client.get("/notifications?limit=5")
    assert resp.status_code == 200
    rows = resp.json()["notifications"]
    assert len(rows) == 5


def test_get_notifications_limit_bounded_below_1_rejected(client):
    resp = client.get("/notifications?limit=0")
    assert resp.status_code == 422  # pydantic/fastapi validation


def test_get_notifications_limit_bounded_above_200_rejected(client):
    resp = client.get("/notifications?limit=999")
    assert resp.status_code == 422


def test_post_notification_read_marks_single(client):
    ids = _seed(n_unread=3, n_read=0)
    resp = client.post(f"/notifications/{ids[0]}/read")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "id": ids[0]}

    # The marked row is no longer in unread list
    unread = client.get("/notifications").json()["notifications"]
    assert len(unread) == 2
    assert ids[0] not in [r["id"] for r in unread]


def test_post_notifications_read_all(client):
    _seed(n_unread=4, n_read=1)
    resp = client.post("/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "marked": 4}

    unread = client.get("/notifications").json()["notifications"]
    assert unread == []


def test_post_notifications_read_all_when_none_unread(client):
    _seed(n_unread=0, n_read=3)
    resp = client.post("/notifications/read-all")
    assert resp.json() == {"ok": True, "marked": 0}
