"""MCP 工具层测试：入参校验、编号证据与引用载荷、只读边界。

全部用假引擎：不打模型、不联网、不打开真实 Qdrant。
"""

from __future__ import annotations

import pytest

from private_rag.config import MAX_QUESTION_CHARS, TOP_K_RERANK
from private_rag.generation import REFUSAL, build_citations
from private_rag.mcp.evidence import citation_payload
from private_rag.mcp.tools import (
    STORAGE_LOCKED_HINT,
    AskTool,
    KbSearchTool,
    build_mcp_tools,
    failure_reason,
)
from private_rag.models import Citation
from private_rag.qa import QaResult, Readiness, RetrievalTrace
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
    """只实现 MCP 工具用到的三个方法，行为由构造参数决定。"""

    def __init__(
        self,
        chunks=(),
        *,
        ready: bool = True,
        reasons=(),
        warnings=(),
        error: Exception | None = None,
        response: str = "橙子富含维生素 C。[1]",
    ):
        self.chunks = list(chunks)
        self.ready = ready
        self.reasons = list(reasons)
        self.warnings = list(warnings)
        self.error = error
        self.response = response
        self.readiness_calls = 0
        self.retrieve_calls: list[tuple[str, object]] = []
        self.answer_calls: list[tuple[str, object]] = []

    def readiness(self) -> Readiness:
        self.readiness_calls += 1
        return Readiness(
            ready=self.ready,
            vector_count=len(self.chunks),
            lexical_available=True,
            reranker_available=True,
            reasons=list(self.reasons),
            warnings=list(self.warnings),
            collection_exists=self.ready,
        )

    def retrieve_with_trace(self, query: str, options) -> RetrievalTrace:
        self.retrieve_calls.append((query, options))
        if self.error is not None:
            raise self.error
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
        self.answer_calls.append((question, options))
        if self.error is not None:
            raise self.error
        citations = build_citations(self.chunks)
        return QaResult(
            answer=self.response if citations else REFUSAL,
            citations=citations,
            retrieved=len(self.chunks),
            used_lexical=True,
            used_rerank=True,
            used_parent=True,
            query_rewrite="off",
            query_count=1,
            context_chars=1234,
        )


# 工具定义


def test_tool_definitions_declare_read_only_surface():
    tools = build_mcp_tools(None)
    assert [tool.name for tool in tools] == ["kb_search", "ask"]
    for tool in tools:
        definition = tool.definition()
        assert definition["annotations"]["readOnlyHint"] is True
        assert definition["annotations"]["destructiveHint"] is False
        assert definition["outputSchema"]["type"] == "object"
        assert definition["inputSchema"]["type"] == "object"


def test_kb_search_schema_requires_query_and_documents_filters():
    schema = build_mcp_tools(None)[0].definition()["inputSchema"]
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "limit", "tags", "tag", "source_type"}
    assert schema["properties"]["source_type"]["enum"] == ["file", "web", "sql"]


def test_ask_schema_requires_question():
    schema = build_mcp_tools(None)[1].definition()["inputSchema"]
    assert schema["required"] == ["question"]
    assert set(schema["properties"]) == {"question", "limit", "tags", "tag", "source_type"}


def test_tool_without_engine_reports_configuration_error():
    outcome = KbSearchTool(None).run({"query": "橙子"})
    assert outcome.ok is False
    assert "未注入" in outcome.error


# kb_search：证据与引用


def test_kb_search_returns_numbered_evidence_and_citations():
    engine = FakeEngine([make_chunk(1, page=3), make_chunk(2)])
    outcome = KbSearchTool(engine).run({"query": "橙子的营养"})

    assert outcome.ok is True
    assert outcome.payload["summary"] == "检索到 2 段资料"
    assert [item["index"] for item in outcome.payload["evidence"]] == [1, 2]
    assert [item["index"] for item in outcome.payload["citations"]] == [1, 2]
    assert "[1]" in outcome.text and "[2]" in outcome.text
    assert "引用列表：" in outcome.text
    assert outcome.payload["retrieved"] == 2
    assert outcome.payload["used_rerank"] is True
    assert outcome.payload["context_chars"] == 1234


