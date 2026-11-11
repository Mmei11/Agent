"""L1：OTC 药品说明书查询边界。案例均为虚构演示数据。"""

import re
from types import SimpleNamespace

import pytest

from backend.app.agent import answer as answer_module
from backend.app.agent.state import ConsultationState
from backend.app.drugs.lookup import find_label, load_labels, render_label, reply_for_message
from backend.app.llm.client import LLMError

DOSAGE_PATTERN = re.compile(r"\d+\s*(mg|毫克|g|克|ml|毫升|片|粒|袋|次)", re.IGNORECASE)


def make_state(message: str) -> ConsultationState:
    return ConsultationState(
        session_id="drug-session",
        turn_id=2,
        latest_user_message=message,
        turn_kind="followup",
        collected_facts={"主要症状": ["头痛"]},
        final_result={
            "status": "completed",
            "next_step": "建议就诊方向：神经内科",
            "source_note": "来源：科室分诊通用原则（未核实）",
        },
        conversation=[
            {"role": "user", "text": "头痛两天"},
            {"role": "agent", "text": "建议就诊方向：神经内科"},
        ],
    )


def test_all_seed_labels_are_otc():
    labels = load_labels()
    assert labels
    assert all(label.otc for label in labels)


def test_label_text_has_no_dosage():
    for label in load_labels():
        text = render_label(label)
        assert "每次" not in text
        assert "每日" not in text
        assert not DOSAGE_PATTERN.search(text)


def test_find_by_generic_and_brand():
    assert find_label("布洛芬有什么禁忌").id == "ibuprofen-er"
    assert find_label("芬必得说明书").id == "ibuprofen-er"
    assert find_label("氯雷他定副作用").id == "loratadine"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("达喜说明书", "hydrotalcite"),
        ("感康能吃吗", "compound-cold"),
        ("快克有什么禁忌", "compound-cold"),
        ("沐舒坦副作用", "ambroxol"),
        ("乘晕宁说明书", "dimenhydrinate"),
        ("杜密克怎么用", "lactulose"),
        ("炉甘石洗剂禁忌", "calamine"),
        ("维生素C说明书", "vitamin-c"),
        ("维生素D滴剂", "vitamin-d"),
        ("西替利嗪能治过敏吗", "cetirizine"),
        ("红霉素软膏说明书", "erythromycin-ointment"),
        ("克霉唑乳膏副作用", "clotrimazole-cream"),
        ("聚乙烯醇滴眼液", "polyvinyl-alcohol-eye-drops"),
        ("扶他林乳胶剂", "diclofenac-gel"),
    ],
)
def test_more_otc_aliases(text, expected):
    label = find_label(text)
    assert label is not None
    assert label.id == expected


def test_aliases_do_not_collide():
    seen = {}
    for label in load_labels():
        for alias in {item.lower() for item in label.aliases}:
            assert alias not in seen or seen[alias] == label.id, alias
            seen[alias] = label.id


def test_known_drug_returns_label_summary():
    reply = reply_for_message("布洛芬有什么禁忌")
    assert reply.kind == "drug_label"
    assert "适应症" in reply.text
    assert "禁忌" in reply.text
    assert "注意事项" in reply.text
    assert "不良反应" in reply.text
    assert "药物相互作用" in reply.text
    assert "来源" in reply.text
    assert "未核实" in reply.text
    assert "不构成用药建议" in reply.text
    assert "不能判断是否适合你" in reply.text
    assert not DOSAGE_PATTERN.search(reply.text)


def test_recommendation_is_refused():
    reply = reply_for_message("我头疼吃什么药")
    assert reply.kind == "drug_guard"
    assert "不能根据症状推荐" in reply.text
    assert "布洛芬" not in reply.text


def test_prescription_drug_is_refused():
    reply = reply_for_message("阿莫西林能吃吗")
    assert reply.kind == "drug_guard"
    assert "处方药" in reply.text
    assert "不提供说明书查询" in reply.text


def test_unknown_drug_is_not_fabricated():
    reply = reply_for_message("某个没收录的药的说明书")
    assert reply.kind == "drug_guard"
    assert "暂未收录" in reply.text


def test_non_drug_message_returns_none():
    assert reply_for_message("那需要注意什么") is None


def test_answer_node_uses_label_template_without_model(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "0")

    def boom(state):
        raise AssertionError("药品查询不应该调用问诊模型")

    monkeypatch.setattr(answer_module, "generate_with_meta", boom)

    result = answer_module.answer_followup(make_state("布洛芬有什么禁忌"))

    assert result["trace"][0]["source"] == "drug_label"
    assert result["trace"][0]["grounded"] is True
    assert result["trace"][0]["drug_id"] == "ibuprofen-er"
    assert result["trace"][0]["drug_answer_source"] == "template"
    assert "说明书摘要" in result["rendered_text"]
    assert "适应症" in result["rendered_text"]
    assert "final_result" not in result


def test_drug_answer_uses_model_intro(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_generate(question, label):
        return (
            SimpleNamespace(
                intro="你问的是布洛芬的禁忌。是否适合你需要医生或药师结合具体情况判断。",
                sections=["contraindications", "precautions"],
            ),
            SimpleNamespace(
                model="fake",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cost_estimate=0.0,
            ),
        )

    monkeypatch.setattr(answer_module, "generate_drug_answer", fake_generate)

    result = answer_module.answer_followup(make_state("布洛芬有什么禁忌"))
    text = result["rendered_text"]

    assert text.startswith("你问的是布洛芬的禁忌")
    assert "禁忌" in text
    assert "注意事项" in text
    assert "适应症：" not in text
    assert "不良反应：" not in text
    assert "来源" in text
    assert result["trace"][0]["drug_answer_source"] == "model"


def test_drug_answer_rejects_unsafe_intro(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def unsafe(question, label):
        return (
            SimpleNamespace(intro="建议服用布洛芬 200mg，一天三次。", sections=["indications"]),
            SimpleNamespace(
                model="fake",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cost_estimate=0.0,
            ),
        )

    monkeypatch.setattr(answer_module, "generate_drug_answer", unsafe)

    result = answer_module.answer_followup(make_state("布洛芬有什么禁忌"))
    text = result["rendered_text"]

    assert "200mg" not in text
    assert "建议服用" not in text
    assert "适应症" in text  # 退回完整说明书模板
    assert result["trace"][0]["drug_answer_source"] == "template"
    assert result["trace"][0]["drug_intro_rejected"] is True


def test_drug_answer_falls_back_on_model_error(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(question, label):
        raise LLMError("down", retryable=True)

    monkeypatch.setattr(answer_module, "generate_drug_answer", boom)

    result = answer_module.answer_followup(make_state("布洛芬有什么禁忌"))

    assert result["trace"][0]["drug_answer_source"] == "template"
    assert "适应症" in result["rendered_text"]


def test_answer_node_uses_guard_without_model(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(state):
        raise AssertionError("推荐药请求不应该调用模型")

    monkeypatch.setattr(answer_module, "generate_with_meta", boom)
    monkeypatch.setattr(answer_module, "generate_drug_answer", boom)

    result = answer_module.answer_followup(make_state("我头疼吃什么药"))

    assert result["trace"][0]["source"] == "drug_guard"
    assert result["trace"][0]["grounded"] is False
    assert "不能根据症状推荐" in result["rendered_text"]
