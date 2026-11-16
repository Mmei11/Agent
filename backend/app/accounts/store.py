"""账号与登录会话存储。

与 feedback 一样独立建表、独立连接：账号是长期数据，不跟 LangGraph
的 checkpoint 混在一起。令牌只存 SHA-256 摘要，登录态可单独吊销。
"""

import os
import hashlib
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from backend.app.storage.saver import DEFAULT_DB_PATH

DEFAULT_SESSION_TTL_DAYS = 7
TOKEN_VERIFY_EMAIL = "verify_email"
TOKEN_RESET_PASSWORD = "reset_password"

# 历史列表里展示的标题长度；超出截断，避免一条长消息撑满侧栏。
SESSION_TITLE_CHARS = 24


class DuplicateEmail(Exception):
    """邮箱已被注册；由接口层转成 409。"""


def _db_path() -> str:
    return os.getenv("CONSULTATION_DB_PATH", DEFAULT_DB_PATH)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


@contextmanager
def _connection():
    conn = sqlite3.connect(_db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            consent_version TEXT NOT NULL DEFAULT '',
            consent_at TEXT,
            email_verified INTEGER NOT NULL DEFAULT 0,
            email_verified_at TEXT
        );

        CREATE TABLE IF NOT EXISTS auth_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            client_ip TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
            ON auth_sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
            ON auth_sessions(expires_at);

        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_active_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_user_sessions_owner
            ON user_sessions(user_id, last_active_at DESC);

        CREATE TABLE IF NOT EXISTS account_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_account_tokens_user
            ON account_tokens(user_id, purpose);
        """
    )
    # 旧库补列：CREATE TABLE IF NOT EXISTS 不会修改已存在的表。
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "consent_version" not in columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN consent_version TEXT NOT NULL DEFAULT ''"
        )
    if "consent_at" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN consent_at TEXT")
    if "email_verified" not in columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0"
        )
    if "email_verified_at" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")

    session_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(user_sessions)")
    }
    if "pinned" not in session_columns:
        conn.execute(
            "ALTER TABLE user_sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
        )


def _user_row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def create_user(
    email: str,
    display_name: str,
    password_hash: str,
    consent_version: str = "",
) -> dict:
    now = _iso(_now())
    user_id = str(uuid.uuid4())
    consent_at = now if consent_version else None
    try:
        with _connection() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (id, email, display_name, password_hash, status,
                     created_at, updated_at, last_login_at,
                     consent_version, consent_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, NULL, ?, ?)
                """,
                (
                    user_id,
                    email,
                    display_name,
                    password_hash,
                    now,
                    now,
                    consent_version,
                    consent_at,
                ),
            )
    except sqlite3.IntegrityError as exc:
        # 并发注册同一邮箱时，唯一索引是最后一道防线。
        raise DuplicateEmail(email) from exc
    return get_user_by_id(user_id)


def record_consent(user_id: str, version: str) -> dict | None:
    now = _iso(_now())
    with _connection() as conn:
        conn.execute(
            "UPDATE users SET consent_version = ?, consent_at = ?, updated_at = ? "
            "WHERE id = ?",
            (version, now, now, user_id),
        )
    return get_user_by_id(user_id)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_account_token(user_id: str, purpose: str, ttl_minutes: int = 60) -> str:
    """生成一次性令牌并只返回明文；库里只存摘要。"""
    raw = secrets.token_urlsafe(32)
    now = _now()
    with _connection() as conn:
        # 同一用途只保留最新一个有效令牌，旧链接立即失效。
        conn.execute(
            "UPDATE account_tokens SET used_at = ? "
            "WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
            (_iso(now), user_id, purpose),
        )
        conn.execute(
            """
            INSERT INTO account_tokens
                (id, user_id, purpose, token_hash, created_at, expires_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                str(uuid.uuid4()),
                user_id,
                purpose,
                _token_digest(raw),
                _iso(now),
                _iso(now + timedelta(minutes=ttl_minutes)),
            ),
        )
    return raw


def consume_account_token(token: str, purpose: str) -> str | None:
    """校验并通过后标记已使用；返回 user_id，失败返回 None。"""
    now = _now()
    with _connection() as conn:
        row = conn.execute(
            "SELECT id, user_id, expires_at, used_at FROM account_tokens "
            "WHERE token_hash = ? AND purpose = ?",
            (_token_digest(token), purpose),
        ).fetchone()
        if row is None or row["used_at"]:
            return None
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            return None
        if expires_at <= now:
            return None
        conn.execute(
            "UPDATE account_tokens SET used_at = ? WHERE id = ?",
            (_iso(now), row["id"]),
        )
        return row["user_id"]


def mark_email_verified(user_id: str) -> None:
    now = _iso(_now())
    with _connection() as conn:
        conn.execute(
            "UPDATE users SET email_verified = 1, email_verified_at = ?, "
            "updated_at = ? WHERE id = ?",
            (now, now, user_id),
        )


def update_password(user_id: str, password_hash: str) -> None:
    now = _iso(_now())
    with _connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, user_id),
        )


def revoke_all_auth_sessions(user_id: str) -> None:
    now = _iso(_now())
    with _connection() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )


def delete_user(user_id: str) -> list[str]:
    """删除账号并返回该账号的 session_id，调用方负责清 checkpoint 与反馈。"""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT session_id FROM user_sessions WHERE user_id = ?", (user_id,)
        ).fetchall()
        ids = [row["session_id"] for row in rows]
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return ids


def sessions_before(cutoff: str) -> list[str]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT session_id FROM user_sessions WHERE last_active_at < ?",
            (cutoff,),
        ).fetchall()
    return [row["session_id"] for row in rows]


def purge_sessions_before(cutoff: str) -> list[str]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT session_id FROM user_sessions WHERE last_active_at < ?",
            (cutoff,),
        ).fetchall()
        ids = [row["session_id"] for row in rows]
        conn.executemany(
            "DELETE FROM user_sessions WHERE session_id = ?",
            [(session_id,) for session_id in ids],
        )
    return ids


def get_user_by_email(email: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    return _user_row(row)


def get_user_by_id(user_id: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row)


def mark_login(user_id: str) -> None:
    now = _iso(_now())
    with _connection() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now, now, user_id),
        )


# --- 问诊会话归属与历史 ---


def make_session_title(text: str) -> str:
    """用第一条用户消息做标题；空白消息返回空串，前端显示默认文案。"""
    clean = " ".join(text.split())
    if not clean:
        return ""
    if len(clean) <= SESSION_TITLE_CHARS:
        return clean
    return clean[:SESSION_TITLE_CHARS] + "…"


def record_session(
    session_id: str, user_id: str | None, title_text: str = ""
) -> None:
    """创建会话时登记归属；游客会话也登记，登录后才能认领。"""
    now = _iso(_now())
    with _connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_sessions
                (session_id, user_id, title, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, user_id, make_session_title(title_text), now, now),
        )


def touch_session_record(session_id: str, title_text: str = "") -> None:
    """每轮结束刷新活跃时间；标题为空时用本轮首条消息补齐。"""
    now = _iso(_now())
    title = make_session_title(title_text)
    with _connection() as conn:
        cursor = conn.execute(
            """
            UPDATE user_sessions
            SET last_active_at = ?,
                title = CASE WHEN title = '' AND ? <> '' THEN ? ELSE title END
            WHERE session_id = ?
            """,
            (now, title, title, session_id),
        )
        if cursor.rowcount == 0:
            # 功能上线前创建的会话没有登记行；补一行游客记录，不抢归属。
            conn.execute(
                """
                INSERT OR IGNORE INTO user_sessions
                    (session_id, user_id, title, created_at, last_active_at)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (session_id, title, now, now),
            )


