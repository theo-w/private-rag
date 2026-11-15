"""把检索片段组织成带编号引用的上下文，并调用本地 LLM 回答。"""

from __future__ import annotations

from typing import Iterator, Optional, Sequence

from langchain_ollama import ChatOllama

from private_rag.config import LLM_MODEL
from private_rag.models import Citation
from private_rag.retrieval.hybrid import RetrievedChunk

REFUSAL = "【资料中没有相关信息】"

PROMPT = """你是知识库问答助手，严格只根据下面带编号的上下文回答。
回答时请在相关句子后用 [编号] 标注依据，例如 [1]；不要编造上下文之外的编号或事实。
如果上下文没有答案，直接回复【资料中没有相关信息】，不要使用你自己的知识。
{history_section}上下文：
{context}

用户问题：
{question}
"""

History = Sequence[tuple[str, str]]


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
        # 父块展开后 text 是整段上下文，引用摘要仍用实际命中的子块
        snippet = " ".join((chunk.snippet_text or chunk.text).split())
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


def render_history(history: Optional[History]) -> str:
    """把会话历史渲染成 prompt 段落；历史只帮助理解指代，不作为事实依据。"""
    if not history:
        return ""
    labels = {"user": "用户", "assistant": "助手"}
    lines = []
    for role, content in history:
        label = labels.get(role)
        if not label:
            continue
        text = " ".join(str(content).split())
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def build_prompt(
    question: str,
    chunks: Sequence[RetrievedChunk],
    history: Optional[History] = None,
) -> str:
    history_text = render_history(history)
    history_section = ""
    if history_text:
        history_section = (
            "对话历史（仅用于理解当前问题的指代，不能作为事实依据）：\n"
            f"{history_text}\n\n"
        )
    return PROMPT.format(
        history_section=history_section,
        context=build_context(chunks),
        question=question,
    )


def generate_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    model: str = LLM_MODEL,
    history: Optional[History] = None,
) -> tuple[str, list[Citation]]:
    """没有候选时直接拒答，避免无意义的 LLM 调用。"""
    if not chunks:
        return REFUSAL, []

    response = ChatOllama(model=model).invoke(build_prompt(question, chunks, history))
    return response.content, build_citations(chunks)


def stream_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    model: str = LLM_MODEL,
    history: Optional[History] = None,
) -> Iterator[str]:
    """逐段生成回答；没有候选时直接返回拒答文本，不调用 LLM。"""
    if not chunks:
        yield REFUSAL
        return

    llm = ChatOllama(model=model)
    for response in llm.stream(build_prompt(question, chunks, history)):
        content = response.content
        if isinstance(content, list):
            # 少数 provider 返回 content blocks，统一拼成文本
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
            content = "".join(parts)
        if content:
            yield str(content)
