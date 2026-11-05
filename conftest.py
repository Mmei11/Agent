"""测试默认不依赖向量库与外部模型。

图与评测默认用 stub_call_tool 跑流程；要验证真实检索时显式设置
KNOWLEDGE_BACKEND=pgvector 再跑。
"""

import os
import tempfile

os.environ.setdefault("KNOWLEDGE_BACKEND", "stub")
# 测试默认不启用鉴权；test_auth.py 会显式覆盖。
os.environ["AUTH_MODE"] = "off"
# 测试默认关闭限流；test_ratelimit.py 会显式开启。
os.environ["RATE_LIMIT_ENABLED"] = "0"
# 图测试会触发风险审计；写到临时目录，避免污染仓库里的 logs/audit.jsonl。
os.environ.setdefault(
    "AUDIT_LOG_PATH",
    os.path.join(tempfile.gettempdir(), "consultation-audit-test.jsonl"),
)