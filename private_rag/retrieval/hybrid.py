"""向量召回 + BM25 召回 + RRF 融合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from private_rag.config import (
    COLLECTION_NAME,
    DENSE_TOP_K,
    FUSION_TOP_K,
    LEXICAL_TOP_K,
    RRF_K,
)
from private_rag.retrieval.lexical import LexicalHit, LexicalIndex


@dataclass
class RetrievedChunk:
    point_id: str
    text: str
    metadata: dict
    dense_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    dense_score: Optional[float] = None
    lexical_score: Optional[float] = None
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None
    # 父块字段只在父子块索引中存在；旧数据保持 None，自动回退子块。
    parent_id: Optional[str] = None
    parent_text: Optional[str] = None
    parent_index: Optional[int] = None
    # 命中的子块原文，用于引用摘要；展开父块后 text 会变成长文本。
    snippet_text: Optional[str] = None

    @classmethod
    def from_payload(
        cls,
        point_id: str,
        payload: Optional[dict],
        **kwargs,
    ) -> "RetrievedChunk":
        """统一解析 Qdrant payload；旧数据没有 parent 字段时按子块处理。"""
        payload = payload or {}
        parent = payload.get("parent")
        if not isinstance(parent, dict):
            parent = {}
        return cls(
            point_id=str(point_id),
            text=str(payload.get("page_content", "")),
            metadata=payload.get("metadata") or {},
            parent_id=parent.get("id"),
            parent_text=parent.get("text"),
            parent_index=parent.get("index"),
            **kwargs,
        )


def _fill_parent(target: RetrievedChunk, source: RetrievedChunk) -> None:
    """融合时补全父块字段：两路命中同一子块，任一路带父块即可。"""
    if target.parent_id is None and source.parent_id is not None:
        target.parent_id = source.parent_id
        target.parent_text = source.parent_text
        target.parent_index = source.parent_index


def _chunk_from_lexical(hit: LexicalHit) -> RetrievedChunk:
    parent = hit.parent if isinstance(hit.parent, dict) else {}
    return RetrievedChunk(
        point_id=hit.point_id,
        text=hit.text,
        metadata=hit.metadata,
        parent_id=parent.get("id"),
        parent_text=parent.get("text"),
        parent_index=parent.get("index"),
    )


def _chunk_from_dense(item: RetrievedChunk) -> RetrievedChunk:
    return RetrievedChunk(
        point_id=item.point_id,
        text=item.text,
        metadata=item.metadata,
        parent_id=item.parent_id,
        parent_text=item.parent_text,
        parent_index=item.parent_index,
    )


def build_filter(
    source_type: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
) -> Optional[Filter]:
    conditions = []
    if source_type:
        conditions.append(
            FieldCondition(key="metadata.source_type", match=MatchValue(value=source_type))
        )
    if tags:
        conditions.append(
            FieldCondition(key="metadata.tags", match=MatchAny(any=list(tags)))
        )
    return Filter(must=conditions) if conditions else None


def reciprocal_rank_fusion(
    dense: Sequence[RetrievedChunk],
    lexical: Sequence[LexicalHit],
    *,
    rrf_k: int = RRF_K,
    top_k: int = FUSION_TOP_K,
) -> list[RetrievedChunk]:
    """按 point id 去重融合两路排名：score = Σ 1/(k + rank)。"""
    merged: dict[str, RetrievedChunk] = {}

    for rank, item in enumerate(dense, start=1):
        chunk = merged.setdefault(item.point_id, _chunk_from_dense(item))
        _fill_parent(chunk, item)
        chunk.dense_rank = rank
        chunk.dense_score = item.dense_score
        chunk.rrf_score += 1.0 / (rrf_k + rank)

    for rank, hit in enumerate(lexical, start=1):
        lexical_chunk = _chunk_from_lexical(hit)
        chunk = merged.setdefault(hit.point_id, lexical_chunk)
        _fill_parent(chunk, lexical_chunk)
        chunk.lexical_rank = rank
        chunk.lexical_score = hit.score
        chunk.rrf_score += 1.0 / (rrf_k + rank)

    return sorted(merged.values(), key=lambda chunk: (-chunk.rrf_score, chunk.point_id))[:top_k]


def fuse_query_results(
    ranked_lists: Sequence[Sequence[RetrievedChunk]],
    *,
    rrf_k: int = RRF_K,
    top_k: int = FUSION_TOP_K,
) -> list[RetrievedChunk]:
    """融合多个查询各自的结果：按 point id 去重并累加 RRF 分数。

    单个查询内部已经做过 dense/BM25 融合；这里再做一层跨查询 RRF，
    让同时被多个改写查询命中的子块排名更靠前。
    """
    merged: dict[str, RetrievedChunk] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            chunk = merged.setdefault(item.point_id, _chunk_from_dense(item))
            _fill_parent(chunk, item)
            if chunk.dense_rank is None:
                chunk.dense_rank = item.dense_rank
                chunk.dense_score = item.dense_score
                chunk.lexical_rank = item.lexical_rank
                chunk.lexical_score = item.lexical_score
            chunk.rrf_score += 1.0 / (rrf_k + rank)
    return sorted(merged.values(), key=lambda chunk: (-chunk.rrf_score, chunk.point_id))[:top_k]


class HybridRetriever:
    """dense + BM25 混合召回；词法索引缺失时自动退回纯向量检索。"""

    def __init__(
        self,
        client: QdrantClient,
        embedding: Embeddings,
        lexical: Optional[LexicalIndex] = None,
        *,
        source_type: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        dense_top_k: int = DENSE_TOP_K,
        lexical_top_k: int = LEXICAL_TOP_K,
        fusion_top_k: int = FUSION_TOP_K,
    ):
        self.client = client
        self.embedding = embedding
        self.lexical = lexical
        self.source_type = source_type
        self.tags = list(tags) if tags else None
        self.dense_top_k = dense_top_k
        self.lexical_top_k = lexical_top_k
        self.fusion_top_k = fusion_top_k

    @property
    def lexical_available(self) -> bool:
        return self.lexical is not None

    def dense_search(self, question: str) -> list[RetrievedChunk]:
        response = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=self.embedding.embed_query(question),
            query_filter=build_filter(self.source_type, self.tags),
            limit=self.dense_top_k,
            with_payload=True,
        )
        results: list[RetrievedChunk] = []
        for point in response.points:
            results.append(
                RetrievedChunk.from_payload(
                    str(point.id),
                    point.payload,
                    dense_score=float(point.score),
                )
            )
        return results

    def lexical_search(self, question: str) -> list[LexicalHit]:
        if self.lexical is None:
            return []
        return self.lexical.search(
            question,
            top_k=self.lexical_top_k,
            source_type=self.source_type,
            tags=self.tags,
        )

    def retrieve(self, question: str) -> list[RetrievedChunk]:
        dense = self.dense_search(question)
        lexical = self.lexical_search(question)
        return reciprocal_rank_fusion(
            dense,
            lexical,
            rrf_k=RRF_K,
            top_k=self.fusion_top_k,
        )
