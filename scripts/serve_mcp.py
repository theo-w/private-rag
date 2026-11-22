"""启动 MCP server（stdio）：把 kb_search / sql_query 暴露成 MCP tools。

与 HTTP 服务同一套装配（build_tool_registry）：kb_search 始终可用；sql_query 只在
sources.yaml 里 sql 源显式 `allow_runtime_query: true` 时挂上，只读护栏与表白名单照旧。
每次工具调用返回统一编号的证据块与带块级定位的引用列表。

注册进 Claude Code 示例：
    claude mcp add private-rag -- python3 scripts/serve_mcp.py --sources config/sources.demo.yaml

两个必须知道的边界：
- stdout 是协议通道，日志一律走 stderr，别往 stdout 打。
- 本地模式 Qdrant 同一路径只允许一个进程打开：这个 server 与 scripts/serve.py
  （或任何直接打开同一 storage 的进程）不能同时跑。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-rerank" in argv:
        # 必须在导入 config 之前设置；config 在导入时读取该开关
        os.environ["RAG_DISABLE_RERANK"] = "1"

    # 延迟导入：确保上面的环境变量先生效
    from private_rag.config import AGENT_SOURCES_CONFIG, AGENT_SQL_ROWS

    parser = argparse.ArgumentParser(
        description="启动 private-rag MCP server（stdio，供 Claude Code 等 MCP 客户端）"
    )
    parser.add_argument(
        "--sources",
        default=str(AGENT_SOURCES_CONFIG),
        help="sources.yaml 路径（默认 config/sources.yaml；演示配置用 config/sources.demo.yaml）",
    )
    parser.add_argument(
        "--no-sql",
        action="store_true",
        help="不挂 sql_query，即使配置里授权了",
    )
    parser.add_argument(
        "--sql-rows",
        type=int,
        default=AGENT_SQL_ROWS,
        help=f"sql_query 返回行数上限（默认 {AGENT_SQL_ROWS}）",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="跳过 reranker 加载，启动更快、占用更低",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug"],
        help="日志级别（日志只走 stderr）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    # stdout 归协议通道：所有日志与启动提示必须走 stderr
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    from private_rag.agent.runtime import build_tool_registry
    from private_rag.mcp.server import MCPServer
    from private_rag.qa import build_engine

    print(
        "🔌 private-rag MCP server（stdio）：stdout 为协议通道；"
        "本地模式 Qdrant 请勿与 serve.py 同时打开同一 storage",
        file=sys.stderr,
    )

    engine = build_engine(use_rerank=False if args.no_rerank else None)
    registry = build_tool_registry(
        engine,
        sources_path=Path(args.sources),
        sql_rows=args.sql_rows,
        allow_sql=not args.no_sql,
    )
    print(f"已暴露 MCP 工具：{', '.join(registry.names()) or '（无）'}", file=sys.stderr)

    server = MCPServer(registry)
    try:
        return server.serve_stdio()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