def test_kb_search_citation_omits_absent_fields_and_keeps_present_ones():
    engine = FakeEngine([make_chunk(1, page=7), make_chunk(2, table="products")])
    first, second = KbSearchTool(engine).run({"query": "x"}).payload["citations"]
    assert first["page"] == 7
    assert "table" not in first and "score" not in first
    assert second["table"] == "products"
    assert second["source_type"] == "sql"


def test_kb_search_truncates_evidence_to_char_budget():
    long_text = "长" * 4000
    engine = FakeEngine([make_chunk(1, text=long_text), make_chunk(2, text=long_text)])
    outcome = KbSearchTool(engine, max_chars=600).run({"query": "x"})

    for item in outcome.payload["evidence"]:
        assert len(item["text"]) <= 600
        assert item["text"].endswith("…")


def test_kb_search_empty_result_is_not_an_error():
    outcome = KbSearchTool(FakeEngine([])).run({"query": "没有的内容"})
    assert outcome.ok is True
    assert outcome.payload["evidence"] == []
    assert outcome.payload["citations"] == []
    assert "没有检索到" in outcome.payload["summary"]


def test_kb_search_passes_filters_through_to_retrieval_options():
    engine = FakeEngine([make_chunk(1)])
    KbSearchTool(engine, default_limit=5).run(
        {"query": "橙子", "limit": 2, "tags": ["水果", "营养"], "tag": "维生素", "source_type": "file"}
    )
    _, options = engine.retrieve_calls[0]
    assert options.top_k == 2
    assert options.tags == ["水果", "营养", "维生素"]
    assert options.source_type == "file"


def test_kb_search_defaults_limit_from_server():
    engine = FakeEngine([make_chunk(1)])
    KbSearchTool(engine).run({"query": "橙子"})
    assert engine.retrieve_calls[0][1].top_k == TOP_K_RERANK


def test_kb_search_dedupes_tags_and_treats_empty_source_type_as_no_filter():
    engine = FakeEngine([make_chunk(1)])
    KbSearchTool(engine).run({"query": "橙子", "tags": ["a", " a ", "b", ""], "source_type": "  "})
    _, options = engine.retrieve_calls[0]
    assert options.tags == ["a", "b"]
    assert options.source_type is None


# kb_search：参数与故障


@pytest.mark.parametrize("args", [{"query": ""}, {"query": "   "}, {"query": 5}, {}, {"query": "橙" * (MAX_QUESTION_CHARS + 1)}])
def test_kb_search_rejects_bad_query(args):
    outcome = KbSearchTool(FakeEngine([make_chunk(1)])).run(args)
    assert outcome.ok is False
    assert "query" in outcome.error


@pytest.mark.parametrize("limit", [0, 11, -1, "3", True, 1.5])
def test_kb_search_rejects_bad_limit(limit):
    outcome = KbSearchTool(FakeEngine([make_chunk(1)])).run({"query": "x", "limit": limit})
    assert outcome.ok is False
    assert "limit" in outcome.error


@pytest.mark.parametrize(
    "tags",
    ["水果", ["水果", 1], ["x" * 51], ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]],
)
def test_kb_search_rejects_bad_tags(tags):
    outcome = KbSearchTool(FakeEngine([make_chunk(1)])).run({"query": "x", "tags": tags})
    assert outcome.ok is False
    assert "tag" in outcome.error


def test_kb_search_rejects_bad_source_type_and_unknown_argument():
    engine = FakeEngine([make_chunk(1)])
    bad_type = KbSearchTool(engine).run({"query": "x", "source_type": "doc"})
    assert bad_type.ok is False and "source_type" in bad_type.error

    unknown = KbSearchTool(engine).run({"query": "x", "top_k": 3})
    assert unknown.ok is False
    assert "top_k" in unknown.error


