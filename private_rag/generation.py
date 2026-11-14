"""把检索片段组织成带编号引用的上下文，并调用本地 LLM 回答。"""

from __future__ import annotations

from typing import Sequence

from langchain_ollama import ChatOllama

from private_rag.config import LLM_MODEL
from private_rag.models import Citation
from private_rag.retrieval.hybrid import RetrievedChunk

REFUSAL = "【资料中没有相关信息】"

PROMPT = """你是知识库问答助手，严格只根据下面带编号的上下文回答。
回答时请在相关句子后用 [编号] 标注依据，例如 [1]；不要编造上下文之外的编号或事实。
如果上下文没有答案，直接回复【资料中没有相关信息】，不要使用你自己的知识。

上下文：
{context}

用户问题：
{question}
"""


def _source_brief(metadata: dict) -> str:
    parts = [metadata.get("source_type", "unknown")]
    if metadata.get("title"):
        parts.append(str(metadata["title"]))
    if metadata.get("source_uri"):
        parts.append(str(metadata["source_uri"]))
    if metadata.get("page"):
        parts.append(f"第 {metadata['page']} 页")
    if metadata.get("table"):
        parts.append(f"表 {metadata['table']}")
    return " | ".join(parts)


def build_context(chunks: Sequence[RetrievedChunk]) -> str:
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        brief = _source_brief(chunk.metadata)
        blocks.append(f"[{index}] (来源: {brief})\n{chunk.text}")
    return "\n\n".join(blocks)


def build_citations(chunks: Sequence[RetrievedChunk]) -> list[Citation]:
    citations: list[Citation] = []
    for index, chunk in enumerate(chunks, start=1):
        metadata = chunk.metadata
        snippet = " ".join(chunk.text.split())
        if len(snippet) > 120:
            snippet = snippet[:120] + "…"
        citations.append(
            Citation(
                index=index,
                title=str(metadata.get("title") or metadata.get("doc_id") or "未知来源"),
                source_type=str(metadata.get("source_type") or "unknown"),
                source_uri=str(metadata.get("source_uri") or ""),
                snippet=snippet,
                page=int(metadata["page"]) if metadata.get("page") is not None else None,
                table=str(metadata["table"]) if metadata.get("table") is not None else None,
                score=chunk.rerank_score,
            )
        )
    return citations


def generate_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    model: str = LLM_MODEL,
) -> tuple[str, list[Citation]]:
    """没有候选时直接拒答，避免无意义的 LLM 调用。"""
    if not chunks:
        return REFUSAL, []

    response = ChatOllama(model=model).invoke(
        PROMPT.format(context=build_context(chunks), question=question)
    )
    return response.content, build_citations(chunks)
