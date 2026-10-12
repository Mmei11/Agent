"""Day 3：模拟模型返回 JSON 文本，再交给原来的 Agent Loop。"""

import json

try:
    from learning.minimal_loop import decide, run_agent
except ModuleNotFoundError:
    from minimal_loop import decide, run_agent


def mock_model(state: dict) -> str:
    """暂用 Day 2 的规则生成动作，再模拟模型把动作作为 JSON 文本返回。"""
    return json.dumps(decide(state), ensure_ascii=False)


def bad_model(state: dict) -> str:
    """模拟模型选择了白名单之外的工具。"""
    return '{"type":"CALL_TOOL","name":"missing","args":{"text":"hello"}}'


if __name__ == "__main__":
    result = run_agent("hello agent world", chooser=mock_model)
    for entry in result["trace"]:
        print(f'第 {entry["step"]} 步：', entry["action"], "→", entry["observation"])
    print("最终结果：", result["final"])

    bad_result = run_agent("hello", chooser=bad_model)
    print("无效动作：", bad_result["final"])
