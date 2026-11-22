"""MCP server 测试：握手、tools/list、tools/call、块级定位与只读护栏。

全部 mock：不启动 Ollama / Qdrant；SQL 走 tmp_path 下的临时 SQLite，
协议层直接喂 JSON-RPC 消息或过一遍 serve_stdio 的行循环。
"""

from __future__ import annotations

import io
import json
import sqlite3
from types import SimpleNamespace

import pytest

from private_rag.agent.runtime import build_tool_registry
from private_rag.agent.tools import KnowledgeSearchTool, SqlQueryTool, ToolRegistry
from private_rag.mcp.server import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    EVIDENCE_OUTPUT_SCHEMA,
    MCPServer,
)
from private_rag.qa import RetrievalOptions
from private_rag.retrieval.hybrid import RetrievedChunk


def make_chunk(index: int, *, text: str = "", snippet: str = "", page=None) -> RetrievedChunk:
    metadata = {
        "source_type": "file",
        "title": f"文档{index}",
        "source_uri": f"data/docs/{index}.md",
        "doc_id": f"doc-{index}",
        "chunk_index": index - 1,
        "chunk_count": 7,
    }
    if page is not None:
        metadata["page"] = page
    body = text or f"这是第 {index} 段资料，里面写了做法和结论。"
    return RetrievedChunk(
        point_id=f"p{index}",
        text=body,
        metadata=metadata,
        snippet_text=snippet or f"第 {index} 段资料",
    )


class FakeEngine:
    """只实现 KnowledgeSearchTool 用到的那一个方法。"""

    def __init__(self, chunks=(), error=None):
        self.chunks = list(chunks)
        self.error = error
        self.calls: list[tuple[str, RetrievalOptions]] = []

    def retrieve_with_trace(self, query, options):
        self.calls.append((query, options))
        if self.error:
            raise self.error
        return SimpleNamespace(
            chunks=list(self.chunks),
            queries=[query],
            used_parent=True,
            used_rerank=True,
            context_chars=1234,
        )


def make_sqlite(tmp_path, rows=(("banana", 3.5), ("apple", 6.0))):
    path = tmp_path / "demo.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE products (name TEXT, price REAL)")
        connection.executemany("INSERT INTO products VALUES (?, ?)", list(rows))
        connection.commit()
    finally:
        connection.close()
    return f"sqlite:///{path}"


def make_registry(tmp_path, chunks) -> ToolRegistry:
    dsn = make_sqlite(tmp_path)
    return ToolRegistry(
        [
            KnowledgeSearchTool(FakeEngine(chunks=chunks)),
            SqlQueryTool(dsn=dsn, label="demo", allowed_tables=["products"], max_rows=5),
        ]
    )


def call_tool(server: MCPServer, name: str, arguments: dict, msg_id: int = 7) -> dict:
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


class TestHandshake:
    def test_initialize_negotiates_supported_version(self, server):
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "claude-code"}},
            }
        )
        assert response["id"] == 1
        assert response["result"]["protocolVersion"] == "2025-03-26"
        assert response["result"]["serverInfo"]["name"] == "private-rag"
        assert "tools" in response["result"]["capabilities"]

    def test_initialize_with_unknown_version_returns_ours(self, server):
        response = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "9999-01-01"}}
        )
        assert response["result"]["protocolVersion"] == PROTOCOL_VERSION

    def test_initialized_notification_gets_no_response(self, server):
        assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_ping(self, server):
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
        assert response["result"] == {}

    def test_unknown_method_is_method_not_found(self, server):
        response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        assert response["error"]["code"] == METHOD_NOT_FOUND

    def test_notification_without_id_is_ignored(self, server):
        assert server.handle({"jsonrpc": "2.0", "method": "resources/list"}) is None

    def test_serve_stdio_round_trip(self, server):
        stdin = io.StringIO(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    "{ 这不是 JSON",
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "kb_search", "arguments": {"query": "增量入库"}},
                        }
                    ),
                    json.dumps({"jsonrpc": "2.0", "method": "bogus"}),
                    "",
                ]
            )
        )
        stdout = io.StringIO()
        assert server.serve_stdio(stdin=stdin, stdout=stdout) == 0

        responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        assert [r.get("id") for r in responses] == [1, None, 2, 3]
        assert responses[1]["error"]["code"] == PARSE_ERROR
        assert [t["name"] for t in responses[2]["result"]["tools"]] == ["kb_search", "sql_query"]
        assert responses[3]["result"]["isError"] is False


