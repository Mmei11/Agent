"""Phase 6 L4：用户反馈存储。

反馈和 checkpoint 分开：它是产品信号，不是问诊状态。
独立表 + 独立连接，写入频率低，每次操作开关连接即可。
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.storage.saver import DEFAULT_DB_PATH

VALID_RATINGS = {"helpful", "unhelpful"}


@dataclass(frozen=True)
class FeedbackRecord:
    session_id: str
    turn_id: int
    rating: str
    reason: str = ""
    trace_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "rating": self.rating,
            "reason": self.reason,
            "trace_id": self.trace_id,
            "created_at": self.created_at
            or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }


def _db_path() -> str:
    return os.getenv("CONSULTATION_DB_PATH", DEFAULT_DB_PATH)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            session_id TEXT NOT NULL,
            turn_id INTEGER NOT NULL,
            rating TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            trace_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, turn_id)
        )
        """
    )
    return conn


def save(record: FeedbackRecord) -> dict:
    """同一会话同一轮重复提交时覆盖，不产生重复行。"""
    if record.rating not in VALID_RATINGS:
        raise ValueError(f"unknown rating: {record.rating}")

    payload = record.to_dict()
    with _connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO feedback (session_id, turn_id, rating, reason, trace_id, created_at)
            VALUES (:session_id, :turn_id, :rating, :reason, :trace_id, :created_at)
            ON CONFLICT(session_id, turn_id) DO UPDATE SET
                rating = excluded.rating,
                reason = excluded.reason,
                trace_id = excluded.trace_id,
                created_at = excluded.created_at
            """,
            payload,
        )
    return payload


def list_for_session(session_id: str) -> list[dict]:
    with _connect(_db_path()) as conn:
        rows = conn.execute(
            "SELECT session_id, turn_id, rating, reason, trace_id, created_at "
            "FROM feedback WHERE session_id = ? ORDER BY turn_id",
            (session_id,),
        ).fetchall()
    keys = ("session_id", "turn_id", "rating", "reason", "trace_id", "created_at")
    return [dict(zip(keys, row)) for row in rows]


def delete_for_sessions(session_ids: list[str]) -> int:
    """删除账号时一并清理这些会话的反馈，避免只删会话留下孤儿反馈。"""
    if not session_ids:
        return 0
    placeholders = ",".join("?" for _ in session_ids)
    with _connect(_db_path()) as conn:
        cursor = conn.execute(
            f"DELETE FROM feedback WHERE session_id IN ({placeholders})",
            session_ids,
        )
        return cursor.rowcount


def stats() -> dict:
    """离线统计好评率；演示与面试时可以直接引用。"""
    with _connect(_db_path()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        helpful = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = 'helpful'"
        ).fetchone()[0]
    return {
        "total": total,
        "helpful": helpful,
        "unhelpful": total - helpful,
        "helpful_rate": round(helpful / total, 4) if total else 0.0,
    }
