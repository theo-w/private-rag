"""Agentic 问答 CLI：意图路由 -> 工具调用 -> 带引用回答 -> 决策轨迹。

与 chat_cli.py 的区别：这里不预设走哪条路，由模型按问题意图决定
是检索知识库、查业务库，还是两路都查，并把每一步决策记录下来。

运行前需要已入库（scripts/ingest.py）；运行时 SQL 还需要在 sources.yaml 的
sql 源上显式加 `allow_runtime_query: true`，否则只挂知识库检索这一个工具。

运行：
    python3 scripts/chat_agent.py "最便宜的香蕉多少钱，应该怎么保存？"
    python3 scripts/chat_agent.py --interactive
    python3 scripts/chat_agent.py --no-sql --compact "怎么增量入库？"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.agent.runtime import AgentOptions, AgentResult, build_agent_runtime
from private_rag.config import AGENT_MAX_STEPS, AGENT_SOURCES_CONFIG, TOP_K_RERANK
from private_rag.models import Citation
from private_rag.qa import build_engine

KIND_LABELS = {
    "route": "意图路由",
    "tool": "工具调用",
    "final": "结论",
    "error": "失败",
    "stop": "提前停止",
}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agentic 问答（意图路由 + 工具调用 + 决策轨迹）"
    )
    parser.add_argument("question", nargs="*", help="问题；省略则进入交互模式")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=AGENT_MAX_STEPS,
        help=f"最多执行多少次工具调用（默认 {AGENT_MAX_STEPS}）",
    )
    parser.add_argument(
        "--no-sql",
        action="store_true",
        help="禁用运行时 SQL，只用知识库检索",
    )
    parser.add_argument(
        "--sources",
        default=str(AGENT_SOURCES_CONFIG),
        help="声明运行时 SQL 授权的 sources 配置路径",
    )
    parser.add_argument("--top-k", type=int, default=TOP_K_RERANK, help="单次检索送入 LLM 的片段数")
    parser.add_argument("--source-type", choices=["file", "web", "sql"], help="知识库检索限定来源类型")
    parser.add_argument("--tag", action="append", help="知识库检索按标签过滤，可重复传入")
    parser.add_argument("--dense-only", action="store_true", help="知识库检索禁用 BM25")
    parser.add_argument("--no-rerank", action="store_true", help="知识库检索跳过重排")
    parser.add_argument(
        "--query-rewrite",
        choices=["off", "multi", "hyde"],
        default="off",
        help="知识库检索的查询改写模式",
    )
    parser.add_argument("--compact", action="store_true", help="只打印回答与引用，不打印决策过程")
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="进入交互模式（也可省略问题直接进入）",
    )
    return parser.parse_args(argv)


def build_options(args: argparse.Namespace) -> AgentOptions:
    return AgentOptions(
        max_steps=args.max_steps,
        allow_sql=not args.no_sql,
        top_k=args.top_k,
        source_type=args.source_type,
        tags=args.tag,
        use_lexical=not args.dense_only,
        use_rerank=not args.no_rerank,
        query_rewrite=args.query_rewrite,
    )


def print_steps(result: AgentResult) -> None:
    print("\n🧭 决策过程：")
    for step in result.steps:
        label = KIND_LABELS.get(step.kind, step.kind)
        head = f"  [{step.index}] {label}"
        if step.tool:
            head += f" → {step.tool}"
        if step.kind == "tool" and not step.ok:
            head += "（失败）"
        print(head)
        if step.reason:
            print(f"      理由：{step.reason}")
        if step.args:
            preview = "; ".join(f"{k}={str(v)[:90]}" for k, v in step.args.items())
            print(f"      参数：{preview}")
        detail = step.error or step.observation
        if detail:
            print(f"      观察：{detail}")


def print_result(answer: str, citations: Sequence[Citation]) -> None:
    print("\n📝 回答：")
    print(answer)
    if not citations:
        print("\n📚 引用：（无）")
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


def print_summary(result: AgentResult, trace_path: Path) -> None:
    print(
        f"\n🔎 意图 {result.intent or '未知'}｜工具调用 {result.tool_calls} 次"
        f"｜证据 {result.evidence_blocks} 段｜停止原因 {result.stop_reason}"
        f"｜耗时 {result.elapsed_ms} ms"
    )
    print(f"🧾 trace_id：{result.trace_id}（回放：python3 scripts/replay_trace.py --trace-id {result.trace_id}）")
    print(f"   轨迹文件：{trace_path}")


def _ask(agent, question: str, options: AgentOptions, args: argparse.Namespace, trace_path: Path) -> None:
    result = agent.run(question, options=options)
    if not args.compact:
        print_steps(result)
    print_result(result.answer, result.citations)
    print_summary(result, trace_path)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    options = build_options(args)

    try:
        engine = build_engine(use_rerank=options.use_rerank)
    except Exception as exc:
        print(f"❌ 初始化问答引擎失败：{exc}")
        return 1

    try:
        readiness = engine.readiness()
        if not readiness.ready:
            print("❌ 知识库未就绪：" + "；".join(readiness.reasons))
            print("   请先运行：python3 scripts/ingest.py")
            return 1
        for warning in readiness.warnings:
            print(f"⚠️ {warning}")

        try:
            agent = build_agent_runtime(engine, sources_path=Path(args.sources))
        except Exception as exc:
            print(f"❌ 初始化 agentic 运行时失败：{exc}")
            return 1

        trace_path = agent.trace_path
        names = agent.registry.names()
        if not options.allow_sql and "sql_query" in names:
            names = [name for name in names if name != "sql_query"]
        print(f"\n🤖 agentic 就绪，可用工具：{'、'.join(names) or '无'}（exit 退出）")
        if "sql_query" not in names:
            print("   提示：运行时 SQL 未启用，可在 sources.yaml 的 sql 源上加 allow_runtime_query: true")

        question = " ".join(args.question).strip()
        if question:
            _ask(agent, question, options, args, trace_path)
            return 0

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
            _ask(agent, user_q, options, args, trace_path)
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
