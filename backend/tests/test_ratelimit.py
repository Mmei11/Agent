"""Phase 6 L3.2：令牌桶与限流。案例均为虚构。"""

import pytest
from fastapi.testclient import TestClient

from backend.app.api import ratelimit, routes
from backend.app.api.ratelimit import RateLimiter, TokenBucket
from backend.app.api.routes import app

client = TestClient(app)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- 桶与限流器单元测试 ---


def test_bucket_allows_burst_then_denies():
    clock = FakeClock()
    bucket = TokenBucket.full(capacity=2, refill_rate=1, now=clock())

    assert bucket.consume(clock())[0] is True
    assert bucket.consume(clock())[0] is True
    allowed, remaining, retry_after = bucket.consume(clock())
    assert allowed is False
    assert remaining == 0
    assert retry_after == pytest.approx(1.0)


def test_bucket_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket.full(capacity=1, refill_rate=1, now=clock())

    assert bucket.consume(clock())[0] is True
    assert bucket.consume(clock())[0] is False

    clock.advance(1.0)
    assert bucket.consume(clock())[0] is True


def test_bucket_never_exceeds_capacity():
    clock = FakeClock()
    bucket = TokenBucket.full(capacity=2, refill_rate=1, now=clock())

    clock.advance(100.0)
    assert bucket.consume(clock())[0] is True
    assert bucket.consume(clock())[0] is True
    assert bucket.consume(clock())[0] is False


def test_limiter_isolates_keys():
    clock = FakeClock()
    limiter = RateLimiter("caller", capacity=1, refill_rate=0.01, clock=clock)

    assert limiter.check("a").allowed is True
    assert limiter.check("a").allowed is False
    assert limiter.check("b").allowed is True


def test_limiter_prunes_idle_buckets():
    clock = FakeClock()
    limiter = RateLimiter(
        "caller", capacity=1, refill_rate=1, clock=clock, idle_ttl=10
    )

    limiter.check("idle")
    clock.advance(20)
    for _ in range(255):
        limiter.check("active")

    assert limiter.bucket_count() == 1


# --- HTTP 集成测试 ---


@pytest.fixture(autouse=True)
def rate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("AUTH_MODE", "off")
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "rate.sqlite"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    # 默认给其他维度足够大的桶，单个用例只收紧自己要测的那一维。
    monkeypatch.setenv("GLOBAL_BURST", "1000")
    monkeypatch.setenv("GLOBAL_RATE_LIMIT_PER_MINUTE", "60000")
    monkeypatch.setenv("RATE_BURST", "100")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "6000")
    monkeypatch.setenv("SESSION_BURST", "100")
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_MINUTE", "6000")
    routes.reset_app()
    ratelimit.reset_limiters()
    yield
    routes.reset_app()
    ratelimit.reset_limiters()


def test_caller_burst_then_429(monkeypatch):
    monkeypatch.setenv("RATE_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    ratelimit.reset_limiters()

    assert client.post("/sessions", json={"initial_message": ""}).status_code == 200
    assert client.post("/sessions", json={"initial_message": ""}).status_code == 200

    response = client.post("/sessions", json={"initial_message": ""})
    assert response.status_code == 429
    assert response.headers["X-RateLimit-Limit"] == "2"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert int(response.headers["Retry-After"]) >= 1


def test_zero_refill_rate_returns_finite_retry_after(monkeypatch):
    """补充速率为 0 时不会永远等下去，也不能因为 ceil(inf) 崩成 500。"""
    monkeypatch.setenv("RATE_BURST", "1")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    ratelimit.reset_limiters()

    assert client.post("/sessions", json={"initial_message": ""}).status_code == 200
    response = client.post("/sessions", json={"initial_message": ""})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"


def test_disabled_allows_many_requests(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    ratelimit.reset_limiters()

    for _ in range(5):
        assert client.post("/sessions", json={"initial_message": ""}).status_code == 200


def test_session_bucket_is_per_session(monkeypatch):
    monkeypatch.setenv("SESSION_BURST", "1")
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_MINUTE", "1")
    ratelimit.reset_limiters()

    first = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]
    second = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]

    assert (
        client.post(
            f"/sessions/{first}/messages", json={"message": "头部不适"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/sessions/{first}/messages", json={"message": "前两天开始的"}
        ).status_code
        == 429
    )
    assert (
        client.post(
            f"/sessions/{second}/messages", json={"message": "头部不适"}
        ).status_code
        == 200
    )


def test_rate_limit_audit_has_no_raw_ip(monkeypatch, tmp_path):
    monkeypatch.setenv("RATE_BURST", "1")
    ratelimit.reset_limiters()

    client.post("/sessions", json={"initial_message": ""})
    client.post("/sessions", json={"initial_message": ""})

    content = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert '"rule": "RL001"' in content
    assert "127.0.0.1" not in content
    assert "testclient" not in content


def test_auth_runs_before_rate_limit(monkeypatch):
    """认证失败应该返回 401，而不是先被限流成 429。"""
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("API_KEYS", "secret-key")
    monkeypatch.setenv("RATE_BURST", "1")
    ratelimit.reset_limiters()

    assert client.post("/sessions", json={"initial_message": ""}).status_code == 401
