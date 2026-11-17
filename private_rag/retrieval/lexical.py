"""jieba 分词 + BM25 词法索引，落盘后供混合检索复用。"""

from __future__ import annotations

import os
import pickle
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from rank_bm25 import BM25Okapi
from qdrant_client import QdrantClient

from private_rag.config import LEXICAL_INDEX_PATH, LEXICAL_TOP_K
from private_rag.retrieval.tokenizer import STOPWORDS, tokenize
from private_rag.store import scroll_all

def _passes_filter(metadata: dict, source_type: Optional[str], tags: Optional[Sequence[str]]) -> bool:
    if source_type and metadata.get("source_type") != source_type:
        return False
    if tags:
        document_tags = set(metadata.get("tags") or [])
        if not document_tags.intersection(tags):
            return False
    return True


@dataclass
class LexicalHit:
    point_id: str
    text: str
    metadata: dict
    score: float
    # 父块信息随 payload 一起走，命中后可直接展开父块；旧索引没有此字段。
    parent: Optional[dict] = None


class LexicalIndex:
    """一份可持久化的 BM25 索引；文档内容与 Qdrant payload 保持一致。"""

    VERSION = 1

    def __init__(self, documents: list[dict]):
        self.documents = documents
        self.tokens = [tokenize(document.get("text", "")) for document in documents]
        self.bm25 = BM25Okapi(self.tokens) if self.tokens else None

    def __len__(self) -> int:
        return len(self.documents)

    def search(
        self,
        query: str,
        top_k: int = LEXICAL_TOP_K,
        source_type: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> list[LexicalHit]:
        query_tokens = tokenize(query)
        if not query_tokens or self.bm25 is None:
            return []

        scores = self.bm25.get_scores(query_tokens)
        hits: list[LexicalHit] = []
        for score, document in zip(scores, self.documents):
            if score <= 0:
                # 与查询没有任何词项重叠：不打分、不进入候选
                continue
            if not _passes_filter(document.get("metadata", {}), source_type, tags):
                continue
            hits.append(
                LexicalHit(
                    point_id=document["point_id"],
                    text=document.get("text", ""),
                    metadata=document.get("metadata", {}),
                    score=float(score),
                    parent=document.get("parent"),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.point_id))
        return hits[:top_k]

    def save(self, path: Path = LEXICAL_INDEX_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "tokenizer": "jieba",
            "documents": self.documents,
        }
        # 原子替换，避免进程中断留下半个索引
        with tempfile.NamedTemporaryFile("wb", dir=str(path.parent), delete=False) as handle:
            pickle.dump(payload, handle, protocol=4)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        return path

    @classmethod
    def load(cls, path: Path = LEXICAL_INDEX_PATH) -> Optional["LexicalIndex"]:
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                payload = pickle.load(handle)
        except Exception:
            return None
        if payload.get("version") != cls.VERSION:
            return None
        return cls(payload.get("documents", []))


def build_from_store(
    client: QdrantClient,
    path: Path = LEXICAL_INDEX_PATH,
    logger: Optional[Callable[[str], None]] = None,
) -> int:
    """从 Qdrant 全量 payload 重建词法索引，返回索引条数。"""
    records = scroll_all(client, with_payload=True)
    documents = []
    for record in records:
        payload = record.payload or {}
        text = payload.get("page_content", "")
        if not text:
            continue
        documents.append(
            {
                "point_id": str(record.id),
                "text": text,
                "metadata": payload.get("metadata", {}),
                "parent": payload.get("parent"),
            }
        )
    index = LexicalIndex(documents)
    path.parent.mkdir(parents=True, exist_ok=True)
    index.save(path)
    if logger:
        logger(f"词法索引写入 {path}（{len(index)} 条）")
    return len(index)
