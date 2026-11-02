"""Phase 5 L3：输出层护栏。

生成时约束和事后检查不是二选一：模板负责“尽量不生成越界内容”，
这一层负责“万一生成了，也必须拦下来”。guardrails 只做校验器编排，
不发第二次模型请求（不用 reask），因此离线可测、延迟可控。
"""

from dataclasses import dataclass

from guardrails import Guard

from backend.app.safety.validators import (
    SAFE_FALLBACK,
    CitationRequired,
    DisclaimerRequired,
    NoDiagnosis,
    NoTreatmentAdvice,
)

_guard = Guard().use(
    NoDiagnosis(on_fail="fix"),
    NoTreatmentAdvice(on_fail="fix"),
    CitationRequired(on_fail="fix"),
    DisclaimerRequired(on_fail="fix"),
)
# 不向 guardrails 的云端发遥测；医疗项目里默认关闭。
_guard.configure(allow_metrics_collection=False)


@dataclass(frozen=True)
class OutputVerdict:
    passed: bool
    text: str
    violations: tuple[str, ...]


def check(text: str, *, uses_knowledge: bool = False) -> OutputVerdict:
    """检查最终文案。任一校验器失败，整段替换成固定安全文案。"""
    outcome = _guard.validate(text, metadata={"uses_knowledge": uses_knowledge})
    violations = tuple(
        summary.failure_reason
        for summary in outcome.validation_summaries
        if summary.validator_status == "fail"
    )

    if violations:
        return OutputVerdict(passed=False, text=SAFE_FALLBACK, violations=violations)

    # 校验通过时返回原始文案，而不是 guardrails 处理过的副本。
    return OutputVerdict(passed=True, text=text, violations=())
