"""Phase 7：会话归属账号、历史列表、认领与删除。案例均为虚构。"""

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "history.sqlite"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("AUTH_MODE", "off")
    routes.reset_app()
    yield
    routes.reset_app()


def account_client(email="demo@example.com", password="pass1234"):
    client = TestClient(app)
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": "演示用户"},
    )
    assert resp.status_code == 201
    return client


def create_session(client, message=""):
    resp = client.post("/sessions", json={"initial_message": message})
    assert resp.status_code == 200
    return resp.json()["session_id"]


def history(client):
    resp = client.get("/me/sessions")
    assert resp.status_code == 200
    return resp.json()


def test_starting_new_session_keeps_previous_history():
    """开启新会话不能顺带删掉旧会话；历史由用户显式删除。"""
    client = account_client()
    first = create_session(client, "头部不适")
    second = create_session(client, "腹痛三天")

    assert [item["session_id"] for item in history(client)] == [second, first]


def test_history_survives_logout_and_login():
    client = account_client()
    sid = create_session(client, "头部不适")

    assert client.post("/auth/logout").status_code == 200
    assert client.post(
        "/auth/login", json={"email": "demo@example.com", "password": "pass1234"}
    ).status_code == 200

    assert [item["session_id"] for item in history(client)] == [sid]


def test_history_requires_login():
    guest = TestClient(app)
    create_session(guest, "头部不适")
    assert guest.get("/me/sessions").status_code == 401


def test_created_sessions_are_listed_with_titles():
    client = account_client()
    sid = create_session(client, "头部不适两天，前额")
    items = history(client)

    assert [item["session_id"] for item in items] == [sid]
    assert items[0]["title"].startswith("头部不适")


def test_empty_session_gets_title_after_first_message():
    client = account_client()
    sid = create_session(client, "")

    titles = {item["session_id"]: item["title"] for item in history(client)}
    assert titles[sid] == ""

    resp = client.post(f"/sessions/{sid}/messages", json={"message": "腹痛三天"})
    assert resp.status_code == 200

    titles = {item["session_id"]: item["title"] for item in history(client)}
    assert titles[sid].startswith("腹痛三天")


def test_guest_session_can_be_claimed_once():
    guest = TestClient(app)
    sid = create_session(guest, "头部不适")
    owner = account_client("owner@example.com")

    first = owner.post("/me/sessions/claim", json={"session_id": sid})
    assert first.status_code == 200
    assert first.json()["claimed"] is True
    assert any(item["session_id"] == sid for item in history(owner))

    again = owner.post("/me/sessions/claim", json={"session_id": sid})
    assert again.status_code == 200
    assert again.json()["claimed"] is False

    other = account_client("other@example.com")
    assert other.post("/me/sessions/claim", json={"session_id": sid}).status_code == 409


def test_claim_missing_session_returns_404():
    client = account_client()
    resp = client.post(
        "/me/sessions/claim", json={"session_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert resp.status_code == 404


def test_delete_owned_session_removes_checkpoint():
    client = account_client()
    sid = create_session(client, "头部不适")
    assert client.get(f"/sessions/{sid}").status_code == 200

    assert client.delete(f"/me/sessions/{sid}").status_code == 200
    assert client.get(f"/sessions/{sid}").status_code == 404
    assert all(item["session_id"] != sid for item in history(client))


def test_cannot_delete_another_users_session():
    owner = account_client("owner@example.com")
    sid = create_session(owner, "头部不适")

    intruder = account_client("intruder@example.com")
    assert intruder.delete(f"/me/sessions/{sid}").status_code == 404
    assert owner.get(f"/sessions/{sid}").status_code == 200


def test_public_delete_clears_history_row():
    client = account_client()
    sid = create_session(client, "头部不适")

    assert client.delete(f"/sessions/{sid}").status_code == 200
    assert history(client) == []


def test_history_limit_is_capped():
    client = account_client()
    for index in range(3):
        create_session(client, f"第{index}次头部不适")

    assert len(history(client)) == 3
    assert len(client.get("/me/sessions?limit=2").json()) == 2
    assert len(client.get("/me/sessions?limit=999").json()) == 3


def test_rename_session_updates_title():
    client = account_client()
    sid = create_session(client, "头部不适")

    resp = client.patch(f"/me/sessions/{sid}", json={"title": "偏头痛记录"})

    assert resp.status_code == 200
    assert resp.json()["title"] == "偏头痛记录"
    assert history(client)[0]["title"] == "偏头痛记录"


def test_rename_rejects_empty_title():
    client = account_client()
    sid = create_session(client, "头部不适")

    resp = client.patch(f"/me/sessions/{sid}", json={"title": "  "})

    assert resp.status_code == 400


def test_pinned_session_sorts_first_and_can_be_unpinned():
    client = account_client()
    first = create_session(client, "头部不适")
    second = create_session(client, "腹痛三天")
    assert [item["session_id"] for item in history(client)] == [second, first]

    assert client.patch(f"/me/sessions/{first}", json={"pinned": True}).status_code == 200
    items = history(client)
    assert [item["session_id"] for item in items] == [first, second]
    assert items[0]["pinned"] is True

    assert client.patch(f"/me/sessions/{first}", json={"pinned": False}).status_code == 200
    assert [item["session_id"] for item in history(client)] == [second, first]


def test_cannot_update_another_users_session():
    owner = account_client("owner@example.com")
    sid = create_session(owner, "头部不适")

    intruder = account_client("intruder@example.com")
    resp = intruder.patch(f"/me/sessions/{sid}", json={"title": "越权修改"})

    assert resp.status_code == 404
    assert owner.get("/me/sessions").json()[0]["title"].startswith("头部不适")
