"""Phase 1 第二课测试：证据闸门与否定闸门。不联网、不花钱。"""

from backend.app.agent.intake_llm import keep_supported, to_field_dict
from backend.app.llm.schemas import FIELD_MAP, Fact, IntakeFacts


def fact(value: str, evidence: str | None = None, confidence: float = 0.9) -> Fact:
    return Fact(value=value, evidence=evidence or value, confidence=confidence)


def make_facts(**fields) -> IntakeFacts:
    data = {name: [] for name in FIELD_MAP}
    data.update(fields)
    return IntakeFacts(**data)


def test_keeps_supported_facts():
    """IN03：常规表达，四个字段都该保留。"""
    text = "肚子疼得厉害，已经三天了，在右下腹"
    facts = make_facts(
        chief_complaint=[fact("肚子疼")],
        duration=[fact("三天")],
        location=[fact("右下腹")],
        severity=[fact("厉害")],
    )

    result = to_field_dict(keep_supported(facts, text))

    assert result == {
        "主要症状": ["肚子疼"],
        "持续时间": ["三天"],
        "部位": ["右下腹"],
        "程度": ["厉害"],
    }


def test_drops_negated_symptom():
    """IN04：呼吸困难被否定，证据闸门会放行，否定闸门必须拦住。"""
    text = "咳嗽，有点发热，没有呼吸困难"
    facts = make_facts(
        chief_complaint=[fact("咳嗽")],
        associated=[fact("有点发热"), fact("呼吸困难")],
    )

    result = to_field_dict(keep_supported(facts, text))

    assert result == {"主要症状": ["咳嗽"], "伴随表现": ["有点发热"]}


def test_drops_negated_evidence_prefix():
    """证据整段以否定词开头时也要丢掉。"""
    text = "没有发热"
    facts = make_facts(associated=[fact("发热", evidence="没有发热")])

    assert to_field_dict(keep_supported(facts, text)) == {}


def test_drops_hallucinated_fact():
    """IN05：拒答样本里编造出的字段必须作废。"""
    text = "我不太想说我哪里不舒服"
    facts = make_facts(chief_complaint=[fact("头痛")])

    assert to_field_dict(keep_supported(facts, text)) == {}


def test_value_must_appear_in_text():
    """value 不在原文里，即使 evidence 存在也不能算事实。"""
    text = "我吃了布洛芬还是头疼"
    facts = make_facts(aggravating=[fact("缓解", evidence="吃了布洛芬")])

    assert to_field_dict(keep_supported(facts, text)) == {}


def test_medication_action_is_not_a_relief_factor():
    """“吃了布洛芬”不是加重/缓解因素，用户还说“还是头疼”。"""
    text = "我吃了布洛芬还是头疼"
    facts = make_facts(aggravating=[fact("吃了布洛芬")])

    assert to_field_dict(keep_supported(facts, text)) == {}


def test_real_factor_is_kept():
    text = "活动后加重，休息后缓解"
    facts = make_facts(
        aggravating=[fact("活动后加重"), fact("休息后缓解")]
    )

    result = to_field_dict(keep_supported(facts, text))

    assert result == {"加重缓解因素": ["活动后加重", "休息后缓解"]}


def test_keeps_multiple_values():
    """IN06：同一字段有多个表达时都要保留。"""
    text = "最近一周总觉得累，没什么力气"
    facts = make_facts(
        chief_complaint=[fact("累"), fact("没什么力气")],
        duration=[fact("最近一周")],
    )

    assert to_field_dict(keep_supported(facts, text)) == {
        "主要症状": ["累", "没什么力气"],
        "持续时间": ["最近一周"],
    }


def test_keeps_negative_background():
    """背景里的"无病史"是有效信息，不能当否定丢掉。"""
    text = "无病史，最近有点头晕"
    facts = make_facts(background=[fact("无病史")])

    assert to_field_dict(keep_supported(facts, text)) == {"已知背景": ["无病史"]}


def test_symptom_word_with_negation_char_is_not_negated():
    """'头部不适'里的'不'是症状用词，不能把后面的'两天'判成否定。"""
    text = "头部不适两天，前额，程度轻"
    facts = make_facts(duration=[fact("两天", evidence="头部不适两天")])

    assert to_field_dict(keep_supported(facts, text)) == {"持续时间": ["两天"]}


def test_negation_does_not_cross_punctuation():
    """IN21：前一句的"没有发热"不能把下一句的咳嗽判成否定。"""
    text = "没有发热，就是一直咳嗽，两周了"
    facts = make_facts(chief_complaint=[fact("咳嗽", evidence="就是一直咳嗽")])

    assert to_field_dict(keep_supported(facts, text)) == {"主要症状": ["咳嗽"]}


def test_dedupes_same_value():
    """模型重复输出同一个值时只保留一份。"""
    text = "头痛三天"
    facts = make_facts(chief_complaint=[fact("头痛"), fact("头痛")])

    assert to_field_dict(keep_supported(facts, text)) == {"主要症状": ["头痛"]}