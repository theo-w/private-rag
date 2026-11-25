"""共享问答引擎：检索、精排、同步/流式生成，CLI 与 API 复用同一条链路。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Optional, Sequence

from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient

from private_rag.config import (
    COLLECTION_NAME,
    CONTEXT_MAX_CHARS,
    EMB_MODEL,
    LEXICAL_INDEX_PATH,
    LLM_MODEL,
    RERANK_ENABLED,
    TOP_K_RERANK,
    TREE_INDEX_ENABLED,
    TREE_INDEX_PATH,
    TREE_KNOWLEDGE_ONLY,
)
from private_rag.generation import build_citations, generate_answer, stream_answer
from private_rag.models import Citation
from private_rag.query_rewrite import rewrite_queries
from private_rag.retrieval.hybrid import (
    HybridRetriever,
    RetrievedChunk,
    fuse_query_results,
)
from private_rag.retrieval.lexical import LexicalIndex
from private_rag.retrieval.parent import context_chars as count_context_chars, expand_parents
from private_rag.retrieval.rerank import Reranker, load_reranker
from private_rag.retrieval.tree import TreeIndex, attach_tree_context
from private_rag.store import close_client, count_points, open_client

logger = logging.getLogger(__name__)


@dataclass
class RetrievalOptions:
    source_type: Optional[str] = None
    tags: Optional[list[str]] = None
    top_k: int = TOP_K_RERANK
    use_lexical: bool = True
    use_rerank: bool = True
    use_parent: bool = True
    # None 表示「按 config.TREE_INDEX_ENABLED 决定」，在调用时读而不是构造时读，
    # 这样同一个进程里可以在两轮之间打开/关闭树索引做 A/B。
    use_tree: Optional[bool] = None
    query_rewrite: str = "off"
    # 路由判出的意图（`knowledge` / `database` / `mixed` / `none`），只有树这一路的开关判定用得到；
    # 空字符串表示「没有意图」——经典链路与固定检索就是这种，它们没有工具循环。
    intent: str = ""


def tree_enabled_for_intent(intent: str) -> bool:
    """树这一路此刻要不要启用（每次检索时读配置，不是启动时定死）。

    按意图收窄是因为实跑读数：打开这一路以后混合问题读到一整节带路径的正文就判定证据够了，
    少查一次数据库，`both_sides_hit` 退一条（`docs/ROADMAP.md` 的 v8.5）。
    没有意图也当知识处理：经典链路一次检索一次生成，不存在提前收手这回事。
    """
    if not TREE_INDEX_ENABLED:
        return False
    if not TREE_KNOWLEDGE_ONLY:
        return True
    return str(intent or "") in ("", "knowledge")


@dataclass
class RetrievalTrace:
    """一次检索的完整诊断信息，API meta 与评估报告共用。"""

    chunks: list[RetrievedChunk]
    used_lexical: bool
    used_rerank: bool
    used_parent: bool
    queries: list[str]
    query_rewrite: str
    query_count: int
    context_chars: int
    rewrite_fallback: bool = False
    rewrite_reason: str = ""
    # 树索引这一路：used_tree 表示上下文里真的出现过章节还原，tree_sections 是节数
    used_tree: bool = False
    tree_sections: int = 0


@dataclass
class QaResult:
    answer: str
    citations: list[Citation]
    retrieved: int
    used_lexical: bool
    used_rerank: bool
    used_parent: bool = False
    query_rewrite: str = "off"
    query_count: int = 1
    context_chars: int = 0
    used_tree: bool = False


@dataclass
class QaEvent:
    """流式事件：citations -> token* -> done。"""

    kind: str
    text: str = ""
    citations: list[Citation] = field(default_factory=list)
    retrieved: int = 0
    used_lexical: bool = False
    used_rerank: bool = False
    used_parent: bool = False
    query_rewrite: str = "off"
    query_count: int = 1
    context_chars: int = 0
    used_tree: bool = False


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
        tree: Optional[TreeIndex] = None,
        *,
        llm_model: str = LLM_MODEL,
    ):
        self.client = client
        self.embedding = embedding
        self.lexical = lexical
        self.reranker = reranker
        # 只要文件在就加载，开关只决定用不用，方便同进程 A/B 来回切
        self.tree = tree
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
        # 只在开关打开却没有索引时提醒：默认关闭的情况下没有索引是正常的，不该报警
        if TREE_INDEX_ENABLED and self.tree is None:
            warnings.append("树索引缺失，章节上下文这一路不可用")

        return Readiness(
            ready=not reasons,
            vector_count=vectors,
            lexical_available=self.lexical is not None,
            reranker_available=self.reranker is not None,
            reasons=reasons,
            warnings=warnings,
            collection_exists=exists,
        )

    def retrieve_with_trace(
        self,
        question: str,
        options: RetrievalOptions,
    ) -> RetrievalTrace:
        """查询改写 -> 多查询召回融合 -> 原问题精排 -> 父块扩展。"""
        rewrite = rewrite_queries(
            question,
            options.query_rewrite,
            model=self.llm_model,
        )
        lexical = self.lexical if options.use_lexical else None
        retriever = HybridRetriever(
            self.client,
            self.embedding,
            lexical,
            source_type=options.source_type,
            tags=options.tags,
        )
        with self._retrieval_lock:
            ranked = [retriever.retrieve(query) for query in rewrite.queries]

        if len(ranked) == 1:
            chunks = ranked[0]
        else:
            chunks = fuse_query_results(ranked)

        used_lexical = lexical is not None
        used_rerank = options.use_rerank and self.reranker is not None
        # 精排始终用原问题：改写查询只负责扩大召回，不改变相关性判断
        if used_rerank:
            with self._rerank_lock:
                chunks = self.reranker.rerank(question, chunks, top_k=options.top_k)
        else:
            chunks = chunks[: options.top_k]

        use_tree = (
            options.use_tree if options.use_tree is not None
            else tree_enabled_for_intent(options.intent)
        )
        used_tree = False
        used_parent = False
        tree_sections = 0

        if use_tree and self.tree is not None:
            tree_chunks = attach_tree_context(
                chunks, self.tree, max_chars=CONTEXT_MAX_CHARS
            )
            tree_sections = sum(1 for chunk in tree_chunks if chunk.tree_node_id)
            if tree_sections:
                # 树这一路与父块扩展并列，成功还原时不再做父块扩展：
                # 两次替换文本会互相覆盖，上下文口径也就说不清了
                chunks = tree_chunks
                used_tree = True

        if not used_tree:
            if options.use_parent:
                chunks = expand_parents(chunks, max_chars=CONTEXT_MAX_CHARS)
                used_parent = any(chunk.parent_id for chunk in chunks)
            else:
                chunks = [
                    replace(chunk, snippet_text=chunk.snippet_text or chunk.text)
                    for chunk in chunks
                ]

        return RetrievalTrace(
            chunks=chunks,
            used_lexical=used_lexical,
            used_rerank=used_rerank,
            used_parent=used_parent,
            queries=list(rewrite.queries),
            query_rewrite=options.query_rewrite,
            query_count=len(rewrite.queries),
            context_chars=count_context_chars(chunks),
            rewrite_fallback=rewrite.fallback,
            rewrite_reason=rewrite.reason,
            used_tree=used_tree,
            tree_sections=tree_sections,
        )

    def retrieve(
        self,
        question: str,
        options: RetrievalOptions,
    ) -> tuple[list[RetrievedChunk], bool, bool]:
        trace = self.retrieve_with_trace(question, options)
        return trace.chunks, trace.used_lexical, trace.used_rerank

    def answer(
        self,
        question: str,
        *,
        options: Optional[RetrievalOptions] = None,
        history: Optional[Sequence[tuple[str, str]]] = None,
    ) -> QaResult:
        options = options or RetrievalOptions()
        trace = self.retrieve_with_trace(question, options)
        answer, citations = generate_answer(
            question,
            trace.chunks,
            model=self.llm_model,
            history=history,
        )
        return QaResult(
            answer=answer,
            citations=citations,
            retrieved=len(trace.chunks),
            used_lexical=trace.used_lexical,
            used_rerank=trace.used_rerank,
            used_parent=trace.used_parent,
            query_rewrite=trace.query_rewrite,
            query_count=trace.query_count,
            context_chars=trace.context_chars,
            used_tree=trace.used_tree,
        )

    def stream(
        self,
        question: str,
        *,
        options: Optional[RetrievalOptions] = None,
        history: Optional[Sequence[tuple[str, str]]] = None,
    ) -> Iterator[QaEvent]:
        options = options or RetrievalOptions()
        trace = self.retrieve_with_trace(question, options)
        chunks = trace.chunks
        citations = build_citations(chunks)
        yield QaEvent(
            "citations",
            citations=citations,
            retrieved=len(chunks),
            used_lexical=trace.used_lexical,
            used_rerank=trace.used_rerank,
            used_parent=trace.used_parent,
            query_rewrite=trace.query_rewrite,
            query_count=trace.query_count,
            context_chars=trace.context_chars,
            used_tree=trace.used_tree,
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
            used_lexical=trace.used_lexical,
            used_rerank=trace.used_rerank,
            used_parent=trace.used_parent,
            query_rewrite=trace.query_rewrite,
            query_count=trace.query_count,
            context_chars=trace.context_chars,
            used_tree=trace.used_tree,
        )

    def close(self) -> None:
        close_client(self.client)


def build_engine(
    *,
    client: Optional[QdrantClient] = None,
    embedding: Optional[Embeddings] = None,
    lexical_path: Path = LEXICAL_INDEX_PATH,
    tree_path: Path = TREE_INDEX_PATH,
    use_rerank: Optional[bool] = None,
) -> RagEngine:
    """加载 Qdrant、BM25 与 reranker；缺失的组件会降级而不是让服务启动失败。"""
    if embedding is None:
        embedding = OllamaEmbeddings(model=EMB_MODEL)
    if client is None:
        client = open_client()
    lexical = LexicalIndex.load(lexical_path)
    # 树索引在开关关闭时也加载：开关在每次检索时读，同进程可以来回切
    tree = TreeIndex.load(tree_path)
    if use_rerank is None:
        use_rerank = RERANK_ENABLED
    reranker = load_reranker() if use_rerank else None
    return RagEngine(client, embedding, lexical, reranker, tree)
