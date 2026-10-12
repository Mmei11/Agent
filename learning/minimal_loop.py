"""Day 2 的最小 Agent Loop；Day 3 增加 JSON 动作校验入口。"""

import signal

try:
    from learning.day3_actions import InvalidAction, parse_action
except ModuleNotFoundError:
    # 直接运行本文件和从项目根目录导入时，Python 的模块搜索路径不同。
    from day3_actions import InvalidAction, parse_action


def to_upper(text: str) -> str:
    """把文本转换为大写。"""
    return text.upper()


def count_words(text: str) -> int:
    """按空白字符统计英文单词数。"""
    return len(text.split())


def render_string_result(state: dict) -> str:
    """从已执行的工具结果生成最终展示文本。"""
    results = state["tool_results"]
    if not isinstance(results.get("to_upper"), str) or not isinstance(
        results.get("count_words"), int
    ):
        raise ValueError("缺少有效的工具结果，不能结束")
    return f'{results["to_upper"]}（{results["count_words"]} 个单词）'


def decide(state: dict) -> dict:
    """根据已有工具结果，选择下一步动作。"""
    results = state["tool_results"]

    if "to_upper" not in results:
        return {
            "type": "CALL_TOOL",
            "name": "to_upper",
            "args": {"text": state["task"]},
        }

    if "count_words" not in results:
        return {
            "type": "CALL_TOOL",
            "name": "count_words",
            "args": {"text": results["to_upper"]},
        }

    return {
        "type": "FINALIZE",
        "content": render_string_result(state),
    }


TOOLS = {
    "to_upper": to_upper,
    "count_words": count_words,
}


def call_tool_with_timeout(tool, args: dict, timeout_seconds: float | None):
    """在 macOS/Linux 命令行主线程中限制同步工具的执行时间。"""
    if timeout_seconds is None:
        return tool(**args)
    if timeout_seconds <= 0:
        raise ValueError("工具超时时间必须大于 0")

    def on_timeout(signum, frame):
        raise TimeoutError(f"超过 {timeout_seconds} 秒")

    previous_handler = signal.signal(signal.SIGALRM, on_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        return tool(**args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def run_agent(
    task: str,
    max_steps: int = 5,
    chooser=decide,
    tools=None,
    finalizer=None,
    tool_timeout_seconds: float | None = None,
) -> dict:
    """反复决策并执行工具，直到结束或达到步骤上限。"""
    registered_tools = TOOLS if tools is None else tools
    state = {
        "task": task,
        "tool_results": {},
        "trace": [],
        "final": None,
    }

    for step in range(1, max_steps + 1):
        raw_action = None
        try:
            raw_action = chooser(state)
            action = (
                parse_action(raw_action, set(registered_tools))
                if isinstance(raw_action, str)
                else raw_action
            )
        except InvalidAction as exc:
            state["final"] = f"模型动作无效：{exc}"
            state["trace"].append(
                {
                    "step": step,
                    "action": raw_action,
                    "observation": {"error": "invalid_action", "detail": str(exc)},
                }
            )
            return state
        except Exception as exc:
            state["final"] = f"决策器调用失败：{exc}"
            state["trace"].append(
                {
                    "step": step,
                    "action": None,
                    "observation": {"error": "decision_error", "detail": str(exc)},
                }
            )
            return state

        if action["type"] == "FINALIZE":
            try:
                state["final"] = (
                    action["content"] if finalizer is None else finalizer(state)
                )
            except Exception as exc:
                state["final"] = f"最终输出校验失败：{exc}"
                state["trace"].append(
                    {
                        "step": step,
                        "action": action,
                        "observation": {"error": "invalid_final", "detail": str(exc)},
                    }
                )
                return state
            state["trace"].append(
                {
                    "step": step,
                    "action": action,
                    "observation": (
                        None if finalizer is None else {"rendered_final": state["final"]}
                    ),
                }
            )
            return state

        if action["type"] != "CALL_TOOL":
            state["final"] = f"无法执行的动作：{action['type']}"
            state["trace"].append(
                {"step": step, "action": action, "observation": {"error": "invalid_action"}}
            )
            return state

        name = action["name"]
        if name not in registered_tools:
            state["final"] = f"未知工具：{name}"
            state["trace"].append(
                {"step": step, "action": action, "observation": {"error": "unknown_tool"}}
            )
            return state

        try:
            result = call_tool_with_timeout(
                registered_tools[name], action["args"], tool_timeout_seconds
            )
        except TimeoutError as exc:
            state["final"] = f"工具 {name} 超时：{exc}"
            state["trace"].append(
                {
                    "step": step,
                    "action": action,
                    "observation": {"error": "tool_timeout", "detail": str(exc)},
                }
            )
            return state
        except Exception as exc:
            state["final"] = f"工具 {name} 执行失败：{exc}"
            state["trace"].append(
                {
                    "step": step,
                    "action": action,
                    "observation": {"error": "tool_error", "detail": str(exc)},
                }
            )
            return state

        state["tool_results"][name] = result
        state["trace"].append(
            {"step": step, "action": action, "observation": {"result": result}}
        )

    state["final"] = f"达到最大步骤数 {max_steps}，任务已停止。"
    return state


if __name__ == "__main__":
    result = run_agent("hello agent world")
    for entry in result["trace"]:
        print(f'第 {entry["step"]} 步：', entry["action"], "→", entry["observation"])
    print("最终结果：", result["final"])