class TestToolsList:
    def test_tool_definitions(self, server):
        tools = server.tool_definitions()
        assert [t["name"] for t in tools] == ["kb_search", "sql_query"]
        assert tools[0]["inputSchema"]["required"] == ["query"]
        # sql_query 的描述来自 SqlQueryTool：白名单表必须让客户端看得见
        assert "products" in tools[1]["description"]
        assert "只读" in tools[1]["description"]
        for tool in tools:
            assert tool["outputSchema"] == EVIDENCE_OUTPUT_SCHEMA

    def test_kb_only_registry_exposes_one_tool(self, server_kb_only):
        assert [t["name"] for t in server_kb_only.tool_definitions()] == ["kb_search"]


class TestKbSearchCall:
    def test_success_payload_numbers_blocks(self, server):
        response = call_tool(server, "kb_search", {"query": "怎么增量入库"})
        assert response["result"]["isError"] is False
        content = response["result"]["content"][0]
        assert content["type"] == "text"
        assert "[1] (来源:" in content["text"]
        assert "引用列表：" in content["text"]

        structured = response["result"]["structuredContent"]
        assert structured["summary"] == "检索到 3 段资料"
        assert [e["index"] for e in structured["evidence"]] == [1, 2, 3]
        assert [c["index"] for c in structured["citations"]] == [1, 2, 3]
        assert structured["citations"][0]["source_type"] == "file"

    def test_citation_locator_has_block_position(self, server):
        response = call_tool(server, "kb_search", {"query": "x"})
        locator = response["result"]["structuredContent"]["citations"][0]["locator"]
        assert locator["source_uri"] == "data/docs/1.md"
        assert locator["doc_id"] == "doc-1"
        assert locator["chunk_index"] == 0
        assert locator["chunk_count"] == 7
        # 片段偏移是命中片段在返回块文本里的位置，可直接高亮
        assert locator["snippet_offset"] == "这是第 1 段资料，里面写了做法和结论。".find("第 1 段资料")

    def test_page_passes_into_citation_and_locator(self, server_with_page):
        citation = call_tool(server_with_page, "kb_search", {"query": "x"})["result"]["structuredContent"]["citations"][0]
        assert citation["page"] == 3
        assert citation["locator"]["page"] == 3

    def test_no_hits_is_ok_with_empty_evidence(self, server_no_hits):
        response = call_tool(server_no_hits, "kb_search", {"query": "无关问题"})
        structured = response["result"]["structuredContent"]
        assert response["result"]["isError"] is False
        assert structured["evidence"] == []
        assert structured["citations"] == []
        assert "没有检索到" in structured["summary"]

    def test_retrieval_failure_is_tool_error_not_protocol_error(self, failing_engine_server):
        response = call_tool(failing_engine_server, "kb_search", {"query": "x"})
        assert response["id"] == 7
        assert response["result"]["isError"] is True
        assert "检索失败" in response["result"]["content"][0]["text"]
        assert "structuredContent" not in response["result"]

    def test_missing_query_rejected_before_call(self, server):
        response = call_tool(server, "kb_search", {})
        assert response["result"]["isError"] is True
        assert "query" in response["result"]["content"][0]["text"]

    def test_non_string_query_rejected(self, server):
        response = call_tool(server, "kb_search", {"query": 42})
        assert response["result"]["isError"] is True


