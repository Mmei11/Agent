"""Day 3：百炼薄适配层。只返回模型文本，不执行工具。"""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

SYSTEM_PROMPT = """你是无医疗内容的英文字符串练习 Agent 的动作选择器。
请只输出一个 JSON 对象，不要 Markdown 或解释。可选格式：
1. {"type":"CALL_TOOL","name":"to_upper","args":{"text":"字符串"}}
2. {"type":"CALL_TOOL","name":"count_words","args":{"text":"字符串"}}
3. {"type":"FINALIZE","content":"最终结果"}
目标：先将 task 转为大写，再统计大写结果的英文单词数，最后输出“大写结果（N 个单词）”。
每次只能选择一个动作。只依据提供的 task 和 tool_results 决定下一步；不要编造工具结果。
如果没有 to_upper 结果，调用 to_upper；否则如果没有 count_words 结果，调用 count_words；
两个结果都有时，输出 FINALIZE。"""


class ModelAPIError(RuntimeError):
    """只放可安全显示的配置、网络或响应错误。"""


def load_env_file() -> None:
    """读取项目根目录的 .env；当前终端已有同名变量时以终端为准。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    allowed_names = {"DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_MODEL"}
    for line_number, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in allowed_names:
            raise ModelAPIError(f".env 第 {line_number} 行格式或变量名不正确")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def qwen_choose(state: dict) -> str:
    """向百炼请求下一步动作，返回原始 JSON 文本。"""
    load_env_file()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ModelAPIError("请在项目根目录的 .env 中填写 DASHSCOPE_API_KEY")

    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not base_url.startswith("https://"):
        raise ModelAPIError("DASHSCOPE_BASE_URL 必须是 HTTPS 地址")

    payload = {
        "model": os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"task": state["task"], "tool_results": state["tool_results"]},
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
        "max_tokens": 160,
    }
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            response_data = json.load(response)
    except HTTPError as exc:
        raise ModelAPIError(f"百炼返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ModelAPIError("连接百炼失败或请求超时") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelAPIError("百炼响应不是合法 JSON") from exc

    try:
        choice = response_data["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ModelAPIError("百炼响应缺少完整的文本结果") from exc
    return content
