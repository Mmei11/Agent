"""Day 6：结构化信息提取。从用户输入中提取必要字段，区分已知与缺失。案例均为虚构。"""

from backend.app.agent.state import ConsultationState

REQUIRED_FIELDS = ["主要症状", "持续时间", "部位", "程度", "伴随表现", "已知背景"]

# 关键词映射：每个字段对应一组触发词
FIELD_KEYWORDS = {
    "主要症状": ["头部不适", "头痛", "头晕", "恶心", "发热", "呼吸困难", "咳嗽", "腹痛"],
    "持续时间": ["两天", "三天", "一周", "一天", "几天", "半个月", "一个月"],
    "部位": ["前额", "头部", "胸部", "腹部", "背部", "左侧", "右侧"],
    "程度": ["轻", "明显", "严重", "剧烈", "轻微"],
    "伴随表现": ["暂无", "恶心", "发热", "胃部不适", "咳嗽", "乏力"],
    "已知背景": ["高血压", "糖尿病", "过敏", "无病史", "既往"],
}


def extract_facts(message: str) -> dict:
    """从用户消息中提取已知字段。未匹配到的字段不写入。

    用关键词匹配：消息里包含某字段的关键词，就记入该字段。
    每个字段的值是列表，保留匹配到的原词，方便多轮追加。
    """
    facts = {}
    for field, keywords in FIELD_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in message]
        if matched:
            facts[field] = matched
    return facts


def compute_missing(collected: dict) -> list:
    """计算还缺失的字段：必要字段中不在 collected_facts 里的。"""
    return [f for f in REQUIRED_FIELDS if f not in collected]


def intake(state: ConsultationState) -> dict:
    """入口节点：提取字段，合并多轮信息，更新缺失列表。

    新值追加到已有字段列表；collected_facts 直接覆盖（节点自己控制合并逻辑）。
    missing_fields 覆盖（每轮重新计算）。
    """
    message = state.latest_user_message
    new_facts = extract_facts(message)

    collected = {k: list(v) for k, v in state.collected_facts.items()}
    for field, values in new_facts.items():
        if field in collected:
            for v in values:
                if v not in collected[field]:
                    collected[field].append(v)
        else:
            collected[field] = list(values)

    missing = compute_missing(collected)

    return {
        "turn_id": state.turn_id + 1,
        "conversation": [{"role": "user", "text": message}],
        "collected_facts": collected,
        "missing_fields": missing,
        "final_result": {},
        "rendered_text": "",
        "trace": [{"node": "intake", "extracted": new_facts, "missing": missing}],
    }
