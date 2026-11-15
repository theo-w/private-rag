"""RRF 融合与元数据过滤测试。"""

from __future__ import annotations

import pytest

from private_rag.retrieval.hybrid import (
    HybridRetriever,
    RetrievedChunk,
    build_filter,
    reciprocal_rank_fusion,
)
from private_rag.retrieval.lexical import LexicalHit


def _chunk(point_id: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        point_id=point_id,
        text=f"text-{point_id}",
        metadata={"doc_id": f"doc-{point_id}", "chunk_index": 0},
        dense_score=score,
    )


def _hit(point_id: str, score: float = 1.0) -> LexicalHit:
    return LexicalHit(
        point_id=point_id,
        text=f"text-{point_id}",
        metadata={"doc_id": f"doc-{point_id}", "chunk_index": 0},
        score=score,
    )


def test_rrf_prefers_chunk_hit_by_both_routes():
    fused = reciprocal_rank_fusion([_chunk("a"), _chunk("b")], [_hit("b"), _hit("c")], top_k=3)
    assert [chunk.point_id for chunk in fused] == ["b", "a", "c"]
    assert fused[0].dense_rank == 2
    assert fused[0].lexical_rank == 1
    assert fused[0].rrf_score > fused[1].rrf_score


def test_rrf_deduplicates_same_point_id():
    fused = reciprocal_rank_fusion([_chunk("a")], [_hit("a")], top_k=5)
    assert len(fused) == 1
    assert fused[0].rrf_score == pytest.approx(2 / 61)


def test_rrf_respects_top_k():
    fused = reciprocal_rank_fusion(
        [_chunk(f"p{i}") for i in range(10)],
        [],
        top_k=3,
    )
    assert len(fused) == 3


def test_build_filter_empty():
    assert build_filter() is None
    assert build_filter(source_type="web") is not None
    assert build_filter(tags=["business"]) is not None


def test_build_filter_conditions():
    qdrant_filter = build_filter(source_type="sql", tags=["business", "demo"])
    assert qdrant_filter is not None
    assert len(qdrant_filter.must) == 2
    assert qdrant_filter.must[0].key == "metadata.source_type"
    assert qdrant_filter.must[1].key == "metadata.tags"


class _FakeResponse:
    def __init__(self, points):
        self.points = points


class _FakePoint:
    def __init__(
        self,
        point_id: str,
        score: float,
        text: str,
        metadata: dict,
        parent: dict | None = None,
    ):
        self.id = point_id
        self.score = score
        self.payload = {"page_content": text, "metadata": metadata}
        if parent is not None:
            self.payload["parent"] = parent


class _FakeClient:
    def __init__(self):
        self.kwargs = None

    def query_points(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse([_FakePoint("dense-1", 0.9, "dense text", {"source_type": "file"})])


class _FakeEmbedding:
    def embed_query(self, text: str):
        return [0.1, 0.2, 0.3]


def test_hybrid_retriever_passes_filters_and_degrades_without_lexical():
    client = _FakeClient()
    retriever = HybridRetriever(
        client,
        _FakeEmbedding(),
        lexical=None,
        source_type="file",
        tags=["local"],
    )
    chunks = retriever.retrieve("苹果")
    assert retriever.lexical_available is False
    assert [chunk.point_id for chunk in chunks] == ["dense-1"]
    assert client.kwargs["query_filter"] is not None
    assert client.kwargs["limit"] == retriever.dense_top_k


class _ParentClient:
    def query_points(self, **kwargs):
        return _FakeResponse(
            [
                _FakePoint(
                    "dense-1",
                    0.9,
                    "命中子块",
                    {"source_type": "file"},
                    parent={"id": "parent-1", "index": 0, "count": 2, "text": "父块全文"},
                )
            ]
        )


def test_dense_search_parses_parent_fields():
    chunks = HybridRetriever(_ParentClient(), _FakeEmbedding()).dense_search("苹果")
    assert chunks[0].text == "命中子块"
    assert chunks[0].parent_id == "parent-1"
    assert chunks[0].parent_text == "父块全文"
    assert chunks[0].parent_index == 0


def test_rrf_fills_parent_from_lexical_when_dense_lacks_it():
    dense = [_chunk("a")]
    hit = LexicalHit(
        point_id="a",
        text="text-a",
        metadata={"doc_id": "doc-a"},
        score=1.0,
        parent={"id": "parent-a", "text": "父块 A", "index": 0},
    )
    fused = reciprocal_rank_fusion(dense, [hit], top_k=1)
    assert fused[0].parent_id == "parent-a"
    assert fused[0].parent_text == "父块 A"
    assert fused[0].dense_rank == 1
    assert fused[0].lexical_rank == 1
