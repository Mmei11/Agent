"""Phase F：邮箱验证与找回密码。"""

import pytest
from fastapi.testclient import TestClient

from backend.app.api import accounts as accounts_api
from backend.app.api import routes
from backend.app.api.routes import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "email.sqlite"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    routes.reset_app()
    yield
    routes.reset_app()


def register_and_capture(monkeypatch):
    sent = {}

    def capture_verify(to, token):
        sent["verify"] = token
        return True

    monkeypatch.setattr(
        accounts_api.account_email, "send_verification_email", capture_verify
    )
    response = client.post(
        "/auth/register",
        json={
            "email": "demo@example.com",
            "password": "pass1234",
            "display_name": "演示用户",
            "consent": True,
            "consent_version": "2026-11-16",
        },
    )
    assert response.status_code == 201
    return response, sent


def test_register_sends_verification_and_verifies_once(monkeypatch):
    response, sent = register_and_capture(monkeypatch)
    assert response.json()["user"]["email_verified"] is False
    token = sent["verify"]

    verified = client.post("/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert client.get("/auth/me").json()["email_verified"] is True

    reused = client.post("/auth/verify-email", json={"token": token})
    assert reused.status_code == 400


def test_forgot_password_resets_and_revokes_sessions(monkeypatch):
    register_and_capture(monkeypatch)
    reset = {}

    def capture_reset(to, token):
        reset["token"] = token
        return True

    monkeypatch.setattr(
        accounts_api.account_email, "send_password_reset_email", capture_reset
    )
    response = client.post(
        "/auth/forgot-password", json={"email": "demo@example.com"}
    )
    assert response.status_code == 200
    token = reset["token"]

    changed = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "newpass123"},
    )
    assert changed.status_code == 200

    # 旧登录态必须失效。
    assert client.get("/auth/me").status_code == 401

    old_login = client.post(
        "/auth/login", json={"email": "demo@example.com", "password": "pass1234"}
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login", json={"email": "demo@example.com", "password": "newpass123"}
    )
    assert new_login.status_code == 200
    assert new_login.json()["user"]["email_verified"] is True

    reused = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "another123"},
    )
    assert reused.status_code == 400


def test_forgot_password_does_not_reveal_unknown_email(monkeypatch):
    called = []
    monkeypatch.setattr(
        accounts_api.account_email,
        "send_password_reset_email",
        lambda to, token: called.append(to) or True,
    )

    response = client.post(
        "/auth/forgot-password", json={"email": "nobody@example.com"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert called == []


def test_expired_token_is_rejected(tmp_path, monkeypatch):
    from backend.app.accounts import store

    user = store.create_user(
        email="expired@example.com",
        display_name="过期",
        password_hash="x",
    )
    token = store.create_account_token(
        user["id"], store.TOKEN_VERIFY_EMAIL, ttl_minutes=-1
    )

    assert store.consume_account_token(token, store.TOKEN_VERIFY_EMAIL) is None
