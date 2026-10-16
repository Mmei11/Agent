"""Day 9：受限工具实现。工具只负责按约束执行，不做决策。案例均为虚构。"""

# 症状关键词 → 科室方向的受限映射
SYMPTOM_DEPARTMENT_MAP = {
    "头部不适": "神经内科",
    "头痛": "神经内科",
    "头晕": "神经内科",
    "腹痛": "消化内科",
    "咳嗽": "呼吸内科",
    "恶心": "消化内科",
}


def department_suggest(collected_facts: dict) -> dict:
    """从已收集的症状信息映射科室方向。

    参数：collected_facts（intake 节点提取的字典）
    返回：{"department": 科室名} 或 {"department": None, "reason": "暂无法判断"}
    """
    symptoms = collected_facts.get("主要症状", [])
    for symptom in symptoms:
        if symptom in SYMPTOM_DEPARTMENT_MAP:
            return {"department": SYMPTOM_DEPARTMENT_MAP[symptom]}
    return {"department": None, "reason": "暂无法判断"}


def visit_preparation(collected_facts: dict) -> dict:
    """按已收集事实生成就诊时可描述的信息清单。

    参数：collected_facts
    返回：{"checklist": [清单条目列表]}
    有已知信息就组织进去；没有就用通用清单。
    """
    checklist = []
    if "主要症状" in collected_facts:
        checklist.append(f"主要不适：{'、'.join(collected_facts['主要症状'])}")
    if "持续时间" in collected_facts:
        checklist.append(f"持续时间：{'、'.join(collected_facts['持续时间'])}")
    if "部位" in collected_facts:
        checklist.append(f"部位：{'、'.join(collected_facts['部位'])}")
    if "程度" in collected_facts:
        checklist.append(f"程度：{'、'.join(collected_facts['程度'])}")
    if "伴随表现" in collected_facts:
        checklist.append(f"伴随表现：{'、'.join(collected_facts['伴随表现'])}")

    if not checklist:
        checklist = ["请准备描述不适的开始时间、部位和程度"]

    return {"checklist": checklist}


# 工具注册表：决策器只能从这里选工具
TOOL_REGISTRY = {
    "department_suggest": department_suggest,
    "visit_preparation": visit_preparation,
}
