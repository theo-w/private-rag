"""启动 MCP server（stdio）：把知识库检索、问答与业务库只读查询暴露成 MCP tools。

与 CLI / HTTP 同一套装配（`build_engine` → `RagEngine`），检索、精排、生成一条链路
都不重写。默认只有两个只读工具：`kb_search`（只检索）、`ask`（检索 + 作答）。
`sql_query`（业务库只读查询）要人在命令行上显式开：

    python3 scripts/serve_mcp.py --allow-sql

开开以后它仍要过两道既有门（`sources.yaml` 里对应 sql 源的 `allow_runtime_query: true`、
`sql_guard` 的只读护栏），装配与 agentic 共用 `build_tool_registry`，两个入口的授权语义一致。
没开、或配置里没授权，就只暴露两个知识库工具，并在 stderr 说清为什么。
入库/写库一律不暴露——读与写是两条不同的边界。

注册进客户端：
    claude mcp add private-rag -- python3 scripts/serve_mcp.py --no-rerank
    # Claude Desktop 的 mcpServers 配置片段见 README「MCP 暴露」一节

三个必须知道的边界：
- stdout 是协议通道，日志与启动提示一律走 stderr，别往 stdout 打。
- 本地模式 Qdrant 同一路径只允许一个进程打开：这个 server 与 scripts/serve.py
  （或任何打开同一 storage/ 的进程）不能同时跑；打不开时进程退出码 3 并给出原因。
- 没有鉴权：能启动这个进程的人就能读知识库（开了 `--allow-sql` 还能读白名单内的业务表），
  与本机 HTTP 服务的无鉴权同级。
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
    from private_rag.config import DEFAULT_SOURCES_CONFIG, TOP_K_RERANK

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
    parser.add_argument(
        "--allow-sql",
        action="store_true",
        help="额外暴露 sql_query（业务库只读查询）：仍需 sources.yaml 里对应 sql 源带 allow_runtime_query: true",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=DEFAULT_SOURCES_CONFIG,
        help=f"sql 源配置文件（默认 {DEFAULT_SOURCES_CONFIG}）",
    )
    return parser.parse_args(argv)


def build_sql_tool(engine, sources_path: Path) -> tuple[Optional[object], str]:
    """从 agentic 装配里取那颗运行时查询工具；拿不到就返回一句话原因。

    共用 `build_tool_registry` 而不是在这里另写一遍授权：表白名单、有界采样、缺配置降级
    这几套语义只有一处实现。装配失败（配置写坏、业务库连不上）不能让服务起不来——
    退回只暴露知识库工具，但必须把原因说出来，不然客户端只看到两个工具会以为不支持查库。
    """
    from private_rag.agent.runtime import build_tool_registry
    from private_rag.mcp.tools import failure_reason

    path = Path(sources_path)
    try:
        registry = build_tool_registry(engine, sources_path=path, allow_sql=True)
    except Exception as exc:
        return None, f"业务库装配失败，本次不暴露 sql_query：{failure_reason(exc)}｜配置路径：{path}"

    tool = registry.get("sql_query")
    if tool is not None:
        return tool, ""
    if not path.exists():
        return None, (
            f"sql 源配置不存在，本次不暴露 sql_query：{path}"
            "（需要查库请先写这份配置，并给对应 sql 源加 allow_runtime_query: true）"
        )
    return None, (
        f"{path} 里没有 allow_runtime_query: true 的 sql 源，本次不暴露 sql_query"
        "（需要查库请给对应 sql 源加这个开关）"
    )


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
        if args.allow_sql:
            print(
                "ℹ️ --list-tools 只列知识库两个工具：sql_query 的定义要读 sources.yaml 并连业务库取表结构，"
                "这个模式不打开它们",
                file=sys.stderr,
            )
        return 0

    from private_rag.qa import build_engine

    try:
        engine = build_engine(use_rerank=False if args.no_rerank else None)
    except Exception as exc:
        # 本地模式打不开 Qdrant 多半是被 serve.py 占着，说明要写到能照着做的下一步
        print(f"❌ 知识库打不开：{failure_reason(exc)}", file=sys.stderr)
        return 3

    sql_tool = None
    if args.allow_sql:
        sql_tool, reason = build_sql_tool(engine, args.sources)
        if reason:
            print(f"ℹ️ {reason}", file=sys.stderr)

    server = MCPServer(build_mcp_tools(engine, default_limit=args.top_k, sql_tool=sql_tool))
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
