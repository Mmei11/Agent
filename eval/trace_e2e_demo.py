"""Phase 2 第二课：跑一次多轮回诊，把真实轨迹写进 JSONL。

用法：
    PYTHONPATH=. uv run python eval/trace_e2e_demo.py
然后：
    PYTHONPATH=. uv run python eval/trace_report.py --path logs/traces-e2e.jsonl
"""

import os
import tempfile
from pathlib import Path

TRACE_PATH = Path("logs/traces-e2e.jsonl")
TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
TRACE_PATH.unlink(missing_ok=True)

# 必须在导入路由之前设置，get_sink() 和 get_app() 才会读到这些值。
os.environ["TRACE_SINK"] = "jsonl"
os.environ["TRACE_PATH"] = str(TRACE_PATH)
os.environ.setdefault("USE_MODEL", "0")
os.environ["CONSULTATION_DB_PATH"] = tempfile.mktemp(suffix=".sqlite")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api import routes  # noqa: E402


def main() -> None:
    # 重置模块级懒加载，确保用的是上面设置的临时数据库。
    routes._compiled_app = None
    routes._saver = None

    with TestClient(routes.app) as client:
        created = client.post("/sessions", json={"initial_message": "头部不适"})
        session_id = created.json()["session_id"]
        print(f"创建会话：{session_id}")

        for message in ("前两天开始的", "在前额", "程度轻，暂无其他不适，无病史"):
            response = client.post(
                f"/sessions/{session_id}/messages", json={"message": message}
            )
            payload = response.json()
            print(
                f"  发送「{message}」→ HTTP {response.status_code}，"
                f"trace_id={payload.get('trace_id', '')[:8]}，"
                f"状态={payload.get('final_result', {}).get('status', '追问中')}"
            )

    print(f"\n轨迹文件：{TRACE_PATH}")
    print(f"会话 ID：{session_id}")


if __name__ == "__main__":
    main()