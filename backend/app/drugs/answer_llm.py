"""L1：模型阅读说明书并生成简短回应，说明书原文仍由确定性模板拼接。

模型只负责“针对问题写 1-3 句 + 选择要展示的段落”，不能新增药品、
剂量、诊断或治疗建议；调用失败或回答越界时退回固定模板。
"""

import json

from backend.app.drugs.lookup import DrugLabel
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import DrugAnswer
from backend.app.observability import span

PROMPT_NAME = "drug.answer"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是"药品说明书阅读助手"，不是医生。
用户问了一个关于某个 OTC 药品的问题。你只能使用提供的说明书字段回答。

要求：
1. 用 1 到 3 句话回应用户的问题，只复述说明书内容，不新增药品、剂量、诊断或治疗建议。
2. 不判断用户是否适合使用这个药；涉及个人适用性时，说明需要医生或药师判断。
3. 不给用法用量，不推荐其他药品，不列举疾病或做鉴别诊断。
4. sections 选择回答这个问题需要展示的说明书部分，选 1 到 3 个：
   indications（适应症）、contraindications（禁忌）、precautions（注意事项）、
   adverse_reactions（不良反应）、interactions（药物相互作用）。
5. 只输出 JSON：{"intro": "...", "sections": ["..."]}，不要 Markdown 或额外字段。"""


def build_context(question: str, label: DrugLabel) -> dict:
    return {
        "question": question,
        "drug": {
            "name": label.name,
            "indications": label.indications,
            "contraindications": label.contraindications,
            "precautions": label.precautions,
            "adverse_reactions": label.adverse_reactions,
            "interactions": label.interactions,
        },
    }


def generate(question: str, label: DrugLabel) -> tuple[DrugAnswer, LLMResult]:
    context = build_context(question, label)
    with span(
        "drug.answer",
        kind="internal",
        attributes={"drug.id": label.id},
    ) as record:
        answer, usage = chat_structured(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            DrugAnswer,
            max_tokens=300,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
        record.attributes["drug.intro_chars"] = len(answer.intro)
        record.attributes["drug.sections"] = list(answer.sections)
    return answer, usage
