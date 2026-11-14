"""共享问答引擎：检索、精排、同步/流式生成，CLI 与 API 复用同一条链路。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient

from private_rag.config import (
    COLLECTION_NAME,
    EMB_MODEL,
    LEXICAL_INDEX_PATH,
    LLM_MODEL,
    RERANK_ENABLED,
    TOP_K_RERANK,
)
from private_rag.generation import build_citations, generate_answer, stream_answer
from private_rag.models import Citation
from private_rag.retrieval.hybrid import HybridRetriever, RetrievedChunk
from private_rag.retrieval.lexical import LexicalIndex
from private_rag.retrieval.rerank import Reranker, load_reranker
from private_rag.store import close_client, count_points, open_client

logger = logging.getLogger(__name__)


@dataclass
class RetrievalOptions:
    source_type: Optional[str] = None
    tags: Optional[list[str]] = None
    top_k: int = TOP_K_RERANK
    use_lexical: bool = True
    use_rerank: bool = True


@dataclass
class QaResult:
    answer: str
    citations: list[Citation]
    retrieved: int
    used_lexical: bool
    used_rerank: bool


@dataclass
class QaEvent:
    """流式事件：citations -> token* -> done。"""

    kind: str
    text: str = ""
    citations: list[Citation] = field(default_factory=list)
    retrieved: int = 0
    used_lexical: bool = False
    used_rerank: bool = False


@dataclass
class Readiness:
    ready: bool
    vector_count: int
    lexical_available: bool
    reranker_available: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    collection_exists: bool = False


class RagEngine:
    """共享的检索与生成服务；Qdrant 本地 client 与 reranker 由锁保护。"""

    def __init__(
        self,
        client: QdrantClient,
        embedding: Embeddings,
        lexical: Optional[LexicalIndex] = None,
        reranker: Optional[Reranker] = None,
        *,
        llm_model: str = LLM_MODEL,
    ):
        self.client = client
        self.embedding = embedding
        self.lexical = lexical
        self.reranker = reranker
        self.llm_model = llm_model
        self._retrieval_lock = threading.Lock()
        self._rerank_lock = threading.Lock()

    def readiness(self) -> Readiness:
        reasons: list[str] = []
        warnings: list[str] = []
        vectors = 0
        try:
            with self._retrieval_lock:
                exists = self.client.collection_exists(COLLECTION_NAME)
                if exists:
                    vectors = count_points(self.client)
        except Exception:
            logger.exception("检查知识库状态失败")
            reasons.append("知识库访问失败，请检查 Qdrant 存储")
            exists = False

        if not exists:
            reasons.append("知识库集合不存在，请先运行 ingest")
        elif vectors <= 0:
            reasons.append("知识库为空，请先运行 ingest")

        if self.lexical is None:
            warnings.append("BM25 索引缺失，将只使用向量检索")
        if self.reranker is None:
            warnings.append("重排模型未加载，将使用融合结果")

        return Readiness(
            ready=not reasons,
            vector_count=vectors,
            lexical_available=self.lexical is not None,
            reranker_available=self.reranker is not None,
            reasons=reasons,
            warnings=warnings,
            collection_exists=exists,
        )

    def retrieve(
        self,
        question: str,
        options: RetrievalOptions,
    ) -> tuple[list[RetrievedChunk], bool, bool]:
        lexical = self.lexical if options.use_lexical else None
        retriever = HybridRetriever(
            self.client,
            self.embedding,
            lexical,
            source_type=options.source_type,
            tags=options.tags,
        )
        with self._retrieval_lock:
            chunks = retriever.retrieve(question)

        used_lexical = lexical is not None
        used_rerank = options.use_rerank and self.reranker is not None
        if used_rerank:
            with self._rerank_lock:
                chunks = self.reranker.rerank(question, chunks, top_k=options.top_k)
        else:
            chunks = chunks[: options.top_k]
        return chunks, used_lexical, used_rerank

    def answer(
        self,
        question: str,
        *,
        options: Optional[RetrievalOptions] = None,
        history: Optional[Sequence[tuple[str, str]]] = None,
    ) -> QaResult:
        options = options or RetrievalOptions()
        chunks, used_lexical, used_rerank = self.retrieve(question, options)
        answer, citations = generate_answer(
            question,
            chunks,
            model=self.llm_model,
            history=history,
        )
        return QaResult(
            answer=answer,
            citations=citations,
            retrieved=len(chunks),
            used_lexical=used_lexical,
            used_rerank=used_rerank,
        )

    def stream(
        self,
        question: str,
        *,
        options: Optional[RetrievalOptions] = None,
        history: Optional[Sequence[tuple[str, str]]] = None,
    ) -> Iterator[QaEvent]:
        options = options or RetrievalOptions()
        chunks, used_lexical, used_rerank = self.retrieve(question, options)
        citations = build_citations(chunks)
        yield QaEvent(
            "citations",
            citations=citations,
            retrieved=len(chunks),
            used_lexical=used_lexical,
            used_rerank=used_rerank,
        )

        parts: list[str] = []
        for token in stream_answer(
            question,
            chunks,
            model=self.llm_model,
            history=history,
        ):
            parts.append(token)
            yield QaEvent("token", text=token, retrieved=len(chunks))
        yield QaEvent(
            "done",
            text="".join(parts),
            citations=citations,
            retrieved=len(chunks),
            used_lexical=used_lexical,
            used_rerank=used_rerank,
        )

    def close(self) -> None:
        close_client(self.client)


def build_engine(
    *,
    client: Optional[QdrantClient] = None,
    embedding: Optional[Embeddings] = None,
    lexical_path: Path = LEXICAL_INDEX_PATH,
    use_rerank: Optional[bool] = None,
) -> RagEngine:
    """加载 Qdrant、BM25 与 reranker；缺失的组件会降级而不是让服务启动失败。"""
    if embedding is None:
        embedding = OllamaEmbeddings(model=EMB_MODEL)
    if client is None:
        client = open_client()
    lexical = LexicalIndex.load(lexical_path)
    if use_rerank is None:
        use_rerank = RERANK_ENABLED
    reranker = load_reranker() if use_rerank else None
    return RagEngine(client, embedding, lexical, reranker)
