"""Phase 6 L3.2：令牌桶限流。

固定窗口的问题是窗口边界会放行两倍突发。令牌桶允许有限突发，
同时限制长期速率：

    tokens = min(capacity, tokens + elapsed × refill_rate)

每次请求消耗 1 个；不足时拒绝，并告诉客户端还要等多久。
三个维度：全局 → 调用方（API Key / IP）→ 会话。
"""

import hashlib
import math
import os
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from backend.app.api.auth import CallerIdentity, caller_key, require_api_key
from backend.app.config import load_env_file
from backend.app.observability.tracer import span
from backend.app.safety.audit import AuditEvent
from backend.app.safety.audit import write as write_audit

# 空闲桶超过这个秒数且已补满时回收，避免内存随 key 数量无限增长。
IDLE_TTL_SECONDS = 300.0

# 补充速率为 0 时（配置错误或故意关门），等待时间是无穷大；
# Retry-After 必须是有限整数，这里给一个大的合理值。
MAX_RETRY_AFTER_SECONDS = 3600


@dataclass
class TokenBucket:
    """单个桶的状态；时间一律用单调时钟，不受系统改时间影响。"""

    capacity: float
    refill_rate: float  # 每秒补充的令牌数
    tokens: float
    updated_at: float

    @classmethod
    def full(cls, capacity: float, refill_rate: float, now: float) -> "TokenBucket":
        return cls(capacity=capacity, refill_rate=refill_rate, tokens=capacity, updated_at=now)

    def consume(self, now: float, cost: float = 1.0) -> tuple[bool, float, float]:
        """尝试消耗令牌，返回 (是否允许, 剩余, 需要等待的秒数)。"""
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.updated_at = now

        if self.tokens >= cost:
            self.tokens -= cost
            return True, self.tokens, 0.0

        needed = cost - self.tokens
        retry_after = needed / self.refill_rate if self.refill_rate > 0 else math.inf
        return False, self.tokens, retry_after


@dataclass(frozen=True)
class RateLimitDecision:
    name: str
    allowed: bool
    limit: int
    remaining: int
    retry_after: float = 0.0


class RateLimiter:
    """按 key 管理令牌桶。

    依赖注入的时钟让测试可以推进时间，不必真的 sleep。
    内部加锁：FastAPI 的同步依赖跑在线程池里，多个线程可能同时操作同一个桶。
    """

    def __init__(
        self,
        name: str,
        capacity: float,
        refill_rate: float,
        clock=time.monotonic,
        idle_ttl: float = IDLE_TTL_SECONDS,
    ) -> None:
        self.name = name
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._clock = clock
        self._idle_ttl = idle_ttl
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, key: str, cost: float = 1.0) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket.full(self.capacity, self.refill_rate, now)
                self._buckets[key] = bucket

            allowed, remaining, retry_after = bucket.consume(now, cost)

            # 每 256 次检查回收一次空闲桶，开销摊薄到可忽略。
            self._calls += 1
            if self._calls % 256 == 0:
                self._prune(now)

            return RateLimitDecision(
                name=self.name,
                allowed=allowed,
                limit=int(self.capacity),
                remaining=max(0, int(remaining)),
                retry_after=0.0 if allowed else retry_after,
            )

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at > self._idle_ttl
            and min(
                bucket.capacity,
                bucket.tokens + (now - bucket.updated_at) * bucket.refill_rate,
            )
            >= bucket.capacity
        ]
        for key in stale:
            self._buckets.pop(key, None)

    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)


def _number(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _per_second(per_minute: float) -> float:
    return max(per_minute, 0.0) / 60.0


def build_limiters(
    clock=time.monotonic,
) -> tuple[RateLimiter, RateLimiter, RateLimiter]:
    """按环境变量构建三个维度的限流器（全局 / 调用方 / 会话）。"""
    load_env_file()

    global_rate = _number("GLOBAL_RATE_LIMIT_PER_MINUTE", 600)
    global_burst = _number("GLOBAL_BURST", 100)
    caller_rate = _number("RATE_LIMIT_PER_MINUTE", 60)
    caller_burst = _number("RATE_BURST", 10)
    session_rate = _number("SESSION_RATE_LIMIT_PER_MINUTE", 30)
    session_burst = _number("SESSION_BURST", 5)

    return (
        RateLimiter("global", global_burst, _per_second(global_rate), clock),
        RateLimiter("caller", caller_burst, _per_second(caller_rate), clock),
        RateLimiter("session", session_burst, _per_second(session_rate), clock),
    )


_limiters: tuple[RateLimiter, RateLimiter, RateLimiter] | None = None
_limiter_lock = threading.Lock()


def get_limiters() -> tuple[RateLimiter, RateLimiter, RateLimiter]:
    global _limiters
    if _limiters is None:
        with _limiter_lock:
            if _limiters is None:
                _limiters = build_limiters()
    return _limiters


def reset_limiters() -> None:
    """清空限流器状态；测试切换环境变量时调用。"""
    global _limiters
    with _limiter_lock:
        _limiters = None


def _enabled() -> bool:
    load_env_file()
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _deny(decision: RateLimitDecision, client_id: str) -> HTTPException:
    if math.isfinite(decision.retry_after):
        retry_after = max(1, math.ceil(decision.retry_after))
    else:
        # 速率为 0：永远不会补充，给客户端一个大的等待值而不是崩溃。
        retry_after = MAX_RETRY_AFTER_SECONDS

    # 审计只落调用方标识的短哈希；不记录原始 IP 或完整 key 摘要。
    digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:12]
    write_audit(
        AuditEvent(
            layer="rate_limit",
            rule="RL001",
            action="DENY",
            input_digest=digest,
            detail=f"{decision.name} limit={decision.limit}",
        )
    )

    return HTTPException(
        status_code=429,
        detail=f"请求过于频繁（{decision.name}）",
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        },
    )


async def rate_limit(
    request: Request,
    caller: CallerIdentity = Depends(require_api_key),
) -> None:
    """FastAPI 依赖：先认证，再按全局 → 调用方 → 会话依次扣令牌。

    顺序有意为之：最便宜的全局检查最先做，只有前面放行才继续，
    避免被拒绝的请求还去消耗会话桶。
    """
    if not _enabled():
        return

    # 从路径参数取会话；/sessions 没有这个参数，自然跳过会话维度。
    session_id = request.path_params.get("session_id")

    global_limiter, caller_limiter, session_limiter = get_limiters()

    # 认证通过后 key_hash 是完整摘要；off 模式为匿名，退回按 IP 分桶。
    caller_id = caller_key(caller, request)

    decisions = [global_limiter.check("global")]
    if decisions[-1].allowed:
        decisions.append(caller_limiter.check(caller_id))
    if decisions[-1].allowed and session_id:
        decisions.append(session_limiter.check(session_id))

    with span("api.rate_limit", kind="gate") as record:
        record.attributes["rate_limit.buckets"] = [item.name for item in decisions]
        for decision in decisions:
            if not decision.allowed:
                record.attributes["rate_limit.allowed"] = False
                record.attributes["rate_limit.denied"] = decision.name
                record.attributes["rate_limit.retry_after"] = round(
                    decision.retry_after, 3
                )
                raise _deny(decision, caller_id)
        record.attributes["rate_limit.allowed"] = True
