"""外部工具结果的中文摘要。

只在 USE_MODEL=1 时调用模型：把英文/混合文本整理成简洁、信息完整的
中文摘要，保留编号和来源；mock/CI 或调用失败时保留原文。
"""

import os
import re

from backend.app.llm.client import LLMError, chat

PROMPT_NAME = "external.localize"
PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """你是医学信息编辑。把下面的外部工具结果整理成简洁、信息完整的简体中文摘要。

要求：
- 保留所有编号与标识：PMID、NCT、DOI、Set ID、召回号、申请号、日期、药名、机构名、来源；
- 先用 1-3 句概括核心结论或状态，再按需列出关键条目；
- 如果只展示了部分条目，明确写“展示的条目中…”，不要把部分统计说成全部；
- 第一句只陈述总数与展示条数；任何状态/分类统计必须限定在“展示的条目”内，不能推断未展示的条目；
- 不要用省略号省略关键结论；原文中的省略号按已有内容概括；
- 疾病/主题名称、剂型、包装、数量、分类、状态、原因翻译成中文；药品通用名/商品名保留英文；
- 不要添加原文没有的事实；
- 不要给诊断、治疗、用药或剂量建议；
- 输出以中文为主，不超过 300 字，保留分行结构。"""


def _chinese_chars(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def localize_external(text: str, *, timeout: float = 20.0) -> str:
    if os.getenv("USE_MODEL") != "1" or not text.strip():
        return text

    try:
        result = chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请整理成中文摘要：\n\n{text}"},
            ],
            max_tokens=min(1200, max(256, len(text))),
            temperature=0.0,
            timeout=timeout,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
    except LLMError:
        return text
    except Exception:
        # 摘要是增强项，任何失败都不该挡住外部工具回答。
        return text

    localized = (result.content or "").strip()
    if not localized:
        return text

    chinese = _chinese_chars(localized)
    if chinese < 5:
        return text
    # 防止模型把整段内容改写成英文。
    if chinese / max(len(localized), 1) < 0.05:
        return text
    return localized
