"""Phase D：技能 manifest 的 Pydantic 契约。

manifest 只描述“这类病人怎么问”，不包含代码。加载失败时注册表会跳过该技能，
并回退到 default。
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    description: str
    # 数字越小越优先；多个技能同时命中时用它决胜。
    priority: int = 100
    when_to_use: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(min_length=1)
    optional_fields: list[str] = Field(default_factory=list)
    followup_priority: list[str] = Field(min_length=1)
    question_templates: dict[str, str]
    reply_options: dict[str, list[str]] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    output_template: str = ""
    version: str = "v1"

    @model_validator(mode="after")
    def _check_field_coverage(self):
        required = set(self.required_fields)
        missing_priority = required - set(self.followup_priority)
        if missing_priority:
            raise ValueError(
                f"required_fields 未出现在 followup_priority：{sorted(missing_priority)}"
            )

        missing_templates = required - set(self.question_templates)
        if missing_templates:
            raise ValueError(
                f"required_fields 缺少追问模板：{sorted(missing_templates)}"
            )

        if not self.description.strip():
            raise ValueError("description 不能为空；路由摘要需要它")
        return self
