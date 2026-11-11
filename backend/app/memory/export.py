"""Phase B：会话记忆导出视图。

只读：从 checkpoint 里取 `session_memory`，渲染成人和程序都能读的形式。
它不是第二份存储——写入仍只发生在 intake，事实与结论仍以
`collected_facts` / `final_result` 为准。
"""

from __future__ import annotations

from typing import Any


def _text(value: Any, limit: int = 200) -> str:
    """单行化，避免 Markdown 视图被换行撑乱。"""
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def render_markdown(session_id: str, memory: dict | None, turn_id: int = 0) -> str:
    """把记忆卡渲染成一份可读的 Markdown。"""
    memory = memory or {}
    details = list(memory.get("details") or [])
    open_questions = [str(item) for item in (memory.get("open_questions") or [])]
    recent_topic = _text(memory.get("recent_topic"), limit=300)

    lines = [
        "# 会话记忆",
        "",
        f"- 会话：`{session_id}`",
        f"- 轮次：{turn_id}",
        f"- 记住的细节：{len(details)} 条",
        "",
        "## 记住的细节",
        "",
    ]

    if details:
        for item in details:
            turn = item.get("turn")
            prefix = f"[第 {turn} 轮] " if turn else ""
            lines.append(f"- {prefix}{_text(item.get('text'))}")
    else:
        lines.append("暂无记忆细节。")

    lines += ["", "## 未决问题", ""]
    if open_questions:
        lines.extend(f"- {_text(item)}" for item in open_questions)
    else:
        lines.append("无。")

    lines += ["", "## 最近话题", "", recent_topic or "无。", ""]
    lines += [
        "---",
        "完整字段与结论请查看 `GET /sessions/{session_id}`；本视图只展示非字段细节。",
        "",
    ]
    return "\n".join(lines)


def memory_payload(session_id: str, values: dict) -> dict:
    """API 响应：结构化字段 + 渲染文本。"""
    memory = values.get("session_memory") or {}
    turn_id = int(values.get("turn_id") or 0)
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "details": list(memory.get("details") or []),
        "open_questions": list(memory.get("open_questions") or []),
        "recent_topic": memory.get("recent_topic", ""),
        "turns": int(memory.get("turns") or 0),
        "rendered": render_markdown(session_id, memory, turn_id),
    }
