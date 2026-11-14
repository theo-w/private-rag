"""文档入库脚本：把 txt / md / pdf 切分、向量化并写入本地 Qdrant。

特点：
- 支持单个文件或整个目录（递归扫描）
- 按文件内容 hash 做幂等：内容没变时直接跳过
- 文件更新后先删除该 source 的旧向量，再写入新向量，避免残留
- 只在入库时打开向量库，问答脚本不再重建

用法：
    python scripts/ingest.py                     # 增量入库 data/docs 目录
    python scripts/ingest.py --recreate          # 清空集合并全量重建
    python scripts/ingest.py --path data/docs/a.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from pathlib import Path

# 允许从任意目录直接运行脚本时导入项目根目录下的 config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointIdsList, VectorParams

from config.settings import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    DOCS_DIR,
    EMB_MODEL,
    ROOT,
    STORAGE_DIR,
)

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}

embedding = OllamaEmbeddings(model=EMB_MODEL)


def file_hash(path: Path) -> str:
    """文件内容 sha256，用于判断是否需要重新入库。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_documents(path: Path) -> list[Document]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PyPDFLoader(str(path)).load()
    if suffix in {".txt", ".md"}:
        return TextLoader(str(path), encoding="utf-8").load()
    raise ValueError(f"不支持的文件类型：{path}")


def iter_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def source_name(path: Path) -> str:
    """记录相对项目根的路径，引用展示更简洁；项目外的文件保留绝对路径。"""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def indexed_hash(client: QdrantClient, source: str) -> str | None:
    """返回该 source 已入库的文件 hash；没有记录时返回 None。"""
    condition = Filter(must=[FieldCondition(key="metadata.source", match=MatchValue(value=source))])
    points, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=condition,
        limit=1,
        with_payload=True,
    )
    if not points:
        return None
    metadata = points[0].payload.get("metadata", {})
    return metadata.get("file_hash")


def delete_source(client: QdrantClient, source: str) -> int:
    """删除某个 source 下的全部旧向量，返回删除数量。"""
    condition = Filter(must=[FieldCondition(key="metadata.source", match=MatchValue(value=source))])
    ids: list[str] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=condition,
            limit=256,
            offset=offset,
            with_payload=False,
        )
        ids.extend(str(p.id) for p in points)
        if offset is None:
            break
    if ids:
        client.delete(COLLECTION_NAME, points_selector=PointIdsList(points=ids))
    return len(ids)


def ensure_collection(client: QdrantClient) -> None:
    """集合不存在时按 embedding 维度创建（bge-m3 实测 1024 维）。"""
    if client.collection_exists(COLLECTION_NAME):
        return
    dim = len(embedding.embed_query("维度探测"))
    client.create_collection(
        COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"🆕 已创建集合 {COLLECTION_NAME}（{dim} 维）")


def main() -> int:
    parser = argparse.ArgumentParser(description="把文档写入本地 Qdrant 向量库")
    parser.add_argument("--path", default=str(DOCS_DIR), help="文件或目录路径，默认 data/docs")
    parser.add_argument("--recreate", action="store_true", help="清空集合并全量重建")
    args = parser.parse_args()

    target = Path(args.path)
    files = iter_files(target)
    if not files:
        print(f"⚠️ 没有找到可入库文件：{target}")
        return 1

    client = QdrantClient(path=str(STORAGE_DIR))
    try:
        if args.recreate and client.collection_exists(COLLECTION_NAME):
            client.delete_collection(COLLECTION_NAME)
            print("🧹 已清空集合")
        ensure_collection(client)

        store = QdrantVectorStore(
            client=client,
            collection_name=COLLECTION_NAME,
            embedding=embedding,
        )
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        added = skipped = 0
        for path in files:
            content_hash = file_hash(path)
            source = source_name(path)

            # 非重建模式下，内容没变就跳过，保证重复执行不会产生重复向量
            if not args.recreate and indexed_hash(client, source) == content_hash:
                print(f"⏭️ 跳过（内容未变）：{source}")
                skipped += 1
                continue

            documents = load_documents(path)
            for doc in documents:
                # 统一覆盖为项目相对路径，便于引用展示和后续按 source 删除
                doc.metadata.update(
                    {
                        "source": source,
                        "file_name": path.name,
                        "file_hash": content_hash,
                    }
                )
            chunks = splitter.split_documents(documents)
            if not chunks:
                print(f"⚠️ 文件没有可入库内容：{source}")
                continue

            # 同一 source 若存在旧版本，先删掉，避免新旧内容同时被检索到
            removed = delete_source(client, source)
            if removed:
                print(f"🧹 已移除旧版本 {removed} 个向量：{source}")

            # 确定性 ID：同一文件同一块重复写入时是覆盖而不是新增
            ids = [
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{content_hash}:{i}"))
                for i in range(len(chunks))
            ]
            store.add_documents(chunks, ids=ids)
            print(f"✅ 已入库：{source}，{len(chunks)} 个 chunk")
            added += 1

        total = client.count(COLLECTION_NAME).count if client.collection_exists(COLLECTION_NAME) else 0
        print(f"\n📊 完成：新增/更新 {added} 个文件，跳过 {skipped} 个，集合共 {total} 个向量")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
