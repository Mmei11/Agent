"""Phase 6 L4：把用户反馈送进观测平台。

优先写 Langfuse score；没配置或发送失败时，本地 JSONL 仍然保留一份，
保证离线也能统计好评率、按 trace 回放。
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import load_env_file
from backend.app.observability.redact import redact_text

DEFAULT_SCORE_PATH = "logs/scores.jsonl"
MAX_COMMENT_LENGTH = 200

_client = None
_client_lock = threading.Lock()


def _langfuse_client():
    """延迟初始化 Langfuse 客户端；配置不全时返回 None。"""
    global _client
    if _client is not None:
        return _client

    load_env_file()
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return None

    with _client_lock:
        if _client is None:
            try:
                from langfuse import Langfuse

                _client = Langfuse(
                    public_key=public_key,
                    secret_key=secret_key,
                    host=os.getenv("LANGFUSE_HOST", "http://127.0.0.1:3000"),
                )
            except Exception:
                # 观测是旁路，初始化失败不能影响反馈写入。
                _client = None
    return _client


def reset_client() -> None:
    """测试用：清掉缓存的客户端。"""
    global _client
    with _client_lock:
        _client = None


def emit_score(
    name: str,
    value: float,
    trace_id: str = "",
    session_id: str = "",
    comment: str = "",
    data_type: str = "BOOLEAN",
) -> dict:
    """写一条评分；本地 JSONL 必写，Langfuse 尽力而为。"""
    payload = {
        "name": name,
        "value": value,
        "trace_id": trace_id,
        "session_id": session_id,
        "comment": redact_text(comment or "")[:MAX_COMMENT_LENGTH],
        "data_type": data_type,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }

    load_env_file()
    path = Path(os.getenv("SCORE_LOG_PATH", DEFAULT_SCORE_PATH))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass

    client = _langfuse_client()
    if client is not None:
        try:
            # Langfuse v4 要求 traceId / sessionId / datasetRunId 三选一，
            # 同时传 trace_id 和 session_id 会被 400 拒绝。
            client.create_score(
                name=name,
                value=value,
                trace_id=trace_id or None,
                session_id=None if trace_id else (session_id or None),
                comment=payload["comment"] or None,
                data_type=data_type,
            )
        except Exception:
            # score 失败不影响用户已经提交的反馈。
            pass

    return payload
