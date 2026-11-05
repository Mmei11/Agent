"""Day 5：定义问诊 Agent 的状态。案例均为虚构，不涉及真实诊疗。"""

import operator
from typing import Annotated

from pydantic import BaseModel, Field

# trace 只保留最近这么多条，避免 checkpoint 随对话轮次无限膨胀。
# 评测集最多 4 轮、约 24 条，80 条留有充足余量。
MAX_TRACE_ENTRIES = 80


def _append_trace(left: list, right: list) -> list:
    """追加但截断：只保留最近的记录，旧记录由外部观测库负责保存。"""
    return (list(left) + list(right))[-MAX_TRACE_ENTRIES:]


class ConsultationState(BaseModel):
    """LangGraph 在节点之间传递的状态。

    用 Annotated[list, operator.add] 声明的字段，多个节点返回的值会追加，
    不会互相覆盖。其余字段由节点直接写入最新值。
    """

    # --- 会话标识 ---
    session_id: str = ""
    turn_id: int = 0

    # --- 用户输入 ---
    latest_user_message: str = ""
    # --- 输入层结论（Phase 5）---
    # input_level: safe / vague / risk / jailbreak；intake 写入，risk_check 读取。
    input_level: str = ""
    input_labels: list = []
    # 对话按轮追加，供刷新页面时从 SQLite 检查点恢复。
    conversation: Annotated[list, operator.add] = []

    # --- 信息收集 ---
    # collected_facts：{"症状": "头痛", "部位": "前额", ...}
    # 你来决定用 dict 还是 list，以及是否需要追加合并
    collected_facts: dict = {}

    # fact_evidence：{"主要症状": ["一阵一阵胀痛"], ...}
    # 每个值的原文出处，和 collected_facts 同步跨轮合并；关键词模式为空。
    fact_evidence: dict = {}

    # missing_fields：还没收集到的字段名，每轮重新计算（覆盖）
    missing_fields: list = []


    # asked_questions：已问过的问题，只增不减
    asked_questions: Annotated[list, operator.add] = []

    # suggested_replies：当前追问的可点选回答，每轮覆盖；收束时清空。
    suggested_replies: list = []

    # --- 追踪与控制 ---
    followup_count: int = 0
    # 连续含糊轮数：命中 vague 时 +1，正常轮重置为 0；供策略层决定何时收束。
    vague_turns: int = 0
    risk_flags: list = []  # 每轮风险筛查覆盖上轮结果
    tool_results: dict = {}

    # next_action：决策节点选出的动作
    next_action: str = ""

    # --- 输出 ---
    # final_result：结构化结果字典，给测试和前端解析用
    final_result: dict = {}
    # rendered_text：从结构化结果渲染的文案，给用户直接看
    rendered_text: str = ""

    # trace_id：本轮请求在外部观测库里的 id，用于从会话跳转到完整轨迹。
    trace_id: str = ""

    # trace：每一步的摘要，只增不减但会被截断到 MAX_TRACE_ENTRIES。
    # 完整细节（模型输入、工具返回）由 observability 的 sink 保存，不再塞进 checkpoint。
    trace: Annotated[list, _append_trace] = []
