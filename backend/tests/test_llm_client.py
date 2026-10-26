"""Phase 1 模型层测试：归一化、成本估算、错误分类。全程不联网、不花钱。"""

from types import SimpleNamespace

import pytest

from backend.app.llm.client import LLMError, chat


def fake_response(
    content='{"ok": true}',
    prompt=10,
    completion=5,
    model="qwen-plus",
    with_usage=True,
):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=(
            SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
            if with_usage
            else None
        ),
        model=model,
    )


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.completions = FakeCompletions(response, error)
        self.chat = SimpleNamespace(completions=self.completions)


class FakeStatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_chat_returns_normalized_result(monkeypatch):
    """成功调用：内容、模型、token、延迟都归一化到 LLMResult。"""
    monkeypatch.delenv("MODEL_PRICE_INPUT_PER_1K", raising=False)
    monkeypatch.delenv("MODEL_PRICE_OUTPUT_PER_1K", raising=False)

    result = chat(
        [{"role": "user", "content": "hi"}],
        model="qwen-plus",
        client=FakeClient(fake_response()),
    )

    assert result.content == '{"ok": true}'
    assert result.model == "qwen-plus"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.latency_ms >= 0
    assert result.cost_estimate is None


def test_chat_estimates_cost_when_prices_configured(monkeypatch):
    """配了单价时，成本 = 输入 token × 输入单价 + 输出 token × 输出单价。"""
    monkeypatch.setenv("MODEL_PRICE_INPUT_PER_1K", "0.004")
    monkeypatch.setenv("MODEL_PRICE_OUTPUT_PER_1K", "0.012")

    result = chat([{"role": "user", "content": "hi"}], client=FakeClient(fake_response()))

    assert result.cost_estimate == pytest.approx(0.0001)


def test_chat_keeps_tokens_none_when_usage_missing():
    """供应商没返回 usage 时记 None，不能记成 0。"""
    result = chat(
        [{"role": "user", "content": "hi"}],
        client=FakeClient(fake_response(with_usage=False)),
    )

    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.cost_estimate is None


def test_chat_marks_400_non_retryable():
    """参数错误再试也没用。"""
    with pytest.raises(LLMError) as caught:
        chat(
            [{"role": "user", "content": "hi"}],
            client=FakeClient(error=FakeStatusError(400)),
        )

    assert caught.value.retryable is False


def test_chat_marks_429_retryable():
    """限流值得退避后重试。"""
    with pytest.raises(LLMError) as caught:
        chat(
            [{"role": "user", "content": "hi"}],
            client=FakeClient(error=FakeStatusError(429)),
        )

    assert caught.value.retryable is True


def test_chat_omits_response_format_when_none():
    """没要求 JSON 时不要把 response_format 发出去。"""
    client = FakeClient(fake_response())

    chat([{"role": "user", "content": "hi"}], client=client)

    assert "response_format" not in client.completions.calls[0]


def test_chat_passes_response_format_and_limits():
    """显式传入的参数要原样到达模型调用。"""
    client = FakeClient(fake_response())

    chat(
        [{"role": "user", "content": "hi"}],
        model="qwen-plus",
        response_format={"type": "json_object"},
        max_tokens=32,
        timeout=3.0,
        client=client,
    )

    call = client.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_tokens"] == 32
    assert call["timeout"] == 3.0
    assert call["model"] == "qwen-plus"
    assert call["extra_body"] == {"enable_thinking": False}