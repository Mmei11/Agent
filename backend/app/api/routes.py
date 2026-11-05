"""Day 14：问诊 Agent 的 FastAPI 接口。案例均为虚构，不涉及真实诊疗。"""

import asyncio
import contextvars
import json
import logging
import os
import threading
import uuid  # 生成会话 ID（UUID 随机字符串）
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request  # FastAPI 框架和 HTTP 错误响应
from pydantic import BaseModel, Field  # 定义请求和响应的数据结构
from sse_starlette.sse import EventSourceResponse  # Phase 6：服务端发送事件流

from backend.app.agent.graph import build_graph  # 组装 LangGraph 状态图
from backend.app.api import inflight  # Phase 6 L3.3：会话在途与流计数
from backend.app.api.auth import CallerIdentity, caller_key, require_api_key  # 认证
from backend.app.api.limits import (  # Phase 6 L3.3：体积与消息长度边界
    BodySizeLimitMiddleware,
    check_message_length,
)
from backend.app.api.ratelimit import rate_limit  # Phase 6 L3.2：令牌桶限流
from backend.app.config import load_env_file  # 统一读取 .env
from backend.app.feedback import scores as feedback_scores  # Phase 6 L4：反馈评分
from backend.app.feedback import store as feedback_store  # Phase 6 L4：反馈存储
from backend.app.feedback.store import FeedbackRecord
from backend.app.observability import current_trace, start_trace  # 一次请求 = 一条 trace
from backend.app.observability.instrument import summarize_node_result  # 节点摘要白名单
from backend.app.observability.redact import redact_text  # 反馈原因脱敏
from backend.app.observability.request_trace import RequestTraceMiddleware  # 请求级 trace
from backend.app.storage.saver import DEFAULT_DB_PATH, create_saver  # SQLite saver

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

# 体积检查放在最外层：超大 body 在认证、限流、解析之前就被拒绝。
app.add_middleware(BodySizeLimitMiddleware)
# 请求级 trace 包在最外层，认证/限流的 span 才有地方落。
app.add_middleware(RequestTraceMiddleware)


def _parent_trace_attributes() -> dict:
    """把当前请求 trace 的 id 挂到本轮 turn trace 上，方便串联。"""
    parent = current_trace()
    return {"parent.trace_id": parent.trace_id} if parent else {}

# 图是同步的（SqliteSaver 没有异步接口），所以给图单独一个线程池，
# 不跟 FastAPI 的 anyio 线程池抢名额。线程数就是"同时在跑的图"的上限。
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

# 共享一个 saver 和编译后的图。
#
# WHY 不每线程一个连接：L2 实测过每线程连接 + WAL + 64 线程，
# p50 从 250ms 降到 130ms，但 p95 从 400ms 涨到 1050ms——50 个线程
# 同时写 SQLite，busy_timeout 等待把尾部拉长。SqliteSaver 内部本来
# 就有一把 threading.Lock，它天然起到"数据库访问排队"的作用，
# 在本项目这个量级下反而让尾延迟更可预测。
_saver = None
_compiled_app = None
_app_path: str | None = None
_app_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                load_env_file()
                workers = os.getenv("GRAPH_MAX_WORKERS")
                if not workers:
                    # mock 模式是 CPU 密集：线程越多，GIL 竞争越重，8 个左右尾延迟最好。
                    # 模型模式是 I/O 密集：线程大部分时间在等网络，64 个能撑住 50 并发。
                    workers = "64" if os.getenv("USE_MODEL") == "1" else "8"
                _executor = ThreadPoolExecutor(
                    max_workers=int(workers), thread_name_prefix="graph"
                )
    return _executor


async def _run_sync(fn, *args):
    """把同步函数丢进图线程池，并保留当前 ContextVar（trace 依赖它）。"""
    context = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_executor(), context.run, fn, *args)


def get_app():
    """返回编译后的图；数据库路径变化时重建（测试会切临时库）。"""
    global _saver, _compiled_app, _app_path

    load_env_file()
    path = os.getenv("CONSULTATION_DB_PATH", DEFAULT_DB_PATH)

    with _app_lock:
        if _compiled_app is None or _app_path != path:
            if _saver is not None:
                _close_saver(_saver)
            _saver = create_saver(path)
            _compiled_app = build_graph(checkpointer=_saver)
            _app_path = path
        return _compiled_app


def _current_saver():
    get_app()
    with _app_lock:
        return _saver


def _close_saver(saver) -> None:
    try:
        saver.conn.close()
    except Exception:
        pass


def reset_app() -> None:
    """关闭当前 saver 并清空缓存；测试在切换临时库时调用。"""
    global _saver, _compiled_app, _app_path
    with _app_lock:
        if _saver is not None:
            _close_saver(_saver)
        _saver = None
        _compiled_app = None
        _app_path = None


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


