"""Day 7：动态追问生成。基于缺失字段、已问问题和字段优先级，选一个最有价值的问题。案例均为虚构。"""

import os

from backend.app.agent.state import ConsultationState
from backend.app.observability import current_trace

# 字段优先级：数字越小优先级越高
FIELD_PRIORITY = {
    "主要症状": 1,
    "持续时间": 2,
    "部位": 3,
    "伴随表现": 4,
    "程度": 5,
    "已知背景": 6,
}

# 字段对应的问题模板
QUESTION_TEMPLATES = {
    "主要症状": "请问主要的不适是什么？",
    "持续时间": "请问这种情况持续多久了？",
    "部位": "请问具体在哪个部位？",
    "伴随表现": "请问还有其他不适吗？",
    "程度": "请问不适的程度如何，是轻微、明显还是严重？",
    "已知背景": "请问有没有既往病史或正在用药？",
}


# 字段对应的追问快捷选项；用户可点选，仍允许自由输入。
# 这是没有模型时的兜底，模型模式下会结合已收集信息重新生成。
REPLY_OPTIONS = {
    "主要症状": ["头部不适", "腹痛", "发热"],
    "持续时间": ["一天内", "两三天", "一周以上"],
    "部位": ["头部", "腹部", "胸部"],
    "伴随表现": ["没有其他不适", "有恶心", "有乏力"],
    "程度": ["轻微", "明显", "严重"],
    "已知背景": ["无病史", "有慢性病", "正在用药"],
}

MAX_REPLIES = 3
MAX_REPLY_CHARS = 12


def _known_fragments(state: ConsultationState) -> list[str]:
    return [
        str(value).strip()
        for values in state.collected_facts.values()
        for value in values
        if str(value).strip()
    ]


def clean_replies(candidates: list, state: ConsultationState) -> list[str]:
    """去噪、去重，并丢掉用户已经说过的信息。

    例如主要症状已经写了"头部不适"，部位选项里的"头部"就没有意义。
    """
    known = _known_fragments(state)
    cleaned = []
    for candidate in candidates:
        reply = str(candidate).strip().strip("。！？!?，,、；;：: ")
        if not reply or len(reply) > MAX_REPLY_CHARS or "\n" in reply:
            continue
        if any(reply in value for value in known):
            continue
        if reply in cleaned:
            continue
        cleaned.append(reply)
        if len(cleaned) >= MAX_REPLIES:
            break
    return cleaned


def suggest_replies(
    state: ConsultationState,
    field: str | None,
    question: str,
    *,
    options: dict[str, list[str]] | None = None,
) -> tuple[list[str], dict]:
    """优先让模型结合已收集信息生成候选；失败或未启用时回退静态选项。

    返回（候选回答, 来源与用量）；来源写进 trace，便于比较模型与兜底的比例。
    """
    fallback_options = REPLY_OPTIONS if options is None else options
    fallback = clean_replies(fallback_options.get(field) or [], state)
    if field is None or os.getenv("USE_MODEL") != "1":
        return fallback, {"source": "static"}

    from backend.app.agent.followup_llm import generate_with_meta
    from backend.app.llm.client import LLMError

    try:
        suggestions, usage = generate_with_meta(state, field, question)
    except LLMError:
        # 追问生成本身不能打断问诊；静态选项至少保证用户还能点。
        return fallback, {"source": "fallback", "reason": "model_error"}

    replies = clean_replies(suggestions.replies, state)
    if not replies:
        return fallback, {"source": "fallback", "reason": "all_filtered"}

    return replies, {
        "source": "model",
        "usage": {
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_ms": usage.latency_ms,
            "cost_estimate": usage.cost_estimate,
        },
    }


def select_next_field(
    missing_fields: list,
    asked_questions: list,
    *,
    priority: dict[str, int] | None = None,
    templates: dict[str, str] | None = None,
) -> str | None:
    """从缺失字段中选一个还没问过的，按优先级排序。

    技能层传入自己的 priority / templates；不传时保持原有默认行为。
    """
    if priority is None:
        active_priority = FIELD_PRIORITY
    elif isinstance(priority, dict):
        active_priority = priority
    else:
        # manifest 用列表表达顺序；转成“字段 → 序号”供排序。
        active_priority = {field: index for index, field in enumerate(priority)}
    active_templates = QUESTION_TEMPLATES if templates is None else templates
    asked_text = " ".join(asked_questions)

    # 筛掉已经问过的字段：检查问题模板是否已出现在 asked_questions 里
    not_asked = [
        f for f in missing_fields if active_templates.get(f, "") not in asked_text
    ]

    if not not_asked:
        return None

    # 按优先级排序，取第一个
    not_asked.sort(key=lambda f: active_priority.get(f, 99))
    return not_asked[0]


def generate_question(
    field: str | None, *, templates: dict[str, str] | None = None
) -> str:
    """根据字段名生成一个简短问题。没有字段时返回收束提示。"""
    if field is None:
        return "已收集足够信息，将为您整理结果。"
    active = QUESTION_TEMPLATES if templates is None else templates
    return active.get(field, f"请补充{field}相关的信息。")


def followup(state: ConsultationState) -> dict:
    """追问节点：按当前技能选字段、生成问题、更新已问列表和追问计数。

    ASK_FOLLOWUP 动作到达此节点。返回的问题会显示给用户。
    """
    from backend.app.skills.context import skill_for

    skill = skill_for(state)
    manifest = skill.manifest
    field = select_next_field(
        state.missing_fields,
        state.asked_questions,
        priority=manifest.followup_priority,
        templates=manifest.question_templates,
    )
    question = generate_question(field, templates=manifest.question_templates)
    replies, reply_meta = suggest_replies(
        state, field, question, options=manifest.reply_options
    )
    scope = current_trace()

    return {
        "followup_count": state.followup_count + 1,
        "asked_questions": [question],
        "conversation": [{"role": "agent", "text": question}],
        "suggested_replies": replies,
        "trace": [
            {
                "node": "followup",
                "skill": manifest.name,
                "selected_field": field,
                "count": state.followup_count + 1,
                "trace_id": scope.trace_id if scope else "",
                "reply_meta": reply_meta,
                # 问句本身已在 asked_questions 和 conversation 里，不重复存。
            }
        ],
    }
