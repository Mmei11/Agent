"""Day 3 验收：五个真实模型任务和四条本地失败路径。"""

import json
import time

try:
    from learning.day3_qwen import qwen_choose
    from learning.minimal_loop import render_string_result, run_agent
except ModuleNotFoundError:
    from day3_qwen import qwen_choose
    from minimal_loop import render_string_result, run_agent


TASKS = [
    "hello",
    "agent loop",
    "hello agent world",
    "Python tools make progress",
    "one two three four five",
]


def fixed_tool_action(state: dict) -> str:
    """每次都要求调用 to_upper，用来检查步骤上限。"""
    return json.dumps(
        {"type": "CALL_TOOL", "name": "to_upper", "args": {"text": state["task"]}}
    )


def unknown_tool_action(state: dict) -> str:
    """模拟模型选择了未注册的工具。"""
    return '{"type":"CALL_TOOL","name":"missing","args":{"text":"hello"}}'


def invalid_args_action(state: dict) -> str:
    """模拟模型遗漏工具必需的 text 参数。"""
    return '{"type":"CALL_TOOL","name":"to_upper","args":{}}'


def slow_tool(text: str) -> str:
    """故意等待，验证 Agent 能在截止时间到来时停止。"""
    time.sleep(0.2)
    return text


def check_failure_paths() -> tuple[int, int]:
    """不请求模型，检查四条可读的失败退路。"""
    bad_tool = run_agent(
        "hello",
        chooser=unknown_tool_action,
    )
    bad_args = run_agent(
        "hello",
        chooser=invalid_args_action,
    )
    max_steps = run_agent("hello", max_steps=2, chooser=fixed_tool_action)
    timeout = run_agent(
        "hello",
        chooser=fixed_tool_action,
        tools={"to_upper": slow_tool},
        tool_timeout_seconds=0.02,
    )

    checks = [
        ("未知工具", bad_tool, "invalid_action"),
        ("无效参数", bad_args, "invalid_action"),
        ("步骤上限", max_steps, None),
        ("工具超时", timeout, "tool_timeout"),
    ]
    passed_count = 0
    for label, state, expected_error in checks:
        actual_error = (
            state["trace"][-1]["observation"].get("error")
            if state["trace"] and state["trace"][-1]["observation"]
            else None
        )
        passed = actual_error == expected_error and bool(state["final"])
        if label == "步骤上限":
            passed = passed and state["final"].startswith("达到最大步骤数")
        passed_count += int(passed)
        print(f'{"通过" if passed else "失败"}｜{label}｜{state["final"]}')
    return passed_count, len(checks)


def check_live_tasks() -> tuple[int, int]:
    """调用真实百炼模型；每个任务通常需要三次模型请求。"""
    passed_count = 0
    for task in TASKS:
        state = run_agent(
            task,
            chooser=qwen_choose,
            finalizer=render_string_result,
            tool_timeout_seconds=2,
        )
        expected = f'{task.upper()}（{len(task.split())} 个单词）'
        passed = state["final"] == expected
        passed_count += int(passed)
        print(f'{"通过" if passed else "失败"}｜{task}｜{state["final"]}')
    return passed_count, len(TASKS)


if __name__ == "__main__":
    failure_passed, failure_total = check_failure_paths()
    live_passed, live_total = check_live_tasks()
    print(f"汇总：失败路径 {failure_passed}/{failure_total}，真实任务 {live_passed}/{live_total}")
    if failure_passed != failure_total or live_passed != live_total:
        raise SystemExit(1)
