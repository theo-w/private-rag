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
        chunk = merged.setdefault(
            item.point_id,
            RetrievedChunk(point_id=item.point_id, text=item.text, metadata=item.metadata),
        )
        chunk.dense_rank = rank
        chunk.dense_score = item.dense_score
        chunk.rrf_score += 1.0 / (rrf_k + rank)

    for rank, hit in enumerate(lexical, start=1):
        chunk = merged.setdefault(
            hit.point_id,
            RetrievedChunk(point_id=hit.point_id, text=hit.text, metadata=hit.metadata),
        )
        chunk.lexical_rank = rank
        chunk.lexical_score = hit.score
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
            payload = point.payload or {}
            results.append(
                RetrievedChunk(
                    point_id=str(point.id),
                    text=payload.get("page_content", ""),
                    metadata=payload.get("metadata", {}),
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
