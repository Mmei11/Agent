"""Phase 6 L3.1：API Key 认证。

认证的是"调用方程序"，不是终端用户。浏览器前端不携带 key（前端代码是公开的），
公开演示用 AUTH_MODE=off + IP 限流；机器客户端用 required + X-API-Key。
"""

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from backend.app.config import load_env_file
from backend.app.observability.tracer import span
from backend.app.safety.audit import AuditEvent
from backend.app.safety.audit import write as write_audit

logger = logging.getLogger(__name__)

AUTH_MODE_OFF = "off"
AUTH_MODE_REQUIRED = "required"


@dataclass(frozen=True)
class CallerIdentity:
    """认证通过后的调用方身份，供后续限流按调用方分桶。"""

    mode: str
    key_hash: str = ""  # 完整摘要只留在内存；审计只取前 12 位

    @property
    def audit_id(self) -> str:
        return self.key_hash[:12]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _configured_keys() -> list[str]:
    raw = os.getenv("API_KEYS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _audit_denial(reason: str, key_hash: str) -> None:
    """拒绝事件只落短哈希和原因，绝不落原始 key。"""
    write_audit(
        AuditEvent(
            layer="auth",
            rule="A001",
            action="DENY",
            input_digest=key_hash[:12],
            detail=reason,
        )
    )


def caller_key(caller: CallerIdentity, request: Request) -> str:
    """限流/连接计数用的调用方标识：有 key 用 key 摘要，否则退回 IP。

    只用直连地址，不信任 X-Forwarded-For；代理场景应在网关层限流。
    """
    if caller.key_hash:
        return caller.key_hash
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> CallerIdentity:
    """FastAPI 依赖：校验调用方身份。

    - off：放行，返回匿名身份；
    - required：必须带正确 key；
    - 未知值：按 required 处理（fail closed），避免配置写错反而开放。
    """
    load_env_file()
    mode = os.getenv("AUTH_MODE", AUTH_MODE_OFF).strip().lower()

    with span("api.auth", kind="gate") as record:
        record.attributes["auth.mode"] = mode

        if mode == AUTH_MODE_OFF:
            record.attributes["auth.result"] = "allow"
            return CallerIdentity(mode=AUTH_MODE_OFF)

        if mode != AUTH_MODE_REQUIRED:
            logger.error("unknown AUTH_MODE=%r; falling back to required", mode)
            record.attributes["auth.mode_fallback"] = "required"

        configured = _configured_keys()
        if not configured:
            # 配了 required 却没配 key，是部署错误；宁可拒绝也不能静默放行。
            logger.error("AUTH_MODE=required but API_KEYS is empty")
            record.attributes["auth.result"] = "deny"
            record.attributes["auth.reason"] = "auth_not_configured"
            _audit_denial("auth_not_configured", "")
            raise HTTPException(status_code=503, detail="认证未配置")

        if not x_api_key or not x_api_key.strip():
            record.attributes["auth.result"] = "deny"
            record.attributes["auth.reason"] = "missing_key"
            _audit_denial("missing_key", "")
            raise HTTPException(
                status_code=401,
                detail="缺少 X-API-Key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        provided = _digest(x_api_key.strip())

        # 逐个比较且不提前返回：避免通过响应时间推断匹配到了第几个 key。
        matched = False
        for item in configured:
            matched |= secrets.compare_digest(provided, _digest(item))

        if not matched:
            record.attributes["auth.result"] = "deny"
            record.attributes["auth.reason"] = "invalid_key"
            record.attributes["auth.key_id"] = provided[:12]
            _audit_denial("invalid_key", provided)
            raise HTTPException(
                status_code=401,
                detail="无效的 X-API-Key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        record.attributes["auth.result"] = "allow"
        record.attributes["auth.key_id"] = provided[:12]
        return CallerIdentity(mode=AUTH_MODE_REQUIRED, key_hash=provided)
