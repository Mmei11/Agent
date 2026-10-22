"""Day 11：风险门控与输出边界检查。规则版本，不接模型。案例均为虚构。"""

# 预设风险词：命中直接走固定风险分支
RISK_KEYWORDS = [
    "呼吸困难",
    "剧烈胸痛",
    "胸口剧烈疼痛",
    "胸口剧烈痛",
    "意识丧失",
    "意识不清",
    "大量出血不止",
]

# 含糊表述词：不直接命中，标记为不确定，保守提示
VAGUE_RISK_KEYWORDS = [
    "有点喘",
    "好像有点喘",
    "胸闷",
    "喘不上气",
    "不太对劲",
    "无法描述",
]

# 中文否定词：出现在风险词前面时，说明用户在否定这个症状
NEGATION_WORDS = ["没有", "无", "不", "没", "不是", "并未", "未曾"]

# 否定词检查范围：风险词前面取多少个字来检查否定
NEGATION_WINDOW = 5


def check_negation(text: str, keyword: str) -> bool:
    """检查风险词前面是否有否定词。有否定返回 True。"""
    pos = text.find(keyword)  # 找到风险词的位置
    if pos == -1:
        return False  # 文本里没有这个风险词

    # 取风险词前面的文字，最多取 NEGATION_WINDOW 个字
    start = max(0, pos - NEGATION_WINDOW)
    before = text[start:pos]  # 风险词前面的文字片段

    # 检查这段文字里有没有否定词
    for neg in NEGATION_WORDS:
        if neg in before:
            return True  # 前面有否定词 → 用户在否定这个症状
    return False


def screen(message: str) -> dict:
    """对一条用户消息做风险检查。

    返回：
        {"level": "risk", "rule": "R001", "reason": ...}  命中风险
        {"level": "vague", "rule": "V001", "reason": ...} 含糊表述，保守提示
        {"level": "safe", "rule": None, "reason": None}    安全
    """
    # 第一层：检查预设风险词
    for keyword in RISK_KEYWORDS:
        if keyword in message:
            if check_negation(message, keyword):
                # 否定句：用户说"没有呼吸困难"，不命中风险
                continue
            return {
                "level": "risk",
                "rule": "R001",
                "reason": f"命中风险词：{keyword}",
            }

    # 第二层：检查含糊表述词
    for keyword in VAGUE_RISK_KEYWORDS:
        if keyword in message:
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

    return {"level": "safe", "rule": None, "reason": None}
