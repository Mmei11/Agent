"""Day 13 会话存储测试：恢复、隔离、关闭再启动。案例均为虚构。"""

import os
import tempfile

from backend.app.agent.graph import build_graph
from backend.app.storage.saver import get_saver


def _make_saver(tmp_path):
    """用临时目录创建 saver，测试完自动清理。"""
    db_path = os.path.join(tmp_path, "test_consultations.sqlite")
    return get_saver(db_path), db_path


def test_same_thread_recovers_state(tmp_path):
    """同一 thread_id 连续两次 invoke：第二次能看到第一次收集的字段。"""
    saver, _ = _make_saver(str(tmp_path))
    with saver as s:
        app = build_graph(checkpointer=s)
        config = {"configurable": {"thread_id": "test-1"}}

        # 第一轮：只说症状
        r1 = app.invoke({"latest_user_message": "头部不适"}, config=config)
        assert "头部不适" in str(r1["collected_facts"])

        # 第二轮：补充时间（同一 thread_id）
        r2 = app.invoke(
            {"latest_user_message": "前两天开始的"},
            config=config,
        )
        # 第二轮应该看到第一轮的"头部不适"
        assert "头部不适" in str(r2["collected_facts"])
        # 以及第二轮补充的"两天"
        assert "两天" in str(r2["collected_facts"])


def test_different_threads_isolated(tmp_path):
    """不同 thread_id 互不污染：会话2 不该有会话1 的信息。"""
    saver, _ = _make_saver(str(tmp_path))
    with saver as s:
        app = build_graph(checkpointer=s)

        # 会话1
        r1 = app.invoke(
            {"latest_user_message": "头部不适"},
            config={"configurable": {"thread_id": "session-a"}},
        )
        assert "头部不适" in str(r1["collected_facts"])

        # 会话2
        r2 = app.invoke(
            {"latest_user_message": "腹痛三天"},
            config={"configurable": {"thread_id": "session-b"}},
        )
        # 会话2 不该有会话1 的"头部不适"
        assert "头部不适" not in str(r2["collected_facts"])
        assert "腹痛" in str(r2["collected_facts"])


def test_close_and_reopen_recovers(tmp_path):
    """关闭 saver 再创建新 saver，同一 thread_id 状态从 SQLite 恢复。"""
    db_path = os.path.join(str(tmp_path), "reopen_test.sqlite")

    # 第一次：创建会话，存入"头部不适"
    saver1 = get_saver(db_path)
    with saver1 as s:
        app = build_graph(checkpointer=s)
        app.invoke(
            {"latest_user_message": "头部不适"},
            config={"configurable": {"thread_id": "persist-1"}},
        )

    # 第二次：新建 saver（模拟程序重启），读回状态
    saver2 = get_saver(db_path)
    with saver2 as s:
        app = build_graph(checkpointer=s)
        r = app.invoke(
            {"latest_user_message": "前两天开始的"},
            config={"configurable": {"thread_id": "persist-1"}},
        )
        # 新 saver 从 SQLite 读回了"头部不适"——会话恢复成功
        assert "头部不适" in str(r["collected_facts"])
        assert "两天" in str(r["collected_facts"])
