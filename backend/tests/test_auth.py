"""Phase 6 L3.1：API Key 认证。案例均为虚构。"""

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    """认证测试使用独立数据库与审计文件，默认关闭鉴权。"""
    monkeypatch.setenv("AUTH_MODE", "off")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "auth.sqlite"))
    routes.reset_app()
    yield
    routes.reset_app()


def _require(monkeypatch, keys: str = "secret-key") -> None:
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("API_KEYS", keys)


def test_off_mode_allows_without_key():
    assert client.post("/sessions", json={"initial_message": ""}).status_code == 200


def test_required_missing_key_401(monkeypatch):
    _require(monkeypatch)
    response = client.post("/sessions", json={"initial_message": ""})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "ApiKey"


def test_required_invalid_key_401(monkeypatch):
    _require(monkeypatch)
    response = client.post(
        "/sessions", json={"initial_message": ""}, headers={"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


def test_required_valid_key_200(monkeypatch):
    _require(monkeypatch)
    response = client.post(
        "/sessions", json={"initial_message": ""}, headers={"X-API-Key": "secret-key"}
    )
    assert response.status_code == 200


def test_multiple_keys_all_valid(monkeypatch):
    _require(monkeypatch, "key-one,key-two")
    for key in ("key-one", "key-two"):
        response = client.post(
            "/sessions", json={"initial_message": ""}, headers={"X-API-Key": key}
        )
        assert response.status_code == 200, key


def test_prefix_is_not_enough(monkeypatch):
    """前缀相同不能通过：比较的是完整 key 的摘要。"""
    _require(monkeypatch, "secret-key-123")
    response = client.post(
        "/sessions", json={"initial_message": ""}, headers={"X-API-Key": "secret-key"}
    )
    assert response.status_code == 401


def test_required_without_configured_keys_503(monkeypatch):
    """配了 required 却没配 key：部署错误，宁可拒绝也不放行。"""
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.delenv("API_KEYS", raising=False)
    response = client.post("/sessions", json={"initial_message": ""})
    assert response.status_code == 503


def test_unknown_mode_fails_closed(monkeypatch):
    """AUTH_MODE 拼错时按 required 处理，不能静默开放。"""
    monkeypatch.setenv("AUTH_MODE", "requried")
    monkeypatch.setenv("API_KEYS", "secret-key")
    assert client.post("/sessions", json={"initial_message": ""}).status_code == 401
    assert (
        client.post(
            "/sessions",
            json={"initial_message": ""},
            headers={"X-API-Key": "secret-key"},
        ).status_code
        == 200
    )


def test_all_session_routes_require_key(monkeypatch):
    _require(monkeypatch)
    headers = {"X-API-Key": "secret-key"}
    created = client.post(
        "/sessions", json={"initial_message": ""}, headers=headers
    )
    session_id = created.json()["session_id"]

    requests = [
        ("POST", "/sessions", {"initial_message": ""}),
        ("POST", f"/sessions/{session_id}/messages", {"message": "头部不适"}),
        ("POST", f"/sessions/{session_id}/stream", {"message": "头部不适"}),
        ("GET", f"/sessions/{session_id}", None),
        ("DELETE", f"/sessions/{session_id}", None),
    ]

    for method, url, body in requests:
        response = client.request(method, url, json=body)
        assert response.status_code == 401, f"{method} {url} 没有被认证保护"


def test_denial_audit_does_not_leak_key(monkeypatch, tmp_path):
    _require(monkeypatch)
    audit_path = tmp_path / "audit.jsonl"

    client.post(
        "/sessions",
        json={"initial_message": ""},
        headers={"X-API-Key": "super-secret-wrong"},
    )

    content = audit_path.read_text(encoding="utf-8")
    assert "super-secret-wrong" not in content
    assert '"rule": "A001"' in content
    assert '"layer": "auth"' in content
    assert '"action": "DENY"' in content


def test_valid_request_writes_no_denial_audit(monkeypatch, tmp_path):
    _require(monkeypatch)
    audit_path = tmp_path / "audit.jsonl"

    client.post(
        "/sessions", json={"initial_message": ""}, headers={"X-API-Key": "secret-key"}
    )

    assert not audit_path.exists() or audit_path.read_text(encoding="utf-8") == ""
