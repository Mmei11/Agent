"""Phase 2 第一课：Trace / Span / Session 三层观测模型。

先记住三层关系（医院类比）：
- Session：一个病人的整本病历本 → 对应 session_id，跨多轮对话。
- Trace：一次就诊，从进门到离开 → 对应一次请求，图从 START 跑到 END。
- Span：就诊里的一个环节，如挂号、问诊、化验 → 对应一个节点或一次模型调用。
  Span 可以嵌套：decision 这个环节里，套着一次 llm.decision 子 span。

两条不可妥协的设计原则：
1. 观测是旁路。sink 写失败、磁盘满、观测服务挂了，都不能让问诊返回 500。
2. 没有 trace 时，span() 必须退化成 no-op，这样单测和脚本不用改代码。
"""

import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


def _now_iso() -> str:
    """墙上时间，用 UTC + 毫秒，便于跨机器、跨时区对齐日志。

    注意：这个时间只用于“什么时候发生”，不用于计算耗时。
    计算耗时用 time.perf_counter()，它是单调时钟，不会被系统改时间影响。
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class SpanRecord:
    """一个环节的完整记录。

    字段名尽量贴近 OpenTelemetry 的通用概念，将来换成 Langfuse、Jaeger
    或任何 OTel 后端时，映射关系是直接的，不需要重新设计数据结构。
    """

    # --- 身份：我在哪条 trace、属于哪个会话、第几轮 ---
    trace_id: str          # 一次请求一个，本轮所有 span 共享
    span_id: str           # 本环节自己的 ID，只要求同一条 trace 内唯一
    parent_span_id: str | None  # 父环节 ID；顶层 span 为 None
    session_id: str        # 会话 ID，跨轮聚合的关键
    turn_id: int           # 第几轮对话，从 1 开始

    # --- 这是什么环节 ---
    name: str              # 如 "intake" / "llm.decision" / "tool.department_suggest"
    kind: str              # node（图节点）/ llm（模型调用）/ tool（工具调用）

    # --- 时间 ---
    started_at: str        # ISO 字符串，方便人看和跨系统对齐
    ended_at: str          # span 结束时才填；未结束的 span 这里是空串
    duration_ms: int       # 耗时，毫秒；由 perf_counter 差值算出

    # --- 结果 ---
    status: str = "ok"     # ok / error
    attributes: dict = field(default_factory=dict)
    # attributes 放“这一步的细节”，命名遵循 OTel GenAI 约定，例如：
    # {"gen_ai.request.model": "qwen-plus",
    #  "gen_ai.usage.input_tokens": 820,
    #  "risk_level": "safe"}
    error: str | None = None  # 失败时只存异常类型，如 "ValueError"

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的普通字典，交给 sink 落盘。"""
        return asdict(self)


class TraceSink(Protocol):
    """Span 的落地方式。

    这里用 Protocol 而不是抽象基类：任何对象只要实现 emit(record)，
    就能当 sink 用，不需要继承。换平台就是换一个 sink，埋点代码不动。
    """

    def emit(self, record: SpanRecord) -> None: ...


class NullSink:
    """默认 sink：什么都不做。

    生产环境默认关闭观测，是为了避免测试、脚本、CI 意外写盘。
    需要时用 TRACE_SINK=jsonl 显式打开。
    """

    def emit(self, record: SpanRecord) -> None:
        return None


class MemorySink:
    """测试和本地演示用：把 span 留在内存列表里，方便断言。"""

    def __init__(self) -> None:
        self.records: list[SpanRecord] = []

    def emit(self, record: SpanRecord) -> None:
        # 这里不做任何异常处理，因为 TraceScope.emit 会统一兜底。
        self.records.append(record)


