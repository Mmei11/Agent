"""Phase 5 L2：安全审计事件。

与 trace 刻意分开：trace 可以关（默认就是关的），审计必须落盘。
每次风险升级都要留下“时间、规则、输入摘要、动作”，供事后回放。
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import load_env_file
from backend.app.observability.redact import redact_text
from backend.app.observability.tracer import span

DEFAULT_AUDIT_PATH = "logs/audit.jsonl"
MAX_DETAIL_LENGTH = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def input_digest(text: str) -> str:
    """只存短哈希，不存原文；既能关联同一输入，又不把病情写进审计。"""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class AuditEvent:
    layer: str
    rule: str
    action: str
    session_id: str = ""
    turn_id: int = 0
    input_digest: str = ""
    detail: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp or _now_iso(),
            "layer": self.layer,
            "rule": self.rule,
            "action": self.action,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "input_digest": self.input_digest,
            "detail": redact_text(self.detail)[:MAX_DETAIL_LENGTH],
        }


def write(event: AuditEvent, path: str | Path | None = None) -> dict:
    """写一条审计事件；写盘失败不抛异常，不能因为审计拖垮问诊。"""
    load_env_file()
    payload = event.to_dict()
    target = Path(path or os.getenv("AUDIT_LOG_PATH") or DEFAULT_AUDIT_PATH)

    written = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        written = True
    except OSError:
        pass

    with span("safety.escalation", kind="safety", attributes=payload) as record:
        record.attributes["safety.written"] = written

    return payload
