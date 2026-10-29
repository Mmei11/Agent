"""Phase 3 第 4 课：追问相关性确定性判定的测试。"""

from eval.followup_check import (
    TEMPLATE_TO_FIELD,
    analyze_followups,
    score_followups,
)


def _conv(*messages: tuple[str, str]) -> list[dict]:
    return [{"role": role, "text": text} for role, text in messages]


def _score(conversation: list[dict]) -> dict:
    return score_followups(analyze_followups(conversation))


def test_all_question_templates_map_to_fields():
    assert len(TEMPLATE_TO_FIELD) == 6


def test_no_questions_scores_five():
    conversation = _conv(
        ("user", "模拟人物突然呼吸困难"),
        ("agent", "检测到预设风险场景，请立即寻求专业帮助。"),
    )

    assert _score(conversation)["score"] == 5


def test_repeated_chief_complaint_scores_three():
    conversation = _conv(
        ("user", "肚子疼，今天开始的"),
        ("agent", "请问主要的不适是什么？"),
    )

    result = _score(conversation)

    assert result["score"] == 3
    assert "重复" in result["reason"]


def test_question_before_refusal_is_valid():
    conversation = _conv(
        ("user", "头部不适"),
        ("agent", "请问这种情况持续多久了？"),
        ("user", "拒绝补充更多信息"),
        ("agent", "建议就诊方向：神经内科"),
    )

    assert _score(conversation)["score"] == 5


def test_refusal_before_same_question_scores_two():
    conversation = _conv(
        ("user", "头部不适"),
        ("agent", "请问这种情况持续多久了？"),
        ("user", "拒绝补充更多信息"),
        ("agent", "请问这种情况持续多久了？"),
    )

    assert _score(conversation)["score"] == 2


def test_misaligned_user_answer_does_not_penalize():
    conversation = _conv(
        ("user", "头部不适"),
        ("agent", "请问这种情况持续多久了？"),
        ("user", "大约两天了"),
        ("agent", "请问还有其他不适吗？"),
        ("user", "前额，程度轻"),
        ("agent", "请问有没有既往病史或正在用药？"),
        ("user", "不知道有没有其他表现"),
    )

    assert _score(conversation)["score"] == 5


def test_out_of_order_valid_questions_score_four():
    conversation = _conv(
        ("user", "乏力"),
        ("agent", "请问具体在哪个部位？"),
        ("user", "全身"),
        ("agent", "请问这种情况持续多久了？"),
    )

    assert _score(conversation)["score"] == 4


def test_field_specific_refusal_does_not_block_other_fields():
    conversation = _conv(
        ("user", "胃部不适"),
        ("agent", "请问不适的程度如何，是轻微、明显还是严重？"),
        ("user", "不知道程度"),
        ("agent", "请问这种情况持续多久了？"),
        ("user", "今天开始"),
    )

    checks = analyze_followups(conversation)
    duration = next(check for check in checks if check.field == "持续时间")

    assert duration.status == "valid"