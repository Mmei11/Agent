"""Day 5：定义问诊 Agent 的状态。案例均为虚构，不涉及真实诊疗。"""

import operator
from typing import Annotated

from pydantic import BaseModel, Field


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

    # --- 信息收集 ---
    # collected_facts：{"症状": "头痛", "部位": "前额", ...}
    # 你来决定用 dict 还是 list，以及是否需要追加合并
    collected_facts: dict = {}

    # missing_fields：还没收集到的字段名，每轮重新计算（覆盖）
    missing_fields: list = []


    # asked_questions：已问过的问题，只增不减
    asked_questions: Annotated[list, operator.add] = []

    # --- 追踪与控制 ---
    followup_count: int = 0
    risk_flags: Annotated[list, operator.add] = []  # 可追加
    tool_results: dict = {}

    # next_action：决策节点选出的动作
    next_action: str = ""

    # --- 输出 ---
    # final_result：结构化结果字典，给测试和前端解析用
    final_result: dict = {}
    # rendered_text：从结构化结果渲染的文案，给用户直接看
    rendered_text: str = ""

    # trace：每一步的执行记录，只增不减
    trace: Annotated[list, operator.add] = []