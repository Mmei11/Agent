"""Phase 1：结构化抽取的领域 Schema。

字段名用英文，是为了代码里的标识符稳定；对外的中文键名由 FIELD_MAP
在边界处转换，评测脚本和 state 不用改。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Fact(BaseModel):
    """一个字段值，连同它的原文出处和模型自评置信度。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(description="字段内容，取原文中最短的完整片段，不做归一化")
    evidence: str = Field(description="原文中逐字出现的证据片段")
    confidence: float = Field(ge=0, le=1, description="模型自评置信度，0 到 1")


class IntakeFacts(BaseModel):
    """一轮用户消息里抽到的全部字段。空列表表示没抽到，不写空字符串。"""

    model_config = ConfigDict(extra="forbid")

    chief_complaint: list[Fact] = Field(default_factory=list, description="主要症状")
    duration: list[Fact] = Field(default_factory=list, description="持续时间，含起病时间")
    location: list[Fact] = Field(default_factory=list, description="部位")
    severity: list[Fact] = Field(default_factory=list, description="程度，只描述主要症状")
    associated: list[Fact] = Field(
        default_factory=list, description="伴随表现；被否定的症状不计入"
    )
    background: list[Fact] = Field(default_factory=list, description="已知背景")
    aggravating: list[Fact] = Field(default_factory=list, description="加重缓解因素")
    pattern: list[Fact] = Field(default_factory=list, description="时间模式，发作规律与频次")
    measurement: list[Fact] = Field(default_factory=list, description="测量值，如体温血压")


FIELD_MAP = {
    "chief_complaint": "主要症状",
    "duration": "持续时间",
    "location": "部位",
    "severity": "程度",
    "associated": "伴随表现",
    "background": "已知背景",
    "aggravating": "加重缓解因素",
    "pattern": "时间模式",
    "measurement": "测量值",
}


class DecisionChoice(BaseModel):
    """一次动作决策：做什么、为什么、有多大把握。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["ASK_FOLLOWUP", "CALL_TOOL", "FINALIZE"] = Field(
        description="下一步动作，只能是三个之一"
    )
    reason: str = Field(
        min_length=1, max_length=200, description="选择这个动作的依据，引用字段或原话片段"
    )
    confidence: float = Field(ge=0, le=1, description="自评置信度，依据不足时如实给低分")


class ReplySuggestions(BaseModel):
    """追问的快捷回答候选：用户可以直接点选，也可以自由输入。"""

    model_config = ConfigDict(extra="forbid")

    replies: list[str] = Field(
        min_length=1,
        max_length=5,
        description="2 到 10 字的候选回答，不能重复已收集的信息",
    )


class FollowupAnswer(BaseModel):
    """问诊结果生成后，对用户继续提问的自然语言回答。"""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        min_length=1,
        max_length=400,
        description="根据已有事实与结果回答，不新增诊断或用药建议",
    )
    grounded: bool = Field(
        description="回答是否使用了 final_result 中的就诊方向/风险提示/知识库来源；纯通用常识为 false"
    )


class DrugAnswer(BaseModel):
    """L1 药品说明书回答：模型只写简短回应并选择要展示的段落。"""

    model_config = ConfigDict(extra="forbid")

    intro: str = Field(
        min_length=1,
        max_length=200,
        description="1 到 3 句回应，只复述说明书内容，不判断个人适用性",
    )
    sections: list[
        Literal[
            "indications",
            "contraindications",
            "precautions",
            "adverse_reactions",
            "interactions",
        ]
    ] = Field(
        min_length=1,
        max_length=3,
        description="回答这个问题需要展示的说明书段落",
    )


class RoutedReply(BaseModel):
    """路由结果：普通问题直接带回答，医疗问题只返回 intent。"""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["medical", "general"]
    answer: str = Field(
        default="",
        max_length=300,
        description="intent=general 时的简短自然回答；医疗问题留空",
    )


class SessionSummary(BaseModel):
    """长会话滚动摘要：只保留继续对话所需的上下文。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=300,
        description="2 到 5 句、最多 300 字的会话摘要",
    )