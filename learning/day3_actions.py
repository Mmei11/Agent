"""Day 3：把模型返回的 JSON 文本解析并校验为受限动作。"""

import json


class InvalidAction(ValueError):
    """模型返回的动作不符合我们约定的格式。"""


def parse_action(raw: str, allowed_tools: set[str]) -> dict:
    """返回已校验的动作；格式或参数错误时抛出 InvalidAction。"""
    if not isinstance(raw, str):
        raise InvalidAction("模型输出必须是文本")

    try:
        action = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidAction("模型输出不是合法 JSON") from exc

    if not isinstance(action, dict):
        raise InvalidAction("JSON 顶层必须是对象")

    action_type = action.get("type")
    if action_type == "CALL_TOOL":
        name = action.get("name")
        args = action.get("args")
        if not isinstance(name, str) or name not in allowed_tools:
            raise InvalidAction(f"未知工具：{name}")
        if not isinstance(args, dict) or set(args) != {"text"} or not isinstance(args["text"], str):
            raise InvalidAction("工具参数必须且只能包含字符串 text")
        return action

    if action_type == "FINALIZE":
        content = action.get("content")
        if not isinstance(content, str) or not content.strip():
            raise InvalidAction("最终内容必须是非空字符串")
        return action

    raise InvalidAction(f"未知动作类型：{action_type}")


if __name__ == "__main__":
    allowed = {"to_upper", "count_words"}
    examples = [
        '{"type":"CALL_TOOL","name":"to_upper","args":{"text":"hello agent world"}}',
        '{"type":"CALL_TOOL","name":"missing","args":{"text":"hello"}}',
        '{"type":"CALL_TOOL","name":"count_words","args":{}}',
        'not json',
    ]
    for raw_text in examples:
        try:
            print("通过：", parse_action(raw_text, allowed))
        except InvalidAction as exc:
            print("拒绝：", exc)
