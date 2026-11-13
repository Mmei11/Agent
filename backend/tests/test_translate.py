"""外部结果中文摘要：只在 USE_MODEL=1 时调用模型，失败保留原文。"""

from backend.app.agent import translate
from backend.app.llm.client import LLMError, LLMResult


def _result(content: str) -> LLMResult:
    return LLMResult(
        content=content,
        model="qwen-plus",
        input_tokens=10,
        output_tokens=10,
        latency_ms=10,
        cost_estimate=None,
    )


def test_localize_skipped_without_model(monkeypatch):
    monkeypatch.delenv("USE_MODEL", raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("mock 模式不应调用模型")

    monkeypatch.setattr(translate, "chat", boom)

    assert translate.localize_external("English text") == "English text"


def test_localize_uses_model(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(
        translate, "chat", lambda *args, **kwargs: _result("中文摘要结果")
    )

    assert translate.localize_external("English text") == "中文摘要结果"


def test_localize_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(*args, **kwargs):
        raise LLMError("模型不可用", retryable=False)

    monkeypatch.setattr(translate, "chat", boom)

    assert translate.localize_external("English text") == "English text"


def test_localize_rejects_english_output(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(
        translate,
        "chat",
        lambda *args, **kwargs: _result("English only summary"),
    )

    assert translate.localize_external("English text") == "English text"


def test_localize_allows_shorter_chinese_summary(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    source = "FDA 召回记录（共 31 条）：这是一段很长的中文说明。"
    monkeypatch.setattr(
        translate,
        "chat",
        lambda *args, **kwargs: _result("共 31 条召回，详见来源。"),
    )

    assert translate.localize_external(source) == "共 31 条召回，详见来源。"
