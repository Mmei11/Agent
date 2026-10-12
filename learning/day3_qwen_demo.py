"""Day 3：真实模型选动作，最小循环执行并保存 JSONL 轨迹。"""

import json
from pathlib import Path

try:
    from learning.day3_qwen import qwen_choose
    from learning.minimal_loop import render_string_result, run_agent
except ModuleNotFoundError:
    from day3_qwen import qwen_choose
    from minimal_loop import render_string_result, run_agent


def save_trace(state: dict, path: Path) -> None:
    """每一步写一行 JSON，供后续评测脚本读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        for entry in state["trace"]:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    result = run_agent(
        "hello agent world",
        chooser=qwen_choose,
        finalizer=render_string_result,
        tool_timeout_seconds=2,
    )
    trace_path = Path(__file__).parent / "traces" / "day3.jsonl"
    save_trace(result, trace_path)
    for entry in result["trace"]:
        print(f'第 {entry["step"]} 步：', entry["action"], "→", entry["observation"])
    print("最终结果：", result["final"])
    print("轨迹文件：", trace_path)
