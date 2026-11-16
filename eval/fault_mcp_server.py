"""可控故障 MCP server：给可靠性回归制造真实超时。

只暴露一个 `pubmed_search_articles`。当 `FAULT_MARKER` 文件存在时，
处理函数会真的睡满 `FAULT_DELAY_S` 秒，让客户端在 1 秒超时后断开；
文件删除后恢复正常返回。用文件做开关，回归脚本可以在同一进程外
切换故障与恢复，不需要重启 server。

启动：
    FAULT_MARKER=/tmp/fault uv run python -m eval.fault_mcp_server --http --port 8799
"""

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

server = MCPServer(
    name="fault-pubmed",
    title="Fault-injection PubMed",
    description="可靠性回归用的可控故障 PubMed 替身，不提供真实医学内容。",
)

MARKER = Path(os.getenv("FAULT_MARKER", "/tmp/consultation-fault-marker"))
COUNTER = Path(os.getenv("FAULT_COUNTER", "/tmp/consultation-fault-counter"))
DELAY_S = float(os.getenv("FAULT_DELAY_S", "30"))

HEALTHY_MARKDOWN = """## PubMed Search Results

**Total Found:** 2 | **Returned:** 2

#### Exercise training and blood pressure in adults: a systematic review
**PMID:** 40000001
**Source:** J Hypertens
**Published:** 2025
**DOI:** 10.1000/reliability.1

#### Salt intake and hypertension: an umbrella review
**PMID:** 40000002
**Source:** Nutrients
**Published:** 2024
**DOI:** 10.1000/reliability.2
"""


@server.tool(
    name="pubmed_search_articles",
    description="检索 PubMed 文献题录（回归替身，返回固定内容）。",
)
async def pubmed_search_articles(
    query: Annotated[str, Field(min_length=1, description="检索词")],
    maxResults: Annotated[int, Field(ge=1, le=10)] = 3,
    summaryCount: Annotated[int, Field(ge=1, le=3)] = 3,
) -> str:
    # 每次真实到达 server 的调用都记一行，回归脚本用它证明“重试确实发生了”。
    COUNTER.parent.mkdir(parents=True, exist_ok=True)
    with COUNTER.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.time():.3f}\n")
    if MARKER.exists():
        # 用异步 sleep，避免阻塞 server 事件循环；两次重试都能被受理。
        await asyncio.sleep(DELAY_S)
    return HEALTHY_MARKDOWN


def main() -> None:
    parser = argparse.ArgumentParser(description="可靠性回归的故障 MCP server")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    if args.http:
        asyncio.run(server.run_streamable_http_async(host=args.host, port=args.port))
    else:
        asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
