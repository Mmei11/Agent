"""Day 11：风险门控与输出边界检查。规则版本，不接模型。案例均为虚构。"""

import re
import unicodedata

# 预设风险词：命中直接走固定风险分支
RISK_KEYWORDS = [
    "呼吸困难",
    "剧烈胸痛",
    "胸口剧烈疼痛",
    "胸口剧烈痛",
    "胸口剧烈疼",
    "胸口剧痛",
    "胸痛",
    "意识丧失",
    "意识不清",
    "大量出血不止",
    # 英文说法也要覆盖，避免换语言绕过。
    "difficulty breathing",
    "shortness of breath",
    "chest pain",
    "unconscious",
    "severe bleeding",
]

# 含糊表述词：不直接命中，标记为不确定，保守提示
VAGUE_RISK_KEYWORDS = [
    "有点喘",
    "好像有点喘",
    "胸闷",
    "喘不上气",
    "喘不过气",
    "呼吸急促",
    "喘得厉害",
    "上不来气",
    "不太对劲",
    "无法描述",
]

# 中文否定词：出现在风险词前面时，说明用户在否定这个症状
NEGATION_WORDS = ["没有", "无", "不", "没", "不是", "并未", "未曾"]

# 双重否定等于肯定：“我不是没有呼吸困难”不能当成否定
DOUBLE_NEGATION = ["不是没有", "并非没有", "不是不", "未尝没有", "不是没"]

# 风险词字符之间允许出现空格或标点，防止“呼吸 困难”逃逸
SEPARATORS = r"[\s　，,。.；;！!？?、·\-_/]*"

# 剂量单位：最终输出出现即视为越界
DOSAGE_PATTERN = re.compile(r"\d+\s*(mg|毫克|g|克|片|粒|ml|毫升)", re.IGNORECASE)

# 否定词检查范围：风险词前面取多少个字来检查否定
NEGATION_WINDOW = 5

# 否定只在本小句内生效，跨过标点就不再算同一个否定
CLAUSE_MARKS = "，。；！？,.;!?、\n"

# 这些词里的"不/没"是词的一部分，不是否定词
NON_NEGATION_COMPOUNDS = ("不适", "不舒服", "不得劲", "无力", "不固定")


def check_negation(text: str, keyword_or_position: str | int) -> bool:
    """检查关键词（或指定位置）之前是否有否定词。

    传关键词时取第一次出现的位置，兼容 intake_llm 的旧调用。
    """
    if isinstance(keyword_or_position, int):
        position = keyword_or_position
    else:
        position = text.find(keyword_or_position)
        if position == -1:
            return False

    # 取风险词前面的文字，最多取 NEGATION_WINDOW 个字
    start = max(0, position - NEGATION_WINDOW)
    before = text[start:position]  # 风险词前面的文字片段

    # 只保留同一小句：从最后一个标点之后开始算
    cut = max((before.rfind(mark) for mark in CLAUSE_MARKS), default=-1)
    before = before[cut + 1:]

    # 去掉"不适""不舒服"这类症状词，避免把词内的"不"当成否定
    for compound in NON_NEGATION_COMPOUNDS:
        before = before.replace(compound, "")

    # "不是没有""并非没有"是双重否定，语义为肯定
    if any(pattern in before for pattern in DOUBLE_NEGATION):
        return False

    # 检查这段文字里有没有否定词
    for neg in NEGATION_WORDS:
        if neg in before:
            return True  # 前面有否定词 → 用户在否定这个症状
    return False


def _keyword_pattern(keyword: str) -> re.Pattern:
    """字符之间允许空格和标点，防止“呼吸 困难”逃逸。"""
    return re.compile(
        SEPARATORS.join(re.escape(char) for char in keyword),
        re.IGNORECASE,
    )


def _has_keyword(text: str, keyword: str) -> bool:
    for match in _keyword_pattern(keyword).finditer(text):
        if not check_negation(text, match.start()):
            return True
    return False


def screen(message: str) -> dict:
    """对一条用户消息做风险检查。

    返回：
        {"level": "risk", "rule": "R001", "reason": ...}  命中风险
        {"level": "vague", "rule": "V001", "reason": ...} 含糊表述，保守提示
        {"level": "safe", "rule": None, "reason": None}    安全
    """
    # NFKC 统一全角/半角，lower 覆盖英文写法；位置仍可复用。
    text = unicodedata.normalize("NFKC", message).lower()

    # 第一层：检查预设风险词
    for keyword in RISK_KEYWORDS:
        if _has_keyword(text, keyword):
            return {
                "level": "risk",
                "rule": "R001",
                "reason": f"命中风险词：{keyword}",
            }

    # 第二层：检查含糊表述词
    for keyword in VAGUE_RISK_KEYWORDS:
        if _has_keyword(text, keyword):
            return {
                "level": "vague",
                "rule": "V001",
                "reason": f"含糊表述：{keyword}，如不确定请寻求专业帮助",
            }

    # 第三层：安全
    return {"level": "safe", "rule": None, "reason": None}


def final_check(output_text: str) -> dict:
    """最终输出前的检查：确保输出文本里不包含风险词或越界内容。

    这一层防止模型或工具生成的草稿里夹带风险词。
    """
    for keyword in RISK_KEYWORDS:
        if keyword in output_text:
            return {
                "level": "risk",
                "rule": "F001",
                "reason": f"最终输出包含风险词：{keyword}",
            }

    # 检查是否包含越界内容：确诊、处方、剂量、停药
    for boundary in ["确诊", "处方", "剂量", "停药"]:
        if boundary in output_text:
            return {
                "level": "boundary",
                "rule": "F002",
                "reason": f"最终输出包含越界内容：{boundary}",
            }

    # 剂量数字加单位，即使没有“剂量”二字也算越界
    if DOSAGE_PATTERN.search(output_text):
        return {
            "level": "boundary",
            "rule": "F002",
            "reason": "最终输出包含剂量数字与单位",
        }

    return {"level": "safe", "rule": None, "reason": None}
