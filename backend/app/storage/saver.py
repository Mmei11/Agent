"""Day 13：SqliteSaver 工厂。提供创建和获取 checkpointer 的方法。案例均为虚构。"""

import os
import sqlite3

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


def create_saver(db_path: str | None = None) -> SqliteSaver:
    """创建一个独占连接的 saver，供每个工作线程各持一份。

    WHY 不用共享连接：SqliteSaver 内部有一把 threading.Lock，所有线程的
    checkpoint 读写都会排在这把锁上。每线程一个连接可以保留 SQLite 自己的
    读并发（WAL 下读不阻塞写），写仍然由数据库串行化。

    busy_timeout 让并发写在锁冲突时等待而不是立刻抛 database is locked。
    """
    path = db_path or os.getenv("CONSULTATION_DB_PATH", DEFAULT_DB_PATH)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return SqliteSaver(conn)
