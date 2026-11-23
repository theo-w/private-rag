"""MCP server：用最小 stdio JSON-RPC 把工具面暴露给外部 MCP 客户端。

手写的协议实现（每行一个 JSON 对象），只实现客户端真正会调的四个方法：
`initialize`（版本协商）、`ping`、`tools/list`、`tools/call`，加上对
`notifications/*` 的忽略。不引入官方 `mcp` SDK——它从第一个发布版本起就要求
Python 3.10+，而本仓库钉在 3.9（CI matrix 只有 3.9）。选型与代价见 `docs/adr/0004`。

通道与边界：
- stdout 是协议通道，日志与启动提示一律走 stderr（入口 `scripts/serve_mcp.py` 已这样配置）。
- 没有鉴权：能启动这个进程的人就能读知识库，与本机 HTTP 服务的无鉴权同级。
- 只读工具面：没有入库/写库，也没有运行时 SQL（见 `private_rag/mcp/tools.py`）。
- 本地模式 Qdrant 同一路径只允许一个进程打开，别与 `scripts/serve.py` 同时跑。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, Sequence, TextIO

from private_rag.config import APP_VERSION
from private_rag.mcp.tools import McpTool, ToolOutcome

# 我方声明的协议版本；客户端带已知版本时按客户端的来
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-10-22", "2025-03-26", "2025-06-18")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_error(msg_id: Any, message: str) -> dict[str, Any]:
    """工具层失败按 MCP 约定回 `isError` 结果而不是协议错误：客户端能读到原因。"""
    return _result(msg_id, {"content": [{"type": "text", "text": message}], "isError": True})


class MCPServer:
    """把一组 `McpTool` 映射成 MCP tools；单进程 stdio，无鉴权。"""

    def __init__(
        self,
        tools: Sequence[McpTool],
        *,
        name: str = "private-rag",
        version: str = APP_VERSION,
    ):
        self._tools: dict[str, McpTool] = {}
        for tool in tools:
            if not tool.name:
                raise ValueError("工具必须有 name")
            self._tools[tool.name] = tool
        self.name = name
        self.version = version

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]

    def handle(self, message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """处理一条 JSON-RPC 消息；`notifications/*` 返回 None。

        请求方法即使没带 id 也回应（id 原样回 None）：客户端漏了 id 时不该让它
        一直等一个不会来的响应。
        """
        msg_id = message.get("id")
        method = message.get("method")

        if not isinstance(method, str) or not method:
            return _error(msg_id, INVALID_REQUEST, "消息缺少 method") if msg_id is not None else None
        if method.startswith("notifications/"):
            return None

        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params 必须是对象") if msg_id is not None else None

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
            return _result(
                msg_id,
                {
                    "protocolVersion": version,
                    # 只声明 tools：没有资源与提示词模板
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
        # 客户端（尤其 Claude Desktop）会顺手调 resources/* 与 prompts/*：
        # 这里没有这类内容，返回空列表比抛 method not found 少一次报错噪音。
        if method == "resources/list":
            return _result(msg_id, {"resources": []})
        if method == "resources/templates/list":
            return _result(msg_id, {"resourceTemplates": []})
        if method == "prompts/list":
            return _result(msg_id, {"prompts": []})
        return (
            _error(msg_id, METHOD_NOT_FOUND, f"不支持的方法：{method}")
            if msg_id is not None
            else None
        )

    def _call(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return _error(msg_id, INVALID_PARAMS, "tools/call 需要给出工具 name")
        tool = self._tools.get(name.strip())
        if tool is None:
            return _error(msg_id, INVALID_PARAMS, f"未知工具：{name}")

        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _tool_error(msg_id, "失败原因：arguments 必须是对象")

        try:
            outcome: ToolOutcome = tool.run(args)
        except Exception as exc:  # 工具内部没兜住的异常不能把服务打死
            return _tool_error(msg_id, f"失败原因：{type(exc).__name__}")

        if not outcome.ok:
            return _tool_error(msg_id, f"失败原因：{outcome.error or '工具执行失败'}")
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": outcome.text}],
                "structuredContent": outcome.payload,
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
    # ensure_ascii=False 让中文证据在客户端日志里可读；json 会转义正文里的换行
    stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    stdout.flush()
