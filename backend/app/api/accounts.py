"""Phase 7：账号注册、登录与登录态接口。

与 API Key（api/auth.py）分工不同：
- API Key 认证的是机器调用方；
- 这里认证的是终端用户，令牌放在 HttpOnly Cookie 里，浏览器脚本读不到。

接口全部挂在 /auth 下：register、login、logout、me。
"""

import hashlib
import math
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from backend.app.accounts import email as account_email
from backend.app.accounts import security, store
from backend.app.api.ratelimit import RateLimiter
from backend.app.config import load_env_file
from backend.app.safety.audit import AuditEvent
from backend.app.safety.audit import write as write_audit

router = APIRouter(prefix="/auth", tags=["账号"])

COOKIE_NAME = "consult_auth"
COOKIE_PATH = "/"
SESSION_TTL_DAYS = store.DEFAULT_SESSION_TTL_DAYS
PRIVACY_VERSION = "2026-11-16"
DEFAULT_RETENTION_DAYS = 90

# 登录/注册的尝试频率。按 IP 和邮箱各一桶：前者挡刷接口，后者挡单账号爆破。
_attempt_limiter = RateLimiter(
    name="auth_attempt",
    capacity=10,
    refill_rate=10 / 60,
)


class RegisterRequest(BaseModel):
    email: str = ""
    password: str = ""
    display_name: str = ""
    consent: bool = False
    consent_version: str = ""


class LoginRequest(BaseModel):
    email: str = ""
    password: str = ""


class ConsentRequest(BaseModel):
    version: str = PRIVACY_VERSION


class VerifyEmailRequest(BaseModel):
    token: str = ""


class ForgotPasswordRequest(BaseModel):
    email: str = ""


class ResetPasswordRequest(BaseModel):
    token: str = ""
    new_password: str = ""


class UserOut(BaseModel):
    """对外只暴露公开字段；password_hash 永远不出账号模块。"""

    id: str
    email: str
    display_name: str
    status: str
    created_at: str
    last_login_at: str | None = None
    consent_version: str = ""
    consent_at: str | None = None
    consent_required: bool = False
    email_verified: bool = False


class AuthResponse(BaseModel):
    user: UserOut
    expires_at: str


