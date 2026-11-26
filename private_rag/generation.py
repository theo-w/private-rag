"""把检索片段组织成带编号引用的上下文，并调用本地 LLM 回答。"""

from __future__ import annotations

from typing import Any, Iterator, Optional, Sequence

from langchain_ollama import ChatOllama

from private_rag.config import LLM_MODEL
from private_rag.llm_params import classic_generation_kwargs, seed_kwargs
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


def chunk_locator(chunk: RetrievedChunk, block_text: str = "") -> dict[str, Any]:
    """这条引用出自哪一块、命中片段在实际返回的那段文本里的什么位置。

    只给对外通道用（MCP 的工具返回），agentic 循环不读它。**拿不到的键直接不出现**，
    与 `build_citations` 的省略口径一致，不臆造数字：

    - `chunk_id` 是入库时算出的块身份（`doc_id:内容哈希:块序号` 的稳定 id），内容一改就换 id。
    - `doc_id` / `chunk_index` / `chunk_count` 指**命中的子块**，不是父块扩展后的整段；
      开启父块扩展时另外给 `parent_id`。
    - `snippet_offset` 相对调用方传进来的 `block_text` 算（父块扩展后返回文本比命中片段长，
      截断以后也更短），命中片段不在那段文本里时整个不出现。
    """
    metadata = chunk.metadata or {}
    locator: dict[str, Any] = {}
    chunk_id = str(getattr(chunk, "point_id", "") or "")
    if chunk_id:
        locator["chunk_id"] = chunk_id
    if metadata.get("doc_id") is not None:
        locator["doc_id"] = str(metadata["doc_id"])
    for key in ("chunk_index", "chunk_count"):
        value = metadata.get(key)
        if value is not None:
            try:
                locator[key] = int(value)
            except (TypeError, ValueError):
                pass
    parent_id = getattr(chunk, "parent_id", None) or metadata.get("parent_id")
    if parent_id:
        locator["parent_id"] = str(parent_id)
    if metadata.get("page") is not None:
        locator["page"] = int(metadata["page"])
    if metadata.get("table") is not None:
        locator["table"] = str(metadata["table"])
    snippet = str(getattr(chunk, "snippet_text", "") or "")
    if snippet:
        offset = str(block_text or "").find(snippet)
        if offset >= 0:
            locator["snippet_offset"] = offset
    return locator


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


def _llm_kwargs(model: str) -> dict[str, Any]:
    """经典链路的模型参数：默认只有 ``model``，与改动前一致。

    改动前这两处既不传温度也不传 seed，用的是服务端默认抽样，同一条上下文两次生成可以
    给出不同答案；评估链路会把两者钉死（见 ``configure_eval_env``），运行时仍然不传。
    """
    kwargs: dict[str, Any] = {"model": model}
    kwargs.update(classic_generation_kwargs())
    kwargs.update(seed_kwargs())
    return kwargs


def generate_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    model: str = LLM_MODEL,
    history: Optional[History] = None,
) -> tuple[str, list[Citation]]:
    """没有候选时直接拒答，避免无意义的 LLM 调用。"""
    if not chunks:
        return REFUSAL, []

    response = ChatOllama(**_llm_kwargs(model)).invoke(build_prompt(question, chunks, history))
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

    llm = ChatOllama(**_llm_kwargs(model))
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
