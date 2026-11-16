"""Phase F：知情同意、数据导出、账号删除与保留期。"""

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "privacy.sqlite"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("PRIVACY_CONSENT_REQUIRED", raising=False)
    routes.reset_app()
    yield
    routes.reset_app()


def register(consent=True, email="demo@example.com"):
    return client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "pass1234",
            "display_name": "演示用户",
            "consent": consent,
            "consent_version": "2026-11-16",
        },
    )


def test_register_records_consent():
    data = register(consent=True).json()["user"]

    assert data["consent_version"] == "2026-11-16"
    assert data["consent_at"]
    assert data["consent_required"] is False


def test_existing_user_can_record_consent():
    data = register(consent=False).json()["user"]
    assert data["consent_required"] is True

    resp = client.post("/auth/consent", json={"version": "2026-11-16"})

    assert resp.status_code == 200
    assert resp.json()["consent_required"] is False
    assert resp.json()["consent_version"] == "2026-11-16"


def test_old_consent_version_requires_new_consent():
    data = register(consent=True).json()["user"]
    assert data["consent_required"] is False

    # 模拟隐私说明升级：旧版本同意不能算当前版本已同意。
    from backend.app.accounts import store as account_store

    account_store.record_consent(data["id"], "2025-01-01")
    me = client.get("/auth/me").json()
    assert me["consent_required"] is True

    client.post("/auth/consent", json={"version": "2026-11-16"})
    assert client.get("/auth/me").json()["consent_required"] is False


def test_consent_required_blocks_new_session(monkeypatch):
    monkeypatch.setenv("PRIVACY_CONSENT_REQUIRED", "1")
    register(consent=False)

    blocked = client.post("/sessions", json={"initial_message": "头部不适"})
    assert blocked.status_code == 403

    client.post("/auth/consent", json={"version": "2026-11-16"})
    allowed = client.post("/sessions", json={"initial_message": "头部不适"})
    assert allowed.status_code == 200


def test_export_contains_user_and_session_state():
    register()
    created = client.post(
        "/sessions", json={"initial_message": "头部不适两天"}
    ).json()
    client.post(
        f"/sessions/{created['session_id']}/feedback",
        json={"rating": "helpful", "reason": "清楚"},
    )

    resp = client.get("/auth/export")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["user"]["email"] == "demo@example.com"
    assert payload["sessions"]
    exported = payload["sessions"][0]
    assert exported["session"]["session_id"] == created["session_id"]
    assert "conversation" in exported["state"]
    assert "asked_questions" in exported["state"]
    assert "tool_results" in exported["state"]
    assert exported["feedback"]
    assert exported["feedback"][0]["rating"] == "helpful"
    assert "password_hash" not in resp.text


def test_delete_account_requires_password_and_removes_data():
    register()
    created = client.post(
        "/sessions", json={"initial_message": "头部不适两天"}
    ).json()

    wrong = client.post("/auth/delete", json={"password": "wrong1234"})
    assert wrong.status_code == 401

    deleted = client.post("/auth/delete", json={"password": "pass1234"})
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert client.get("/auth/me").status_code == 401

    with sqlite3.connect(os.getenv("CONSULTATION_DB_PATH")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE session_id = ?",
                (created["session_id"],),
            ).fetchone()[0]
            == 0
        )


def test_retention_dry_run_and_purge():
    from backend.app.accounts import store as account_store
    from backend.app.privacy import retention

    user = account_store.create_user(
        email="old@example.com",
        display_name="旧用户",
        password_hash="x",
    )
    account_store.record_session("old-session", user["id"], "旧会话")
    with sqlite3.connect(os.getenv("CONSULTATION_DB_PATH")) as conn:
        conn.execute(
            "UPDATE user_sessions SET last_active_at = ? WHERE session_id = ?",
            ("2000-01-01T00:00:00.000+00:00", "old-session"),
        )

    assert "old-session" in retention.purge(days=30, dry_run=True)
    assert "old-session" in retention.purge(days=30, dry_run=False)
    assert account_store.sessions_before(retention.cutoff_iso(30)) == []
