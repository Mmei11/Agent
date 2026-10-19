"""Day 11 风险门控测试：否定句、含糊表述、最终边界检查。案例均为虚构。"""

from backend.app.safety.rule_engine import final_check, screen


def test_risk_keyword_hit():
    """肯定句命中风险词。"""
    result = screen("模拟人物突然呼吸困难")
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


def test_safe_message():
    """普通消息：安全。"""
    result = screen("头部不适两天，前额，程度轻")
    assert result["level"] == "safe"


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
