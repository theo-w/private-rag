"""v8 MCP 工具暴露：把 kb_search / sql_query 作为 MCP tools 经 stdio 提供给外部客户端。"""

from private_rag.mcp.evidence import citation_payload, result_payload, result_text
from private_rag.mcp.server import MCPServer

__all__ = [
    "MCPServer",
    "citation_payload",
    "result_payload",
    "result_text",
]
