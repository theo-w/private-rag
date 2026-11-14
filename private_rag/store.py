"""Qdrant client 与集合操作的公共封装。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    VectorParams,
)

from private_rag.config import COLLECTION_NAME, STORAGE_DIR


def open_client(path: Path = STORAGE_DIR) -> QdrantClient:
    return QdrantClient(path=str(path))


def close_client(client: QdrantClient) -> None:
    try:
        client.close()
    except Exception:
        pass


def ensure_collection(client: QdrantClient, embedding: Embeddings, recreate: bool = False) -> bool:
    """集合不存在时创建；recreate=True 时先删除重建。返回是否新建。"""
    exists = client.collection_exists(COLLECTION_NAME)
    if recreate and exists:
        client.delete_collection(COLLECTION_NAME)
        exists = False
    if exists:
        return False

    dim = len(embedding.embed_query("维度探测"))
    client.create_collection(
        COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    return True


def doc_filter(doc_id: str) -> Filter:
    return Filter(
        must=[FieldCondition(key="metadata.doc_id", match=MatchValue(value=doc_id))]
    )


def source_filter(source_key: str) -> Filter:
    return Filter(
        must=[FieldCondition(key="metadata.source_key", match=MatchValue(value=source_key))]
    )


def find_doc_hash(client: QdrantClient, doc_id: str) -> Optional[str]:
    """返回某个 doc_id 已入库的 content_hash；没有记录时返回 None。"""
    if not client.collection_exists(COLLECTION_NAME):
        return None
    points, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=doc_filter(doc_id),
        limit=1,
        with_payload=True,
    )
    if not points:
        return None
    metadata = (points[0].payload or {}).get("metadata", {})
    return metadata.get("content_hash")


def delete_doc(client: QdrantClient, doc_id: str) -> int:
    """删除某个 doc_id 的全部向量，返回删除数量。"""
    if not client.collection_exists(COLLECTION_NAME):
        return 0
    ids: list[str] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=doc_filter(doc_id),
            limit=256,
            offset=offset,
            with_payload=False,
        )
        ids.extend(str(point.id) for point in points)
        if offset is None:
            break
    if ids:
        client.delete(COLLECTION_NAME, points_selector=PointIdsList(points=ids))
    return len(ids)


def scroll_all(
    client: QdrantClient,
    scroll_filter: Optional[Filter] = None,
    with_payload: bool = True,
    page_size: int = 256,
) -> list:
    """分页读取集合内全部 point（用于词法索引与 prune）。"""
    if not client.collection_exists(COLLECTION_NAME):
        return []
    records = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=page_size,
            offset=offset,
            with_payload=with_payload,
            with_vectors=False,
        )
        records.extend(points)
        if offset is None:
            break
    return records


def prune_source(client: QdrantClient, source_key: str, seen_doc_ids: Sequence[str]) -> int:
    """删除某个 source 下本次没有再次出现的 doc_id。"""
    seen = set(seen_doc_ids)
    records = scroll_all(client, scroll_filter=source_filter(source_key), with_payload=True)
    stale_ids: list[str] = []
    for record in records:
        metadata = (record.payload or {}).get("metadata", {})
        if metadata.get("doc_id") not in seen:
            stale_ids.append(str(record.id))
    if stale_ids:
        client.delete(COLLECTION_NAME, points_selector=PointIdsList(points=stale_ids))
    return len(stale_ids)


def count_points(client: QdrantClient) -> int:
    if not client.collection_exists(COLLECTION_NAME):
        return 0
    return client.count(COLLECTION_NAME).count