def test_kb_search_reports_not_ready_with_reasons():
    engine = FakeEngine([], ready=False, reasons=["知识库集合不存在，请先运行 ingest"], warnings=["BM25 索引缺失"])
    outcome = KbSearchTool(engine).run({"query": "x"})
    assert outcome.ok is False
    assert "请先运行 ingest" in outcome.error
    assert "BM25 索引缺失" in outcome.error
    assert engine.retrieve_calls == []


def test_kb_search_reports_retrieval_failure():
    outcome = KbSearchTool(FakeEngine(error=RuntimeError("嵌入服务不可用"))).run({"query": "x"})
    assert outcome.ok is False
    assert "检索失败" in outcome.error
    assert "RuntimeError" in outcome.error


def test_kb_search_translates_storage_lock_to_actionable_hint():
    locked = RuntimeError("Storage folder 'storage/qdrant_storage' is already accessed by another instance of Qdrant client")
    outcome = KbSearchTool(FakeEngine(error=locked)).run({"query": "x"})
    assert outcome.ok is False
    assert STORAGE_LOCKED_HINT in outcome.error
    assert "检索失败" in outcome.error


def test_failure_reason_keeps_exception_type_and_clip():
    reason = failure_reason(ValueError("x" * 500))
    assert reason.startswith("ValueError：")
    assert len(reason) <= 220


# ask：回答与引用


def test_ask_returns_answer_with_citations():
    engine = FakeEngine([make_chunk(1, page=2)])
    outcome = AskTool(engine).run({"question": "橙子有什么营养"})

    assert outcome.ok is True
    assert outcome.payload["answer"] == "橙子富含维生素 C。[1]"
    assert [c["index"] for c in outcome.payload["citations"]] == [1]
    assert outcome.payload["no_evidence"] is False
    assert outcome.payload["meta"]["retrieved"] == 1
    assert outcome.payload["meta"]["elapsed_ms"] >= 0
    assert "橙子富含维生素 C。[1]" in outcome.text
    assert "引用列表：" in outcome.text


def test_ask_without_evidence_marks_no_evidence():
    engine = FakeEngine([])
    outcome = AskTool(engine).run({"question": "库里没有的问题"})
    assert outcome.ok is True
    assert outcome.payload["no_evidence"] is True
    assert outcome.payload["answer"] == REFUSAL
    assert outcome.payload["citations"] == []


def test_ask_passes_options_and_checks_readiness_first():
    engine = FakeEngine([make_chunk(1)], ready=False, reasons=["知识库为空，请先运行 ingest"])
    outcome = AskTool(engine).run({"question": "x"})
    assert outcome.ok is False and "请先运行 ingest" in outcome.error
    assert engine.answer_calls == []

    ready_engine = FakeEngine([make_chunk(1)])
    AskTool(ready_engine, default_limit=4).run({"question": "x", "limit": 1, "tag": "营养"})
    question, options = ready_engine.answer_calls[0]
    assert question == "x"
    assert options.top_k == 1 and options.tags == ["营养"]


def test_ask_reports_generation_failure():
    outcome = AskTool(FakeEngine(error=ConnectionError("Ollama 未启动"))).run({"question": "x"})
    assert outcome.ok is False
    assert "问答失败" in outcome.error
    assert "Ollama 未启动" in outcome.error


@pytest.mark.parametrize("args", [{"question": ""}, {"question": None}, {"question": "问" * (MAX_QUESTION_CHARS + 1)}])
def test_ask_rejects_bad_question(args):
    outcome = AskTool(FakeEngine([make_chunk(1)])).run(args)
    assert outcome.ok is False
    assert "question" in outcome.error


def test_citation_payload_shape():
    citation = Citation(
        index=2,
        title="橙子",
        source_type="sql",
        source_uri="demo.products#1",
        snippet="name: 橙子",
        table="products",
        score=0.8123,
    )
    payload = citation_payload(citation)
    assert payload["index"] == 2 and payload["table"] == "products" and payload["score"] == 0.8123
    assert "page" not in payload
