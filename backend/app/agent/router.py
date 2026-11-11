"""单路由：判断医疗/普通，普通问题在同一次调用里直接回答。

确定性路径（药品、会话信息、日期、风险规则）在 router 之前处理；
这里只负责剩余追问，避免关键词表漏掉正常问法。
"""

from backend.app.agent.general import DISPLAY_TZ, WEEKDAY_CN
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import RoutedReply
from backend.app.observability import span

PROMPT_NAME = "router.reply"
PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """你是"Consult/Lab"的模拟问诊助手。
先判断用户这一轮消息是否与医疗/疾病/身体不适、症状、药品、检查、治疗、就医判断或医疗风险相关。

- 相关：intent="medical"，answer 留空字符串，不要回答医疗内容。
- 不相关：intent="general"，用 1 到 3 句自然回答；不提供诊断、处方、药物名称、剂量或治疗方案。
  如果问题涉及医疗判断，提醒用户描述症状或咨询医生。
- 寒暄、身份、日期时间、天气、闲聊、产品使用、挂号/医保/体检流程、非医疗常识都算 general。
- 生活方式、饮食搭配、营养常识、运动、作息等日常健康管理问题算 general；
  只有描述身体不适、症状、疾病、药品、检查、治疗、就医判断或医疗风险时才算 medical。
  例："这样的饮食均衡吗""晚上吃这些健康吗" → general；
  "吃了这些以后胃疼" → medical。
- 情绪安抚、倾诉、想被安慰（如心情不好、压力大）也算 general；
  但不要对情绪状态做诊断（如抑郁症、焦虑症）。
- 先看有没有具体的身体不适或医疗诉求；没有就选 general。确实拿不准时选 medical。

只输出 JSON：{"intent": "medical" 或 "general", "answer": "..."}，不要 Markdown 或额外字段。"""


def build_router_prompt() -> str:
    from datetime import datetime

    now = datetime.now(DISPLAY_TZ)
    return (
        SYSTEM_PROMPT
        + f"\n\n当前日期时间：{now:%Y-%m-%d %H:%M}，{WEEKDAY_CN[now.weekday()]}（北京时间）。"
        "用户询问日期或时间时，以这个为准，不要使用训练数据里的日期。"
    )


def route(message: str) -> tuple[RoutedReply, LLMResult]:
    with span("router.reply", kind="internal") as record:
        reply, usage = chat_structured(
            [
                {"role": "system", "content": build_router_prompt()},
                {"role": "user", "content": message},
            ],
            RoutedReply,
            max_tokens=300,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
        record.attributes["router.intent"] = reply.intent
        record.attributes["router.answer_chars"] = len(reply.answer)
    return reply, usage
