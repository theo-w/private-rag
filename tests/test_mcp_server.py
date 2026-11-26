"""MCP server 测试：JSON-RPC 消息面、stdio 通道、只读工具面与入口 CLI。

不打模型、不联网、不打开真实 Qdrant：工具用假引擎，入口脚本用打桩的 build_engine。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
from pathlib import Path

import pytest

from private_rag.mcp.server import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    MCPServer,
)
from private_rag.mcp.tools import McpTool, ToolOutcome, build_mcp_tools
from private_rag.qa import QaResult, Readiness, RetrievalTrace
from private_rag.retrieval.hybrid import RetrievedChunk


def make_chunk(index: int, *, text: str = "", page=None, doc_id=None, chunk_index=None, chunk_count=None) -> RetrievedChunk:
    metadata = {
        "source_type": "file",
        "title": f"文档{index}",
        "source_uri": f"data/docs/{index}.md",
        "doc_id": doc_id or f"doc-{index}",
    }
    if chunk_index is not None:
        metadata["chunk_index"] = chunk_index
    if chunk_count is not None:
        metadata["chunk_count"] = chunk_count
    if page is not None:
        metadata["page"] = page
    return RetrievedChunk(
        point_id=f"p{index}",
        text=text or f"这是第 {index} 段资料。",
        metadata=metadata,
        snippet_text=text or f"第 {index} 段",
    )


class FakeEngine:
    """只实现 MCP 工具用到的三个方法。"""

    def __init__(self, chunks=(), *, ready: bool = True, reasons=()):
        self.chunks = list(chunks)
        self.ready = ready
        self.reasons = list(reasons)

    def readiness(self) -> Readiness:
        return Readiness(
            ready=self.ready,
            vector_count=len(self.chunks),
            lexical_available=True,
            reranker_available=True,
            reasons=list(self.reasons),
            collection_exists=self.ready,
        )

    def retrieve_with_trace(self, query: str, options) -> RetrievalTrace:
        return RetrievalTrace(
            chunks=list(self.chunks),
            used_lexical=True,
            used_rerank=True,
            used_parent=True,
            queries=[query],
            query_rewrite="off",
            query_count=1,
            context_chars=1234,
        )

    def answer(self, question: str, *, options=None, history=None) -> QaResult:
        from private_rag.generation import REFUSAL, build_citations

        citations = build_citations(self.chunks)
        return QaResult(
            answer="橙子富含维生素 C。[1]" if citations else REFUSAL,
            citations=citations,
            retrieved=len(self.chunks),
            used_lexical=True,
            used_rerank=True,
            used_parent=True,
            query_rewrite="off",
            query_count=1,
            context_chars=1234,
        )


def make_server(chunks=(), **engine_kwargs) -> MCPServer:
    return MCPServer(build_mcp_tools(FakeEngine(chunks, **engine_kwargs)))


def call(server: MCPServer, method: str, params=None, msg_id=1) -> dict:
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def serve_lines(server: MCPServer, text: str) -> list[dict]:
    out = io.StringIO()
    assert server.serve_stdio(io.StringIO(text), out) == 0
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


# initialize / ping / 通知


def test_initialize_echoes_known_protocol_version():
    result = call(make_server(), "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "private-rag"
    assert result["serverInfo"]["version"]


def test_initialize_falls_back_to_own_version_for_unknown_request():
    result = call(make_server(), "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION


def test_initialize_without_params_still_works():
    response = call(make_server(), "initialize", msg_id=7)
    assert "protocolVersion" in response["result"]


def test_ping_returns_empty_result():
    assert call(make_server(), "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_notifications_produce_no_response():
    server = make_server()
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}) is None
    # 通知即使带 id 也不回应
    assert server.handle({"jsonrpc": "2.0", "id": 5, "method": "notifications/progress"}) is None
    # 请求漏了 id 也要回应，否则客户端会一直等
    assert call(server, "tools/list", msg_id=None)["result"]["tools"]


# 协议错误


def test_message_without_method_is_invalid_request():
    assert call(make_server(), "", msg_id=3)["error"]["code"] == INVALID_REQUEST


def test_params_must_be_object():
    assert call(make_server(), "tools/call", "not-an-object")["error"]["code"] == INVALID_PARAMS


def test_unknown_method_is_method_not_found():
    response = call(make_server(), "unknown/method")
    assert response["error"]["code"] == METHOD_NOT_FOUND
    assert "unknown/method" in response["error"]["message"]


def test_resources_and_prompts_lists_are_empty_not_errors():
    server = make_server()
    assert call(server, "resources/list")["result"] == {"resources": []}
    assert call(server, "resources/templates/list")["result"] == {"resourceTemplates": []}
    assert call(server, "prompts/list")["result"] == {"prompts": []}


# tools/list


def test_tools_list_exposes_only_read_only_kb_tools():
    tools = call(make_server(), "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["kb_search", "ask"]
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["outputSchema"]["required"]
        assert tool["annotations"]["readOnlyHint"] is True


def test_empty_registry_lists_no_tools():
    assert MCPServer([]).tool_definitions() == []
    assert call(MCPServer([]), "tools/list")["result"]["tools"] == []


def test_server_requires_named_tools():
    class Nameless(McpTool):
        def run(self, args):
            return ToolOutcome(True, text="x")

    with pytest.raises(ValueError):
        MCPServer([Nameless()])


# tools/call


def test_tools_call_kb_search_returns_text_and_structured_content():
    server = make_server([make_chunk(1, page=3), make_chunk(2)])
    response = call(server, "tools/call", {"name": "kb_search", "arguments": {"query": "橙子"}}, msg_id=42)

    assert response["id"] == 42
    assert response["result"]["isError"] is False
    assert "[1]" in response["result"]["content"][0]["text"]
    assert response["result"]["structuredContent"]["citations"][0]["page"] == 3


def test_tools_call_ask_returns_answer_and_citations():
    response = call(make_server([make_chunk(1)]), "tools/call", {"name": "ask", "arguments": {"question": "橙子"}})
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["answer"] == "橙子富含维生素 C。[1]"
    assert response["result"]["content"][0]["text"].startswith("橙子富含维生素 C。")


def test_kb_search_citations_and_evidence_carry_the_same_locator(monkeypatch):
    set_locator_enabled(monkeypatch, True)
    server = make_server([make_chunk(1, page=3, doc_id="doc-a", chunk_index=2, chunk_count=5)])
    structured = call(server, "tools/call", {"name": "kb_search", "arguments": {"query": "橙子"}})["result"][
        "structuredContent"
    ]

    locator = structured["citations"][0]["locator"]
    assert locator["chunk_id"] == "p1"
    assert locator["doc_id"] == "doc-a"
    assert locator["chunk_index"] == 2
    assert locator["chunk_count"] == 5
    assert locator["page"] == 3
    # 偏移相对实际返回的那段证据文本，客户端可以直接拿去高亮
    assert locator["snippet_offset"] == structured["evidence"][0]["text"].find("第 1 段")
    assert structured["evidence"][0]["locator"] == locator


def test_clipped_block_omits_snippet_offset_rather_than_inventing_one(monkeypatch):
    set_locator_enabled(monkeypatch, True)
    server = MCPServer(build_mcp_tools(FakeEngine([make_chunk(1, text="字" * 5000)]), max_chars=200))
    structured = call(server, "tools/call", {"name": "kb_search", "arguments": {"query": "橙子"}})["result"][
        "structuredContent"
    ]
    assert structured["evidence"][0]["text"].endswith("…")
    assert "snippet_offset" not in structured["citations"][0]["locator"]
    assert structured["citations"][0]["locator"]["chunk_id"] == "p1"


def test_ask_citations_have_no_locator_in_payload_or_schema():
    """ask 的引用来自生成阶段、没有证据块，所以补不出定位，定义里也不承诺这个键。"""
    response = call(make_server([make_chunk(1)]), "tools/call", {"name": "ask", "arguments": {"question": "橙子"}})
    assert "locator" not in response["result"]["structuredContent"]["citations"][0]

    listed = call(make_server([make_chunk(1)]), "tools/list")["result"]["tools"]
    ask = next(tool for tool in listed if tool["name"] == "ask")
    assert "locator" not in ask["outputSchema"]["properties"]["citations"]["items"]["properties"]


def set_locator_enabled(monkeypatch, on: bool) -> None:
    """开关要在装配之前改：工具定义在构造工具时就拼好了。"""
    monkeypatch.setattr("private_rag.mcp.tools.CITATION_LOCATOR", on)
    monkeypatch.setattr("private_rag.mcp.evidence.CITATION_LOCATOR", on)
    monkeypatch.setattr("private_rag.agent.tools.CITATION_LOCATOR", on)


def test_switch_off_restores_today_payload_and_tool_definitions(monkeypatch, tmp_path):
    set_locator_enabled(monkeypatch, False)
    structured = make_server([make_chunk(1, page=3)]).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "kb_search", "arguments": {"query": "橙子"}}}
    )["result"]["structuredContent"]
    assert structured["citations"][0] == {
        "index": 1,
        "title": "文档1",
        "source_type": "file",
        "source_uri": "data/docs/1.md",
        "snippet": "第 1 段",
        "page": 3,
    }
    assert structured["evidence"][0] == {"index": 1, "label": "file | 文档1 | data/docs/1.md | 第 3 页", "text": "这是第 1 段资料。"}

    sql_server = make_sql_server(tmp_path)
    sql_structured = sql_server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "sql_query", "arguments": {"sql": "SELECT name FROM products"}}}
    )["result"]["structuredContent"]
    assert "locator" not in sql_structured["citations"][0]
    assert "locator" not in sql_structured["evidence"][0]

    for tool in call(sql_server, "tools/list")["result"]["tools"]:
        properties = tool["outputSchema"]["properties"]
        assert "locator" not in properties["citations"]["items"]["properties"]
        if "evidence" in properties:
            assert "locator" not in properties["evidence"]["items"]["properties"]


def test_tools_call_with_no_arguments_defaults_to_empty():
    response = call(make_server(), "tools/call", {"name": "kb_search"})
    assert response["result"]["isError"] is True
    assert "query" in response["result"]["content"][0]["text"]


def test_tools_call_missing_name_is_invalid_params():
    assert call(make_server(), "tools/call", {})["error"]["code"] == INVALID_PARAMS
    assert call(make_server(), "tools/call", {"name": "  "})["error"]["code"] == INVALID_PARAMS


def test_tools_call_unknown_tool_is_invalid_params():
    response = call(make_server(), "tools/call", {"name": "ingest", "arguments": {}})
    assert response["error"]["code"] == INVALID_PARAMS
    assert "ingest" in response["error"]["message"]


def test_tools_call_arguments_must_be_object():
    response = call(make_server(), "tools/call", {"name": "kb_search", "arguments": "橙子"})
    assert response["result"]["isError"] is True
    assert "arguments" in response["result"]["content"][0]["text"]


def test_tool_failure_is_returned_as_is_error_result():
    response = call(make_server(), "tools/call", {"name": "kb_search", "arguments": {"query": ""}})
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "失败原因" in response["result"]["content"][0]["text"]


def test_uncaught_tool_exception_does_not_break_the_server():
    class Boom(McpTool):
        name = "boom"

        def run(self, args):
            raise RuntimeError("意外异常")

    response = call(MCPServer([Boom()]), "tools/call", {"name": "boom", "arguments": {}})
    assert response["result"]["isError"] is True
    assert "RuntimeError" in response["result"]["content"][0]["text"]


# stdio 通道


def test_serve_stdio_answers_each_line_in_order():
    server = make_server([make_chunk(1)])
    responses = serve_lines(
        server,
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-10-22"}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "ask", "arguments": {"question": "橙子"}},
                    }
                ),
                "",
            ]
        ),
    )
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert responses[0]["result"]["protocolVersion"] == "2024-10-22"
    assert [t["name"] for t in responses[1]["result"]["tools"]] == ["kb_search", "ask"]
    assert responses[2]["result"]["structuredContent"]["no_evidence"] is False


def test_serve_stdio_reports_bad_lines_without_stopping():
    server = make_server([make_chunk(1)])
    responses = serve_lines(
        server,
        "\n".join(
            [
                "{不是 JSON",
                json.dumps([1, 2]),
                json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}),
            ]
        ),
    )
    assert responses[0]["error"]["code"] == PARSE_ERROR
    assert responses[1]["error"]["code"] == INVALID_REQUEST
    assert responses[2]["result"] == {}


def test_serve_stdio_exits_cleanly_on_eof():
    assert make_server().serve_stdio(io.StringIO(""), io.StringIO()) == 0


def test_stdout_keeps_chinese_readable():
    server = make_server([make_chunk(1, text="橙子富含维生素 C")])
    raw = io.StringIO()
    server.serve_stdio(
        io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "kb_search", "arguments": {"query": "橙子"}},
                }
            )
        ),
        raw,
    )
    assert "橙子富含维生素 C" in raw.getvalue()
    assert "\\u" not in raw.getvalue()


def test_response_is_single_line_json():
    server = make_server([make_chunk(1, text="第一行\n第二行")])
    raw = io.StringIO()
    server.serve_stdio(
        io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "kb_search", "arguments": {"query": "x"}},
                }
            )
        ),
        raw,
    )
    assert len(raw.getvalue().rstrip("\n").split("\n")) == 1


# 入口 CLI（scripts/ 不是包，按路径加载）


@pytest.fixture
def clean_rerank_env(monkeypatch):
    monkeypatch.delenv("RAG_DISABLE_RERANK", raising=False)


def load_script():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("serve_mcp", root / "scripts" / "serve_mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_no_rerank_sets_env_before_config(clean_rerank_env):
    load_script().parse_args(["--no-rerank", "--top-k", "5"])
    assert os.getenv("RAG_DISABLE_RERANK") == "1"


def test_script_top_k_default_matches_config(clean_rerank_env):
    import private_rag.config as config

    assert load_script().parse_args([]).top_k == config.TOP_K_RERANK


def test_script_list_tools_prints_definitions_without_opening_storage(clean_rerank_env, monkeypatch, capsys):
    import private_rag.qa as qa

    def refuse(*args, **kwargs):
        raise AssertionError("--list-tools 不该打开知识库")

    monkeypatch.setattr(qa, "build_engine", refuse)
    module = load_script()

    assert module.main(["--list-tools"]) == 0
    definitions = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in definitions] == ["kb_search", "ask"]
    assert definitions[0]["inputSchema"]["required"] == ["query"]


def test_script_exits_with_storage_hint_when_kb_cannot_open(clean_rerank_env, monkeypatch, capsys):
    import private_rag.qa as qa

    def locked(*args, **kwargs):
        raise RuntimeError("Storage folder is already accessed by another instance of Qdrant client")

    monkeypatch.setattr(qa, "build_engine", locked)
    module = load_script()

    assert module.main([]) == 3
    captured = capsys.readouterr()
    assert captured.err.startswith("❌ 知识库打不开")
    assert "scripts/serve.py" in captured.err


def test_script_serves_stdio_with_real_tools(clean_rerank_env, monkeypatch, capsys):
    import private_rag.qa as qa

    monkeypatch.setattr(qa, "build_engine", lambda **kwargs: FakeEngine([make_chunk(1)]))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"))
    module = load_script()

    assert module.main(["--no-rerank"]) == 0
    captured = capsys.readouterr()
    assert [t["name"] for t in json.loads(captured.out)["result"]["tools"]] == ["kb_search", "ask"]
    assert "已启动" in captured.err


# sql_query：只有装配给出那颗工具时才进工具面

def make_sqlite_db(tmp_path):
    db = tmp_path / "demo.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("CREATE TABLE products (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO products VALUES (1, '橙子')")
        connection.commit()
    finally:
        connection.close()
    return db


def make_sql_server(tmp_path):
    from private_rag.agent.tools import SqlQueryTool as RuntimeSqlQueryTool

    runtime_tool = RuntimeSqlQueryTool(
        dsn=f"sqlite:///{make_sqlite_db(tmp_path)}",
        label="demo.products",
        allowed_tables=["products"],
        max_rows=5,
    )
    # 传 agentic 那颗工具本身：`build_mcp_tools` 负责包装成 MCP 工具
    return MCPServer(build_mcp_tools(FakeEngine([make_chunk(1)]), sql_tool=runtime_tool))


def sources_yaml(db, *, opt_in: str = "") -> str:
    return (
        "sources:\n"
        "  - type: sql\n"
        f"    dsn: sqlite:///{db}\n"
        "    query: SELECT id, name FROM products ORDER BY id\n"
        "    id_column: id\n"
        "    title_column: name\n"
        "    content_columns: [name]\n"
        "    label: demo.products\n"
        f"{opt_in}"
    )


def test_tools_list_includes_sql_query_when_assembled(tmp_path):
    listed = make_sql_server(tmp_path).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert [t["name"] for t in listed["result"]["tools"]] == ["kb_search", "ask", "sql_query"]


def test_sql_call_returns_structured_rows(monkeypatch, tmp_path):
    set_locator_enabled(monkeypatch, True)
    response = make_sql_server(tmp_path).handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "sql_query", "arguments": {"sql": "SELECT name FROM products"}},
        }
    )
    result = response["result"]
    assert result["isError"] is False
    assert "执行 SQL：" in result["content"][0]["text"]
    structured = result["structuredContent"]
    assert structured["row_count"] == 1
    assert structured["columns"] == ["name"]
    assert structured["citations"][0]["source_type"] == "sql"
    locator = structured["citations"][0]["locator"]
    assert locator["table"] == "demo.products"
    assert locator["sql"] == structured["sql"]
    assert locator["row_count"] == 1
    assert locator["truncated"] is False
    assert structured["evidence"][0]["locator"] == locator


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"sql": "DELETE FROM products"}, "只允许 SELECT"),
        ({"sql": "SELECT * FROM secrets"}, "未授权的表"),
        ({}, "sql"),
    ],
)
def test_sql_call_reports_guard_rejections_as_tool_error(tmp_path, arguments, expected):
    response = make_sql_server(tmp_path).handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sql_query", "arguments": arguments}}
    )
    assert response["result"]["isError"] is True
    assert expected in response["result"]["content"][0]["text"]


def test_script_defaults_to_no_sql(clean_rerank_env, monkeypatch, capsys):
    import private_rag.qa as qa

    monkeypatch.setattr(qa, "build_engine", lambda **kwargs: FakeEngine([make_chunk(1)]))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"),
    )

    assert load_script().main([]) == 0
    captured = capsys.readouterr()
    assert [t["name"] for t in json.loads(captured.out)["result"]["tools"]] == ["kb_search", "ask"]
    assert "sql_query" not in captured.err


def test_script_allow_sql_lists_three_tools(clean_rerank_env, monkeypatch, capsys, tmp_path):
    import private_rag.qa as qa

    monkeypatch.setattr(qa, "build_engine", lambda **kwargs: FakeEngine([make_chunk(1)]))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"),
    )
    config = tmp_path / "sources.yaml"
    config.write_text(sources_yaml(make_sqlite_db(tmp_path), opt_in="    allow_runtime_query: true\n"), encoding="utf-8")
    module = load_script()

    assert module.main(["--allow-sql", "--sources", str(config)]) == 0
    captured = capsys.readouterr()
    assert [t["name"] for t in json.loads(captured.out)["result"]["tools"]] == ["kb_search", "ask", "sql_query"]
    assert "sql_query" in captured.err  # 启动提示列出实际工具面


def test_script_allow_sql_without_opt_in_stays_kb_only_and_says_why(clean_rerank_env, monkeypatch, capsys, tmp_path):
    import private_rag.qa as qa

    monkeypatch.setattr(qa, "build_engine", lambda **kwargs: FakeEngine([make_chunk(1)]))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"),
    )
    config = tmp_path / "sources.yaml"
    config.write_text(sources_yaml(make_sqlite_db(tmp_path)), encoding="utf-8")
    module = load_script()

    assert module.main(["--allow-sql", "--sources", str(config)]) == 0
    captured = capsys.readouterr()
    assert [t["name"] for t in json.loads(captured.out)["result"]["tools"]] == ["kb_search", "ask"]
    assert "不暴露 sql_query" in captured.err
    assert "allow_runtime_query" in captured.err


def test_script_allow_sql_with_missing_config_explains_path(clean_rerank_env, monkeypatch, capsys, tmp_path):
    import private_rag.qa as qa

    monkeypatch.setattr(qa, "build_engine", lambda **kwargs: FakeEngine([make_chunk(1)]))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"),
    )
    module = load_script()

    assert module.main(["--allow-sql", "--sources", str(tmp_path / "sources.yaml")]) == 0
    captured = capsys.readouterr()
    assert [t["name"] for t in json.loads(captured.out)["result"]["tools"]] == ["kb_search", "ask"]
    assert "sql 源配置不存在" in captured.err
    assert str(tmp_path / "sources.yaml") in captured.err


def test_script_list_tools_does_not_open_sql_source(clean_rerank_env, monkeypatch, capsys, tmp_path):
    import private_rag.qa as qa

    def refuse(*args, **kwargs):
        raise AssertionError("--list-tools 不该打开知识库")

    monkeypatch.setattr(qa, "build_engine", refuse)
    module = load_script()

    assert module.main(["--list-tools", "--allow-sql"]) == 0
    definitions = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in definitions] == ["kb_search", "ask"]
