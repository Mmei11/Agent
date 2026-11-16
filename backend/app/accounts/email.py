"""Phase F：账号邮件。

默认 console 后端把邮件写到日志，便于本地演示；配置 SMTP_* 后走真实 SMTP。
发送失败只记录警告，不阻断注册/找回密码流程。
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _console(to: str, subject: str, body: str) -> bool:
    logger.warning("EMAIL(console) to=%s subject=%s\n%s", to, subject, body)
    return True


def _smtp(to: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        logger.warning("EMAIL_BACKEND=smtp 但未配置 SMTP_HOST")
        return False

    message = EmailMessage()
    message["To"] = to
    message["From"] = os.getenv("SMTP_FROM", "no-reply@localhost")
    message["Subject"] = subject
    message.set_content(body)

    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=10) as client:
            if os.getenv("SMTP_STARTTLS", "1") == "1":
                client.starttls()
            user = os.getenv("SMTP_USER", "")
            password = os.getenv("SMTP_PASSWORD", "")
            if user:
                client.login(user, password)
            client.send_message(message)
        return True
    except Exception as exc:
        logger.warning("邮件发送失败 to=%s：%s", to, exc)
        return False


def send_email(to: str, subject: str, body: str) -> bool:
    backend = os.getenv("EMAIL_BACKEND", "console").strip().lower()
    if backend == "smtp":
        return _smtp(to, subject, body)
    return _console(to, subject, body)


def send_verification_email(to: str, token: str) -> bool:
    return send_email(
        to,
        "验证你的 Consult/Lab 账号邮箱",
        "请在应用中输入以下验证码完成邮箱验证：\n\n"
        f"{token}\n\n"
        "验证码 24 小时内有效。如果不是你本人操作，请忽略本邮件。",
    )


def send_password_reset_email(to: str, token: str) -> bool:
    return send_email(
        to,
        "重置你的 Consult/Lab 账号密码",
        "请在应用中输入以下验证码重置密码：\n\n"
        f"{token}\n\n"
        "验证码 30 分钟内有效，且只能使用一次。如果不是你本人操作，请忽略本邮件。",
    )
