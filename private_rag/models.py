"""领域数据结构。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SourceDocument:
    """一个可入库的原始文档单元（文件、网页、数据库行或 PDF 页）。"""

    doc_id: str
    text: str
    title: str
    source_type: str
    source_uri: str
    source_key: str
    updated_at: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class Citation:
    """回答中可展示的引用信息。"""

    index: int
    title: str
    source_type: str
    source_uri: str
    snippet: str
    page: Optional[int] = None
    table: Optional[str] = None
    score: Optional[float] = None


@dataclass
class IngestStats:
    """一次入库的汇总，CLI 和测试都复用它。"""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    pruned: int = 0
    chunks: int = 0
    lexical_chunks: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "IngestStats") -> None:
        self.added += other.added
        self.updated += other.updated
        self.skipped += other.skipped
        self.pruned += other.pruned
        self.chunks += other.chunks
        self.lexical_chunks = other.lexical_chunks or self.lexical_chunks
        self.errors.extend(other.errors)
