"""Phase 7：账号注册、登录与登录态。案例均为虚构。"""

import hashlib
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.accounts import security
from backend.app.api import routes
from backend.app.api.routes import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_accounts(tmp_path, monkeypatch):
    """每个用例独立数据库；限流默认关闭，需要时在用例内打开。"""
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "accounts.sqlite"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    routes.reset_app()
    yield
    routes.reset_app()


def register(email="demo@example.com", password="pass1234", name="演示用户"):
    return client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": name},
    )


def test_register_returns_user_and_sets_cookie():
    resp = register()
    assert resp.status_code == 201
    data = resp.json()
    assert data["user"]["email"] == "demo@example.com"
    assert data["user"]["display_name"] == "演示用户"
    assert "password_hash" not in data["user"]

    cookie = resp.headers["set-cookie"]
    assert "consult_auth=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_register_duplicate_email_conflicts_case_insensitively():
    assert register("Demo@Example.com").status_code == 201
    resp = register("demo@example.com")
    assert resp.status_code == 409


def test_register_rejects_bad_email_and_weak_password():
    assert register("not-an-email").status_code == 400
    assert register(password="short1").status_code == 400
    assert register(password="onlyletters").status_code == 400
    assert register(password="12345678").status_code == 400


def test_login_and_me():
    register()
    client.cookies.clear()

    resp = client.post(
        "/auth/login",
        json={"email": "DEMO@example.com", "password": "pass1234"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "demo@example.com"

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "演示用户"


def test_me_requires_login():
    client.cookies.clear()
    assert client.get("/auth/me").status_code == 401


def test_wrong_password_and_unknown_email_share_detail():
    register()
    client.cookies.clear()

    wrong = client.post(
        "/auth/login", json={"email": "demo@example.com", "password": "wrong1234"}
    )
    unknown = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong1234"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_logout_revokes_token():
    register()
    token = client.cookies.get("consult_auth")
    assert client.get("/auth/me").status_code == 200

    assert client.post("/auth/logout").status_code == 200
    assert client.cookies.get("consult_auth") is None

    # 已吊销的令牌即使被重新塞回 Cookie 也不能用。
    client.cookies.set("consult_auth", token)
    assert client.get("/auth/me").status_code == 401


def test_token_is_stored_hashed():
    resp = register()
    raw_token = resp.cookies.get("consult_auth")

    with sqlite3.connect(os.getenv("CONSULTATION_DB_PATH")) as conn:
        rows = conn.execute("SELECT token_hash FROM auth_sessions").fetchall()

    assert rows and rows[0][0] != raw_token
    assert rows[0][0] == hashlib.sha256(raw_token.encode()).hexdigest()


def test_expired_session_is_rejected():
    register()
    with sqlite3.connect(os.getenv("CONSULTATION_DB_PATH")) as conn:
        conn.execute(
            "UPDATE auth_sessions SET expires_at = ?",
            ("2000-01-01T00:00:00.000+00:00",),
        )

    assert client.get("/auth/me").status_code == 401


def test_password_hash_is_salted_and_verifies():
    first = security.hash_password("pass1234")
    second = security.hash_password("pass1234")

    assert first != second  # 同一密码两次哈希不同，说明用了随机盐
    assert first.startswith("pbkdf2_sha256$")
    assert security.verify_password("pass1234", first)
    assert not security.verify_password("pass12345", first)
    assert not security.verify_password("pass1234", "broken")


def test_login_throttled_after_repeated_failures(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    client.cookies.clear()

    statuses = []
    for _ in range(11):
        statuses.append(
            client.post(
                "/auth/login",
                json={"email": "throttle@example.com", "password": "wrong1234"},
            ).status_code
        )

    assert statuses[-1] == 429
    assert 401 in statuses
