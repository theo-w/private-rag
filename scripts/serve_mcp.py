"""启动 MCP server（stdio）：把知识库检索与问答暴露成 MCP tools。

与 CLI / HTTP 同一套装配（`build_engine` → `RagEngine`），检索、精排、生成一条链路
都不重写。对外只有两个只读工具：`kb_search`（只检索）、`ask`（检索 + 作答）。
没有入库/写库入口，也没有运行时 SQL——外部客户端读到的只有已入库的内容。

注册进客户端：
    claude mcp add private-rag -- python3 scripts/serve_mcp.py --no-rerank
    # Claude Desktop 的 mcpServers 配置片段见 README「MCP 暴露」一节

三个必须知道的边界：
- stdout 是协议通道，日志与启动提示一律走 stderr，别往 stdout 打。
- 本地模式 Qdrant 同一路径只允许一个进程打开：这个 server 与 scripts/serve.py
  （或任何打开同一 storage/ 的进程）不能同时跑；打不开时进程退出码 3 并给出原因。
- 没有鉴权：能启动这个进程的人就能读知识库，与本机 HTTP 服务的无鉴权同级。
"""

from __future__ import annotations

import argparse
import json
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
    from private_rag.config import TOP_K_RERANK

    parser = argparse.ArgumentParser(
        description="启动 private-rag MCP server（stdio，供 Claude Desktop / Claude Code 等客户端）"
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=TOP_K_RERANK,
        help=f"工具参数 limit 的默认值：一次返回几段证据（默认 {TOP_K_RERANK}）",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="跳过 reranker 加载，直接使用融合结果（启动更快、占用更低）",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug"],
        help="日志级别（日志只走 stderr）",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="只打印 tools/list 的工具定义（stdout，JSON）后退出：不打开知识库，也不进入协议会话",
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

    from private_rag.mcp.server import MCPServer
    from private_rag.mcp.tools import build_mcp_tools, failure_reason

    if args.list_tools:
        # 诊断模式：不注入引擎，因此不碰 storage/，也不需要 Ollama 在跑
        definitions = [tool.definition() for tool in build_mcp_tools(None, default_limit=args.top_k)]
        print(json.dumps(definitions, ensure_ascii=False, indent=2))
        return 0

    from private_rag.qa import build_engine

    try:
        engine = build_engine(use_rerank=False if args.no_rerank else None)
    except Exception as exc:
        # 本地模式打不开 Qdrant 多半是被 serve.py 占着，说明要写到能照着做的下一步
        print(f"❌ 知识库打不开：{failure_reason(exc)}", file=sys.stderr)
        return 3

    server = MCPServer(build_mcp_tools(engine, default_limit=args.top_k))
    print(
        f"🔌 private-rag MCP server（stdio）已启动，工具：{', '.join(server.tool_names)}；"
        "stdout 为协议通道，本地模式 Qdrant 请勿与 serve.py 同时打开同一 storage/",
        file=sys.stderr,
    )
    try:
        return server.serve_stdio()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
