"""Day 10：模型决策器与动作校验。模型提出动作，程序校验是否允许。案例均为虚构。"""

import json  # 解析模型返回的 JSON 文本
import os  # 读取环境变量里的 API Key
from urllib.error import HTTPError, URLError  # 捕获网络请求错误
from urllib.request import Request, urlopen  # 发送 HTTP 请求到百炼

from backend.app.agent.followup import select_next_field  # 判断还有没有值得问的字段
from backend.app.agent.state import ConsultationState  # 状态类型

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 百炼默认地址

SYSTEM_PROMPT = """你是模拟问诊 Agent 的动作选择器。请只输出一个 JSON 对象，不要 Markdown 或解释。
可选动作：
1. {"action":"ASK_FOLLOWUP"}    # 需要追问缺失信息
2. {"action":"CALL_TOOL"}        # 需要调用科室建议工具
3. {"action":"FINALIZE"}         # 信息足够或无法继续，结束

选择依据：
- 如果缺失字段还有未问过的（missing_fields 非空且有可问的）→ ASK_FOLLOWUP
- 如果工具还没调用过 → CALL_TOOL
- 如果工具已调用 → FINALIZE
每次只能选一个动作。"""


class DecisionError(RuntimeError):
    """决策器产生的可安全显示的错误。"""


FOLLOWUP_DECLINE_PHRASES = (
    "拒绝补充",
    "拒绝回答",
    "不愿补充",
    "不愿回答",
    "不想补充",
    "不想回答",
    "不想继续说",
    "不再补充",
)


def user_declined_followup(message: str) -> bool:
    """Detect explicit refusal phrases so the graph can stop ordinary follow-up."""
    return any(phrase in message for phrase in FOLLOWUP_DECLINE_PHRASES)


def load_env_file() -> None:
    """读取项目根目录的 .env；终端已有同名变量时以终端为准。"""
    from pathlib import Path

    # 从 decision.py 往上跳 4 级目录找到项目根目录的 .env
    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if not env_path.exists():
        return  # 没有 .env 文件就算了

    allowed_names = {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "MODEL_PRICE_INPUT_PER_1K",
        "MODEL_PRICE_OUTPUT_PER_1K",
    }  # 只认这几个变量，其余写在 .env 里也不会被读入
    for line in env_path.read_text(encoding="utf-8").splitlines():  # 逐行读
        line = line.strip()  # 去掉首尾空白
        if not line or line.startswith("#"):  # 空行和注释跳过
            continue
        name, separator, value = line.partition("=")  # 按 = 拆成变量名和值
        name = name.strip()
        if not separator or name not in allowed_names:  # 没有 = 或不是允许的变量名就跳过
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":  # 去掉首尾引号
            value = value[1:-1]
        os.environ.setdefault(name, value)  # 终端已有同名变量时不覆盖（终端优先）


def estimate_cost(usage: dict) -> float | None:
    """按环境变量里的单价估算本次调用成本；没配单价时返回 None，不瞎猜价格。"""
    price_in = os.getenv("MODEL_PRICE_INPUT_PER_1K")
    price_out = os.getenv("MODEL_PRICE_OUTPUT_PER_1K")
    if not price_in or not price_out:
        return None
    try:
        return round(
            (usage.get("prompt_tokens") or 0) / 1000 * float(price_in)
            + (usage.get("completion_tokens") or 0) / 1000 * float(price_out),
            6,
        )
    except (TypeError, ValueError):
        return None


