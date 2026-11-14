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

from private_rag.config import TOP_K_RERANK
from private_rag.models import Citation
from private_rag.qa import RagEngine, RetrievalOptions, build_engine


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地知识库问答（混合检索 + 引用溯源）")
    parser.add_argument("question", nargs="*", help="问题；省略则进入交互模式")
    parser.add_argument("--source-type", choices=["file", "web", "sql"], help="只检索某一类来源")
    parser.add_argument("--tag", action="append", help="按标签过滤，可重复传入")
    parser.add_argument("--dense-only", action="store_true", help="禁用 BM25，仅向量召回（用于对比）")
    parser.add_argument("--no-rerank", action="store_true", help="跳过重排，便于观察召回效果")
    parser.add_argument("--top-k", type=int, default=TOP_K_RERANK, help="最终送进 LLM 的片段数")
    return parser.parse_args(argv)


def build_options(args: argparse.Namespace) -> RetrievalOptions:
    return RetrievalOptions(
        source_type=args.source_type,
        tags=args.tag,
        top_k=args.top_k,
        use_lexical=not args.dense_only,
        use_rerank=not args.no_rerank,
    )


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


def _check_ready(engine: RagEngine) -> bool:
    readiness = engine.readiness()
    if not readiness.ready:
        print("❌ 知识库未就绪：" + "；".join(readiness.reasons))
        print("   请先运行：python scripts/ingest.py")
        return False
    for warning in readiness.warnings:
        print(f"⚠️ {warning}")
    return True


def _print_filters(args: argparse.Namespace) -> str:
    filters = []
    if args.source_type:
        filters.append(f"source_type={args.source_type}")
    if args.tag:
        filters.append(f"tags={','.join(args.tag)}")
    return f"（过滤：{'；'.join(filters)}）" if filters else ""


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    options = build_options(args)

    try:
        engine = build_engine(use_rerank=options.use_rerank)
    except Exception as exc:
        print(f"❌ 初始化问答引擎失败：{exc}")
        return 1

    try:
        if not _check_ready(engine):
            return 1

        question = " ".join(args.question).strip()
        if question:
            result = engine.answer(question, options=options)
            print_result(result.answer, result.citations)
            return 0

        print(f"\n🤖 RAG 就绪{_print_filters(args)}，输入 exit 退出")
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
            result = engine.answer(user_q, options=options)
            print_result(result.answer, result.citations)
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
