"""Day 11 风险门控测试：否定句、含糊表述、最终边界检查。案例均为虚构。"""

from backend.app.safety.rule_engine import final_check, screen


def test_risk_keyword_hit():
    """肯定句命中风险词。"""
    result = screen("模拟人物突然呼吸困难")
    assert result["level"] == "risk"
    assert result["rule"] == "R001"


def test_day18_risk_phrase_variants_route():
    """Day 18：评测集中的胸痛、意识不清和大量出血表达走风险分支。"""
    for message in [
        "模拟人物突然胸口剧烈疼痛",
        "模拟人物突然意识不清",
        "模拟人物大量出血不止",
    ]:
        result = screen(message)
        assert result["level"] == "risk"
        assert result["rule"] == "R001"


def test_negation_not_risk():
    """否定句不命中：'没有呼吸困难' 前面有否定词。"""
    result = screen("模拟人物没有呼吸困难")
    assert result["level"] == "safe"


def test_negation_at_sentence_start():
    """否定在句首：'无呼吸困难' 也不命中。"""
    result = screen("无呼吸困难，只是想了解演示流程")
    assert result["level"] == "safe"


def test_vague_expression():
    """含糊表述：'有点喘' 标记为 vague，保守提示。"""
    result = screen("模拟人物好像有点喘")
    assert result["level"] == "vague"
    assert result["rule"] == "V001"


def test_day18_vague_description_routes_conservatively():
    """Day 18：难以描述的突发不适按含糊输入保守分流。"""
    result = screen("似乎突然不太对劲，但无法描述")
    assert result["level"] == "vague"
    assert result["rule"] == "V001"


def test_safe_message():
    """普通消息：安全。"""
    result = screen("头部不适两天，前额，程度轻")
    assert result["level"] == "safe"


def test_cardiopulmonary_variants_route_to_risk():
    """心肺急症的常见口语表达也要命中风险。"""
    for message in [
        "我心脏现在爆痛",
        "我有点呼吸停止的症状",
        "胸口有一阵剧痛",
        "不能呼吸",
        "无法呼吸",
        "呼吸很费力",
        "心脏痛",
    ]:
        result = screen(message)
        assert result["level"] == "risk", message
        assert result["rule"] == "R001"


def test_cardiopulmonary_negation_and_unrelated_stay_safe():
    """否定或不相关的“爆痛”不能误报。"""
    for message in [
        "心脏没有爆痛",
        "呼吸没有停止",
        "没有胸痛",
        "牙爆痛",
    ]:
        result = screen(message)
        assert result["level"] == "safe", message


def test_final_check_blocks_risk_word():
    """最终输出包含风险词：拦截。"""
    result = final_check("根据情况，可能是呼吸困难")
    assert result["level"] == "risk"
    assert result["rule"] == "F001"


def test_final_check_blocks_boundary_content():
    """最终输出包含'确诊'：拦截。"""
    result = final_check("确诊为普通感冒")
    assert result["level"] == "boundary"
    assert result["rule"] == "F002"


def test_final_check_blocks_prescription():
    """最终输出包含'处方'：拦截。"""
    result = final_check("建议处方：某药物")
    assert result["level"] == "boundary"
    assert result["rule"] == "F002"


def test_final_check_safe_output():
    """正常输出：通过。"""
    result = final_check("建议方向：神经内科")
    assert result["level"] == "safe"


def test_spaced_risk_keyword_still_hits():
    """空格拆词不能绕过：'呼吸 困难' 仍命中风险。"""
    assert screen("呼吸 困难，胸口剧 烈疼痛")["level"] == "risk"


def test_double_negation_is_positive():
    """双重否定等于肯定：'我不是没有呼吸困难' 必须命中风险。"""
    assert screen("我不是没有呼吸困难")["level"] == "risk"


def test_dosage_unit_is_boundary_violation():
    """剂量数字加单位，即使没有“剂量”二字也算越界。"""
    assert final_check("布洛芬 400mg，每天两次")["rule"] == "F002"
