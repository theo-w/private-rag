"""MCP server：把 agentic 工具注册表经 stdio 暴露给任意 MCP 客户端（Claude Code 等）。

手写的最小 MCP 协议实现（JSON-RPC 2.0，每行一个 JSON 对象）：支持 initialize、
notifications/*、ping、tools/list、tools/call。不引入第三方 MCP SDK——本项目全本地
离线并钉在 Python 3.9，而 SDK 要求 3.10+；这里只实现用到的四个方法，边界写在测试里。

工具来自 build_tool_registry：kb_search 始终可用，sql_query 只在 sources.yaml 显式
授权（allow_runtime_query: true）时出现——客户端看到的工具与 agentic 循环完全一致，
只读护栏与表白名单照旧在 sql_guard 里兜底。

通道与边界：
- stdout 是协议通道，日志必须走 stderr（入口 scripts/serve_mcp.py 已这样配置）。
- 没有鉴权：能启动这个进程的人就能查演示库与白名单表，与 HTTP 服务的本地无鉴权同级。
- 本地模式 Qdrant 同一路径只允许一个进程打开，别与 scripts/serve.py 同时跑。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, TextIO

from private_rag.agent.tools import ToolRegistry
from private_rag.config import APP_VERSION
from private_rag.mcp.evidence import result_payload, result_text

# 我方声明的协议版本；客户端带已知版本时按客户端的协商
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-10-22", "2025-03-26", "2025-06-18")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# 入参 schema 与 agentic 循环的 args 同名，客户端与内部循环对同一工具行为一致
TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "kb_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索用的查询语句"},
        },
        "required": ["query"],
    },
    "sql_query": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "单条只读 SELECT/WITH；经只读护栏：写操作与白名单外的表直接拒绝，"
                    "并强制不超过上限的 LIMIT"
                ),
            },
        },
        "required": [],
    },
}

# 两个工具共用的返回结构：编号证据块 + 带块级定位的引用列表
EVIDENCE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "证据块统一编号；无引用的块没有该字段"},
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["label", "text"],
            },
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "source_type": {"type": "string"},
                    "source_uri": {"type": "string"},
                    "snippet": {"type": "string"},
                    "page": {"type": "integer"},
                    "table": {"type": "string"},
                    "score": {"type": "number"},
                    "locator": {
                        "type": "object",
                        "description": (
                            "块级定位：source_uri + doc_id/chunk_index/chunk_count/snippet_offset/page/table，"
                            "SQL 引用则带执行查询与行窗"
                        ),
                    },
                },
                "required": ["index", "title", "source_type", "source_uri"],
            },
        },
        "rows": {"type": "integer"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "data": {"type": "object"},
    },
    "required": ["summary", "evidence", "citations"],
}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_error(msg_id: Any, message: str) -> dict[str, Any]:
    """工具层失败：按 MCP 约定返回 isError 结果而不是协议错误，客户端能看到原因。"""
    return _result(msg_id, {"content": [{"type": "text", "text": message}], "isError": True})


def _validate_args(args: dict[str, Any], schema: Optional[dict[str, Any]]) -> str:
    """最小入参校验；不通过时返回一句面向客户端的说明。"""
    if not schema:
        return ""
    for key in schema.get("required", []):
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"参数 {key} 必须是字符串"
    for key, spec in schema.get("properties", {}).items():
        if key in args and spec.get("type") == "string" and not isinstance(args[key], str):
            return f"参数 {key} 必须是字符串"
    return ""


class MCPServer:
    """把 ToolRegistry 里的工具映射成 MCP tools；单进程 stdio，无鉴权。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        name: str = "private-rag",
        version: str = APP_VERSION,
    ):
        self.registry = registry
        self.name = name
        self.version = version

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools = []
        for tool in self.registry.tools():
            definition: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": TOOL_INPUT_SCHEMAS.get(tool.name, {"type": "object", "properties": {}}),
                "outputSchema": EVIDENCE_OUTPUT_SCHEMA,
            }
            tools.append(definition)
        return tools

    def handle(self, message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """处理一条 JSON-RPC 消息；通知（notifications/* 或无 id）返回 None。"""
        msg_id = message.get("id")
        method = message.get("method")

        if not isinstance(method, str):
            return _error(msg_id, INVALID_REQUEST, "消息缺少 method") if msg_id is not None else None
        if method.startswith("notifications/"):
            return None

        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params 必须是对象") if msg_id is not None else None

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
            return _result(
                msg_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            )
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": self.tool_definitions()})
        if method == "tools/call":
            return self._call(msg_id, params)
        return _error(msg_id, METHOD_NOT_FOUND, f"不支持的方法：{method}") if msg_id is not None else None

    def _call(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return _error(msg_id, INVALID_PARAMS, "tools/call 需要给出工具 name")
        tool = self.registry.get(name)
        if tool is None:
            return _error(msg_id, INVALID_PARAMS, f"未知工具：{name}")

        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _tool_error(msg_id, "失败原因：arguments 必须是对象")
        invalid = _validate_args(args, TOOL_INPUT_SCHEMAS.get(tool.name))
        if invalid:
            return _tool_error(msg_id, f"失败原因：{invalid}")

        result = tool.run(args)
        if not result.ok:
            return _tool_error(msg_id, result.observation or "失败原因：工具执行失败")
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": result_text(result)}],
                "structuredContent": result_payload(result),
                "isError": False,
            },
        )

    def serve_stdio(self, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
        """逐行读 stdin、逐行写 stdout；坏行按协议错误回应，不退出。"""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                _write(stdout, _error(None, PARSE_ERROR, "消息不是合法 JSON"))
                continue
            if not isinstance(message, dict):
                _write(stdout, _error(None, INVALID_REQUEST, "每行只接受一个 JSON 对象"))
                continue
            response = self.handle(message)
            if response is not None:
                _write(stdout, response)
        return 0


def _write(stdout: TextIO, response: dict[str, Any]) -> None:
    stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    stdout.flush()
