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
    # 运行开关
    "USE_MODEL",
    "CONSULTATION_DB_PATH",
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