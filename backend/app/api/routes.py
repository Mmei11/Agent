"""Day 14：问诊 Agent 的 FastAPI 接口。案例均为虚构，不涉及真实诊疗。"""

import os
import sqlite3  # 创建 SQLite 连接给 SqliteSaver
import uuid  # 生成会话 ID（UUID 随机字符串）

from fastapi import FastAPI, HTTPException  # FastAPI 框架和 HTTP 错误响应
from pydantic import BaseModel  # 定义请求和响应的数据结构

from backend.app.agent.graph import build_graph  # 组装 LangGraph 状态图
from backend.app.storage.saver import DEFAULT_DB_PATH  # SQLite 文件默认路径

# 创建 FastAPI 应用实例，title 和 description 会显示在 Swagger 文档页
app = FastAPI(
    title="智能问诊 Agent API",
    description="仅面向虚构模拟病例的求职演示。不提供真实医疗诊断、治疗或用药指导。",
)

# 全局变量：所有请求共享同一个 saver 和编译后的图
# 不能每次请求新建，否则不同请求看不到彼此的会话状态
_saver = None
_compiled_app = None


def get_app():
    """懒加载编译后的图。第一次调用时创建 saver 和图。

    懒加载 = 不在模块加载时就创建，而是第一次有人调时才创建。
    这样测试里不会因为 import 就连数据库。
    """
    global _saver, _compiled_app
    if _compiled_app is None:  # 还没创建过
        from langgraph.checkpoint.sqlite import SqliteSaver

        # 创建 SQLite 连接，check_same_thread=False 允许多线程共享连接
        conn = sqlite3.connect(os.getenv("CONSULTATION_DB_PATH", DEFAULT_DB_PATH), check_same_thread=False)
        _saver = SqliteSaver(conn)  # 用这个连接创建 checkpointer
        _compiled_app = build_graph(checkpointer=_saver)  # 编译图时传入 checkpointer
    return _compiled_app


# --- 请求和响应 schema ---

class CreateSessionRequest(BaseModel):
    """创建会话的请求体（可选初始消息）。

    前端发 POST /sessions 时，body 是 {"initial_message": "头部不适"}。
    initial_message 可以为空，只创建空会话不处理消息。
    """
    initial_message: str = ""  # 默认空字符串，不是必填


class SendMessageRequest(BaseModel):
    """发送消息的请求体。

    前端发 POST /sessions/{id}/messages 时，body 是 {"message": "前两天开始的"}。
    message 是必填字段，不能为空。
    """
    message: str  # 必填，没有默认值


class SessionResponse(BaseModel):
    """会话状态响应。所有接口的返回都用这个格式。

    前端拿到后可以直接渲染：collected_facts 显示已知信息，
    asked_questions 显示追问历史，final_result 显示最终结果。
    """
    session_id: str  # 会话 ID，前端保存用于后续请求
    collected_facts: dict = {}  # 已收集的字段，{"主要症状": ["头部不适"], ...}
    missing_fields: list = []  # 仍缺失的字段名
    asked_questions: list = []  # 已问过的问题列表
    followup_count: int = 0  # 已追问次数
    risk_flags: list = []  # 命中的风险规则
    final_result: dict = {}  # 结构化最终结果
    rendered_text: str = ""  # 给用户直接看的渲染文案
    conversation: list = []  # 完整对话，随检查点保存


# --- 四个接口 ---

@app.post("/sessions", response_model=SessionResponse)
def create_session(req: CreateSessionRequest):
    """创建新会话。如果传了 initial_message，立即处理。

    流程：生成 session_id → 如果有消息就调图处理 → 返回状态。
    """
    session_id = str(uuid.uuid4())  # 生成随机会话 ID，如 "a3f2b1c4-..."

    if req.initial_message.strip():  # 有消息（去掉首尾空格后非空）
        graph = get_app()  # 获取编译后的图
        config = {"configurable": {"thread_id": session_id}}  # 用 session_id 作为 thread_id
        graph.invoke(  # 调图处理用户消息
            {"latest_user_message": req.initial_message, "session_id": session_id},
            config=config,  # 传 config 让 checkpointer 按 thread_id 存档
        )
        return _get_state(session_id)  # 从 checkpointer 读回状态返回
    else:
        # 空会话也落盘，确保返回的 ID 随后能 GET 和发送消息。
        graph = get_app()
        graph.update_state({"configurable": {"thread_id": session_id}}, {"session_id": session_id})
        return _get_state(session_id)


@app.post("/sessions/{session_id}/messages", response_model=SessionResponse)
def send_message(session_id: str, req: SendMessageRequest):
    """向已有会话发送消息，返回处理后的状态。

    session_id 从 URL 路径取，如 POST /sessions/a3f2b1c4/messages。
    """
    message = req.message.strip()  # 取消息内容，去首尾空格
    if not message:  # 空消息
        raise HTTPException(status_code=400, detail="消息不能为空")  # 400 Bad Request

    graph = get_app()
    config = {"configurable": {"thread_id": session_id}}  # 同一个 session_id → 恢复之前的会话状态

    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="会话不存在")
    if snapshot.values.get("final_result"):
        raise HTTPException(status_code=409, detail="本轮问诊已结束，请开启新会话")

    result = graph.invoke(  # 调图处理消息，checkpointer 自动恢复之前的 state
        {"latest_user_message": message, "session_id": session_id},
        config=config,
    )

    # 把图返回的 state 打包成 SessionResponse 返回给前端
    return SessionResponse(
        session_id=session_id,
        collected_facts=result.get("collected_facts", {}),  # 已收集字段
        missing_fields=result.get("missing_fields", []),  # 仍缺失字段
        asked_questions=result.get("asked_questions", []),  # 追问历史
        followup_count=result.get("followup_count", 0),  # 追问次数
        risk_flags=result.get("risk_flags", []),  # 风险标记
        final_result=result.get("final_result", {}),  # 结构化最终结果
        rendered_text=result.get("rendered_text", ""),  # 渲染文案
        conversation=result.get("conversation", []),
    )


@app.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str):
    """查看会话当前状态，不处理新消息。

    前端可以用这个接口刷新页面后恢复会话显示。
    """
    return _get_state(session_id)  # 从 checkpointer 读状态


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """删除这个会话的 SQLite 检查点。"""
    get_app()
    _saver.delete_thread(session_id)
    return {"session_id": session_id, "status": "deleted"}


def _get_state(session_id: str) -> SessionResponse:
    """从 checkpointer 读取会话状态。

    这是内部辅助函数，不是接口。create_session 和 get_session 都调它。
    """
    graph = get_app()
    config = {"configurable": {"thread_id": session_id}}

    state = graph.get_state(config)  # 从 SQLite 读回该 thread_id 的状态

    # 不存在的会话：state 为 None 或 values 为空
    if state is None or state.values is None or not state.values:
        raise HTTPException(status_code=404, detail="会话不存在")  # 404 Not Found

    values = state.values  # 取出状态字典
    return SessionResponse(
        session_id=session_id,
        collected_facts=values.get("collected_facts", {}),  # 用 get 兜底，字段可能不存在
        missing_fields=values.get("missing_fields", []),
        asked_questions=values.get("asked_questions", []),
        followup_count=values.get("followup_count", 0),
        risk_flags=values.get("risk_flags", []),
        final_result=values.get("final_result", {}),
        rendered_text=values.get("rendered_text", ""),
        conversation=values.get("conversation", []),
    )
