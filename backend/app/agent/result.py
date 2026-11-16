"""Day 12：结构化结果构建与渲染。统一四种结局的结构化格式。案例均为虚构。"""

from pydantic import BaseModel, Field  # Pydantic 用于定义结构化 schema 和校验


class ConsultationResult(BaseModel):
    """最终结果的结构化 schema。渲染前用这个校验。

    四种结局都走这个 schema：
    - completed：信息收集完，调了工具，输出科室方向
    - risk_route：命中风险或含糊，固定提示
    - error：工具失败或输出校验没过
    """

    status: str  # completed / risk_route / refused / error / degraded
    known_facts: dict = {}  # 已收集的字段，{"主要症状": ["头部不适"], ...}
    unknowns: list = []  # 仍不确定的字段名，["已知背景"]
    tools_used: list = []  # 调过的工具名，["department_route"]
    next_step: str = ""  # 下一步建议，如"建议就诊方向：神经内科"
    # 引用知识时的出处；没有引用则为空。输出层用它判断是否需要来源校验。
    source_note: str = ""
    risk_message: str | None = None  # 风险提示，无则 None
    limitations: str = "本系统不提供确诊、处方或用药建议，请咨询专业人员。"  # 固定限制说明


def build_completed_result(
    collected_facts: dict,
    missing_fields: list,
    tool_results: dict,
    output_template: str = "",
) -> dict:
    """正常完成：信息收集完，调了工具，输出科室方向。

    参数：
        collected_facts：intake 提取的已知字段
        missing_fields：仍然缺失的字段名
        tool_results：工具调用的结果字典
        output_template：技能自定义的下一步模板，支持 {department}
    返回：结构化结果字典
    """
    tools_used = list(tool_results.keys())  # 取所有调过的工具名
    dept_result = tool_results.get("department_route", {})  # 取科室建议结果，默认空字典
    dept = dept_result.get("department")  # 从结果里取科室名

    if dept:  # 有匹配的科室
        template = (output_template or "建议就诊方向：{department}").strip()
        try:
            next_step = template.format(department=dept)
        except (KeyError, IndexError, ValueError):
            # 模板写坏了不能挡住结果，退回默认文案。
            next_step = f"建议就诊方向：{dept}"
    else:  # 无匹配，不编造
        next_step = "暂无法判断科室方向，建议结合实际情况就诊"

    # 有命中就带出处；未核实的出处必须标明，避免看起来像权威指南。
    source_note = ""
    hits = dept_result.get("hits") or []
    if dept and hits:
        source = hits[0].get("source") or {}
        title = source.get("title") or source.get("id") or ""
        if title:
            if not source.get("verified"):
                suffix = "（未核实）"
            elif source.get("review_status") == "source_mapped":
                suffix = "（来源已核对，临床审核待完成）"
            else:
                suffix = ""
            source_note = f"来源：{title}{suffix}"

    # 用 ConsultationResult schema 构建并校验，model_dump() 转成普通字典
    result = ConsultationResult(
        status="completed",
        known_facts=collected_facts,  # 已知信息
        unknowns=missing_fields,  # 仍不确定的字段
        tools_used=tools_used,  # 调过的工具
        next_step=next_step,  # 下一步建议
        source_note=source_note,
    )
    return result.model_dump()  # Pydantic 模型转字典，方便 JSON 序列化


def build_risk_result(rule: str) -> dict:
    """风险路径：命中风险，固定提示。

    参数：rule — 命中的规则编号，如 "R001"
    返回：结构化结果字典，status=risk_route
    """
    message = f"检测到预设风险场景（规则 {rule}），请立即寻求专业帮助。"
    result = ConsultationResult(
        status="risk_route",
        risk_message=message,  # 风险提示
        next_step="请立即联系急救或前往最近医院",  # 紧急建议
    )
    return result.model_dump()


