"""bge-reranker 精排：对融合后的候选做 cross-encoder 打分。"""

from __future__ import annotations

from typing import Optional, Sequence

from private_rag.config import RERANK_MODEL, TOP_K_RERANK
from private_rag.retrieval.hybrid import RetrievedChunk


class Reranker:
    """延迟加载模型，轻量命令（如纯检索测试）不必付出加载成本。"""

    def __init__(self, model_name: str = RERANK_MODEL, use_fp16: bool = True):
        from FlagEmbedding import FlagReranker

        self.model = FlagReranker(model_name, use_fp16=use_fp16)

    def rerank(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        top_k: int = TOP_K_RERANK,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        scores = self.model.compute_score([[question, chunk.text] for chunk in chunks])
        if not isinstance(scores, list):
            scores = [scores]
        for chunk, score in zip(chunks, scores):
            chunk.rerank_score = float(score)
        return sorted(
            chunks, key=lambda chunk: (chunk.rerank_score or 0.0), reverse=True
        )[:top_k]


def load_reranker(model_name: str = RERANK_MODEL) -> Optional[Reranker]:
    try:
        return Reranker(model_name)
    except Exception as exc:
        print(f"⚠️ 重排模型加载失败，将直接使用融合结果：{exc}")
        return None