def claim_session(session_id: str, user_id: str) -> str:
    """把游客会话绑定到当前账号；返回 claimed / already / conflict / missing。"""
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE user_sessions SET user_id = ? "
            "WHERE session_id = ? AND user_id IS NULL",
            (user_id, session_id),
        )
        if cursor.rowcount > 0:
            return "claimed"
        row = conn.execute(
            "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return "missing"
    return "already" if row["user_id"] == user_id else "conflict"


def list_sessions(user_id: str, limit: int = 50) -> list[dict]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT session_id, title, pinned, created_at, last_active_at "
            "FROM user_sessions WHERE user_id = ? "
            "ORDER BY pinned DESC, last_active_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        {**dict(row), "pinned": bool(row["pinned"])}
        for row in rows
    ]


def update_session(
    session_id: str,
    user_id: str,
    *,
    title: str | None = None,
    pinned: bool | None = None,
) -> bool:
    """更新自己的会话标题或置顶状态；返回是否确实更新了一行。"""
    sets: list[str] = []
    params: list = []
    if title is not None:
        sets.append("title = ?")
        params.append(make_session_title(title))
    if pinned is not None:
        sets.append("pinned = ?")
        params.append(1 if pinned else 0)
    if not sets:
        return False
    params.extend([session_id, user_id])
    with _connection() as conn:
        cursor = conn.execute(
            f"UPDATE user_sessions SET {', '.join(sets)} "
            "WHERE session_id = ? AND user_id = ?",
            params,
        )
        return cursor.rowcount > 0


def get_session_record(session_id: str, user_id: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT session_id, title, pinned, created_at, last_active_at "
            "FROM user_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    return {**dict(row), "pinned": bool(row["pinned"])} if row else None


def user_owns_session(session_id: str, user_id: str) -> bool:
    with _connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    return row is not None


def forget_session(session_id: str) -> None:
    """会话被删除后一并清掉历史行，避免列表里留下打不开的条目。"""
    with _connection() as conn:
        conn.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))


def create_session(
    user_id: str,
    token_hash: str,
    client_ip: str = "",
    user_agent: str = "",
    ttl_days: int = DEFAULT_SESSION_TTL_DAYS,
) -> dict:
    now = _now()
    session = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "token_hash": token_hash,
        "client_ip": client_ip[:64],
        "user_agent": user_agent[:256],
        "created_at": _iso(now),
        "last_seen_at": _iso(now),
        "expires_at": _iso(now + timedelta(days=ttl_days)),
        "revoked_at": None,
    }
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions
                (id, user_id, token_hash, client_ip, user_agent,
                 created_at, last_seen_at, expires_at, revoked_at)
            VALUES
                (:id, :user_id, :token_hash, :client_ip, :user_agent,
                 :created_at, :last_seen_at, :expires_at, :revoked_at)
            """,
            session,
        )
    return session


def find_session(token_hash: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM auth_sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    return _user_row(row)


def touch_session(session_id: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
            (_iso(_now()), session_id),
        )


def revoke_session(token_hash: str) -> bool:
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (_iso(_now()), token_hash),
        )
        return cursor.rowcount > 0
