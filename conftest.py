"""测试默认不依赖向量库与外部模型。

图与评测默认用 stub_call_tool 跑流程；要验证真实检索时显式设置
KNOWLEDGE_BACKEND=pgvector 再跑。
"""

import os

os.environ.setdefault("KNOWLEDGE_BACKEND", "stub")