def _public_user(user: dict) -> UserOut:
    consent_version = user.get("consent_version") or ""
    return UserOut(
        id=user["id"],
        email=user["email"],
        display_name=user["display_name"],
        status=user["status"],
        created_at=user["created_at"],
        last_login_at=user.get("last_login_at"),
        consent_version=consent_version,
        consent_at=user.get("consent_at"),
        consent_required=consent_version != PRIVACY_VERSION,
        email_verified=bool(user.get("email_verified")),
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limiting_enabled() -> bool:
    load_env_file()
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _throttle(kind: str, request: Request, email: str) -> None:
    if not _limiting_enabled():
        return
    keys = [f"{kind}:ip:{_client_ip(request)}"]
    if email:
        keys.append(f"{kind}:email:{email}")
    for key in keys:
        decision = _attempt_limiter.check(key)
        if not decision.allowed:
            retry_after = (
                max(1, math.ceil(decision.retry_after))
                if math.isfinite(decision.retry_after)
                else 3600
            )
            raise HTTPException(
                status_code=429,
                detail="尝试过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )


def _audit_failure(email: str, reason: str) -> None:
    """只落邮箱摘要和原因，不记录密码或邮箱明文。"""
    write_audit(
        AuditEvent(
            layer="auth",
            rule="A001",
            action="DENY",
            input_digest=hashlib.sha256(email.encode("utf-8")).hexdigest()[:12],
            detail=reason,
        )
    )


def _cookie_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded == "https"


def _set_auth_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path=COOKIE_PATH,
    )


def _open_session(user: dict, request: Request, response: Response) -> dict:
    token = security.new_token()
    session = store.create_session(
        user_id=user["id"],
        token_hash=security.token_digest(token),
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        ttl_days=SESSION_TTL_DAYS,
    )
    _set_auth_cookie(response, request, token)
    return session


def _expired(expires_at: str) -> bool:
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return expires_at <= now


def current_user(request: Request) -> dict:
    """FastAPI 依赖：从 Cookie 解析当前登录用户，失败统一 401。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise _unauthorized()

    session = store.find_session(security.token_digest(token))
    if session is None or session.get("revoked_at") or _expired(session["expires_at"]):
        raise _unauthorized()

    user = store.get_user_by_id(session["user_id"])
    if user is None or user["status"] != "active":
        raise _unauthorized()

    store.touch_session(session["id"])
    return user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="登录状态已失效，请重新登录",
        headers={"WWW-Authenticate": "Cookie"},
    )


def optional_current_user(request: Request) -> dict | None:
    """未登录时返回 None，供公开接口记录会话归属。"""
    try:
        return current_user(request)
    except HTTPException:
        return None


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(req: RegisterRequest, request: Request, response: Response):
    """注册并直接登录：省掉“注册成功后再输一次密码”的步骤。"""
    email = security.normalize_email(req.email)
    _throttle("register", request, email)

    for problem in (
        security.email_problem(email),
        security.password_problem(req.password),
        security.display_name_problem(req.display_name.strip()),
    ):
        if problem:
            raise HTTPException(status_code=400, detail=problem)

    if store.get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录")

    display_name = security.normalize_display_name(req.display_name, email)
    try:
        user = store.create_user(
            email=email,
            display_name=display_name,
            password_hash=security.hash_password(req.password),
            consent_version=(req.consent_version or PRIVACY_VERSION)
            if req.consent
            else "",
        )
    except store.DuplicateEmail:
        raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录")

    store.mark_login(user["id"])
    session = _open_session(user, request, response)
    user = store.get_user_by_id(user["id"])

    verification = store.create_account_token(
        user["id"], store.TOKEN_VERIFY_EMAIL, ttl_minutes=24 * 60
    )
    account_email.send_verification_email(user["email"], verification)

    return AuthResponse(user=_public_user(user), expires_at=session["expires_at"])


@router.post("/login", response_model=AuthResponse)
def login(req: LoginRequest, request: Request, response: Response):
    """邮箱 + 密码登录。

    账号不存在和密码错误返回同一句话，避免暴露邮箱是否注册。
    """
    email = security.normalize_email(req.email)
    _throttle("login", request, email)

    if not email or not req.password:
        raise HTTPException(status_code=400, detail="请输入邮箱和密码")

    user = store.get_user_by_email(email)
    if user is None:
        # 与真实校验同成本的空转，抹平响应时间差。
        security.burn_password_time(req.password)
        _audit_failure(email, "login_failed")
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")

    if user["status"] != "active":
        _audit_failure(email, "account_disabled")
        raise HTTPException(status_code=403, detail="账号已停用")

    if not security.verify_password(req.password, user["password_hash"]):
        _audit_failure(email, "login_failed")
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")

    store.mark_login(user["id"])
    session = _open_session(user, request, response)
    user = store.get_user_by_id(user["id"])
    return AuthResponse(user=_public_user(user), expires_at=session["expires_at"])


@router.post("/logout")
def logout(request: Request, response: Response):
    """吊销当前令牌并清掉 Cookie；重复调用也返回成功。"""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        store.revoke_session(security.token_digest(token))
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(current_user)):
    """前端启动时用它判断是否已登录。"""
    return _public_user(user)


@router.post("/consent", response_model=UserOut)
def consent(req: ConsentRequest, user: dict = Depends(current_user)):
    """记录用户已阅读当前版本的隐私说明。"""
    updated = store.record_consent(user["id"], req.version or PRIVACY_VERSION)
    if updated is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _public_user(updated)


@router.get("/privacy")
def privacy():
    """给前端展示的隐私说明元信息；正文在页面里维护。"""
    retention_days = int(os.getenv("DATA_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    return {
        "version": PRIVACY_VERSION,
        "retention_days": retention_days,
        "export_endpoint": "/auth/export",
        "delete_endpoint": "/auth/delete",
    }


@router.post("/verify-email")
def verify_email(req: VerifyEmailRequest):
    """用邮件里的一次性验证码完成邮箱验证。"""
    user_id = store.consume_account_token(req.token, store.TOKEN_VERIFY_EMAIL)
    if user_id is None:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
    store.mark_email_verified(user_id)
    return {"ok": True}


@router.post("/resend-verification")
def resend_verification(user: dict = Depends(current_user)):
    """给当前账号重发验证码；已验证的账号直接返回。"""
    if user.get("email_verified"):
        return {"ok": True, "already": True}

    token = store.create_account_token(
        user["id"], store.TOKEN_VERIFY_EMAIL, ttl_minutes=24 * 60
    )
    sent = account_email.send_verification_email(user["email"], token)
    return {"ok": True, "sent": sent}


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, request: Request):
    """发送找回密码验证码；无论账号是否存在都返回 ok，避免枚举邮箱。"""
    email = security.normalize_email(req.email)
    _throttle("forgot", request, email)

    user = store.get_user_by_email(email)
    if user is not None and user["status"] == "active":
        token = store.create_account_token(
            user["id"], store.TOKEN_RESET_PASSWORD, ttl_minutes=30
        )
        account_email.send_password_reset_email(user["email"], token)
    return {"ok": True}


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest):
    """用一次性验证码重置密码；成功后吊销所有登录态。"""
    problem = security.password_problem(req.new_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    user_id = store.consume_account_token(
        req.token, store.TOKEN_RESET_PASSWORD
    )
    if user_id is None:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    store.update_password(user_id, security.hash_password(req.new_password))
    store.revoke_all_auth_sessions(user_id)
    # 能收到重置邮件，说明邮箱归属已确认。
    store.mark_email_verified(user_id)
    return {"ok": True}
