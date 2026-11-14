"""冒烟测试：验证向量库可用、检索能命中对应水果段落，可选验证完整问答。

默认只测检索层（快）；加 --e2e 会加载重排模型并调用 LLM（慢）。
运行前需先执行：python scripts/ingest.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

import chat_cli
from config.settings import (
    COLLECTION_NAME,
    EMB_MODEL,
    STORAGE_DIR,
    TOP_K_RECALL,
)

# 库内问题 -> 期望命中的关键词
RETRIEVAL_CASES = [
    ("苹果适合放冰箱吗？", "苹果"),
    ("香蕉能不能放冰箱？", "香蕉"),
    ("橙子有什么营养？", "橙子"),
]


def check_store() -> None:
    """集合存在且有数据，否则给出可操作的提示。"""
    client = QdrantClient(path=str(STORAGE_DIR))
    try:
        assert client.collection_exists(COLLECTION_NAME), (
            f"集合 {COLLECTION_NAME} 不存在，请先运行：python scripts/ingest.py"
        )
        count = client.count(COLLECTION_NAME).count
        assert count > 0, "向量库为空，请先运行：python scripts/ingest.py"
        print(f"✅ 向量库可用，共 {count} 个向量")
    finally:
        client.close()


def open_retriever():
    store = QdrantVectorStore.from_existing_collection(
        embedding=OllamaEmbeddings(model=EMB_MODEL),
        path=str(STORAGE_DIR),
        collection_name=COLLECTION_NAME,
    )
    return store, store.as_retriever(search_kwargs={"k": TOP_K_RECALL})


def test_retrieval() -> None:
    store, retriever = open_retriever()
    try:
        for question, keyword in RETRIEVAL_CASES:
            docs = retriever.invoke(question)
            assert docs, f"问题没有召回任何片段：{question}"
            assert any(keyword in doc.page_content for doc in docs[:3]), (
                f"问题「{question}」的 top3 片段中没有「{keyword}」"
            )
            print(f"✅ 检索命中：{question} -> {keyword}")
    finally:
        store.client.close()


def test_e2e() -> None:
    store, retriever = open_retriever()
    try:
        reranker = chat_cli.build_reranker()

        answer, _ = chat_cli.answer("香蕉要放冰箱吗？", retriever, reranker)
        assert ("不适合" in answer) or ("不能" in answer), f"库内问题回答异常：{answer}"
        print("✅ 端到端：库内问题回答正确")

        answer, _ = chat_cli.answer("苹果手机多少钱？", retriever, reranker)
        assert "没有相关信息" in answer, f"库外问题没有正确拒答：{answer}"
        print("✅ 端到端：库外问题正确拒答")
    finally:
        store.client.close()


def main() -> int:
    try:
        check_store()
        test_retrieval()
        if "--e2e" in sys.argv[1:]:
            test_e2e()
    except AssertionError as exc:
        print(f"❌ {exc}")
        return 1
    print("\n🎉 冒烟测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
