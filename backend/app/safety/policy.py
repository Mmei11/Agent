"""Phase 5 L2：决策层策略。

规则负责“是不是红旗”，模型负责“下一步做什么”，策略层负责
“谁的话优先”和“什么时候升级”。三者分开，才能单独测试、单独评审。
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from backend.app.agent.state import ConsultationState
from backend.app.config import load_env_file
from backend.app.safety.input_rail import InputVerdict

DEFAULT_POLICY_PATH = Path(__file__).parent / "policies" / "safety-policy.json"

DEFAULT_POLICY = {
    "vague_close_after": 2,
    "risk_action": "FINALIZE",
    "jailbreak_action": "FINALIZE",
}


def load_policy(path: str | Path | None = None) -> dict:
    """读取策略配置；文件缺失或格式错误时退回默认值，保证主流程可跑。"""
    load_env_file()
    target = Path(path or os.getenv("SAFETY_POLICY_PATH") or DEFAULT_POLICY_PATH)
    config = dict(DEFAULT_POLICY)
    try:
        config.update(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return config


@dataclass(frozen=True)
class PolicyDecision:
    """策略层的结论。action=None 表示不干预，沿用模型/规则选的动作。"""

    action: str | None
    level: str
    rule: str | None
    reason: str
    model_action: str = ""
    audit: bool = False

    @property
    def overrides_model(self) -> bool:
        return self.action is not None and self.action != self.model_action


def decide(
    verdict: InputVerdict | None,
    state: ConsultationState,
    model_action: str,
    policy: dict | None = None,
) -> PolicyDecision:
    """按 risk > jailbreak > 连续含糊 > 正常 的顺序给出策略结论。"""
    config = policy or load_policy()

    if verdict is not None:
        level = verdict.level
        is_jailbreak = verdict.is_jailbreak
    else:
        labels = tuple(state.risk_flags)
        level = (
            "risk"
            if any(label in {"R001", "R002"} for label in labels)
            else "vague"
            if any(label in {"V001", "V002"} for label in labels)
            else "safe"
        )
        is_jailbreak = any(label.startswith("I") for label in labels)

    if level == "risk":
        return PolicyDecision(
            action=config["risk_action"],
            level="risk",
            rule="P001",
            reason="红旗优先于模型动作",
            model_action=model_action,
            audit=True,
        )

    if is_jailbreak:
        return PolicyDecision(
            action=config["jailbreak_action"],
            level="jailbreak",
            rule="P002",
            reason="输入试图改写系统规则",
            model_action=model_action,
            audit=True,
        )

    close_after = int(config["vague_close_after"])
    if level == "vague" and state.vague_turns >= close_after:
        return PolicyDecision(
            action="FINALIZE",
            level="vague",
            rule="P003",
            reason=f"连续含糊达到 {close_after} 轮，主动收束",
            model_action=model_action,
            audit=True,
        )

    return PolicyDecision(
        action=None,
        level=level,
        rule=None,
        reason="策略层不介入",
        model_action=model_action,
        audit=False,
    )
