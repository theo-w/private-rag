"""冒烟测试：知识库、混合检索与引用，可选验证完整问答与拒答。

默认只测检索层（快）；加 --e2e 会加载重排模型并调用 LLM（慢）。
运行前需先执行：python scripts/ingest.py

注意：函数名刻意不以 test_ 开头，避免被 pytest 误收集（本文件需要本地模型与已有知识库）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from private_rag.config import COLLECTION_NAME, LEXICAL_INDEX_PATH, STORAGE_DIR
from private_rag.qa import RetrievalOptions, build_engine
from private_rag.retrieval.lexical import LexicalIndex
from private_rag.store import close_client, count_points, open_client

# 库内问题 -> 期望命中的关键词
RETRIEVAL_CASES = [
    ("苹果适合放冰箱吗？", "苹果"),
    ("香蕉能不能放冰箱？", "香蕉"),
    ("橙子有什么营养？", "橙子"),
]


def check_store() -> None:
    """集合存在且有数据，BM25 索引也已生成。"""
    client = open_client(STORAGE_DIR)
    try:
        assert client.collection_exists(COLLECTION_NAME), (
            f"集合 {COLLECTION_NAME} 不存在，请先运行：python scripts/ingest.py"
        )
        count = count_points(client)
        assert count > 0, "向量库为空，请先运行：python scripts/ingest.py"
        print(f"✅ 向量库可用，共 {count} 个向量")
    finally:
        close_client(client)

    lexical = LexicalIndex.load(LEXICAL_INDEX_PATH)
    assert lexical is not None, "BM25 索引不存在，请重新运行：python scripts/ingest.py"
    assert len(lexical) > 0
    print(f"✅ BM25 索引可用，共 {len(lexical)} 条")


def check_retrieval() -> None:
    engine = build_engine(use_rerank=False)
    try:
        options = RetrievalOptions(use_rerank=False)
        for question, keyword in RETRIEVAL_CASES:
            chunks, used_lexical, _ = engine.retrieve(question, options)
            assert chunks, f"问题没有召回任何片段：{question}"
            assert used_lexical, "BM25 索引未参与检索"
            assert any(keyword in chunk.text for chunk in chunks), (
                f"问题「{question}」的 top3 片段中没有「{keyword}」"
            )
            print(f"✅ 混合检索命中：{question} -> {keyword}")
    finally:
        engine.close()


def check_e2e() -> None:
    engine = build_engine()
    try:
        readiness = engine.readiness()
        assert readiness.ready, "；".join(readiness.reasons)

        result = engine.answer("香蕉要放冰箱吗？")
        assert ("不适合" in result.answer) or ("不能" in result.answer), (
            f"库内问题回答异常：{result.answer}"
        )
        assert result.citations, "库内问题没有生成引用"
        assert all(citation.source_uri for citation in result.citations), "引用缺少来源 URI"
        print("✅ 端到端：库内问题回答正确，引用完整")

        refusal = engine.answer("苹果手机多少钱？")
        assert "没有相关信息" in refusal.answer, f"库外问题没有正确拒答：{refusal.answer}"
        print("✅ 端到端：库外问题正确拒答")
    finally:
        engine.close()


def main() -> int:
    try:
        check_store()
        check_retrieval()
        if "--e2e" in sys.argv[1:]:
            check_e2e()
    except AssertionError as exc:
        print(f"❌ {exc}")
        return 1
    print("\n🎉 冒烟测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
