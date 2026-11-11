"""Phase B：上下文预算。

先用字符预算，不引 tokenizer：目标是“防止某一段无限膨胀”，
不是精确复现模型 token。真实 token 由 trace 的 `gen_ai.usage.*` 统计。
"""

# 回答节点：最近对话
ANSWER_RECENT_ITEMS = 6
ANSWER_RECENT_CHARS = 1200

# 回答节点：会话记忆卡
ANSWER_MEMORY_ITEMS = 8
ANSWER_MEMORY_CHARS = 600

# 参考上下文（intake / decision）：只用来理解指代，不当作新事实
REFERENCE_MEMORY_ITEMS = 4
REFERENCE_MEMORY_CHARS = 300
REFERENCE_RECENT_ITEMS = 4
REFERENCE_RECENT_CHARS = 800
