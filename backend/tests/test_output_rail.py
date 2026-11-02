"""Phase 5 L3 输出层测试：诊断、药物、劝阻就医、免责声明、来源。"""

from backend.app.safety.output_rail import check
from backend.app.safety.validators import SAFE_FALLBACK

DISCLAIMER = "本系统不提供确诊、处方或用药建议，请咨询专业人员。"


def _text(core: str) -> str:
    return f"{core}\n\n{DISCLAIMER}"


def test_safe_completed_text_passes():
    text = _text("已知信息：\n主要症状：头痛\n\n建议就诊方向：神经内科")
    verdict = check(text)
    assert verdict.passed
    assert verdict.text == text
    assert verdict.violations == ()


def test_risk_message_passes():
    verdict = check(_text("检测到预设风险场景（规则 R001），请立即寻求专业帮助。"))
    assert verdict.passed


def test_explicit_diagnosis_is_blocked():
    verdict = check(_text("根据症状，确诊为普通感冒。"))
    assert not verdict.passed
    assert any("诊断结论" in item for item in verdict.violations)
    assert verdict.text == SAFE_FALLBACK


def test_paraphrased_diagnosis_is_blocked():
    verdict = check(_text("这个情况就是普通感冒，不用担心。"))
    assert not verdict.passed


def test_patient_diagnosis_is_blocked():
    verdict = check(_text("你患了胃炎，需要注意。"))
    assert not verdict.passed


def test_medication_advice_is_blocked():
    verdict = check(_text("吃点消炎药就行。"))
    assert not verdict.passed
    assert any("药物或处理建议" in item for item in verdict.violations)


def test_dosage_is_blocked():
    verdict = check(_text("可以吃布洛芬 400mg。"))
    assert not verdict.passed


def test_discouraging_care_is_blocked():
    verdict = check(_text("不用去医院，在家休息就好。"))
    assert not verdict.passed


def test_self_observation_advice_is_blocked():
    verdict = check(_text("自己在家扛两天看看。"))
    assert not verdict.passed


def test_missing_disclaimer_is_blocked():
    verdict = check("建议就诊方向：神经内科")
    assert not verdict.passed
    assert any("免责声明" in item for item in verdict.violations)


def test_citation_required_but_missing():
    verdict = check(_text("建议就诊方向：神经内科"), uses_knowledge=True)
    assert not verdict.passed
    assert any("来源" in item for item in verdict.violations)


def test_citation_present_passes():
    text = _text("建议就诊方向：神经内科\n来源：分诊原则 K001")
    verdict = check(text, uses_knowledge=True)
    assert verdict.passed


def test_citation_not_required_by_default():
    verdict = check(_text("建议就诊方向：神经内科"))
    assert verdict.passed


def test_safe_fallback_passes_itself():
    verdict = check(SAFE_FALLBACK)
    assert verdict.passed
    assert verdict.text == SAFE_FALLBACK


def test_general_rest_advice_is_allowed():
    verdict = check(_text("建议多喝水、规律休息，必要时就诊。"))
    assert verdict.passed
