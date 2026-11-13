"""外部 MCP 工具演示：openFDA 药品说明书 + PubMed 文献检索。

用法：
    PYTHONPATH=. uv run python -m eval.external_tools_demo
需要 .env 里 MCP_ENABLED=1 与 MCP_SERVERS；默认用托管 HTTP 端点。
"""

import os

os.environ.setdefault("TRACE_SINK", "none")

from backend.app.agent.opening import route_general  # noqa: E402
from backend.app.agent.state import ConsultationState  # noqa: E402
from backend.app.tools.mcp_client import (  # noqa: E402
    load_external_tools,
    shutdown_external_tools,
)

QUESTIONS = (
    "布洛芬的说明书里有什么警示？",
    "布洛芬有没有召回？",
    "卡铂短缺吗？",
    "布洛芬 FDA 批准了吗？",
    "布洛芬有什么不良反应？",
    "有没有关于 community acquired pneumonia 的临床试验？",
    "NCT02205112 的详情",
    "PubMed 上关于 community acquired pneumonia 的研究",
    "PMID 40679934 的摘要",
    "高血压是什么病？",
)


def main() -> None:
    specs = load_external_tools()
    if not specs:
        print("未注册外部工具：检查 .env 的 MCP_ENABLED / MCP_SERVERS")
        return

    print("已注册：" + "、".join(spec.name for spec in specs))
    try:
        for question in QUESTIONS:
            result = route_general(ConsultationState(latest_user_message=question))
            print(f"\n问题：{question}")
            print(result.get("rendered_text", "（未走外部路径）"))
    finally:
        shutdown_external_tools()


if __name__ == "__main__":
    main()