class JsonlSink:
    """一行一个 span，落到本地 JSONL 文件。

    为什么先本地 JSONL，而不是直接上 Langfuse：
    - 数据模型和埋点先做对，再接展示平台；
    - 出问题时能分清是“埋点错了”还是“平台配置错了”；
    - 不依赖任何外部服务，关掉也能开发。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def emit(self, record: SpanRecord) -> None:
        # 每次 append 都确保父目录存在；首次运行不会因为目录缺失而静默丢数据。
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # ensure_ascii=False：中文原样写入，方便直接 grep。
        # default=str：attributes 里万一混入 datetime 等非 JSON 类型，
        # 转成字符串而不是让整个 span 丢掉。
        line = json.dumps(record.to_dict(), ensure_ascii=False, default=str)

        # 追加模式 + 一次写一整行：多个请求并发写时，小行写入通常不会交错。
        # 这是演示级方案；生产级吞吐要换成带缓冲和锁的 writer。
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")


def get_sink() -> TraceSink:
    """按环境变量选择 sink。

    默认 none，避免测试和脚本意外写盘。
    TRACE_SINK=jsonl 时写文件，路径由 TRACE_PATH 指定。
    """
    kind = os.getenv("TRACE_SINK", "none").strip().lower()
    if kind == "jsonl":
        return JsonlSink(os.getenv("TRACE_PATH", "logs/traces.jsonl"))
    return NullSink()


@dataclass
class TraceScope:
    """一条 trace 的运行时容器，只活在当前请求的上下文里。"""

    trace_id: str
    session_id: str
    turn_id: int
    sink: TraceSink

    # 已完成的 span，按“结束时间”排序（子 span 先于父 span 结束）。
    # 它让调用方在 trace 结束后还能拿到整条轨迹，不依赖 sink 是否写成功。
    spans: list[SpanRecord] = field(default_factory=list)

    # 当前还没结束的 span，栈顶就是“当前所在环节”。
    # 新 span 的 parent_span_id 从栈顶取，因此嵌套关系自动成立。
    stack: list[SpanRecord] = field(default_factory=list)

    def emit(self, record: SpanRecord) -> None:
        # 先记内存，再写 sink：即使 sink 挂了，本次 trace 的数据仍然完整。
        self.spans.append(record)
        try:
            self.sink.emit(record)
        except Exception:
            # 这是“观测不能成为单点故障”的代码形态。
            # 这里故意吞掉异常，不记日志也不上抛；观测故障静默降级。
            pass


# 用 ContextVar 而不是全局变量，原因有两个：
# 1. FastAPI 的同步接口跑在线程池里，多个请求可能同时执行；
# 2. ContextVar 天然按执行上下文隔离，不会把 A 会话的 span 挂到 B 会话上。
_current_scope: ContextVar[TraceScope | None] = ContextVar("trace_scope", default=None)


def current_trace() -> TraceScope | None:
    """返回当前上下文里的 trace；不在 trace 里时返回 None。"""
    return _current_scope.get()


@contextmanager
def start_trace(session_id: str, turn_id: int, sink: TraceSink | None = None):
    """开启一轮请求的 trace，退出时自动清理。

    用法：
        with start_trace(session_id, turn_id) as trace:
            with span("intake"):
                ...

    为什么用 token + reset：
    嵌套调用 start_trace 时（例如测试里手动包一层），reset 能精确恢复
    上一层上下文，而不是简单地把 current 置空。
    """
    scope = TraceScope(
        # 128 位随机 ID，冲突概率可以忽略；不做自增，避免多进程冲突。
        trace_id=uuid.uuid4().hex,
        session_id=session_id,
        turn_id=turn_id,
        sink=sink or get_sink(),
    )
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        # 无论正常结束还是异常退出，都要恢复上下文，避免污染后续请求。
        _current_scope.reset(token)


class _NullSpan:
    """没有 trace 时返回的空对象。

    业务代码里写 span.attributes["x"] = 1 不会报错，因此埋点代码可以
    无条件留在业务里，单测和脚本不需要额外判断。
    """

    def __init__(self) -> None:
        self.attributes: dict = {}


@contextmanager
def span(name: str, kind: str = "node", attributes: dict | None = None):
    """记录一个环节。嵌套时自动挂到当前 span 下面。

    参数：
    - name：环节名，建议用 "节点名" 或 "llm.节点名" 这种可排序的格式；
    - kind：node / llm / tool，用来在报表里分组统计；
    - attributes：初始细节，进入 span 后还可以继续往里面加。
    """
    scope = _current_scope.get()

    # 没有 trace 上下文时退化成空操作。这是“埋点不侵入业务”的关键。
    if scope is None:
        yield _NullSpan()
        return

    record = SpanRecord(
        trace_id=scope.trace_id,
        # span_id 只取 uuid 前 16 位：同一条 trace 内够用，日志里也更好读。
        span_id=uuid.uuid4().hex[:16],
        # 栈顶就是当前正在执行的环节；栈空说明这是顶层 span，parent 为 None。
        parent_span_id=scope.stack[-1].span_id if scope.stack else None,
        session_id=scope.session_id,
        turn_id=scope.turn_id,
        name=name,
        kind=kind,
        started_at=_now_iso(),
        ended_at="",       # 结束时才填
        duration_ms=0,     # 结束时才填
        # 复制一份，避免调用方后续修改原字典时，悄悄改掉已经开始的 span 数据。
        attributes=dict(attributes or {}),
    )

    # perf_counter 是单调时钟，专门用来测耗时；不要用 datetime 相减。
    started = time.perf_counter()

    # 先入栈，再执行 with 块：块内再开 span 时就能找到这个父节点。
    scope.stack.append(record)
    try:
        # 把 record 交给 with 块，调用方可以继续写 record.attributes。
        yield record
    except BaseException as exc:
        # 业务异常要如实标记，但必须继续上抛，不能吞掉。
        # 用 BaseException 是为了连 KeyboardInterrupt / SystemExit 也能记录状态。
        record.status = "error"
        record.error = type(exc).__name__  # 只存类型，不存敏感消息
        raise
    finally:
        # 无论成功、失败还是异常，都要做收尾，保证 span 一定出栈、一定落盘。
        record.duration_ms = round((time.perf_counter() - started) * 1000)
        record.ended_at = _now_iso()

        # 先出栈再 emit：即使 emit 内部出问题，栈也已经恢复干净，
        # 后面的兄弟节点不会被错挂到这个 span 下面。
        scope.stack.pop()
        scope.emit(record)