"""Day 13：SqliteSaver 工厂。提供创建和获取 checkpointer 的方法。案例均为虚构。"""

import os

from langgraph.checkpoint.sqlite import SqliteSaver


DEFAULT_DB_PATH = "consultations.sqlite"  # 默认 SQLite 文件路径，已被 .gitignore 忽略


def get_saver(db_path: str | None = None):
    """返回 SqliteSaver 上下文管理器。

    使用方式：
        with get_saver() as saver:
            app = build_graph(checkpointer=saver)
            app.invoke(input, config=...)

    db_path 为 None 时用默认路径；也可传自定义路径。
    SQLite 文件被 .gitignore 忽略（*.sqlite）。
    """
    path = db_path or os.getenv("CONSULTATION_DB_PATH", DEFAULT_DB_PATH)
    return SqliteSaver.from_conn_string(path)
