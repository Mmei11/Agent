"""Phase D：版本化 prompt 文件。"""

import pytest

from backend.app import prompts
from backend.app.agent import answer, decision_llm, followup_llm, intake_llm, router
from backend.app.drugs import answer_llm as drug_answer


@pytest.fixture(autouse=True)
def _reset_cache():
    prompts.reset_prompt_cache()
    yield
    prompts.reset_prompt_cache()


def test_all_modules_load_prompt_from_file():
    modules = (intake_llm, decision_llm, router, followup_llm, answer, drug_answer)

    for module in modules:
        text = prompts.load_prompt(module.PROMPT_NAME, module.PROMPT_VERSION)
        constant = (
            getattr(module, "DECISION_SYSTEM_PROMPT", None)
            or getattr(module, "ANSWER_SYSTEM_PROMPT", None)
            or module.SYSTEM_PROMPT
        )
        assert text == constant
        assert text.strip()


def test_missing_prompt_raises():
    with pytest.raises(prompts.PromptNotFound):
        prompts.load_prompt("missing.prompt", "v1")


def test_invalid_prompt_name_or_version():
    with pytest.raises(ValueError):
        prompts.load_prompt("../etc/passwd", "v1")
    with pytest.raises(ValueError):
        prompts.load_prompt("intake.extract", "latest")


def test_prompts_dir_override(tmp_path, monkeypatch):
    target = tmp_path / "demo" / "prompt" / "v9.txt"
    target.parent.mkdir(parents=True)
    target.write_text("自定义 prompt\n", encoding="utf-8")
    monkeypatch.setenv("PROMPTS_DIR", str(tmp_path))
    prompts.reset_prompt_cache()

    assert prompts.load_prompt("demo.prompt", "v9") == "自定义 prompt"
