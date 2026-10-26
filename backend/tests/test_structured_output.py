"""Phase 1 结构化输出闭环测试：坏 JSON 修复重试与降级。不联网、不花钱。"""

import json
from types import SimpleNamespace

import instructor
import pytest
from openai import OpenAI

from backend.app.llm.client import LLMError, chat_structured
from backend.app.llm.schemas import IntakeFacts

VALID_INTAKE_JSON = json.dumps(
    {
        "chief_complaint": [
            {"value": "咳嗽", "evidence": "咳嗽", "confidence": 0.9}
        ],
        "duration": [],
        "location": [],
        "severity": [],
        "associated": [],
        "background": [],
        "aggravating": [],
        "pattern": [],
        "measurement": [],
    },
    ensure_ascii=False,
)


def _completion(content: str):
    """构造 instructor 需要的响应形状，至少要有 finish_reason 和 usage。"""
    choice = SimpleNamespace(
        index=0,
        finish_reason="stop",
        message=SimpleNamespace(content=content, role="assistant", tool_calls=None),
    )
    return SimpleNamespace(
        id="chatcmpl-fake",
        object="chat.completion",
        created=0,
        model="fake-model",
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class FakeCompletions:
    """按顺序返回预设内容；用完后重复最后一条。"""

    def __init__(self, contents: list[str]):
        self.contents = contents
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return _completion(self.contents[index])


class FakeOpenAI(OpenAI):
    """instructor 要求客户端是 OpenAI 实例，所以继承后替换 chat。"""

    def __init__(self, contents: list[str]):
        self.chat = SimpleNamespace(completions=FakeCompletions(contents))


def make_structured_client(contents: list[str]):
    fake = FakeOpenAI(contents)
    return instructor.from_openai(fake, mode=instructor.Mode.JSON), fake


def test_repairs_invalid_json_with_feedback():
    """第一次输出坏 JSON，第二次按错误提示修复成功。"""
    structured, fake = make_structured_client(["这不是 JSON", VALID_INTAKE_JSON])

    facts, usage = chat_structured(
        [{"role": "user", "content": "咳嗽"}],
        IntakeFacts,
        max_retries=2,
        structured_client=structured,
    )

    assert facts.chief_complaint[0].value == "咳嗽"
    assert len(fake.chat.completions.calls) == 2
    # 第二次请求里必须带着第一次的错误说明，证明错误被回喂了。
    retry_messages = fake.chat.completions.calls[1]["messages"]
    assert any(
        "Correct your JSON" in str(message.get("content", ""))
        for message in retry_messages
    )
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5


def test_degrades_after_retries_exhausted():
    """一直返回坏 JSON 时，重试用尽后抛不可重试的 LLMError。"""
    structured, fake = make_structured_client(["仍然不是 JSON"])

    with pytest.raises(LLMError) as caught:
        chat_structured(
            [{"role": "user", "content": "咳嗽"}],
            IntakeFacts,
            max_retries=2,
            structured_client=structured,
        )

    assert caught.value.retryable is False
    # 首次 + 两次重试，共三次尝试。
    assert len(fake.chat.completions.calls) == 3