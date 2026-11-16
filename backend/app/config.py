"""Phase 2 第三课：统一的本地环境变量加载。

原来只有模型客户端会读 .env，观测相关的开关（TRACE_SINK / LANGFUSE_*）
放在 .env 里不会生效。这里把白名单集中起来，任何模块都能安全读取。
"""

import os
from pathlib import Path

# 只允许这些变量进入进程环境，避免把 .env 里无关的内容带进来。
ALLOWED_NAMES = {
    # 模型
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_MODEL",
    "MODEL_PRICE_INPUT_PER_1K",
    "MODEL_PRICE_OUTPUT_PER_1K",
    # judge 用独立模型与单价：判分模型可以比业务模型更强、更贵。
    "JUDGE_MODEL",
    "JUDGE_PRICE_INPUT_PER_1K",
    "JUDGE_PRICE_OUTPUT_PER_1K",
    # 向量化（Phase 4）：与 chat 共用 DASHSCOPE_API_KEY / BASE_URL。
    "EMBEDDING_PROVIDER",
    "DASHSCOPE_EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_BATCH_SIZE",
    "EMBEDDING_PRICE_PER_1K",
    # 知识库向量存储（Phase 4）。留空则用 docker-compose.knowledge.yml 的默认地址。
    "KNOWLEDGE_DB_URL",
    # 运行开关
    "USE_MODEL",
    "SUMMARY_ENABLED",
    "CONSULTATION_DB_PATH",
    # Phase 6：图线程池大小（同时在跑的问诊轮次上限）
    "GRAPH_MAX_WORKERS",
    # Phase 6 L3：鉴权。required 时必须带 X-API-Key；API_KEYS 逗号分隔、支持轮换。
    "AUTH_MODE",
    "API_KEYS",
    # Phase 6 L3.2：令牌桶限流。按分钟配置速率，突发容量单独配置。
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_BURST",
    "SESSION_RATE_LIMIT_PER_MINUTE",
    "SESSION_BURST",
    "GLOBAL_RATE_LIMIT_PER_MINUTE",
    "GLOBAL_BURST",
    # Phase 6 L3.3：资源边界。请求体、单条消息、每个调用方的并发流数量。
    "MAX_BODY_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_STREAMS_PER_CLIENT",
    # Phase 6 L4：反馈评分落盘路径（Langfuse 不可用时也能离线统计）。
    "SCORE_LOG_PATH",
    # Phase 5 安全
    "SAFETY_POLICY_PATH",
    "AUDIT_LOG_PATH",
    # Phase C：MCP 外部工具。默认关闭；白名单为空等于全部拒绝。
    "MCP_ENABLED",
    "MCP_SERVERS",
    # Phase D：技能层。SKILLS_ENABLED=0 时只保留 default 技能。
    "SKILLS_ENABLED",
    "SKILLS_DIR",
    "SKILL_ROUTER_MODE",
    "PROMPTS_DIR",
    # Phase F：隐私与数据保留
    "DATA_RETENTION_DAYS",
    "PRIVACY_CONSENT_REQUIRED",
    # 本地轨迹
    "TRACE_SINK",
    "TRACE_PATH",
    # Langfuse（可选）
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
}


_loaded = False


def load_env_file() -> None:
    """读取项目根目录的 .env；终端已有同名变量时以终端为准。"""
    global _loaded
    if _loaded:
        return
    _loaded = True

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in ALLOWED_NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # setdefault：显式 export 的变量优先，.env 只做兜底。
        os.environ.setdefault(name, value)