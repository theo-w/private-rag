"""入库流水线测试：幂等、更新替换、prune 与词法索引重建（不依赖 Ollama）。"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient

from private_rag.ingestion.files import load_file_documents
from private_rag.ingestion.pipeline import run_ingest
from private_rag.retrieval.lexical import LexicalIndex
from private_rag.sources import FileSource, SourcesConfig, source_key_for
from private_rag.store import find_doc_hash, scroll_all


class FakeEmbeddings(Embeddings):
    """确定性向量，保证测试不依赖 Ollama。"""

    def __init__(self, dim: int = 8):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255.0 + 0.01 for byte in digest[: self.dim]]

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


def _run(client, config, lexical_path, prune=False):
    return run_ingest(
        config,
        client=client,
        embedding=FakeEmbeddings(),
        prune=prune,
        lexical_path=lexical_path,
        logger=lambda _: None,
    )


def test_ingest_is_idempotent_and_updates_replaced_vectors(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    file_a = docs_dir / "a.txt"
    file_b = docs_dir / "b.txt"
    file_a.write_text("苹果适合冷藏保存。", encoding="utf-8")
    file_b.write_text("香蕉不适合放冰箱。", encoding="utf-8")

    config = SourcesConfig(sources=[FileSource(path=str(docs_dir), tags=["test"])])
    lexical_path = tmp_path / "lexical" / "bm25.pkl"
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        first = _run(client, config, lexical_path)
        assert first.added == 2
        assert first.skipped == 0
        assert first.chunks >= 2
        assert first.lexical_chunks == first.chunks
        assert LexicalIndex.load(lexical_path) is not None

        second = _run(client, config, lexical_path)
        assert second.added == 0
        assert second.updated == 0
        assert second.skipped == 2
        assert second.chunks == 0

        # 修改其中一个文件：只替换该 doc 的向量
        source = source_key_for(config.sources[0])
        documents, _ = load_file_documents(config.sources[0], source)
        doc_a = next(document for document in documents if "a.txt" in document.doc_id)
        old_hash = doc_a.content_hash
        old_ids = {
            str(record.id)
            for record in scroll_all(client)
            if (record.payload or {}).get("metadata", {}).get("doc_id") == doc_a.doc_id
        }

        file_a.write_text("苹果适合冷藏保存，切开后建议尽快食用。", encoding="utf-8")
        third = _run(client, config, lexical_path)
        assert third.updated == 1
        assert third.skipped == 1
        assert third.added == 0

        new_hash = find_doc_hash(client, doc_a.doc_id)
        assert new_hash is not None and new_hash != old_hash
        new_ids = {
            str(record.id)
            for record in scroll_all(client)
            if (record.payload or {}).get("metadata", {}).get("doc_id") == doc_a.doc_id
        }
        assert not (old_ids & new_ids)
    finally:
        client.close()


def test_ingest_persists_parent_payload_and_lexical_parent(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    # 两段各 1500 字，按默认 2000 字符父块切分会得到两个父块，
    # 每个父块再被 700/100 的子块切分器切成多个子块。
    (docs_dir / "long.txt").write_text(
        "甲" * 1500 + "\n\n" + "乙" * 1500,
        encoding="utf-8",
    )

    config = SourcesConfig(sources=[FileSource(path=str(docs_dir))])
    lexical_path = tmp_path / "bm25.pkl"
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        _run(client, config, lexical_path)
        records = list(scroll_all(client))
        assert len(records) > 2

        by_parent = defaultdict(list)
        for record in records:
            payload = record.payload or {}
            parent = payload["parent"]
            assert set(parent) == {"id", "index", "count", "text"}
            assert parent["count"] == 2
            assert payload["metadata"]["parent_id"] == parent["id"]
            assert payload["metadata"]["parent_index"] == parent["index"]
            assert payload["metadata"]["parent_count"] == parent["count"]
            assert payload["page_content"] in parent["text"]
            by_parent[parent["id"]].append(record)

        assert len(by_parent) == 2
        for items in by_parent.values():
            parent_text = items[0].payload["parent"]["text"]
            assert len(parent_text) <= 2000
            assert all(item.payload["parent"]["text"] == parent_text for item in items)

        index = LexicalIndex.load(lexical_path)
        assert index is not None
        assert len(index.documents) == len(records)
        assert {document["parent"]["id"] for document in index.documents} == set(by_parent)
        assert all(document["parent"]["text"] for document in index.documents)
    finally:
        client.close()


def test_prune_removes_disappeared_file(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    file_a = docs_dir / "a.txt"
    file_b = docs_dir / "b.txt"
    file_a.write_text("苹果适合冷藏。", encoding="utf-8")
    file_b.write_text("橙子富含维生素 C。", encoding="utf-8")

    config = SourcesConfig(sources=[FileSource(path=str(docs_dir))])
    lexical_path = tmp_path / "bm25.pkl"
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        _run(client, config, lexical_path)

        source = source_key_for(config.sources[0])
        documents, _ = load_file_documents(config.sources[0], source)
        doc_b = next(document for document in documents if "b.txt" in document.doc_id)
        doc_b_chunks = sum(
            1
            for record in scroll_all(client)
            if (record.payload or {}).get("metadata", {}).get("doc_id") == doc_b.doc_id
        )

        file_b.unlink()
        stats = _run(client, config, lexical_path, prune=True)
        assert stats.pruned == doc_b_chunks
        assert find_doc_hash(client, doc_b.doc_id) is None
        assert stats.lexical_chunks >= 1
    finally:
        client.close()


def test_ingest_recreate_clears_collection(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.txt").write_text("猕猴桃维生素 C 含量高。", encoding="utf-8")

    config = SourcesConfig(sources=[FileSource(path=str(docs_dir))])
    lexical_path = tmp_path / "bm25.pkl"
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        _run(client, config, lexical_path)
        stats = run_ingest(
            config,
            client=client,
            embedding=FakeEmbeddings(),
            recreate=True,
            lexical_path=lexical_path,
            logger=lambda _: None,
        )
        assert stats.added == 1
    finally:
        client.close()
