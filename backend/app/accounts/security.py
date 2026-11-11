"""账号密码与登录令牌的密码学原语。

只用标准库，不引入新依赖：PBKDF2-HMAC-SHA256 加盐哈希，令牌本身
只发给浏览器一次，数据库里存摘要，泄库也拿不到可用令牌。
"""

import base64
import hashlib
import hmac
import re
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
KEY_BYTES = 32
TOKEN_BYTES = 32

MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_CHARS = 128
MAX_EMAIL_CHARS = 254
MAX_DISPLAY_NAME_CHARS = 30

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """返回 pbkdf2_sha256$迭代次数$盐$摘要，迭代次数随哈希一起存，
    以后调高成本时旧密码仍能验证。"""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=KEY_BYTES
    )
    return f"{ALGORITHM}${ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """解析失败一律返回 False，不让脏数据变成 500。"""
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        expected_bytes = _b64decode(expected)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt),
            int(iterations),
            dklen=len(expected_bytes),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected_bytes)


def burn_password_time(password: str) -> None:
    """邮箱不存在时也做一次同样成本的哈希，避免用响应时间探测账号是否注册。"""
    hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), b"consult-auth-dummy", ITERATIONS,
        dklen=KEY_BYTES,
    )


def new_token() -> str:
    """登录令牌：32 字节随机数，只通过 HttpOnly Cookie 下发。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def email_problem(email: str) -> str | None:
    if not email:
        return "请输入邮箱"
    if len(email) > MAX_EMAIL_CHARS:
        return "邮箱过长"
    if not EMAIL_PATTERN.match(email):
        return "邮箱格式不正确"
    return None


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_CHARS:
        return f"密码至少 {MIN_PASSWORD_CHARS} 位"
    if len(password) > MAX_PASSWORD_CHARS:
        return f"密码不能超过 {MAX_PASSWORD_CHARS} 位"
    if not any(char.isalpha() for char in password):
        return "密码需要包含字母"
    if not any(char.isdigit() for char in password):
        return "密码需要包含数字"
    return None


def normalize_display_name(value: str, email: str) -> str:
    """昵称留空时用邮箱前缀兜底，保证界面上总有可展示的名字。"""
    name = value.strip()
    return name or email.split("@", 1)[0][:MAX_DISPLAY_NAME_CHARS]


def display_name_problem(name: str) -> str | None:
    if len(name) > MAX_DISPLAY_NAME_CHARS:
        return f"昵称不能超过 {MAX_DISPLAY_NAME_CHARS} 字"
    return None
