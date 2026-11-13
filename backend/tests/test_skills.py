"""Phase D：技能包、注册表与规则路由。"""

import pytest

from backend.app.skills import registry
from backend.app.skills.registry import (
    DEFAULT_NAME,
    list_skill_summaries,
    load_skill,
    reset_skills_cache,
)
from backend.app.skills.router import route


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_skills_cache()
    yield
    reset_skills_cache()


def test_bundled_skills_are_listed():
    names = {summary.name for summary in list_skill_summaries()}

    assert {"default", "headache", "pediatric_fever"} <= names


def test_summaries_do_not_expose_skill_body():
    summary = next(item for item in list_skill_summaries() if item.name == "headache")

    assert summary.title == "头痛问诊"
    assert "头痛" in summary.when_to_use
    assert not hasattr(summary, "instructions")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("头痛两天了，前额一阵一阵胀痛", "headache"),
        ("脑袋嗡嗡的，从前天傍晚开始", "headache"),
        ("头晕伴恶心，转头时更明显", "headache"),
        ("孩子三岁，发烧三十八度五，精神还可以", "pediatric_fever"),
        ("宝宝一岁半，昨晚开始发热，有点咳嗽", "pediatric_fever"),
        ("小孩烧到三十九度，手脚凉", "pediatric_fever"),
    ],
)
def test_rule_router_matches_m9_samples(message, expected):
    assert route(message) == expected


def test_router_defaults_when_no_skill_matches():
    assert route("最近胃口不太好") == DEFAULT_NAME
    assert route("") == DEFAULT_NAME


def test_child_priority_beats_headache_keyword():
    assert route("孩子头痛发烧") == "pediatric_fever"


def test_skill_instructions_are_lazy_loaded(monkeypatch):
    calls = []

    def fake_read(path):
        calls.append(path)
        return ""

    monkeypatch.setattr(registry, "_read_instructions", fake_read)

    route("头痛两天")
    assert calls == []

    load_skill("headache")
    assert calls  # 只有真正加载技能时才读文件


def test_unknown_skill_falls_back_to_default():
    skill = load_skill("not_a_skill")

    assert skill.manifest.name == DEFAULT_NAME


def test_invalid_manifest_is_skipped(tmp_path, monkeypatch):
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "manifest.yaml").write_text("name: broken\ntitle: 坏了\n", encoding="utf-8")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    reset_skills_cache()

    names = {summary.name for summary in list_skill_summaries()}

    assert names == {DEFAULT_NAME}
    assert route("头痛两天") == DEFAULT_NAME


def test_disabled_skills_only_expose_default(monkeypatch):
    monkeypatch.setenv("SKILLS_ENABLED", "0")
    reset_skills_cache()

    names = {summary.name for summary in list_skill_summaries()}

    assert names == {DEFAULT_NAME}
    assert route("孩子发烧") == DEFAULT_NAME
