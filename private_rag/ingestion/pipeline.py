"""入库流水线：连接器 -> 切分 -> 幂等写入 -> 词法索引重建。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from private_rag.config import CHUNK_OVERLAP, CHUNK_SIZE, COLLECTION_NAME, LEXICAL_INDEX_PATH
from private_rag.ingestion.files import load_file_documents
from private_rag.ingestion.sql import load_sql_documents, redact_dsn
from private_rag.ingestion.web import load_web_documents
from private_rag.models import IngestStats, SourceDocument
from private_rag.retrieval.lexical import build_from_store
from private_rag.sources import (
    FileSource,
    SqlSource,
    SourcesConfig,
    WebSource,
    source_key_for,
)
from private_rag.store import (
    count_points,
    delete_doc,
    ensure_collection,
    find_doc_hash,
    prune_source,
)

Source = Union[FileSource, WebSource, SqlSource]
Logger = Callable[[str], None]

SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", " ", ""]
EMBED_BATCH_SIZE = 32


@dataclass
class LoadedSource:
    source_key: str
    label: str
    documents: list[SourceDocument]
    errors: list[str]


def source_label(source: Source) -> str:
    if isinstance(source, FileSource):
        return f"file:{source.path}"
    if isinstance(source, WebSource):
        return f"web:{len(source.urls)} 个 URL"
    return f"sql:{source.label or redact_dsn(source.dsn)}"


def load_source(source: Source) -> LoadedSource:
    key = source_key_for(source)
    if isinstance(source, FileSource):
        documents, errors = load_file_documents(source, key)
    elif isinstance(source, WebSource):
        documents, errors = load_web_documents(source, key)
    else:
        documents, errors = load_sql_documents(source, key)
    return LoadedSource(key, source_label(source), documents, errors)


def build_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
    )


def chunk_metadata(
    document: SourceDocument,
    chunk_index: int,
    chunk_count: int,
    content_hash: str,
) -> dict:
    """chunk 的 payload 元数据：既能过滤，也能直接生成引用。"""
    metadata = {
        "doc_id": document.doc_id,
        "source_type": document.source_type,
        "source_uri": document.source_uri,
        "source_key": document.source_key,
        "title": document.title,
        "tags": list(document.tags),
        "updated_at": document.updated_at,
        "content_hash": content_hash,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
    }
    for key in ("page", "table", "file_name", "format", "url", "sitename", "pk", "label"):
        value = document.extra.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _ingest_document(
    client: QdrantClient,
    embedding: Embeddings,
    document: SourceDocument,
    splitter: RecursiveCharacterTextSplitter,
    stats: IngestStats,
) -> None:
    content_hash = document.content_hash
    existing_hash = find_doc_hash(client, document.doc_id)
    if existing_hash == content_hash:
        stats.skipped += 1
        return

    chunks = [chunk.strip() for chunk in splitter.split_text(document.text)]
    chunks = [chunk for chunk in chunks if chunk]
    if not chunks:
        stats.skipped += 1
        return

    # 先算向量，再删除旧版本：embedding 失败时至少不会丢数据
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors.extend(embedding.embed_documents(batch))
    delete_doc(client, document.doc_id)

    points = []
    for index, (text, vector) in enumerate(zip(chunks, vectors)):
        point_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{document.doc_id}:{content_hash}:{index}",
            )
        )
        metadata = chunk_metadata(document, index, len(chunks), content_hash)
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={"page_content": text, "metadata": metadata},
            )
        )
    for start in range(0, len(points), 64):
        client.upsert(collection_name=COLLECTION_NAME, points=points[start : start + 64])
    if existing_hash is None:
        stats.added += 1
    else:
        stats.updated += 1
    stats.chunks += len(points)


def run_ingest(
    config: SourcesConfig,
    *,
    client: QdrantClient,
    embedding: Embeddings,
    recreate: bool = False,
    prune: bool = False,
    lexical_path: Path = LEXICAL_INDEX_PATH,
    rebuild_lexical: bool = True,
    logger: Logger = print,
) -> IngestStats:
    """按配置入库，最后重建 BM25 索引；单个 source 失败不阻塞其他来源。"""
    stats = IngestStats()
    if ensure_collection(client, embedding, recreate=recreate):
        logger("🆕 已创建集合（按 embedding 维度）")

    splitter = build_splitter()
    for source in config.sources:
        loaded = load_source(source)
        stats.errors.extend(loaded.errors)
        source_stats = IngestStats()

        for document in loaded.documents:
            try:
                _ingest_document(client, embedding, document, splitter, source_stats)
            except Exception as exc:
                source_stats.errors.append(f"写入失败 {document.doc_id}: {exc}")

        if prune:
            # 有加载错误的 source 不做 prune，避免把临时失败的页面/行误删
            if loaded.errors:
                logger(f"⚠️ {loaded.label} 存在加载错误，跳过 prune")
            else:
                removed = prune_source(client, loaded.source_key, [d.doc_id for d in loaded.documents])
                source_stats.pruned += removed

        stats.merge(source_stats)
        logger(
            f"{loaded.label}: 新增 {source_stats.added}，更新 {source_stats.updated}，"
            f"跳过 {source_stats.skipped}，写入 {source_stats.chunks} 个 chunk"
            + (f"，清理 {source_stats.pruned} 个旧向量" if source_stats.pruned else "")
        )

    if rebuild_lexical:
        stats.lexical_chunks = build_from_store(client, lexical_path)
        logger(f"🔎 词法索引已重建：{stats.lexical_chunks} 个 chunk -> {lexical_path}")

    logger(f"📦 集合现有 {count_points(client)} 个向量")
    return stats
