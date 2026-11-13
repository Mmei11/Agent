"""L1：模型阅读说明书并生成简短回应，说明书原文仍由确定性模板拼接。

模型只负责“针对问题写 1-3 句 + 选择要展示的段落”，不能新增药品、
剂量、诊断或治疗建议；调用失败或回答越界时退回固定模板。
"""

import json

from backend.app.drugs.lookup import DrugLabel
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import DrugAnswer
from backend.app.observability import span
from backend.app.prompts import load_prompt

PROMPT_NAME = "drug.answer"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)


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
