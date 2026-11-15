"""父块扩展：按父块去重、遵守上下文预算，超预算时回退命中的子块。"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from private_rag.config import CONTEXT_MAX_CHARS
from private_rag.retrieval.hybrid import RetrievedChunk


def expand_parents(
    chunks: Sequence[RetrievedChunk],
    *,
    max_chars: int = CONTEXT_MAX_CHARS,
) -> list[RetrievedChunk]:
    """把命中的子块替换成父块文本。

    - 同一个父块被多个子块命中时只保留排名最靠前的一份；
    - 父块放不进剩余预算时回退该子块，避免整条上下文被丢弃；
    - 子块本身超预算时截断，保证总字符数不超过 max_chars；
    - 旧索引没有父块信息时原样返回子块。
    """
    if max_chars <= 0:
        return []

    expanded: list[RetrievedChunk] = []
    seen: set[str] = set()
    used = 0

    for chunk in chunks:
        key = chunk.parent_id or chunk.point_id
        if key in seen:
            continue
        seen.add(key)

        remaining = max_chars - used
        if remaining <= 0:
            break

        snippet = chunk.snippet_text or chunk.text
        parent_text = chunk.parent_text
        if parent_text and len(parent_text) <= remaining:
            text = parent_text
        elif len(chunk.text) <= remaining:
            text = chunk.text
        else:
            text = chunk.text[:remaining]

        if not text:
            continue
        expanded.append(replace(chunk, text=text, snippet_text=snippet))
        used += len(text)

    return expanded


def context_chars(chunks: Sequence[RetrievedChunk]) -> int:
    return sum(len(chunk.text) for chunk in chunks)