def build_semantic_risk_result(evidence: list[str] | None = None) -> dict:
    """语义分诊判定为紧急情况：给出明确的急诊建议，不列诊断。"""
    detail = "；".join(item for item in (evidence or [])[:3] if item).strip()
    if detail:
        message = (
            f"你描述的情况可能需要紧急处理（{detail}），"
            "请立即联系急救或前往最近医院。"
        )
    else:
        message = "你描述的情况可能需要紧急处理，请立即联系急救或前往最近医院。"
    result = ConsultationResult(
        status="risk_route",
        risk_message=message,
        next_step="请立即联系急救或前往最近医院",
    )
    return result.model_dump()


def build_vague_result() -> dict:
    """含糊路径：表述不确定，保守提示。

    不直接判死也不直接放行，提示用户寻求专业帮助。
    返回：结构化结果字典，status=risk_route
    """
    result = ConsultationResult(
        status="risk_route",  # 含糊也走 risk_route，但 risk_message 不同
        risk_message="您提到的描述存在不确定性，如不确定请尽快寻求专业帮助。",
        next_step="建议尽快咨询专业人员",
    )
    return result.model_dump()


def build_refusal_result() -> dict:
    """越狱/注入路径：不执行请求，给固定拒绝话术。

    文案本身不能含“确诊/处方/剂量/停药”，否则会被 final_check 再拦一次。
    """
    result = ConsultationResult(
        status="refused",
        risk_message="无法执行这个请求。本系统只处理虚构症状描述，不提供医疗结论或治疗指导。",
        next_step="请描述症状，或咨询专业医生",
    )
    return result.model_dump()


def build_error_result(reason: str) -> dict:
    """错误路径：工具失败或输出校验没过。

    参数：reason — 出错原因
    返回：结构化结果字典，status=error
    """
    result = ConsultationResult(
        status="error",
        next_step=f"处理出错：{reason}",  # 把出错原因放进 next_step
    )
    return result.model_dump()


def build_unavailable_result(tool_name: str) -> dict:
    """外部只读工具不可用：降级为"暂无依据"，不把整轮问诊变成错误。"""
    result = ConsultationResult(
        status="degraded",
        tools_used=[tool_name],
        next_step="暂时无法获取外部知识依据，建议结合实际情况就诊。",
    )
    return result.model_dump()


def render_text(structured: dict) -> str:
    """从结构化结果渲染成给用户看的文案。

    参数：structured — build_*_result 返回的结构化字典
    返回：多行文案字符串

    注意：limitations 不在这里拼，由 output 节点在边界检查通过后单独追加。
    """
    status = structured.get("status")  # 取结局类型

    if status in ("risk_route", "refused"):
        # 风险/含糊/拒答路径：只返回 risk_message
        return structured.get("risk_message", "")

    if status == "error":
        # 错误路径：只返回 next_step（含出错原因）
        return structured.get("next_step", "处理出错")

    # completed 路径：已知、未知、工具、下一步，分块拼接
    parts = []
    known = structured.get("known_facts", {})  # 已知字段字典
    if known:  # 有已知信息
        # 每个字段渲染成"字段名：值1、值2"
        known_lines = [f"{k}：{'、'.join(v)}" for k, v in known.items()]
        parts.append("已知信息：\n" + "\n".join(known_lines))

    unknowns = structured.get("unknowns", [])  # 仍不确定的字段
    if unknowns:  # 有不确定的字段
        parts.append("尚不确定：\n" + "、".join(unknowns))

    tools = structured.get("tools_used", [])  # 调过的工具
    if tools:  # 有工具调用
        parts.append("调用的工具：" + "、".join(tools))

    parts.append(structured.get("next_step", ""))  # 下一步建议
    if structured.get("source_note"):
        parts.append(structured["source_note"])
    # limitations 不在这里拼，由 output 节点在边界检查通过后追加

    return "\n\n".join(parts)  # 各部分之间空行分隔
