"""量 agentic 决策调用的单次耗时：同一提示词下思考开/关、JSON 与 function calling 两条通道。

评估报告只把单条耗时分成「工具执行」与「其余」两块，路由与步进决策各占多少没有单独记录，
这个脚本把路由、步进决策、最终作答三类调用按同一条真实证据文本分别计时，用来判断延迟该往哪儿省。

需要本机 Ollama 与已经建好的评估库，串行跑以免抢模型：

    RAG_COLLECTION_NAME=rag_eval_v8_latency \
    RAG_STORAGE_DIR=storage/eval/qdrant_v8_latency \
    RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_v8_latency/bm25.pkl \
    python3 scripts/measure_planner_latency.py --ids k01,d01,m01
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 先把评估库的环境变量设好再导入 config / qa；已导出的变量优先，
# 所以想量到某个隔离库时照样能覆盖
from private_rag.eval_env import EVAL_AGENT_SOURCES_CONFIG, configure_eval_env  # noqa: E402

configure_eval_env()

from langchain_ollama import ChatOllama  # noqa: E402

from private_rag.agent.prompts import (  # noqa: E402
    ROUTE_TOOL_SCHEMA,
    STEP_TOOL_SCHEMA,
    build_final_prompt,
    build_route_prompt,
    build_step_prompt,
)
from private_rag.agent.runtime import build_tool_registry  # noqa: E402
from private_rag.agent.tools import render_evidence  # noqa: E402
from private_rag.config import AGENT_LLM_MODEL, AGENT_PLANNER_TOKENS  # noqa: E402
from private_rag.qa import build_engine  # noqa: E402

DEFAULT_IDS = ("k01", "d01", "m01")


def load_samples(dataset: str, ids: list[str]) -> dict[str, dict]:
    wanted = set(ids)
    samples: dict[str, dict] = {}
    with open(dataset) as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("id") in wanted:
                samples[row["id"]] = row
    missing = wanted - set(samples)
    if missing:
        raise SystemExit(f"评估集里找不到样本：{sorted(missing)}")
    return samples


def call(prompt: str, *, thinking: bool, channel: str, schema: dict | None, model: str) -> dict:
    """按运行时同一种参数打一次调用，返回耗时与 token 计数。"""
    kwargs = dict(
        model=model,
        temperature=0,
        num_ctx=8192,
        num_predict=AGENT_PLANNER_TOKENS,
        disable_streaming=True,
        keep_alive="10m",
        reasoning=thinking,
    )
    if channel == "json":
        kwargs["format"] = "json"
    llm = ChatOllama(**kwargs)
    if schema is not None:
        llm = llm.bind_tools([schema])
    started = time.perf_counter()
    try:
        response = llm.invoke(prompt)
    except Exception as exc:  # 网络或模型不可用时要把这一格记成错误，不能让整轮中断
        return {"ms": int((time.perf_counter() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"}
    ms = int((time.perf_counter() - started) * 1000)
    usage = getattr(response, "usage_metadata", None) or {}
    content = response.content if isinstance(response.content, str) else str(response.content)
    tool_calls = getattr(response, "tool_calls", None) or []
    return {
        "ms": ms,
        "output_tokens": usage.get("output_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "tool_calls": len(tool_calls),
        "content_chars": len(content),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="量 agentic 决策调用的单次耗时")
    parser.add_argument("--dataset", default="eval/v7_agent_qa.jsonl")
    parser.add_argument("--ids", default=",".join(DEFAULT_IDS), help="逗号分隔的样本 ID")
    parser.add_argument("--model", default=AGENT_LLM_MODEL)
    parser.add_argument("--sources", default=str(EVAL_AGENT_SOURCES_CONFIG))
    parser.add_argument("--with-final", action="store_true", help="顺带量最终作答（仅作参考，不受思考开关影响）")
    args = parser.parse_args(argv)

    ids = [part.strip() for part in args.ids.split(",") if part.strip()]
    samples = load_samples(args.dataset, ids)
    engine = build_engine(use_rerank=True)
    registry = build_tool_registry(engine, sources_path=args.sources)
    specs = registry.describe()
    kb_tool = registry.get("kb_search")
    evidence: dict[str, str] = {}
    try:
        for sample_id in ids:
            blocks = kb_tool.run({"query": samples[sample_id]["question"]}).blocks
            evidence[sample_id] = render_evidence(blocks)[0]
            print(f"[证据 {sample_id}] blocks={len(blocks)} chars={len(evidence[sample_id])}", flush=True)

        results: dict[str, list[int]] = {}
        for sample_id in ids:
            question = samples[sample_id]["question"]
            route_prompt = build_route_prompt(question, specs)
            step_prompt = build_step_prompt(
                question,
                specs,
                evidence[sample_id],
                [f"步骤1：kb_search：{len(evidence[sample_id])} 字证据"],
            )
            cases = [
                ("route/json", route_prompt, "json", None),
                ("route/function", route_prompt, "function", ROUTE_TOOL_SCHEMA),
                ("step/json", step_prompt, "json", None),
                ("step/function", step_prompt, "function", STEP_TOOL_SCHEMA),
            ]
            if args.with_final:
                cases.append(("final", build_final_prompt(question, evidence[sample_id]), "plain", None))
            for label, prompt, channel, schema in cases:
                for thinking in (True, False):
                    tag = f"{sample_id}/{label}/{'think' if thinking else 'nothink'}"
                    record = call(prompt, thinking=thinking, channel=channel, schema=schema, model=args.model)
                    results.setdefault(f"{label}/{'think' if thinking else 'nothink'}", []).append(
                        record.get("ms", 0)
                    )
                    print(f"{tag}: {json.dumps(record, ensure_ascii=False)}", flush=True)
    finally:
        engine.close()

    print("\n=== 汇总（同类均值）===")
    for key, values in sorted(results.items()):
        print(f"{key}: n={len(values)} mean={sum(values) // len(values)}ms  {values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
