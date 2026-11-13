"""Phase D：技能包、注册表与规则路由。"""

import pytest
import yaml

from backend.app.agent.decision import Decision, validate_decision
from backend.app.agent.decision_llm import build_context
from backend.app.agent.followup import followup
from backend.app.agent.intake import extract_facts
from backend.app.agent.result import build_completed_result
from backend.app.agent.skill_route import skill_route
from backend.app.agent.state import ConsultationState
from backend.app.skills import registry
from backend.app.skills.registry import (
    DEFAULT_NAME,
    list_skill_summaries,
    load_skill,
    repo_root,
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


def test_skill_route_recomputes_missing_by_skill():
    state = ConsultationState(
        latest_user_message="孩子三岁，发烧三十八度五",
        collected_facts={"主要症状": ["发热"], "年龄": ["三岁"], "测量值": ["三十八度五"]},
    )

    result = skill_route(state)

    assert result["skill"] == "pediatric_fever"
    assert "部位" not in result["missing_fields"]
    assert "持续时间" in result["missing_fields"]
    assert "精神状态" in result["missing_fields"]
    assert result["trace"][0]["source"] == "router"


def test_skill_route_is_sticky_once_selected():
    state = ConsultationState(
        latest_user_message="孩子发烧", skill="headache", collected_facts={}
    )

    result = skill_route(state)

    assert result["skill"] == "headache"
    assert result["trace"][0]["source"] == "sticky"


def test_followup_uses_skill_priority_and_template():
    state = ConsultationState(
        skill="pediatric_fever",
        missing_fields=["持续时间", "年龄", "测量值"],
        asked_questions=[],
        collected_facts={},
    )

    result = followup(state)

    assert result["trace"][0]["skill"] == "pediatric_fever"
    assert result["trace"][0]["selected_field"] == "年龄"
    assert result["conversation"][0]["text"] == "孩子多大了，是几个月还是几岁？"
    assert result["suggested_replies"]


def test_intake_extracts_age_and_mental_status():
    facts = extract_facts("孩子三岁，精神还可以")

    assert "年龄" in facts
    assert "精神状态" in facts


def test_graph_routes_pediatric_fever_into_skill_flow():
    from backend.app.agent.graph import build_graph

    app = build_graph()
    result = app.invoke(
        {"latest_user_message": "孩子三岁，发烧三十八度五，精神还可以"}
    )

    assert result["skill"] == "pediatric_fever"
    assert result["next_action"] == "ASK_FOLLOWUP"
    assert "持续多久" in result["conversation"][-1]["text"]


def test_disabled_skills_graph_uses_default(monkeypatch):
    from backend.app.agent.graph import build_graph

    monkeypatch.setenv("SKILLS_ENABLED", "0")
    reset_skills_cache()

    app = build_graph()
    result = app.invoke({"latest_user_message": "孩子三岁，发烧三十八度五"})

    assert result["skill"] == DEFAULT_NAME


def _install_limited_skill(tmp_path, monkeypatch, tools):
    payload = yaml.safe_load(
        (repo_root() / "skills/default/manifest.yaml").read_text(encoding="utf-8")
    )
    payload.update(
        {
            "name": "limited",
            "title": "限制工具",
            "description": "测试工具白名单",
            "priority": 5,
            "when_to_use": ["限制"],
            "tools": tools,
        }
    )
    skill_dir = tmp_path / "limited"
    skill_dir.mkdir()
    (skill_dir / "manifest.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )
    (skill_dir / "SKILL.md").write_text("# 限制工具\n", encoding="utf-8")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    reset_skills_cache()


def test_decision_prompt_only_lists_skill_tools(tmp_path, monkeypatch):
    _install_limited_skill(tmp_path, monkeypatch, ["guideline_retrieve"])
    state = ConsultationState(
        skill="limited", collected_facts={"主要症状": ["头痛"]}, missing_fields=[]
    )

    context = build_context(state, max_followups=3)

    assert [entry["name"] for entry in context["tools"]] == ["guideline_retrieve"]


def test_decision_rejects_tool_outside_whitelist(tmp_path, monkeypatch):
    _install_limited_skill(tmp_path, monkeypatch, ["guideline_retrieve"])
    state = ConsultationState(
        skill="limited",
        collected_facts={"主要症状": ["头痛"]},
        missing_fields=[],
        followup_count=0,
    )
    decision = Decision(
        action="CALL_TOOL",
        reason="测试",
        confidence=0.9,
        tool_name="department_route",
        tool_args={},
    )

    final = validate_decision(decision, state, max_followups=3)

    assert final.tool_name == "guideline_retrieve"


def test_skill_output_template_is_used():
    skill = load_skill("pediatric_fever")

    result = build_completed_result(
        collected_facts={"年龄": ["三岁"]},
        missing_fields=[],
        tool_results={"department_route": {"department": "儿科"}},
        output_template=skill.manifest.output_template,
    )

    assert result["next_step"].startswith("建议就诊方向：儿科")
    assert "体温记录" in result["next_step"]


def test_default_output_template_stays_unchanged():
    result = build_completed_result(
        collected_facts={},
        missing_fields=[],
        tool_results={"department_route": {"department": "神经内科"}},
    )

    assert result["next_step"] == "建议就诊方向：神经内科"