class TestSqlQueryCall:
    def test_select_returns_rows_and_locator(self, server, tmp_path):
        response = call_tool(server, "sql_query", {"sql": "SELECT name, price FROM products"})
        structured = response["result"]["structuredContent"]
        assert response["result"]["isError"] is False
        assert structured["rows"] == 2
        assert "banana" in response["result"]["content"][0]["text"]

        citation = structured["citations"][0]
        assert citation["source_type"] == "sql"
        assert citation["source_uri"] == "demo#sql_query"
        locator = citation["locator"]
        assert locator["table"] == "demo"
        assert locator["row_count"] == 2
        assert locator["truncated"] is False
        # 没写 LIMIT 时护栏自动补上限，执行体与警告都要能被客户端复核
        assert locator["sql"].endswith("LIMIT 5")
        assert structured["warnings"] and "LIMIT" in structured["warnings"][0]

    def test_write_statement_rejected_by_guard(self, server):
        response = call_tool(server, "sql_query", {"sql": "UPDATE products SET price = 1"})
        assert response["result"]["isError"] is True
        assert "只允许 SELECT 或 WITH" in response["result"]["content"][0]["text"]

    def test_forbidden_keyword_rejected_by_guard(self, server):
        response = call_tool(server, "sql_query", {"sql": "SELECT delete FROM products"})
        # 写操作关键字即使藏在表达式里也要被拒绝（护栏先于执行）
        assert response["result"]["isError"] is True
        assert "关键字" in response["result"]["content"][0]["text"]

    def test_table_outside_whitelist_rejected(self, server):
        response = call_tool(server, "sql_query", {"sql": "SELECT * FROM secrets"})
        assert response["result"]["isError"] is True
        assert "未授权" in response["result"]["content"][0]["text"]

    def test_limit_clamped_to_max_rows(self, server):
        response = call_tool(server, "sql_query", {"sql": "SELECT name FROM products LIMIT 100"})
        structured = response["result"]["structuredContent"]
        assert structured["data"]["sql"].endswith("LIMIT 5")
        assert any("LIMIT 被压缩" in warning for warning in structured["warnings"])

    def test_sql_error_returns_failure_reason(self, server):
        response = call_tool(server, "sql_query", {"sql": "SELECT nosuchcolumn FROM products"})
        assert response["result"]["isError"] is True
        assert "SQL 执行失败" in response["result"]["content"][0]["text"]


class TestUnknownTool:
    def test_unknown_tool_is_invalid_params(self, server):
        response = call_tool(server, "no_such_tool", {})
        assert response["error"]["code"] == INVALID_PARAMS


class TestSharedAssembly:
    """装配复用：build_tool_registry 是 agentic 与 MCP 共用的入口。"""

    def test_authorized_sql_source_gets_exposed(self, tmp_path):
        dsn = make_sqlite(tmp_path)
        config = tmp_path / "sources.yaml"
        config.write_text(
            f"""sources:
  - type: sql
    dsn: "{dsn}"
    query: SELECT name, price FROM products
    id_column: name
    content_columns: [price]
    label: demo.products
    allow_runtime_query: true
    runtime_tables: [products]
""",
            encoding="utf-8",
        )
        registry = build_tool_registry(FakeEngine(), sources_path=config)
        server = MCPServer(registry)
        assert server.registry.names() == ["kb_search", "sql_query"]

        response = call_tool(server, "sql_query", {"sql": "SELECT price FROM products"})
        assert response["result"]["structuredContent"]["rows"] == 2

    def test_missing_config_degrades_to_kb_search_only(self, tmp_path):
        registry = build_tool_registry(
            FakeEngine(chunks=[make_chunk(1)]), sources_path=tmp_path / "nope.yaml"
        )
        assert registry.names() == ["kb_search"]
        response = call_tool(MCPServer(registry), "sql_query", {"sql": "SELECT 1"})
        assert response["error"]["code"] == INVALID_PARAMS


# 每个测试拿干净的 server：fixture 返回 MCPServer，测试里直接用 handle/tool_definitions


@pytest.fixture
def server(tmp_path):
    return MCPServer(make_registry(tmp_path, [make_chunk(1), make_chunk(2), make_chunk(3)]))


@pytest.fixture
def server_kb_only():
    return MCPServer(ToolRegistry([KnowledgeSearchTool(FakeEngine(chunks=[make_chunk(1)]))]))


@pytest.fixture
def server_with_page(tmp_path):
    return MCPServer(make_registry(tmp_path, [make_chunk(1, page=3)]))


@pytest.fixture
def server_no_hits(tmp_path):
    return MCPServer(make_registry(tmp_path, []))


@pytest.fixture
def failing_engine_server():
    return MCPServer(
        ToolRegistry([KnowledgeSearchTool(FakeEngine(error=RuntimeError("qdrant down")))])
    )
