"""Phase 7：问诊结果生成后的继续追问。

结果生成后，用户还可以继续提问（"那要挂什么科""需要空腹吗"）。
这里用独立节点回答：只解释已有信息和就医方向，不重新做诊断，
也不覆盖已经生成的结构化结果。
"""

import json
import os
import re

from backend.app.agent.state import ConsultationState
from backend.app.context.assembler import assemble_answer_context
from backend.app.context.budget import ANSWER_RECENT_ITEMS as RECENT_CONVERSATION_ITEMS
from backend.app.agent.general import (
    GENERAL_FALLBACK,
    date_answer as build_date_answer,
    general_answer_is_safe,
    is_date_question,
    is_guideline_question,
    is_red_flag_question,
)
from backend.app.agent.memory_ref import is_memory_reference
from backend.app.agent.opening import (
    answer_clinical_trials,
    answer_guideline,
    answer_health_topic,
    answer_red_flags,
    answer_research,
)
from backend.app.agent.router import route as route_reply
from backend.app.agent.session_info import (
    build_answer as build_session_info_answer,
    is_session_info_query,
)
from backend.app.drugs.answer_llm import generate as generate_drug_answer
from backend.app.drugs.lookup import (
    DrugReply,
    load_labels,
    render_label,
    reply_for_message,
)
from backend.app.llm.client import LLMError, LLMResult, chat_structured
from backend.app.llm.schemas import FollowupAnswer
from backend.app.observability import current_trace, span
from backend.app.safety.audit import AuditEvent, input_digest, write as write_audit
from backend.app.safety.output_rail import check as check_output
from backend.app.safety.rule_engine import final_check

# 观测用的 prompt 标识：改 prompt 时同步升版本，才能按版本对比指标。
PROMPT_NAME = "answer.followup"
PROMPT_VERSION = "v6"

MAX_ANSWER_CHARS = 400

SAFE_FALLBACK = (
    "本系统只提供模拟问诊与就医方向整理，不提供确诊、处方或用药建议。"
    "请咨询专业医生。"
)

# 模型服务不可用时不伪装成正常回答：明确告诉用户可以重试。
SERVICE_UNAVAILABLE = (
    "抱歉，回答服务暂时不可用，无法回答这个问题。请稍后重试；"
    "如症状加重，请及时就医。本内容仅供健康参考，不能替代专业医生诊断。"
)

# 鉴别诊断式列举：模型容易用"排查/可能是 + 病名"来解释科室，这在 C 端
# 容易被理解成诊断结论。命中后整段改写为只说明就医方向的安全文案。
DIFFERENTIAL_PATTERNS = (
    re.compile(
        r"(?:排查|排除|可能是|可能为|考虑|警惕)[^。；\n]{0,15}"
        r"(?:偏头痛|头痛|颅内|脑梗|脑出血|肿瘤|癌|颈椎|高血压|糖尿病|感染|炎症|综合征)"
    ),
    re.compile(
        r"(?:可能原因|可能疾病|常见原因)[^。；\n]{0,12}"
        r"(?:偏头痛|头痛|颅内|脑梗|脑出血|肿瘤|癌|颈椎|高血压|糖尿病|感染|炎症|综合征)"
    ),
)

# 药品回答的“开场白”不允许出现处方式表达；说明书正文由模板单独拼接。
DRUG_UNSAFE_INTRO_PATTERNS = (
    re.compile(r"建议(服用|使用|吃)"),
    re.compile(r"推荐(服用|使用|吃)"),
    re.compile(r"(你|您)可以(吃|服用|使用)"),
    re.compile(r"可以(吃|服用)"),
    re.compile(r"(?<!是否)(?<!不)适合(你|您)"),
    re.compile(r"(换|替代|代替)成"),
    re.compile(r"(每次|每日|一日|一次)"),
    re.compile(r"\d+\s*(mg|毫克|g|克|ml|毫升|片|粒|袋|次)", re.IGNORECASE),
    re.compile(r"确诊|诊断为"),
)

