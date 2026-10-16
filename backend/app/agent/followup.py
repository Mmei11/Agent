"""Day 7：动态追问生成。基于缺失字段、已问问题和字段优先级，选一个最有价值的问题。案例均为虚构。"""

from backend.app.agent.state import ConsultationState

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


def select_next_field(missing_fields: list, asked_questions: list) -> str | None:
    """从缺失字段中选一个还没问过的，按优先级排序。

    返回字段名；没有可问的字段时返回 None。
    """
    asked_text = " ".join(asked_questions)

    # 筛掉已经问过的字段：检查问题模板是否已出现在 asked_questions 里
    not_asked = [f for f in missing_fields if QUESTION_TEMPLATES.get(f, "") not in asked_text]

    if not not_asked:
        return None

    # 按优先级排序，取第一个
    not_asked.sort(key=lambda f: FIELD_PRIORITY.get(f, 99))
    return not_asked[0]


def generate_question(field: str | None) -> str:
    """根据字段名生成一个简短问题。没有字段时返回收束提示。"""
    if field is None:
        return "已收集足够信息，将为您整理结果。"
    return QUESTION_TEMPLATES.get(field, f"请补充{field}相关的信息。")


def followup(state: ConsultationState) -> dict:
    """追问节点：选字段、生成问题、更新已问列表和追问计数。

    ASK_FOLLOWUP 动作到达此节点。返回的问题会显示给用户。
    """
    field = select_next_field(state.missing_fields, state.asked_questions)
    question = generate_question(field)

    return {
        "followup_count": state.followup_count + 1,
        "asked_questions": [question],
        "trace": [
            {
                "node": "followup",
                "selected_field": field,
                "question": question,
                "count": state.followup_count + 1,
            }
        ],
    }
