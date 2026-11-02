"""Phase 5 L3：输出层 guardrails 自定义校验器。

四个校验器分别回答四个问题：
- 有没有下诊断结论？
- 有没有给药物/处理建议或劝阻就医？
- 引用了知识，有没有给来源？
- 有没有免责声明？
"""

import re

from guardrails.validators import FailResult, PassResult, Validator, register_validator

SAFE_FALLBACK = (
    "本系统不能提供诊断或治疗建议，请尽快咨询专业医生。"
    "本内容仅供健康参考，不能替代专业医生诊断。"
)

# 系统自己的免责声明里本来就含“确诊/处方/用药”，检查正文前先把它摘掉。
KNOWN_DISCLAIMERS = (
    "本系统不提供确诊、处方或用药建议，请咨询专业人员。",
    "本内容仅供健康参考，不能替代专业医生诊断。",
)

DIAGNOSIS_PATTERNS = (
    re.compile(
        r"(?:确诊|诊断为|诊断是|可以确定是|确定是|肯定是|就是)"
        r"(?:患|得)?[^。；\n]{0,10}(?:病|炎|症|癌|瘤|感染|感冒|流感|胃炎|肺炎|冠心病)"
    ),
    re.compile(
        r"(?:你|患者|病人|他|她)(?:得|患)(?:了)?[^。；\n]{0,8}(?:病|炎|症|癌|瘤|感染)"
    ),
    re.compile(r"(?:患上?|得了)[^。；\n]{0,6}(?:病|炎|症|感冒|流感)"),
)

TREATMENT_PATTERNS = (
    re.compile(
        r"(?:处方|剂量|停药|减量|加量|用药|吃药|服药|服用|口服|含服|外敷|涂抹|"
        r"输液|点滴|消炎药|抗生素|止痛药|退烧药|感冒药|布洛芬|阿司匹林|头孢)"
    ),
    re.compile(
        r"(?:吃|服|用|买|开)(?:点|些|一点)?[^。；\n]{0,6}(?:药|片|胶囊|颗粒|冲剂|口服液)"
    ),
    re.compile(
        r"(?:每次|每日|一天|一日)[^。；\n]{0,6}"
        r"[一二两三四五六七八九十\d]+\s*(?:片|粒|袋|毫升|ml|mg|克|次)"
    ),
    re.compile(r"(?:不用|不必|无需|没必要).{0,6}(?:去医院|就医|看医生|就诊)"),
    re.compile(r"(?:自己|在家).{0,4}(?:扛|忍|观察)"),
)


def strip_disclaimers(text: str) -> str:
    for disclaimer in KNOWN_DISCLAIMERS:
        text = text.replace(disclaimer, "")
    return text


@register_validator(name="consultation-no-diagnosis", data_type="string")
class NoDiagnosis(Validator):
    def __init__(self, on_fail=None):
        super().__init__(on_fail=on_fail)

    def _validate(self, value, metadata):
        for pattern in DIAGNOSIS_PATTERNS:
            match = pattern.search(strip_disclaimers(value))
            if match:
                return FailResult(
                    error_message=f"包含诊断结论：{match.group(0)}",
                    fix_value=SAFE_FALLBACK,
                )
        return PassResult()


@register_validator(name="consultation-no-treatment-advice", data_type="string")
class NoTreatmentAdvice(Validator):
    def __init__(self, on_fail=None):
        super().__init__(on_fail=on_fail)

    def _validate(self, value, metadata):
        for pattern in TREATMENT_PATTERNS:
            match = pattern.search(strip_disclaimers(value))
            if match:
                return FailResult(
                    error_message=f"包含药物或处理建议：{match.group(0)}",
                    fix_value=SAFE_FALLBACK,
                )
        return PassResult()


@register_validator(name="consultation-citation", data_type="string")
class CitationRequired(Validator):
    def __init__(self, required: bool = False, on_fail=None):
        super().__init__(on_fail=on_fail)
        self.required = required

    def _validate(self, value, metadata):
        required = bool((metadata or {}).get("uses_knowledge", self.required))
        if not required:
            return PassResult()
        if any(marker in value for marker in ("来源", "依据", "出处")):
            return PassResult()
        return FailResult(error_message="引用知识但缺少来源", fix_value=SAFE_FALLBACK)


@register_validator(name="consultation-disclaimer", data_type="string")
class DisclaimerRequired(Validator):
    def __init__(self, on_fail=None):
        super().__init__(on_fail=on_fail)

    def _validate(self, value, metadata):
        markers = ("不能替代专业医生", "仅供健康参考", "不提供确诊、处方")
        if any(marker in value for marker in markers):
            return PassResult()
        return FailResult(error_message="缺少免责声明", fix_value=SAFE_FALLBACK)
