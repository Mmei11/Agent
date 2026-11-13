"""单路由：判断医疗/普通，普通问题在同一次调用里直接回答。

确定性路径（药品、会话信息、日期、风险规则）在 router 之前处理；
这里只负责剩余追问，避免关键词表漏掉正常问法。
"""

from backend.app.agent.general import DISPLAY_TZ, WEEKDAY_CN
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import RoutedReply
from backend.app.observability import span
from backend.app.prompts import load_prompt

PROMPT_NAME = "router.reply"
PROMPT_VERSION = "v2"

SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)


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
