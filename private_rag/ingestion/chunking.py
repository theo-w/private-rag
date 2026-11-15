"""父子块切分：父块保留完整语义，子块用于精准召回。

切分顺序是先把文档切成较大的父块（默认 2000 字符、不重叠），
再在每个父块内部切成较小的子块（默认 700 字符、100 重叠）。
检索只对子块做向量/BM25，命中后由检索层按 parent_id 回填父块文本。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from private_rag.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    PARENT_CHUNK_OVERLAP,
    PARENT_CHUNK_SIZE,
)
from private_rag.models import SourceDocument

SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", " ", ""]


def build_splitter(size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=SEPARATORS,
    )


def build_parent_splitter() -> RecursiveCharacterTextSplitter:
    return build_splitter(PARENT_CHUNK_SIZE, PARENT_CHUNK_OVERLAP)


def build_child_splitter() -> RecursiveCharacterTextSplitter:
    return build_splitter(CHUNK_SIZE, CHUNK_OVERLAP)


def parent_point_id(doc_id: str, content_hash: str, parent_index: int) -> str:
    """父块 ID 只由文档身份与父块序号决定，便于去重与增量更新。"""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{doc_id}:{content_hash}:parent:{parent_index}",
        )
    )


@dataclass(frozen=True)
class ChildChunk:
    """一个待入库的子块，同时携带它所属父块的信息。"""

    text: str
    parent_id: str
    parent_text: str
    parent_index: int
    parent_count: int
    parent_chunk_index: int


def split_parent_child(
    document: SourceDocument,
    *,
    parent_splitter: Optional[RecursiveCharacterTextSplitter] = None,
    child_splitter: Optional[RecursiveCharacterTextSplitter] = None,
) -> list[ChildChunk]:
    """把文档切成交替的父块/子块；父块为空或纯空白时跳过。"""
    parent_splitter = parent_splitter or build_parent_splitter()
    child_splitter = child_splitter or build_child_splitter()

    parents = [text.strip() for text in parent_splitter.split_text(document.text)]
    parents = [text for text in parents if text]
    if not parents:
        return []

    children: list[ChildChunk] = []
    for parent_index, parent_text in enumerate(parents):
        parent_id = parent_point_id(
            document.doc_id, document.content_hash, parent_index
        )
        parent_children = [
            text.strip() for text in child_splitter.split_text(parent_text)
        ]
        parent_children = [text for text in parent_children if text]
        for parent_chunk_index, child_text in enumerate(parent_children):
            children.append(
                ChildChunk(
                    text=child_text,
                    parent_id=parent_id,
                    parent_text=parent_text,
                    parent_index=parent_index,
                    parent_count=len(parents),
                    parent_chunk_index=parent_chunk_index,
                )
            )
    return children
