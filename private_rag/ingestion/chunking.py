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

# Markdown 结构分隔符：列表引导句与列表本体之间用单 \n 分隔，
# 如果恰好截断在 700 字符边界就会丢失列表本体。
# 这里把列表项和表格行作为额外分隔符，让切分器优先在结构边界处断开。
STRUCTURE_SEPARATORS = [
    "\n\n",       # 空行（最高优先级）
    "\n- ",       # 无序列表项开始
    "\n* ",       # 无序列表项开始（星号）
    "\n1. ",      # 有序列表项开始
    "\n2. ",
    "\n3. ",
    "\n4. ",
    "\n5. ",
    "\n6. ",
    "\n7. ",
    "\n8. ",
    "\n9. ",
    "\n| ",       # Markdown 表格行
    "\n```",      # 代码块边界
    "\n",         # 换行
    "。", "！", "？", ". ", " ", "",
]


def _split_by_markdown_structure(
    text: str,
    parent_splitter: RecursiveCharacterTextSplitter,
    parent_chunk_size: int = PARENT_CHUNK_SIZE,
) -> list[str]:
    """按 Markdown 结构分段，将连续列表块和表格视为原子单元。

    对不超过父块大小的结构段直接保留，不进一步切分；超过的再用
    原有的 RecursiveCharacterTextSplitter 切分。
    """
    lines = text.split("\n")
    segments: list[str] = []
    current_segment: list[str] = []
    in_code_block = False
    in_table = False

    for line in lines:
        stripped = line.strip()

        # 代码块边界
        if stripped.startswith("```"):
            if in_code_block:
                # 代码块结束：当前段 + 代码块行 一起保留
                if current_segment:
                    current_segment.append(line)
                segments.append("\n".join(current_segment))
                current_segment = []
                in_code_block = False
            else:
                # 代码块开始：先保存当前段
                if current_segment:
                    segments.append("\n".join(current_segment))
                    current_segment = []
                in_code_block = True
            continue

        if in_code_block:
            continue

        # 表格检测：连续以 | 开头的行
        if stripped.startswith("|"):
            if not in_table:
                in_table = True
            current_segment.append(line)
            continue
        elif in_table and stripped:
            # 表格结束
            segments.append("\n".join(current_segment))
            current_segment = []
            in_table = False

        # 标题行：硬边界，强制分段
        if stripped.startswith("#"):
            if current_segment:
                segments.append("\n".join(current_segment))
                current_segment = []
            current_segment.append(line)
            continue

        # 列表项检测
        if stripped and (
            stripped.startswith("- ")
            or stripped.startswith("* ")
            or stripped[0:3] in (
                "1. ",
                "2. ",
                "3. ",
                "4. ",
                "5. ",
                "6. ",
                "7. ",
                "8. ",
                "9. ",
            )
        ):
            # 列表项：追加到当前段，不强制分段
            # 但如果在列表中间遇到空行，先保存当前段
            if not current_segment:
                current_segment.append(line)
            else:
                # 检查上一行是否也是列表项
                last = current_segment[-1].strip()
                if last.startswith("- ") or last.startswith("* ") or (
                    last and last[0].isdigit() and ". " in last[:5]
                ):
                    current_segment.append(line)
                else:
                    current_segment.append(line)
            continue

        # 普通行：追加
        current_segment.append(line)

    # 收尾
    if in_code_block or in_table:
        if current_segment:
            segments.append("\n".join(current_segment))
    elif current_segment:
        segments.append("\n".join(current_segment))

    # 对每个段：不超过父块大小则保留，超过则用原有 splitter 切分
    result: list[str] = []
    for seg in segments:
        seg_stripped = seg.strip()
        if not seg_stripped:
            continue
        if len(seg_stripped) <= parent_chunk_size:
            result.append(seg_stripped)
        else:
            # 大段用原有 splitter 切分
            sub_chunks = parent_splitter.split_text(seg_stripped)
            result.extend([c for c in sub_chunks if c.strip()])

    return result


def build_splitter(size: int, overlap: int, *, structure_aware: bool = False) -> RecursiveCharacterTextSplitter:
    separators = STRUCTURE_SEPARATORS if structure_aware else SEPARATORS
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=separators,
    )


def build_parent_splitter(*, structure_aware: bool = True) -> RecursiveCharacterTextSplitter:
    return build_splitter(PARENT_CHUNK_SIZE, PARENT_CHUNK_OVERLAP, structure_aware=structure_aware)


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
    """把文档切成交替的父块/子块；父块为空或纯空白时跳过。

    父块切分默认启用 Markdown 结构感知：列表块和表格被视为原子单元，
    不会跨结构边界截断。
    """
    parent_splitter = parent_splitter or build_parent_splitter()
    child_splitter = child_splitter or build_child_splitter()

    # 结构感知预处理：按 Markdown 结构分段，再对大段用原有 splitter 切分
    parents = _split_by_markdown_structure(document.text, parent_splitter)
    parents = [text for text in parents if text.strip()]
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
