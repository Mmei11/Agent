"""Day 14：问诊 Agent 的 FastAPI 接口。案例均为虚构，不涉及真实诊疗。"""

import asyncio
import json
import logging
import os
import sqlite3  # 创建 SQLite 连接给 SqliteSaver
import uuid  # 生成会话 ID（UUID 随机字符串）

from fastapi import FastAPI, HTTPException  # FastAPI 框架和 HTTP 错误响应
from pydantic import BaseModel  # 定义请求和响应的数据结构
from sse_starlette.sse import EventSourceResponse  # Phase 6：服务端发送事件流

from backend.app.agent.graph import build_graph  # 组装 LangGraph 状态图
from backend.app.config import load_env_file  # 统一读取 .env
from backend.app.observability import start_trace  # 一次请求 = 一条 trace
from backend.app.observability.instrument import summarize_node_result  # 节点摘要白名单
from backend.app.storage.saver import DEFAULT_DB_PATH  # SQLite 文件默认路径

logger = logging.getLogger(__name__)

# 只把这些节点的进度推给前端；图内部的 checkpoint 等事件不对外。
STREAM_NODES = {"intake", "risk_check", "decision", "followup", "tool", "output"}

# 客户端断开后，后台线程仍要把本轮跑完；这里持有任务引用，避免被垃圾回收。
_background_tasks: set[asyncio.Task] = set()

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
        load_env_file()
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
    trace_id: str = ""  # 本轮请求的观测 id，可用于跳转完整轨迹
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
        # 新会话的第一轮；trace 在这里开启，图内的节点 span 都挂在它下面。
        with start_trace(session_id, turn_id=1):
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

    graph, config, turn_id = _prepare_turn(session_id)

    with start_trace(session_id, turn_id=turn_id):
        result = graph.invoke(  # 调图处理消息，checkpointer 自动恢复之前的 state
            {"latest_user_message": message, "session_id": session_id},
            config=config,
        )

    return _to_response(session_id, result)


@app.post("/sessions/{session_id}/stream")
async def stream_message(session_id: str, req: SendMessageRequest):
    """流式处理一条消息，用 SSE 先推进度、最后推完整结果。

    与 /messages 的区别只在传输方式：图仍然完整跑完并落 checkpoint，
    客户端断开也不会留下半截状态。
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    graph, config, turn_id = _prepare_turn(session_id)

    return EventSourceResponse(
        _event_stream(graph, config, session_id, message, turn_id),
        headers={
            # 代理不能缓冲，否则事件会被攒到整轮结束才发出去。
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream(graph, config: dict, session_id: str, message: str, turn_id: int):
    """把 worker 线程产出的事件转成 SSE 帧。

    图在 worker 线程里跑：同步的 SqliteSaver 不需要异步改造，
    而 start_trace 也在同一个线程里开合，trace 生命周期与连接解耦。
    """
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_stream_turn(graph, config, session_id, message, turn_id, queue))
    _background_tasks.add(task)
    task.add_done_callback(_on_background_done)

    try:
        while True:
            item = await queue.get()
            event, data = item
            if event == "__done__":  # worker 已结束
                break
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
    finally:
        # 客户端断开时不要取消 task：让它跑完并落 checkpoint / 审计。
        # 队列是无界的，剩余事件留在内存里随任务结束被回收。
        if not task.done():
            logger.info("stream client disconnected; turn %s/%s continues", session_id, turn_id)


async def _stream_turn(graph, config: dict, session_id: str, message: str, turn_id: int, queue: asyncio.Queue):
    """在线程里跑图，把节点事件推回事件循环。

    asyncio.to_thread 会复制当前 ContextVar，因此在 worker 里 start_trace
    依然能让节点埋点拿到同一条 trace。
    """
    loop = asyncio.get_running_loop()

    def push(event: str, data: dict) -> None:
        # 线程安全地把事件交给事件循环；不阻塞 worker。
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def work() -> None:
        with start_trace(session_id, turn_id=turn_id) as scope:
            try:
                push(
                    "meta",
                    {"session_id": session_id, "turn_id": turn_id, "trace_id": scope.trace_id},
                )
                payload = {"latest_user_message": message, "session_id": session_id}
                for chunk in graph.stream(payload, config=config, stream_mode="debug"):
                    kind = chunk.get("type")
                    body = chunk.get("payload") or {}
                    node = body.get("name")
                    if node not in STREAM_NODES:
                        continue
                    if kind == "task":
                        push("node", {"node": node, "phase": "start"})
                    elif kind == "task_result":
                        push(
                            "node",
                            {
                                "node": node,
                                "phase": "end",
                                "ok": body.get("error") is None,
                                "summary": summarize_node_result(body.get("result")),
                            },
                        )

                state = graph.get_state(config)
                push("result", _to_response(session_id, state.values).model_dump())
            except Exception:
                # 堆栈只进服务端日志，前端拿到可重试的稳定提示。
                logger.exception("stream turn failed: %s/%s", session_id, turn_id)
                push("error", {"detail": "本轮处理失败，请重试", "retryable": True})
            finally:
                push("__done__", {})

    await asyncio.to_thread(work)


def _on_background_done(task: asyncio.Task) -> None:
    """后台任务结束时回收引用，并取一次异常避免 "never retrieved" 警告。"""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("stream background task failed: %r", error)


def _prepare_turn(session_id: str):
    """校验会话可继续，返回图、config 和本轮 turn_id。

    同步接口与流式接口共用同一套前置检查，避免两条路径行为不一致。
    """
    graph = get_app()
    config = {"configurable": {"thread_id": session_id}}

    snapshot = graph.get_state(config)
    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="会话不存在")
    if snapshot.values.get("final_result"):
        raise HTTPException(status_code=409, detail="本轮问诊已结束，请开启新会话")

    # turn_id 在进入图之前算好：intake 节点会把 state.turn_id 加一。
    turn_id = int((snapshot.values or {}).get("turn_id", 0)) + 1
    return graph, config, turn_id


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


def _to_response(session_id: str, values: dict) -> SessionResponse:
    """把图的状态字典打包成前端契约，同步、流式和查询接口共用。"""
    values = values or {}
    return SessionResponse(
        session_id=session_id,
        trace_id=values.get("trace_id", ""),
        collected_facts=values.get("collected_facts", {}),
        missing_fields=values.get("missing_fields", []),
        asked_questions=values.get("asked_questions", []),
        followup_count=values.get("followup_count", 0),
        risk_flags=values.get("risk_flags", []),
        final_result=values.get("final_result", {}),
        rendered_text=values.get("rendered_text", ""),
        conversation=values.get("conversation", []),
    )


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

    return _to_response(session_id, state.values)