class FeedbackRequest(BaseModel):
    """反馈请求：评分 + 可选原因。"""

    rating: Literal["helpful", "unhelpful"]
    reason: str = Field(default="", max_length=200)


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
    turn_id: int = 0  # 当前轮次，反馈提交时用来定位
    suggested_replies: list = []  # 当前追问的可点选回答
    risk_flags: list = []  # 命中的风险规则
    final_result: dict = {}  # 结构化最终结果
    rendered_text: str = ""  # 给用户直接看的渲染文案
    conversation: list = []  # 完整对话，随检查点保存


# --- 五个接口 ---

@app.post("/sessions", response_model=SessionResponse, dependencies=[Depends(rate_limit)])
async def create_session(req: CreateSessionRequest):
    """创建新会话。如果传了 initial_message，立即处理。

    流程：生成 session_id → 如果有消息就调图处理 → 返回状态。
    """
    session_id = str(uuid.uuid4())  # 生成随机会话 ID，如 "a3f2b1c4-..."
    check_message_length(req.initial_message)
    values = await _run_sync(_create_session_sync, session_id, req.initial_message)
    return _to_response(session_id, values)


def _create_session_sync(session_id: str, initial_message: str) -> dict:
    """在工作线程里建会话；同一线程内的 saver 负责写入。"""
    graph = get_app()
    config = {"configurable": {"thread_id": session_id}}
    message = initial_message.strip()

    if message:
        # 新会话的第一轮；trace 在这里开启，图内的节点 span 都挂在它下面。
        with start_trace(
            session_id, turn_id=1, root_attributes=_parent_trace_attributes()
        ):
            graph.invoke(
                {"latest_user_message": message, "session_id": session_id},
                config=config,
            )
    else:
        # 空会话也落盘，确保返回的 ID 随后能 GET 和发送消息。
        graph.update_state(config, {"session_id": session_id})

    return _read_values(graph, config)


@app.post(
    "/sessions/{session_id}/messages",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit)],
)
async def send_message(session_id: str, req: SendMessageRequest):
    """向已有会话发送消息，返回处理后的状态。

    session_id 从 URL 路径取，如 POST /sessions/a3f2b1c4/messages。
    """
    message = req.message.strip()  # 取消息内容，去首尾空格
    if not message:  # 空消息
        raise HTTPException(status_code=400, detail="消息不能为空")  # 400 Bad Request
    check_message_length(message)

    # 同一会话一次只跑一轮，防止两个请求同时改同一份 checkpoint。
    with inflight.session_guard(session_id):
        values = await _run_sync(_run_turn_sync, session_id, message)
    return _to_response(session_id, values)


def _run_turn_sync(session_id: str, message: str) -> dict:
    """前置检查 + 调图放在同一个线程里，保证用的是同一个 saver 连接。"""
    config, turn_id = _prepare_turn_sync(session_id)
    graph = get_app()

    with start_trace(
        session_id, turn_id=turn_id, root_attributes=_parent_trace_attributes()
    ):
        return graph.invoke(  # checkpointer 自动恢复之前的 state
            {"latest_user_message": message, "session_id": session_id},
            config=config,
        )


