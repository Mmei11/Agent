"""Phase 2 第二课：把图节点包进 span 的小装饰器。

节点函数本身保持原样，埋点作为横切关注点加在注册处：
    graph.add_node("intake", traced_node("intake")(intake))
"""

import functools

from backend.app.observability.tracer import span

# 只把这些“可安全观测的摘要字段”写进 span，不把用户原话或完整工具结果带进去。
SAFE_KEYS = (
    "extractor",
    "fallback",
    "risk_level",
    "rule",
    "model_choice",
    "final_action",
    "confidence",
    "downgraded_by_confidence",
    "same",
    "called",
    "ok",
    "selected_field",
    "count",
    "result_status",
    "boundary_level",
)


def summarize_node_result(result: dict) -> dict:
    """从节点返回的 trace 摘要里挑出可以对外展示的安全字段。

    trace 拿不到摘要时返回空字典；调用方不要把整个节点结果直接推给前端。
    """
    if not isinstance(result, dict):
        return {}
    entries = result.get("trace") or []
    if not entries:
        return {}
    entry = entries[0]
    if not isinstance(entry, dict):
        return {}
    return {key: entry.get(key) for key in SAFE_KEYS if key in entry}


def traced_node(name: str):
    """给节点加一层 span：自动记录耗时、异常状态和关键决策字段。"""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state, *args, **kwargs):
            with span(name, kind="node") as record:
                result = fn(state, *args, **kwargs)
                record.attributes.update(summarize_node_result(result))
                return result

        return wrapper

    return decorator