"""检索问答脚本：向量召回 + bge-reranker 重排 + Ollama 生成。

本脚本只读取已建好的向量库，不做任何入库操作；首次使用或文档更新后请先运行：
    python scripts/ingest.py
"""

import sys
from pathlib import Path

# 允许从任意目录直接运行脚本时导入项目根目录下的 config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from FlagEmbedding import FlagReranker
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore

from config.settings import (
    COLLECTION_NAME,
    EMB_MODEL,
    LLM_MODEL,
    RERANK_MODEL,
    STORAGE_DIR,
    TOP_K_RECALL,
    TOP_K_RERANK,
)

PROMPT = """你是知识库问答助手，严格只根据下面提供的上下文回答。
如果上下文没有答案，直接回复【资料中没有相关信息】，不要编造。

上下文：
{context}

用户问题：
{question}
"""


def build_retriever():
    """打开本地向量库并返回 retriever；集合不存在时给出明确提示。"""
    try:
        store = QdrantVectorStore.from_existing_collection(
            embedding=OllamaEmbeddings(model=EMB_MODEL),
            path=str(STORAGE_DIR),
            collection_name=COLLECTION_NAME,
        )
    except Exception as exc:
        raise SystemExit(
            f"❌ 无法打开向量库：{exc}\n请先运行：python scripts/ingest.py"
        ) from exc
    return store, store.as_retriever(search_kwargs={"k": TOP_K_RECALL})


def build_reranker() -> FlagReranker:
    return FlagReranker(RERANK_MODEL, use_fp16=True)


def answer(question: str, retriever, reranker):
    """一次完整问答：召回 -> 重排 -> 生成，返回（回答, 引用文档）。"""
    raw_docs = retriever.invoke(question)
    if not raw_docs:
        return "【资料中没有相关信息】", []

    pairs = [[question, doc.page_content] for doc in raw_docs]
    scores = reranker.compute_score(pairs)
    if not isinstance(scores, list):
        # 只有一条候选时 FlagEmbedding 可能返回标量
        scores = [scores]
    scored = sorted(zip(raw_docs, scores), key=lambda item: item[1], reverse=True)
    top_docs = [item[0] for item in scored[:TOP_K_RERANK]]

    context = "\n\n".join(
        f"【引用】{doc.metadata.get('source', '')}\n{doc.page_content}"
        for doc in top_docs
    )
    resp = ChatOllama(model=LLM_MODEL).invoke(
        PROMPT.format(context=context, question=question)
    )
    return resp.content, top_docs


def print_result(question: str, result) -> None:
    ans, refs = result
    print("\n📝 回答：")
    print(ans)
    if refs:
        print("\n📚 用到的文档片段：")
        for idx, doc in enumerate(refs, 1):
            print(f"---【引用{idx}】---\n{doc.page_content[:200]}...")


def main() -> None:
    store, retriever = build_retriever()
    try:
        question = " ".join(sys.argv[1:]).strip()
        if question:
            print_result(question, answer(question, retriever, build_reranker()))
            return

        print("\n🤖 RAG 就绪，输入 exit 退出")
        reranker = build_reranker()
        while True:
            try:
                user_q = input("\n❓ 提问：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 退出")
                break
            if not user_q:
                continue
            if user_q.lower() == "exit":
                print("👋 退出")
                break
            print_result(user_q, answer(user_q, retriever, reranker))
    finally:
        store.client.close()


if __name__ == "__main__":
    main()
