"""引用构建与拒答测试。"""

from __future__ import annotations

from private_rag.generation import REFUSAL, build_citations, build_context, generate_answer
from private_rag.retrieval.hybrid import RetrievedChunk


def _chunk(index: int, *, with_page: bool = False, with_table: bool = False) -> RetrievedChunk:
    metadata = {
        "title": f"文档{index}",
        "source_type": "file" if index == 1 else "sql",
        "source_uri": f"data/docs/{index}.pdf" if index == 1 else f"demo.products#{index}",
        "doc_id": f"doc-{index}",
        "chunk_index": 0,
    }
    if with_page:
        metadata["page"] = 2
    if with_table:
        metadata["table"] = "products"
    return RetrievedChunk(
        point_id=f"p{index}",
        text=f"第 {index} 段内容。" * 30,
        metadata=metadata,
        rerank_score=0.9 - index / 10,
    )


def test_build_context_numbers_sources():
    context = build_context([_chunk(1, with_page=True), _chunk(2, with_table=True)])
    assert "[1] (来源:" in context
    assert "[2] (来源:" in context
    assert "第 2 页" in context
    assert "表 products" in context


def test_build_citations_fields_and_snippet():
    citations = build_citations([_chunk(1, with_page=True), _chunk(2, with_table=True)])
    assert [citation.index for citation in citations] == [1, 2]
    assert citations[0].source_type == "file"
    assert citations[0].page == 2
    assert citations[1].table == "products"
    assert citations[0].score == 0.8
    assert citations[0].snippet.endswith("…")
    assert len(citations[0].snippet) <= 121


def test_empty_candidates_refuse_without_llm():
    answer, citations = generate_answer("苹果手机多少钱？", [])
    assert answer == REFUSAL
    assert citations == []