@app.post("/sessions/{session_id}/stream", dependencies=[Depends(rate_limit)])
async def stream_message(
    session_id: str,
    req: SendMessageRequest,
    request: Request,
    caller: CallerIdentity = Depends(require_api_key),
):
    """流式处理一条消息，用 SSE 先推进度、最后推完整结果。

    与 /messages 的区别只在传输方式：图仍然完整跑完并落 checkpoint，
    客户端断开也不会留下半截状态。
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")
    check_message_length(message)

    config, turn_id = await _run_sync(_prepare_turn_sync, session_id)

    # 先占连接名额，再占会话；任何一步失败都要把前一步释放掉。
    caller_id = caller_key(caller, request)
    if not inflight.open_stream(caller_id):
        raise HTTPException(
            status_code=429,
            detail="并发流数量超限",
            headers={"Retry-After": "1"},
        )
    if not inflight.enter_for_stream(session_id):
        inflight.close_stream(caller_id)
        raise HTTPException(
            status_code=409,
            detail="上一轮还在处理，请稍后再试",
            headers={"Retry-After": "1"},
        )

    queue: asyncio.Queue = asyncio.Queue()
    try:
        task = asyncio.create_task(
            _stream_turn(config, session_id, message, turn_id, queue, caller_id)
        )
    except Exception:
        inflight.leave(session_id)
        inflight.close_stream(caller_id)
        raise

    _background_tasks.add(task)
    task.add_done_callback(_on_background_done)

    return EventSourceResponse(
        _event_stream(queue, session_id, turn_id),
        headers={
            # 代理不能缓冲，否则事件会被攒到整轮结束才发出去。
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream(queue: asyncio.Queue, session_id: str, turn_id: int):
    """把 worker 线程产出的事件转成 SSE 帧。

    任务在路由里已经创建；这里只负责消费队列，客户端断开也不会取消它。
    """
    finished = False
    try:
        while True:
            event, data = await queue.get()
            if event == "__done__":  # worker 已结束
                finished = True
                break
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
    finally:
        # 客户端断开时 task 继续跑完并落 checkpoint / 审计。
        if not finished:
            logger.info("stream client disconnected; turn %s/%s continues", session_id, turn_id)


async def _stream_turn(
    config: dict,
    session_id: str,
    message: str,
    turn_id: int,
    queue: asyncio.Queue,
    caller_id: str,
):
    """在线程里跑图，把节点事件推回事件循环。

    图对象在 work 内部获取：每个线程有自己的 saver，图必须在同一个
    线程里创建和使用，不能从请求线程传过来。
    """
    loop = asyncio.get_running_loop()

    def push(event: str, data: dict) -> None:
        # 线程安全地把事件交给事件循环；不阻塞 worker。
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def work() -> None:
        with start_trace(
            session_id, turn_id=turn_id, root_attributes=_parent_trace_attributes()
        ) as scope:
            try:
                graph = get_app()
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

    try:
        await _run_sync(work)
    finally:
        # 任务无论正常结束、异常还是被取消，都要释放会话与连接名额。
        inflight.leave(session_id)
        inflight.close_stream(caller_id)


def _on_background_done(task: asyncio.Task) -> None:
    """后台任务结束时回收引用，并取一次异常避免 "never retrieved" 警告。"""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("stream background task failed: %r", error)


def _prepare_turn_sync(session_id: str):
    """校验会话可继续，返回 config 和本轮 turn_id。

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
    return config, turn_id


@app.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit)],
)
async def get_session(session_id: str):
    """查看会话当前状态，不处理新消息。

    前端可以用这个接口刷新页面后恢复会话显示。
    """
    values = await _run_sync(_read_state_sync, session_id)
    return _to_response(session_id, values)


def _read_state_sync(session_id: str) -> dict:
    config = {"configurable": {"thread_id": session_id}}
    return _read_values(get_app(), config)


def _read_values(graph, config: dict) -> dict:
    state = graph.get_state(config)  # 从 SQLite 读回该 thread_id 的状态
    if state is None or state.values is None or not state.values:
        raise HTTPException(status_code=404, detail="会话不存在")
    return state.values


@app.delete("/sessions/{session_id}", dependencies=[Depends(rate_limit)])
async def delete_session(session_id: str):
    """删除这个会话的 SQLite 检查点。"""
    # 有轮次在跑时不允许删除，避免把正在写的 checkpoint 抽走。
    with inflight.session_guard(session_id):
        await _run_sync(_delete_session_sync, session_id)
    return {"session_id": session_id, "status": "deleted"}


def _delete_session_sync(session_id: str) -> None:
    _current_saver().delete_thread(session_id)


@app.post("/sessions/{session_id}/feedback", dependencies=[Depends(rate_limit)])
async def submit_feedback(session_id: str, req: FeedbackRequest):
    """提交本轮反馈；同一会话同一轮重复提交会覆盖。"""
    return await _run_sync(
        _save_feedback_sync, session_id, req.rating, req.reason
    )


def _save_feedback_sync(session_id: str, rating: str, reason: str) -> dict:
    config = {"configurable": {"thread_id": session_id}}
    values = _read_values(get_app(), config)

    turn_id = int(values.get("turn_id", 0))
    if turn_id <= 0:
        raise HTTPException(status_code=409, detail="还没有可评价的轮次")

    trace_id = values.get("trace_id", "")
    safe_reason = redact_text(reason)[:200]
    feedback_store.save(
        FeedbackRecord(
            session_id=session_id,
            turn_id=turn_id,
            rating=rating,
            reason=safe_reason,
            trace_id=trace_id,
        )
    )
    # 评分是旁路：Langfuse 不可达时不影响反馈已保存。
    feedback_scores.emit_score(
        name="user_feedback",
        value=1 if rating == "helpful" else 0,
        trace_id=trace_id,
        session_id=session_id,
        comment=safe_reason,
    )
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "rating": rating,
        "saved": True,
    }


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
        turn_id=values.get("turn_id", 0),
        suggested_replies=values.get("suggested_replies", []),
        risk_flags=values.get("risk_flags", []),
        final_result=values.get("final_result", {}),
        rendered_text=values.get("rendered_text", ""),
        conversation=values.get("conversation", []),
    )
