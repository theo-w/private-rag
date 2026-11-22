"""MCP 暴露：把知识库检索与问答经 stdio 暴露给外部 MCP 客户端（只读）。

协议实现与工具层分开：`server.py` 只管 JSON-RPC 消息面，`tools.py` 只管
参数校验与载荷形态，两者都不打模型——真实检索与生成走 `private_rag.qa`
那条与 CLI / HTTP 完全相同的链路。选型与代价见 `docs/adr/0004`。
"""

from private_rag.mcp.server import MCPServer
from private_rag.mcp.tools import build_mcp_tools

__all__ = ["MCPServer", "build_mcp_tools"]