def model_choose(state: ConsultationState) -> tuple[str, dict]:
    """向模型请求动作选择，返回（原始 JSON 文本, 用量记录）。"""
    load_env_file()  # 先加载 .env 里的 Key
    api_key = os.getenv("DASHSCOPE_API_KEY")  # 从环境变量取 Key
    if not api_key:
        raise DecisionError("缺少 DASHSCOPE_API_KEY")  # 没 Key 就报错，让上层回退预设

    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")  # 百炼地址
    payload = {  # 发给百炼的请求体
        "model": os.getenv("DASHSCOPE_MODEL", "qwen-plus"),  # 模型名
        "messages": [
            # 第一条：系统提示，告诉模型只能输出三种 JSON 之一
            {"role": "system", "content": SYSTEM_PROMPT},
            # 第二条：把当前状态打包成 JSON 发给模型
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "missing_fields": state.missing_fields,  # 缺什么字段
                        "asked_questions": state.asked_questions,  # 问过什么
                        "tool_results_keys": list(state.tool_results.keys()),  # 工具调没调
                        "followup_count": state.followup_count,  # 追问了几次
                    },
                    ensure_ascii=False,  # 中文不转义
                ),
            },
        ],
        "response_format": {"type": "json_object"},  # 强制模型返回 JSON
        "enable_thinking": False,  # 关闭思考过程
        "max_tokens": 128,  # 限制输出长度，动作 JSON 很短
    }
    request = Request(  # 构造 HTTP 请求
        f"{base_url}/chat/completions",  # 百炼的聊天接口
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),  # 请求体转 JSON 字节
        headers={
            "Authorization": f"Bearer {api_key}",  # 鉴权
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:  # 发请求，20 秒超时
            data = json.load(response)  # 解析百炼返回的 JSON
        content = data["choices"][0]["message"]["content"]  # 取模型输出的文本
        usage = data.get("usage") or {}  # 百炼在 usage 里返回 token 用量
        return content, {
            "mode": "model",
            "model": payload["model"],
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "cost_estimate": estimate_cost(usage),
        }
    except (HTTPError, URLError, TimeoutError) as exc:  # 网络出问题
        raise DecisionError(f"模型请求失败：{exc}") from exc  # 包装成可显示的错误
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:  # 响应格式不对
        raise DecisionError(f"模型响应解析失败：{exc}") from exc


def parse_action(raw: str) -> str:
    """解析模型返回的 JSON，提取动作名。无效时抛 DecisionError。"""
    try:
        action = json.loads(raw)  # 把 JSON 文本解析成字典
    except json.JSONDecodeError as exc:
        raise DecisionError("模型输出不是合法 JSON") from exc  # 不是 JSON 就报错
    name = action.get("action")  # 取 action 字段
    if name not in ("ASK_FOLLOWUP", "CALL_TOOL", "FINALIZE"):  # 检查是不是三个合法动作之一
        raise DecisionError(f"未知动作：{name}")  # 模型回了个不认识的动作就拒掉
    return name  # 返回动作名


def validate_action(model_choice: str, state: ConsultationState, max_followups: int) -> str:
    """校验模型选的动作是否允许执行。不允许时返回兜底动作。

    返回最终执行的动作（可能与模型选择不同）。
    """
    under_limit = state.followup_count < max_followups  # 追问还没到上限
    can_ask = select_next_field(state.missing_fields, state.asked_questions) is not None  # 还有值得问的字段
    tool_called = "department_suggest" in state.tool_results  # 工具已经调过了

    if model_choice == "ASK_FOLLOWUP":
        # 用户明确拒答时，尊重拒答并收束；工具尚未运行则继续做受限整理。
        if user_declined_followup(state.latest_user_message):
            return "FINALIZE" if tool_called else "CALL_TOOL"
        if under_limit and can_ask:  # 没到上限且还有可问的 → 放行
            return "ASK_FOLLOWUP"
        # 模型想追问但没有可问的了 → 兜底
        if tool_called:
            return "FINALIZE"  # 工具调过了 → 结束
        return "CALL_TOOL"  # 工具没调 → 去调工具

    if model_choice == "CALL_TOOL":
        if not tool_called:
            return "CALL_TOOL"  # 工具没调过 → 放行
        # 工具已调用 → 不重复调
        return "FINALIZE"  # 兜底为结束

    # FINALIZE 始终允许，不需要校验
    return "FINALIZE"


def mock_choose(state: ConsultationState) -> str:
    """预设决策器：用规则代替模型，返回 JSON 文本。无 Key 时用这个。"""
    under_limit = state.followup_count < 3  # 追问没到 3 次上限
    can_ask = select_next_field(state.missing_fields, state.asked_questions) is not None  # 还有可问的
    tool_called = "department_suggest" in state.tool_results  # 工具调过了

    if under_limit and can_ask:
        return '{"action":"ASK_FOLLOWUP"}'  # 有缺失没问够 → 追问
    if not tool_called:
        return '{"action":"CALL_TOOL"}'  # 工具没调 → 调工具
    return '{"action":"FINALIZE"}'  # 都做完了 → 结束
