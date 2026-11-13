"""Day 9：受限工具实现。工具只负责按约束执行，不做决策。案例均为虚构。"""

import os

from backend.app.tools.catalog import DEFAULT_CATALOG
from backend.app.tools.contract import ToolResult
from backend.app.tools.knowledge_tools import KNOWLEDGE_TOOL_SPECS

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

# Phase 4：契约化注册表统一走 catalog；上面那份留给旧测试。


def call_tool(name: str, params: dict) -> ToolResult:
    """按目录调用工具，内部工具和外部 MCP 工具走同一条路。

    未注册的名字也返回结构化失败，不抛异常。
    """
    if os.getenv("KNOWLEDGE_BACKEND", "pgvector") == "stub" and name in KNOWLEDGE_TOOL_SPECS:
        # CI / 门禁自检没有向量库：知识类工具用确定性替身，外部工具照常走目录。
        return stub_call_tool(name, params)
    return DEFAULT_CATALOG.call(name, params)


def stub_call_tool(name: str, params: dict) -> ToolResult:
    """没有向量库时的确定性替身：复用旧的关键词映射保住流程。

    只用于跑流程（CI、门禁自检），不代表真实检索质量——它不会返回 hits，
    也没有出处。真实检索请用 call_tool。
    """
    query = str(params.get("query", ""))
    for symptom, department in SYMPTOM_DEPARTMENT_MAP.items():
        if symptom in query:
            return ToolResult(
                ok=True,
                name=name,
                data={"department": department, "reason": None, "hits": []},
            )
    return ToolResult(
        ok=True,
        name=name,
        data={"department": None, "reason": "no_match", "hits": []},
    )