ANSWER_SYSTEM_PROMPT = """你是"模拟问诊"项目的追问回答器。用户已经完成一轮信息整理，现在继续提问。

你会收到一个 JSON 上下文：
- question：用户这一轮的问题
- collected_facts：已经收集到的字段
- final_result：上一轮生成的结构化结果（可能包含就诊方向、风险提示和来源）
- recent_conversation：最近几轮对话
- session_memory：跨轮保留的用户细节（summary 是更早对话的摘要，details 是最近细节）、未决问题和最近话题

回答要求：
1. 只根据 collected_facts、final_result、recent_conversation 和 session_memory 回答，不新增诊断，不推断用户没说过的情况。
   recent_conversation 和 session_memory 只用于回答“我刚才说过什么”这类记忆问题，不要把它们当成新的症状信息。
2. 不列举具体疾病名称，不做鉴别诊断。不要出现"可能是XX病""需要排查XX病"这类表述；
   需要解释原因时统一说"具体原因需要医生面诊判断"。
3. 不使用可能引起焦虑的严重疾病名称。
4. 不做确诊，不给处方，不推荐药物、剂量或具体治疗操作。
5. 用户询问诊断或用药时，明确说明本系统无法提供，并建议咨询专业医生。
6. 用户询问"怎么缓解""怎么止痛"时，可以给低风险、通用性的生活建议，
   例如休息、适量补水、避免强光或噪音、记录症状变化、就诊前准备；
   但必须说明这些不是治疗方案，最终处理以医生面诊为准。
7. 不判断"不用就医"；症状明显加重或出现新的严重表现时，建议及时就医。
8. 用户补充了新的症状信息时，说明当前结果尚未重新评估，建议重新开始一次问诊或及时就医。
9. 问题超出范围时直接说明无法回答，不要编造。
10. 回答 20 到 200 个汉字，口语化，最多两段。
11. grounded：布尔值。回答使用了 final_result 中的就诊方向、风险提示或知识库来源时为 true；
    只是通用健康常识（如空腹、挂号、准备材料、生活建议）时为 false。
12. 用户问“我刚才说过什么”“之前提到的……”时，先看 session_memory.summary 和 details，再看 recent_conversation；
    只复述确实出现过的内容；找不到就明确说“这轮对话里没有记录到”，不要猜，也不要用常识补全。
13. 只输出 JSON：{"answer": "...", "grounded": true}，不要 Markdown 或额外字段。"""


def _usage_dict(usage: LLMResult | None) -> dict:
    if usage is None:
        return {}
    return {
        "model": usage.model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": usage.latency_ms,
        "cost_estimate": usage.cost_estimate,
    }


def build_context(state: ConsultationState) -> dict:
    """只给回答节点需要的上下文，避免把整份状态塞进 prompt。"""
    return assemble_answer_context(state)


