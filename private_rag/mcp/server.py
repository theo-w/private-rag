"""MCP server：用最小 stdio JSON-RPC 把工具面暴露给外部 MCP 客户端。

手写的协议实现（每行一个 JSON 对象），只实现客户端真正会调的四个方法：
`initialize`（版本协商）、`ping`、`tools/list`、`tools/call`，加上对
`notifications/*` 的忽略。不引入官方 `mcp` SDK——它从第一个发布版本起就要求
Python 3.10+，而本仓库钉在 3.9（CI matrix 只有 3.9）。选型与代价见 `docs/adr/0004`。

通道与边界：
- stdout 是协议通道，日志与启动提示一律走 stderr（入口 `scripts/serve_mcp.py` 已这样配置）。
- 没有鉴权：能启动这个进程的人就能读知识库，与本机 HTTP 服务的无鉴权同级。
- 只读工具面：没有入库/写库，也没有运行时 SQL（见 `private_rag/mcp/tools.py`）。
- 长调用带进度通知：客户端在请求的 `params._meta.progressToken` 里给了 token，就在最终响应
  之前先发几条 `notifications/progress`；协议里没有分段的工具结果，结果仍是一次性返回（见 `docs/adr/0010`）。
- 本地模式 Qdrant 同一路径只允许一个进程打开，别与 `scripts/serve.py` 同时跑。
"""

from __future__ import annotations

import json
import sys
from functools import partial
from typing import Any, Callable, Optional, Sequence, TextIO

from private_rag.config import APP_VERSION, MCP_PROGRESS
from private_rag.mcp.tools import McpTool, Progress, ToolOutcome

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


Notify = Callable[[dict[str, Any]], None]


def _progress_notification(token: Any, step: int, total: int, message: str) -> dict[str, Any]:
    """一条进度通知。按 JSON-RPC 它是「通知」而不是响应：没有 id，客户端不会为它等回复。"""
    return {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {
            "progressToken": token,
            "progress": step,
            "total": total,
            "message": message,
        },
    }


def _progress_token(params: dict[str, Any]) -> Optional[Any]:
    """客户端想收这次调用的进度，就在 `params._meta.progressToken` 里给一个不透明 token。

    spec 只允许字符串或整数（Python 里 `bool` 是 `int` 的子类，客户端传 `true` 不能当成 `1`），
    其余形状一律按「没带」处理。整段只做类型判断、不抛异常：`serve_stdio` 只把 `json.loads`
    包在 try 里，这里冒出去的异常会让整个服务进程退出。
    """
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    token = meta.get("progressToken")
    if isinstance(token, str) or (isinstance(token, int) and not isinstance(token, bool)):
        return token
    return None


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

    def handle(self, message: dict[str, Any], *, notify: Optional[Notify] = None) -> Optional[dict[str, Any]]:
        """处理一条 JSON-RPC 消息；`notifications/*` 返回 None。

        请求方法即使没带 id 也回应（id 原样回 None）：客户端漏了 id 时不该让它
        一直等一个不会来的响应。

        `notify` 是写进度通知的出口（`serve_stdio` 传的是写 stdout 的那个）。不传、
        客户端没带 `progressToken`、或 `RAG_DISABLE_MCP_PROGRESS=1` 时一条都不发，
        响应与今天逐字相同。
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
            return self._call(msg_id, params, notify=notify)
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

    def _call(
        self,
        msg_id: Any,
        params: dict[str, Any],
        *,
        notify: Optional[Notify] = None,
    ) -> dict[str, Any]:
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

        token = _progress_token(params)
        total = tool.progress_steps
        reported = [0]  # 已经报到第几步，0 表示这次调用一条通知都没发

        def report(message: str, step: int) -> None:
            reported[0] = step
            notify(_progress_notification(token, step, total, message))

        def close_out(message: str) -> None:
            """补最后一条：只在确实发过通知、而且还没报到最后一步时补（spec 要求
            progress 逐条递增），协议层错误与「未知工具」这些一条都没发的情况不补。"""
            if notify is not None and 0 < reported[0] < total:
                notify(_progress_notification(token, total, total, message))

        # 三个条件同时成立才给工具挂回调：客户端带了合法 token、有人接通知、开关没关。
        # 否则照旧 tool.run(args)，一个关键字参数都不加——旧签名的工具类才不会撞上 TypeError。
        progress: Optional[Progress] = None
        if token is not None and notify is not None and MCP_PROGRESS:
            progress = report

        try:
            outcome: ToolOutcome = (
                tool.run(args, progress=progress) if progress is not None else tool.run(args)
            )
        except Exception as exc:  # 工具内部没兜住的异常不能把服务打死
            close_out("已结束")
            return _tool_error(msg_id, f"失败原因：{type(exc).__name__}")

        if not outcome.ok:
            close_out("已结束")
            return _tool_error(msg_id, f"失败原因：{outcome.error or '工具执行失败'}")
        close_out("已完成")
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": outcome.text}],
                "structuredContent": outcome.payload,
                "isError": False,
            },
        )

    def serve_stdio(self, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
        """逐行读 stdin、逐行写 stdout；坏行按协议错误回应，不退出。

        进度通知与响应共用这一条写路径（单线程顺序写，所以通知天然排在响应之前），
        不引入线程与定时器。
        """
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        notify: Notify = partial(_write, stdout)
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
            response = self.handle(message, notify=notify)
            if response is not None:
                _write(stdout, response)
        return 0


def _write(stdout: TextIO, response: dict[str, Any]) -> None:
    # ensure_ascii=False 让中文证据在客户端日志里可读；json 会转义正文里的换行
    stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    stdout.flush()
