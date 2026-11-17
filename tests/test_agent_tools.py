"""工具层测试：知识库检索工具、运行时 SQL 工具、注册表与证据编号。"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from private_rag.agent.tools import (
    KnowledgeSearchTool,
    SqlQueryTool,
    Tool,
    ToolBlock,
    ToolRegistry,
    ToolResult,
    citation_label,
    render_evidence,
)
from private_rag.models import Citation
from private_rag.qa import RetrievalOptions
from private_rag.retrieval.hybrid import RetrievedChunk


def make_chunk(index: int, *, text: str = "", title: str = "", page=None, table=None) -> RetrievedChunk:
    metadata = {
        "source_type": "file" if table is None else "sql",
        "title": title or f"文档{index}",
        "source_uri": f"data/docs/{index}.md",
    }
    if page is not None:
        metadata["page"] = page
    if table is not None:
        metadata["table"] = table
    return RetrievedChunk(
        point_id=f"p{index}",
        text=text or f"这是第 {index} 段资料，里面写了做法和结论。",
        metadata=metadata,
        snippet_text=text or f"第 {index} 段",
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
            queries=[query, f"{query} 同义改写"],
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


class TestKnowledgeSearchTool:
    def test_returns_block_per_chunk_with_citations(self):
        engine = FakeEngine(chunks=[make_chunk(1), make_chunk(2)])
        result = KnowledgeSearchTool(engine).run({"query": "怎么增量入库"})

        assert result.ok is True
        assert result.error == ""
        assert len(result.blocks) == 2
        assert [c.index for c in [b.citation for b in result.blocks]] == [1, 2]
        assert result.blocks[0].citation.source_type == "file"
        assert "文档1" in result.blocks[0].label
        assert result.summary == "检索到 2 段资料"

    def test_query_aliases(self):
        engine = FakeEngine(chunks=[make_chunk(1)])
        assert KnowledgeSearchTool(engine).run({"q": "别名查询"}).ok is True
        assert engine.calls[0][0] == "别名查询"

    def test_empty_query_is_error(self):
        engine = FakeEngine()
        result = KnowledgeSearchTool(engine).run({"query": "   "})
        assert result.ok is False
        assert "query 不能为空" in result.error
        assert engine.calls == []

    def test_retrieval_failure_becomes_observation(self):
        # 检索失败必须作为观察回灌给模型，而不是让整个请求崩掉
        tool = KnowledgeSearchTool(FakeEngine(error=RuntimeError("qdrant down")))
        result = tool.run({"query": "问题"})
        assert result.ok is False
        assert result.error == "检索失败：RuntimeError"
        assert "检索失败" in result.observation

    def test_no_hits_is_success_without_blocks(self):
        result = KnowledgeSearchTool(FakeEngine(chunks=[])).run({"query": "无关问题"})
        assert result.ok is True
        assert result.blocks == []
        assert "没有检索到" in result.summary
        assert result.data["context_chars"] == 1234

    def test_options_passed_through(self):
        engine = FakeEngine(chunks=[make_chunk(1)])
        tool = KnowledgeSearchTool(
            engine,
            base_options=RetrievalOptions(source_type="file", query_rewrite="multi"),
            top_k=3,
        )
        tool.run({"query": "x"})
        options = engine.calls[0][1]
        assert options.top_k == 3
        assert options.source_type == "file"
        assert options.query_rewrite == "multi"

    def test_long_chunk_truncated(self):
        tool = KnowledgeSearchTool(FakeEngine(chunks=[make_chunk(1, text="字" * 5000)]), max_chars=200)
        result = tool.run({"query": "x"})
        # 单段证据要按字符上限截断，否则会挤掉别的工具的证据
        assert result.blocks[0].text.endswith("…")
        assert len(result.blocks[0].text) < 600


class TestSqlQueryTool:
    def tool(self, dsn, **kwargs):
        kwargs.setdefault("allowed_tables", ["products"])
        return SqlQueryTool(dsn=dsn, label="demo", **kwargs)

    def test_select_returns_rows_and_citation(self, tmp_path):
        result = self.tool(make_sqlite(tmp_path)).run(
            {"sql": "SELECT name, price FROM products WHERE price < 5"}
        )
        assert result.ok is True
        assert result.rows == 1
        assert result.blocks[0].citation.source_type == "sql"
        assert result.blocks[0].citation.table == "demo"
        assert "banana" in result.blocks[0].text
        assert "name | price" in result.blocks[0].text
        assert result.data["columns"] == ["name", "price"]
        assert "LIMIT" in result.data["sql"]

    def test_write_statement_rejected_by_guard(self, tmp_path):
        result = self.tool(make_sqlite(tmp_path)).run({"sql": "DELETE FROM products"})
        assert result.ok is False
        assert "只允许 SELECT" in result.error
        assert result.data["sql"] == "DELETE FROM products"

    def test_unauthorized_table_rejected(self, tmp_path):
        result = self.tool(make_sqlite(tmp_path)).run({"sql": "SELECT * FROM users"})
        assert result.ok is False
        assert "未授权的表" in result.error

    def test_execution_error_reported(self, tmp_path):
        tool = self.tool(make_sqlite(tmp_path), allowed_tables=["missing"])
        result = tool.run({"sql": "SELECT * FROM missing"})
        assert result.ok is False
        assert result.error.startswith("SQL 执行失败：")

    def test_no_sql_falls_back_to_whitelist_table(self, tmp_path):
        result = self.tool(make_sqlite(tmp_path)).run({})
        assert result.ok is True
        assert result.rows == 2
        assert any("模型未给出 SQL" in w for w in result.warnings)

    def test_no_sql_and_no_table_configured(self):
        tool = SqlQueryTool(dsn="sqlite:///x.db", label="demo", allowed_tables=[])
        result = tool.run({})
        assert result.ok is False
        assert "没有给出 SQL" in result.error

    def test_row_cap_enforced_even_for_full_table_scan(self, tmp_path):
        rows = [(f"p{i}", float(i)) for i in range(5)]
        result = self.tool(make_sqlite(tmp_path, rows), max_rows=3).run(
            {"sql": "SELECT name FROM products"}
        )
        assert result.ok is True
        # 护栏补的 LIMIT 已经保证不超过上限，不会走到「结果被截断」那一步
        assert "LIMIT 3" in result.data["sql"]
        assert result.rows == 3
        assert result.data["truncated"] is False
        assert result.blocks[0].text.count("\n") == 3  # 表头 + 3 行

    def test_model_requesting_more_rows_is_clamped(self, tmp_path):
        rows = [(f"p{i}", float(i)) for i in range(5)]
        result = self.tool(make_sqlite(tmp_path, rows), max_rows=2).run(
            {"sql": "SELECT name FROM products LIMIT 100"}
        )
        assert result.rows == 2
        assert any("LIMIT 被压缩" in w for w in result.warnings)

    def test_description_lists_tables_and_columns(self):
        tool = SqlQueryTool(
            dsn="sqlite:///x.db",
            label="demo",
            allowed_tables=["products", "orders"],
            schema_hint="products(name, price)",
        )
        assert "products" in tool.description
        assert "products(name, price)" in tool.description
        assert tool.spec().startswith("- sql_query:")


class TestToolRegistry:
    def test_register_get_and_describe(self):
        class Ping(Tool):
            name = "ping"
            description = "试探"

        registry = ToolRegistry([Ping()])
        assert "ping" in registry
        assert len(registry) == 1
        assert registry.names() == ["ping"]
        assert registry.get(" ping ") is not None
        assert "- ping: 试探" in registry.describe()

    def test_empty_registry_describes_none(self):
        assert "没有可用工具" in ToolRegistry().describe()

    def test_tool_without_name_rejected(self):
        class Nameless(Tool):
            pass

        with pytest.raises(ValueError, match="name"):
            ToolRegistry([Nameless()])

    def test_get_rejects_non_string_and_unknown(self):
        class Ping(Tool):
            name = "ping"

        registry = ToolRegistry([Ping()])
        assert registry.get(None) is None
        assert registry.get(123) is None
        assert registry.get("nope") is None
        assert ("nope" in registry) is False


class TestObservation:
    def test_summary_error_warnings_joined(self):
        result = ToolResult(True, summary="查询返回 2 行", warnings=["自动补上 LIMIT 20"])
        assert result.observation == "查询返回 2 行；· 自动补上 LIMIT 20"

    def test_failure_observation_contains_reason(self):
        result = ToolResult(False, error="SQL 执行失败")
        assert result.observation == "失败原因：SQL 执行失败"


class TestRenderEvidence:
    def citation(self, index: int) -> Citation:
        return Citation(index=index, title=f"来源{index}", source_type="file", source_uri=f"u{index}", snippet="s")

    def test_numbering_is_global_and_citations_reindexed(self):
        blocks = [
            ToolBlock(label="a", text="第一段", citation=self.citation(1)),
            ToolBlock(label="b", text="第二段", citation=self.citation(7)),
        ]
        text, citations = render_evidence(blocks)
        assert text.index("[1]") < text.index("[2]")
        assert [c.index for c in citations] == [1, 2]
        assert citations[1].title == "来源7"
        assert "(来源: b)" in text

    def test_start_index_offsets_numbering(self):
        text, citations = render_evidence([ToolBlock("a", "x", self.citation(1))], start_index=3)
        assert text.startswith("[3]")
        assert citations[0].index == 3

    def test_block_without_citation_marked_and_not_cited(self):
        text, citations = render_evidence([ToolBlock(label="a", text="无法溯源")])
        assert "[无引用]" in text
        assert citations == []

    def test_citation_label_includes_page_and_table(self):
        page = Citation(1, "手册", "file", "a.pdf", "s", page=12)
        table = Citation(2, "库", "sql", "b#sql", "s", table="demo.products")
        assert "第 12 页" in citation_label(page)
        assert "表 demo.products" in citation_label(table)