def generate_with_meta(
    state: ConsultationState,
) -> tuple[FollowupAnswer, LLMResult]:
    context = build_context(state)
    with span(
        "answer.followup",
        kind="internal",
        attributes={
            "answer.result_status": state.final_result.get("status", ""),
            "answer.memory_reference": is_memory_reference(state.latest_user_message),
            "answer.memory_details": len((state.session_memory or {}).get("details") or []),
        },
    ) as record:
        reply, usage = chat_structured(
            [
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            FollowupAnswer,
            max_tokens=300,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
        record.attributes["answer.length"] = len(reply.answer)
    return reply, usage


def fallback_answer(state: ConsultationState) -> str:
    """模型不可用时的确定性回答：至少把结果和就医方向复述清楚。"""
    result = state.final_result or {}
    if result.get("status") == "risk_route":
        return result.get("risk_message") or "如出现紧急不适，请立即就医。"
    next_step = result.get("next_step") or ""
    if next_step:
        return (
            f"根据当前整理结果，{next_step}。"
            "如需更具体的判断，请补充描述或咨询专业医生。"
        )
    return "当前信息已整理完成。如需进一步了解，请补充具体问题，或咨询专业医生。"


def _with_context(
    text: str, result: dict, grounded: bool, *, memory_reference: bool = False
) -> str:
    """按回答是否真的引用分诊结果，决定附加依据还是一般信息说明。

    未核实的出处不在追问气泡里重复，避免把通用建议伪装成有出处的结论；
    详细出处仍保留在右侧的会话结果里。

    记忆类回答不套“一般健康信息”的尾巴：它复述的是本轮对话，不是健康常识。
    """
    limitations = (
        result.get("limitations")
        or "本系统不提供确诊、处方或用药建议，请咨询专业人员。"
    )
    source_note = result.get("source_note") or ""
    parts = [text]
    if limitations and limitations not in text:
        parts.append(limitations)

    if memory_reference:
        parts.append("以上内容来自本轮对话记录。")
    elif grounded:
        if source_note:
            if "未核实" in source_note:
                parts.append("依据：本轮分诊结果，具体原因需医生面诊判断。")
            elif source_note not in text:
                parts.append(source_note)
    else:
        parts.append("以上为一般健康信息，具体原因需医生面诊判断。")
    return "\n\n".join(parts)


def _looks_like_differential(text: str) -> bool:
    return any(pattern.search(text) for pattern in DIFFERENTIAL_PATTERNS)


def _safe_differential_rewrite(result: dict) -> str:
    next_step = result.get("next_step") or "建议咨询专业医生"
    return f"具体原因需要医生面诊判断。根据本轮整理结果，{next_step}。"


def blocked_answer(result: dict) -> str:
    """模型回答被输出层拦下时的统一回复：不提供具体处理，但给出下一步。"""
    next_step = result.get("next_step") or "建议咨询专业医生"
    return (
        "这个问题涉及具体处置建议，本系统无法给出。"
        f"依据本轮分诊结果，{next_step}；如症状明显加重，请及时就医。"
        "本系统不提供确诊、处方或用药建议，请咨询专业人员。"
    )


def _audit_replaced(state: ConsultationState, rule: str, detail: str) -> None:
    write_audit(
        AuditEvent(
            layer="output",
            rule=rule,
            action="REPLACE",
            session_id=state.session_id,
            turn_id=state.turn_id,
            input_digest=input_digest(state.latest_user_message),
            detail=detail,
        )
    )


def _drug_intro_safe(intro: str, label) -> bool:
    """模型开场白只能是说明书内容的转述，不能变成处方或推荐。"""
    text = intro.strip()
    if not 10 <= len(text) <= 200:
        return False
    if any(pattern.search(text) for pattern in DRUG_UNSAFE_INTRO_PATTERNS):
        return False

    lower = text.lower()
    for other in load_labels():
        if other.id == label.id:
            continue
        if any(alias.lower() in lower for alias in other.aliases):
            return False
    return True


def _drug_answer(state: ConsultationState, reply: DrugReply) -> dict:
    """L1 药品说明书查询：说明书正文由受控数据提供，模型只写开场白。"""
    scope = current_trace()
    result = state.final_result or {}
    text = reply.text
    drug_answer_source = "template"
    drug_intro_rejected = False
    usage = None

    if (
        reply.kind == "drug_label"
        and reply.label is not None
        and os.getenv("USE_MODEL") == "1"
    ):
        try:
            answer, usage = generate_drug_answer(
                state.latest_user_message, reply.label
            )
            if _drug_intro_safe(answer.intro, reply.label):
                text = render_label(
                    reply.label,
                    sections=list(answer.sections),
                    intro=answer.intro,
                )
                drug_answer_source = "model"
            else:
                drug_intro_rejected = True
        except LLMError:
            # 模型失败时用完整说明书摘要兜底，药品信息本身仍然可用。
            drug_answer_source = "template"

    return {
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "trace": [
            {
                "node": "answer",
                "source": reply.kind,
                "grounded": reply.kind == "drug_label",
                "drug_id": reply.drug_id,
                "drug_verified": reply.verified,
                "drug_answer_source": drug_answer_source,
                "drug_intro_rejected": drug_intro_rejected,
                "service_unavailable": False,
                "differential_replaced": False,
                "output_passed": True,
                "output_violations": [],
                "boundary_rule": None,
                "result_status": result.get("status"),
                "answer_chars": len(text),
                "model_usage": _usage_dict(usage),
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }


def _general_result(
    state: ConsultationState,
    text: str,
    source: str,
    usage: LLMResult | None = None,
) -> dict:
    """普通回复的统一返回结构：普通气泡、不覆盖问诊结果。"""
    scope = current_trace()
    result = state.final_result or {}
    return {
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "trace": [
            {
                "node": "answer",
                "source": "general",
                "grounded": False,
                "general_answer_source": source,
                "service_unavailable": False,
                "differential_replaced": False,
                "output_passed": True,
                "output_violations": [],
                "boundary_rule": None,
                "result_status": result.get("status"),
                "answer_chars": len(text),
                "model_usage": _usage_dict(usage),
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }


def _date_answer(state: ConsultationState) -> dict:
    text = build_date_answer(state.latest_user_message)
    return _general_result(state, text, "deterministic")


def _router_answer(state: ConsultationState, routed, usage: LLMResult) -> dict:
    if general_answer_is_safe(routed.answer):
        return _general_result(state, routed.answer.strip(), "router", usage)
    return _general_result(state, GENERAL_FALLBACK, "fallback", usage)


def _session_info_answer(state: ConsultationState) -> dict:
    """用户问“你记录了我哪些信息/我的症状有哪些”：直接列事实，不走模型。"""
    scope = current_trace()
    result = state.final_result or {}
    text = build_session_info_answer(state)
    return {
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "trace": [
            {
                "node": "answer",
                "source": "session_info",
                "grounded": False,
                "service_unavailable": False,
                "differential_replaced": False,
                "output_passed": True,
                "output_violations": [],
                "boundary_rule": None,
                "result_status": result.get("status"),
                "answer_chars": len(text),
                "model_usage": {},
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }


def answer_followup(state: ConsultationState) -> dict:
    """回答已出结果后的追问；结构化结果保持不变。"""
    drug_reply = reply_for_message(state.latest_user_message)
    if drug_reply is not None:
        return _drug_answer(state, drug_reply)

    if is_session_info_query(state.latest_user_message):
        return _session_info_answer(state)

    # 记忆指代优先于日期规则：问“我刚才说几月几号要去”时，
    # 答案在对话里，不是今天的日期。
    memory_reference = is_memory_reference(state.latest_user_message)

    if not memory_reference and is_date_question(state.latest_user_message):
        return _date_answer(state)

    # 红旗科普类追问：先于就医准备，允许列出危险信号但不给安抚结论。
    if not memory_reference and is_red_flag_question(state.latest_user_message):
        return answer_red_flags(state, node="answer")

    # 临床试验追问：先于文献检索，避免“临床试验”被当成泛研究问题。
    trial_reply = answer_clinical_trials(state, node="answer")
    if trial_reply is not None:
        return trial_reply

    # 健康主题追问：MedlinePlus 只在工具已注册时接管。
    topic_reply = answer_health_topic(state, node="answer")
    if topic_reply is not None:
        return topic_reply

    # 文献检索追问：只有 PubMed 工具已注册时才接管。
    research_reply = answer_research(state, node="answer")
    if research_reply is not None:
        return research_reply

    # 就医准备类追问：走确定性工具，不交给模型常识。
    if not memory_reference and is_guideline_question(state.latest_user_message):
        return answer_guideline(state, node="answer")

    # 其余追问交给单一路由：普通问题直接回答，医疗问题继续走下面的医疗链路。
    # 记忆指代必须走带上下文的 answer 节点，所以命中后跳过路由。
    router_intent = "memory" if memory_reference else "medical"
    router_usage = None
    if os.getenv("USE_MODEL") == "1" and not memory_reference:
        try:
            routed, router_usage = route_reply(state.latest_user_message)
            router_intent = routed.intent
        except LLMError:
            router_intent = "medical"
        if router_intent == "general":
            return _router_answer(state, routed, router_usage)

    result = state.final_result or {}
    source = "mock"
    usage = None
    # 兜底回答直接复述 final_result，属于有依据的回答。
    grounded = True
    service_unavailable = False

    if os.getenv("USE_MODEL") == "1":
        try:
            reply, usage = generate_with_meta(state)
            text = reply.answer.strip()
            grounded = bool(reply.grounded)
            source = "model"
        except LLMError:
            # 模型不可用时不假装回答；让用户知道可以稍后重试。
            text = SERVICE_UNAVAILABLE
            source = "fallback"
            grounded = False
            service_unavailable = True
    else:
        text = fallback_answer(state)

    if not text:
        text = SAFE_FALLBACK
        source = "fallback"
        grounded = False
    text = text[:MAX_ANSWER_CHARS]

    # 模型仍可能把科室理由写成疾病列举；命中后整段改为只说明就医方向。
    differential_replaced = False
    if _looks_like_differential(text):
        text = _safe_differential_rewrite(result)
        grounded = True
        differential_replaced = True
        _audit_replaced(state, "F002", "鉴别诊断式列举")

    # 与 output 节点相同的两道检查：规则边界 + guardrails 复核。
    boundary = final_check(text)
    boundary_rule = boundary.get("rule")
    output_passed = True
    output_violations = []
    if boundary["level"] != "safe":
        _audit_replaced(state, boundary_rule or "F001", boundary["reason"])
        # 不提供具体处理，但保留就医方向；比固定拒绝文案更有用。
        text = blocked_answer(result)
        grounded = False
        output_passed = False
        output_violations = [boundary["reason"]]
    else:
        # 先过规则边界再补免责声明/依据，避免把“处方”等词喂给 final_check。
        if not service_unavailable:
            text = _with_context(
                text, result, grounded, memory_reference=memory_reference
            )
        with span("safety.output", kind="safety") as record:
            verdict = check_output(
                text,
                uses_knowledge=grounded and bool(result.get("source_note")),
            )
            record.attributes["output.passed"] = verdict.passed
            record.attributes["output.violations"] = len(verdict.violations)
        if not verdict.passed:
            _audit_replaced(state, "F003", "; ".join(verdict.violations))
            output_passed = False
            output_violations = list(verdict.violations)
            text = blocked_answer(result) if result else SAFE_FALLBACK

    scope = current_trace()
    return {
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "trace": [
            {
                "node": "answer",
                "source": source,
                "grounded": grounded,
                "router_intent": router_intent,
                "router_usage": _usage_dict(router_usage),
                "memory_reference": memory_reference,
                "service_unavailable": service_unavailable,
                "differential_replaced": differential_replaced,
                "output_passed": output_passed,
                "output_violations": output_violations,
                "boundary_rule": boundary_rule,
                "result_status": result.get("status"),
                "answer_chars": len(text),
                "model_usage": _usage_dict(usage),
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }
