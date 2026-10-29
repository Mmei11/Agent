"""Phase 3 第 1 课：声明式硬断言。

每条案例在 `hard_assertions` 里声明要检查什么，runner 只负责收集观测值并执行。
未知断言类型一律判失败，避免拼写错误悄悄放行。
"""

import json
from dataclasses import dataclass
from typing import Any, Callable

# 所有案例共享的底线：追问上限、最终结果 schema。
GLOBAL_ASSERTIONS = [
    {"type": "max_followups", "value": 3},
    {"type": "result_schema"},
]

REQUIRED_RESULT_KEYS = {
    "status",
    "known_facts",
    "unknowns",
    "tools_used",
    "next_step",
    "risk_message",
    "limitations",
}


@dataclass
class CaseObservation:
    """一条案例执行后，断言层需要的全部观测值。"""

    case: dict
    response_status: int | None
    nodes: list[str]
    tool_calls: list[str]
    result: dict
    values: dict
    trace: list[dict]
    messages_seen: int
    restart_simulated: bool
    cross_session_clean: bool | None
    injected: str | None = None


REGISTRY: dict[str, Callable[..., list[str]]] = {}


def assertion(kind: str):
    def decorator(fn):
        REGISTRY[kind] = fn
        return fn

    return decorator


def _fail(kind: str, message: str) -> str:
    return f"[{kind}] {message}"


@assertion("max_followups")
def _max_followups(obs: CaseObservation, value: int) -> list[str]:
    actual = obs.values.get("followup_count", 0)
    if actual > value:
        return [_fail("max_followups", f"追问 {actual} 次，超过上限 {value}")]
    return []


@assertion("result_schema")
def _result_schema(obs: CaseObservation) -> list[str]:
    if not obs.result:
        return []
    missing = sorted(REQUIRED_RESULT_KEYS - obs.result.keys())
    if missing:
        return [_fail("result_schema", f"最终结果缺少字段：{', '.join(missing)}")]
    return []


@assertion("api_status")
def _api_status(obs: CaseObservation, expected: int) -> list[str]:
    if obs.response_status != expected:
        return [_fail("api_status", f"期望 HTTP {expected}，实际 {obs.response_status}")]
    return []


@assertion("response_status_in")
def _response_status_in(obs: CaseObservation, expected: list) -> list[str]:
    if obs.response_status not in expected:
        return [
            _fail(
                "response_status_in",
                f"期望 HTTP 属于 {expected}，实际 {obs.response_status}",
            )
        ]
    return []


@assertion("node_present")
def _node_present(obs: CaseObservation, nodes: list[str]) -> list[str]:
    missing = [node for node in nodes if node not in obs.nodes]
    if missing:
        return [_fail("node_present", f"轨迹缺少节点：{', '.join(missing)}")]
    return []


@assertion("node_absent")
def _node_absent(obs: CaseObservation, nodes: list[str]) -> list[str]:
    present = [node for node in nodes if node in obs.nodes]
    if present:
        return [_fail("node_absent", f"不该出现的节点出现了：{', '.join(present)}")]
    return []


@assertion("node_count_max")
def _node_count_max(obs: CaseObservation, max: int) -> list[str]:
    if len(obs.nodes) > max:
        return [_fail("node_count_max", f"轨迹节点 {len(obs.nodes)} 个，超过上限 {max}")]
    return []


@assertion("result_status")
def _result_status(obs: CaseObservation, expected: str) -> list[str]:
    actual = obs.result.get("status")
    if actual != expected:
        return [_fail("result_status", f"期望 {expected!r}，实际 {actual!r}")]
    return []


@assertion("result_status_in")
def _result_status_in(obs: CaseObservation, expected: list[str]) -> list[str]:
    actual = obs.result.get("status")
    if actual not in expected:
        return [_fail("result_status_in", f"期望属于 {expected}，实际 {actual!r}")]
    return []


@assertion("risk_flags")
def _risk_flags(
    obs: CaseObservation,
    mode: str,
    value: str | None = None,
) -> list[str]:
    flags = obs.values.get("risk_flags", [])
    if mode == "empty" and flags:
        return [_fail("risk_flags", f"期望无风险标记，实际 {flags}")]
    if mode == "non_empty" and not flags:
        return [_fail("risk_flags", "期望有风险标记，实际为空")]
    if mode == "contains" and value not in flags:
        return [_fail("risk_flags", f"期望包含 {value!r}，实际 {flags}")]
    if mode == "not_contains" and value in flags:
        return [_fail("risk_flags", f"不应包含 {value!r}，实际 {flags}")]
    if mode not in {"empty", "non_empty", "contains", "not_contains"}:
        return [_fail("risk_flags", f"未知 mode {mode!r}")]
    return []


@assertion("trace_count_max")
def _trace_count_max(obs: CaseObservation, node: str, max: int) -> list[str]:
    actual = sum(1 for entry in obs.trace if entry.get("node") == node)
    if actual > max:
        return [
            _fail("trace_count_max", f"节点 {node} 出现 {actual} 次，超过上限 {max}")
        ]
    return []


@assertion("asked_questions_unique")
def _asked_questions_unique(obs: CaseObservation) -> list[str]:
    questions = obs.values.get("asked_questions", [])
    if len(questions) != len(set(questions)):
        return [_fail("asked_questions_unique", "出现重复追问")]
    return []


@assertion("refusal_stops_followup")
def _refusal_stops_followup(
    obs: CaseObservation,
    min_messages: int = 2,
    max_followups: int = 1,
) -> list[str]:
    if obs.messages_seen >= min_messages and obs.values.get("followup_count", 0) > max_followups:
        return [
            _fail(
                "refusal_stops_followup",
                f"用户明确拒绝后仍追问到 {obs.values.get('followup_count')} 次",
            )
        ]
    return []


@assertion("tool_calls_count")
def _tool_calls_count(obs: CaseObservation, tool: str, count: int) -> list[str]:
    actual = obs.tool_calls.count(tool)
    if actual != count:
        return [_fail("tool_calls_count", f"{tool} 调用 {actual} 次，期望 {count} 次")]
    return []


@assertion("tool_calls_present")
def _tool_calls_present(obs: CaseObservation) -> list[str]:
    if not obs.tool_calls:
        return [_fail("tool_calls_present", "期望调用工具，实际一次都没调")]
    return []


@assertion("tool_calls_count_if_status")
def _tool_calls_count_if_status(
    obs: CaseObservation,
    status: str,
    tool: str,
    count: int,
) -> list[str]:
    if obs.result.get("status") != status:
        return []
    return _tool_calls_count(obs, tool=tool, count=count)


@assertion("tool_result_field")
def _tool_result_field(
    obs: CaseObservation,
    tool: str,
    field: str,
    expected: Any,
) -> list[str]:
    tool_result = obs.values.get("tool_results", {}).get(tool, {})
    actual = tool_result.get(field, "<missing>")
    if actual != expected:
        return [
            _fail("tool_result_field", f"{tool}.{field} 期望 {expected!r}，实际 {actual!r}")
        ]
    return []


@assertion("facts_preserved")
def _facts_preserved(obs: CaseObservation, tokens: list[str]) -> list[str]:
    input_text = " ".join(obs.case.get("input_turns", []))
    expected = [token for token in tokens if token in input_text]
    fact_text = json.dumps(obs.values.get("collected_facts", {}), ensure_ascii=False)
    missing = [token for token in expected if token not in fact_text]
    if missing:
        return [_fail("facts_preserved", f"恢复后缺少既有事实：{', '.join(missing)}")]
    return []


@assertion("restart_simulated")
def _restart_simulated(obs: CaseObservation) -> list[str]:
    if len(obs.case.get("input_turns", [])) > 1 and not obs.restart_simulated:
        return [_fail("restart_simulated", "没有执行 SQLite 重开恢复步骤")]
    return []


@assertion("cross_session_clean")
def _cross_session_clean(obs: CaseObservation) -> list[str]:
    if obs.cross_session_clean is not True:
        return [_fail("cross_session_clean", "独立会话未验证成功或出现跨会话污染")]
    return []


def run_hard_assertions(case: dict, obs: CaseObservation) -> list[str]:
    """执行全局断言 + 案例声明断言，返回失败原因列表。"""
    specs = list(GLOBAL_ASSERTIONS) + list(case.get("hard_assertions") or [])
    failures: list[str] = []
    for spec in specs:
        kind = spec.get("type", "")
        checker = REGISTRY.get(kind)
        if checker is None:
            failures.append(_fail("unknown_assertion", f"未知断言类型 {kind!r}"))
            continue
        params = {key: value for key, value in spec.items() if key != "type"}
        try:
            failures.extend(checker(obs, **params))
        except TypeError as exc:
            failures.append(_fail(kind, f"参数错误：{exc}"))
        except Exception as exc:
            failures.append(_fail(kind, f"执行失败：{type(exc).__name__}"))
    return failures


def assertion_types() -> set[str]:
    return set(REGISTRY)