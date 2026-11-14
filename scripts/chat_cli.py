"""检索问答 CLI：混合召回（dense + BM25）-> RRF 融合 -> bge-reranker -> 带引用回答。

只读取已建好的知识库；首次使用或内容更新后请先运行：
    python scripts/ingest.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_ollama import OllamaEmbeddings

from private_rag.config import (
    COLLECTION_NAME,
    EMB_MODEL,
    LEXICAL_INDEX_PATH,
    TOP_K_RERANK,
)
from private_rag.generation import generate_answer
from private_rag.models import Citation
from private_rag.retrieval.hybrid import HybridRetriever
from private_rag.retrieval.lexical import LexicalIndex
from private_rag.retrieval.rerank import Reranker, load_reranker
from private_rag.store import close_client, open_client


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地知识库问答（混合检索 + 引用溯源）")
    parser.add_argument("question", nargs="*", help="问题；省略则进入交互模式")
    parser.add_argument("--source-type", choices=["file", "web", "sql"], help="只检索某一类来源")
    parser.add_argument("--tag", action="append", help="按标签过滤，可重复传入")
    parser.add_argument("--dense-only", action="store_true", help="禁用 BM25，仅向量召回（用于对比）")
    parser.add_argument("--no-rerank", action="store_true", help="跳过重排，便于观察召回效果")
    parser.add_argument("--top-k", type=int, default=TOP_K_RERANK, help="最终送进 LLM 的片段数")
    return parser.parse_args(argv)


def build_retriever(client, args: argparse.Namespace) -> tuple[HybridRetriever, Optional[Reranker]]:
    if not client.collection_exists(COLLECTION_NAME):
        raise SystemExit("❌ 知识库为空，请先运行：python scripts/ingest.py")

    lexical = None
    if not args.dense_only:
        lexical = LexicalIndex.load(LEXICAL_INDEX_PATH)
        if lexical is None:
            print("⚠️ 未找到 BM25 索引，已降级为纯向量检索；运行 ingest 可重建")

    retriever = HybridRetriever(
        client,
        OllamaEmbeddings(model=EMB_MODEL),
        lexical,
        source_type=args.source_type,
        tags=args.tag,
    )
    reranker = None if args.no_rerank else load_reranker()
    return retriever, reranker


def answer_question(
    question: str,
    retriever: HybridRetriever,
    reranker: Optional[Reranker],
    top_k: int = TOP_K_RERANK,
) -> tuple[str, list[Citation]]:
    chunks = retriever.retrieve(question)
    if reranker is not None:
        chunks = reranker.rerank(question, chunks, top_k=top_k)
    else:
        chunks = chunks[:top_k]
    return generate_answer(question, chunks)


def print_result(answer: str, citations: Sequence[Citation]) -> None:
    print("\n📝 回答：")
    print(answer)
    if not citations:
        return
    print("\n📚 引用：")
    for citation in citations:
        location = citation.source_uri
        if citation.page is not None:
            location += f"｜第 {citation.page} 页"
        if citation.table is not None:
            location += f"｜表 {citation.table}"
        print(f"[{citation.index}] {citation.title}（{citation.source_type}）")
        print(f"    {location}")
        print(f"    {citation.snippet}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = open_client()
    try:
        retriever, reranker = build_retriever(client, args)

        question = " ".join(args.question).strip()
        if question:
            answer, citations = answer_question(question, retriever, reranker, args.top_k)
            print_result(answer, citations)
            return 0

        filters = []
        if args.source_type:
            filters.append(f"source_type={args.source_type}")
        if args.tag:
            filters.append(f"tags={','.join(args.tag)}")
        suffix = f"（过滤：{'；'.join(filters)}）" if filters else ""
        print(f"\n🤖 RAG 就绪{suffix}，输入 exit 退出")
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
            answer, citations = answer_question(user_q, retriever, reranker, args.top_k)
            print_result(answer, citations)
        return 0
    finally:
        close_client(client)


if __name__ == "__main__":
    raise SystemExit(main())
