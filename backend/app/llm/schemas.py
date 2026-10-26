"""Phase 1：结构化抽取的领域 Schema。

字段名用英文，是为了代码里的标识符稳定；对外的中文键名由 FIELD_MAP
在边界处转换，评测脚本和 state 不用改。
"""

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