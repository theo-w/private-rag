"""agentic 单条耗时分解：一次问答里每一次模型调用花了多少时间、吃了多少 token。

延迟优化要先知道时间花在哪一段。评估报告只把一条拆成「工具」与「其余」两半（见
`collect_agent_results` 的口径），这里把「其余」再拆成路由 / 步进决策 / 最终作答三段，
并记下每次调用的提示词字符数、输入输出 token 数与有没有让模型先思考。

只做观察，不改行为：靠替换模块里的调用入口来计时，生产代码一字未动。

用法（要打到本机 Ollama，不打网络）：
    python3 scripts/profile_agent_calls.py --dataset eval/v7_agent_qa.jsonl
    python3 scripts/profile_agent_calls.py --ids k01,m01,c03 --json /tmp/profile.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 当前正在进行的调用；最底层的模型调用往这里回填，外层入口负责起名与结算
_CURRENT: list[dict] = []
RECORDS: list[dict] = []

CALL_KINDS = ("route", "step", "final", "decision")


def new_record(kind: str) -> dict:
    """一条调用记录：耗时、提示词规模、token 数、有没有开思考。"""
    return {
        "kind": kind,
        "ms": 0,
        "wall_ms": 0,
        "prompt_chars": 0,
        "in_tokens": 0,
        "out_tokens": 0,
        "thinking_chars": 0,
        "num_predict": None,
        "thinking": None,
    }


def read_usage(response: Any) -> tuple[int, int, int]:
    """从模型响应里取 (输入 token, 输出 token, 思考字符数)，取不到就记 0。

    思考文本在这版 langchain 里可能落在 `reasoning_content`，也可能落在
    `additional_kwargs["reasoning"]`，两处都看一眼，别把「没记录」当成「没思考」。
    """
    meta = getattr(response, "usage_metadata", None) or {}
    if not isinstance(meta, dict):
        meta = dict(meta)
    extra = getattr(response, "additional_kwargs", None) or {}
    thinking = getattr(response, "reasoning_content", "") or extra.get("reasoning") or ""
    return (
        int(meta.get("input_tokens") or 0),
        int(meta.get("output_tokens") or 0),
        len(str(thinking)),
    )


def wrap_chat_ollama(prompts_mod: Any) -> None:
    """给 `ChatOllama` 套一层计时：每次 invoke 把耗时与 token 数回填到当前调用。"""

    real = prompts_mod.ChatOllama

    class TimedChatOllama(real):  # type: ignore[misc, valid-type]
        def invoke(self, *args, **kwargs):
            started = time.perf_counter()
            response = super().invoke(*args, **kwargs)
            record = _CURRENT[-1] if _CURRENT else None
            if record is None:
                return response
            record["ms"] += int((time.perf_counter() - started) * 1000)
            record["prompt_chars"] = len(str(args[0])) if args else 0
            in_tokens, out_tokens, thinking_chars = read_usage(response)
            record["in_tokens"] = in_tokens
            record["out_tokens"] = out_tokens
            record["thinking_chars"] = thinking_chars
            record["num_predict"] = getattr(self, "num_predict", None)
            record["thinking"] = getattr(self, "reasoning", None)
            return response

    prompts_mod.ChatOllama = TimedChatOllama


def timed(kind: str, fn: Callable) -> Callable:
    """包一层决策调用：记下整段耗时，失败也要留下时间。"""

    def wrapper(*args, **kwargs):
        record = new_record(kind)
        started = time.perf_counter()
        _CURRENT.append(record)
        try:
            out = fn(*args, **kwargs)
        except Exception as exc:  # 决策失败也要留下耗时，否则看不出是不是超时
            record["ms"] = record["wall_ms"] = int((time.perf_counter() - started) * 1000)
            record["error"] = type(exc).__name__
            RECORDS.append(record)
            _CURRENT.pop()
            raise
        wall_ms = int((time.perf_counter() - started) * 1000)
        record["wall_ms"] = wall_ms
        if not record["ms"]:
            record["ms"] = wall_ms
        if isinstance(out, dict):
            record["out_chars"] = len(str(out.get("answer") or ""))
        RECORDS.append(record)
        _CURRENT.pop()
        return out

    return wrapper


def timed_final(fn: Callable) -> Callable:
    """包一层最终作答：额外记下证据文本的字符数（最终作答的耗时主要由它决定）。"""

    def wrapper(*args, **kwargs):
        record = new_record("final")
        started = time.perf_counter()
        evidence = kwargs.get("evidence") or (args[1] if len(args) > 1 else "")
        record["prompt_chars"] = len(str(evidence or ""))
        _CURRENT.append(record)
        try:
            answer = fn(*args, **kwargs)
        finally:
            wall_ms = int((time.perf_counter() - started) * 1000)
            record["wall_ms"] = wall_ms
            if not record["ms"]:
                record["ms"] = wall_ms
            record["out_chars"] = len(str(answer)) if "answer" in locals() else 0
            RECORDS.append(record)
            _CURRENT.pop()
        return answer

    return wrapper


def install_probes(prompts_mod: Any, planner_mod: Any, runtime_mod: Any) -> None:
    """把三个模型调用入口换成带计时的替身（只在本进程内，且不改生产代码）。"""
    wrap_chat_ollama(prompts_mod)
    planner_mod.ask_json = timed("decision", planner_mod.ask_json)
    planner_mod.ask_tool_json = timed("decision", planner_mod.ask_tool_json)
    runtime_mod.generate_final_answer = timed_final(runtime_mod.generate_final_answer)


def classify(records: list[dict], route_calls: int = 1) -> None:
    """决策调用里前 `route_calls` 次是路由，其余是步进：按发生顺序归名。"""
    seen = 0
    for record in records:
        if record["kind"] != "decision":
            continue
        seen += 1
        record["kind"] = "route" if seen <= route_calls else "step"


def summarize(per_sample: list[dict]) -> dict[str, dict]:
    """把所有样本按调用类型汇总（token 与思考字符取合计，提示词取最长）。"""
    agg: dict[str, dict] = {}
    for item in per_sample:
        for kind, v in item["by_kind"].items():
            total = agg.setdefault(kind, {"ms": 0, "n": 0, "in_tokens": 0, "out_tokens": 0,
                                          "thinking_chars": 0, "prompt_chars": 0, "samples": 0})
            total["ms"] += v["ms"]
            total["n"] += v["n"]
            total["in_tokens"] += v["in_tokens"]
            total["out_tokens"] += v["out_tokens"]
            total["thinking_chars"] += v["thinking_chars"]
            total["prompt_chars"] = max(total["prompt_chars"], v["prompt_chars"])
            total["samples"] += 1
    return agg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agentic 每次模型调用的耗时与 token 分解")
    parser.add_argument("--dataset", default="eval/v7_agent_qa.jsonl")
    parser.add_argument("--ids", help="只跑指定样本 ID，逗号分隔")
    parser.add_argument("--limit", type=int, help="只跑前 N 条")
    parser.add_argument("--agent-sources", help="agentic 数据源配置，默认用评估专用那份")
    parser.add_argument("--json", help="把逐条记录写到这个 JSON 文件")
    args = parser.parse_args(argv)

    # 环境变量必须先于 private_rag.config 导入（config 在导入时读环境变量）
    from private_rag.eval_env import EVAL_AGENT_SOURCES_CONFIG, configure_eval_env

    configure_eval_env()
    from private_rag.agent import planner as planner_mod
    from private_rag.agent import prompts as prompts_mod
    from private_rag.agent import runtime as runtime_mod
    from private_rag.agent.runtime import AgentOptions, build_agent_runtime
    from private_rag.config import AGENT_MAX_STEPS
    from private_rag.evaluation import load_dataset
    from private_rag.qa import build_engine

    samples = load_dataset(Path(args.dataset))
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        samples = [s for s in samples if s.id in wanted]
    if args.limit:
        samples = samples[: args.limit]

    install_probes(prompts_mod, planner_mod, runtime_mod)
    agent = build_agent_runtime(
        build_engine(),
        sources_path=Path(args.agent_sources or EVAL_AGENT_SOURCES_CONFIG),
        allow_sql=True,
    )

    per_sample: list[dict] = []
    for index, sample in enumerate(samples, start=1):
        RECORDS.clear()
        started = time.perf_counter()
        # 与 collect_agent_results 完全同一口径：同一个 run、同一套 options、同样的 history
        result = agent.run(
            sample.question,
            options=AgentOptions(max_steps=AGENT_MAX_STEPS, allow_sql=True),
            history=list(sample.history),
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        records = [dict(r) for r in RECORDS]
        classify(records, route_calls=1)
        by_kind: dict[str, dict] = {}
        for record in records:
            agg = by_kind.setdefault(record["kind"], {"ms": 0, "n": 0, "in_tokens": 0,
                                                      "out_tokens": 0, "thinking_chars": 0,
                                                      "prompt_chars": 0})
            agg["ms"] += record["ms"]
            agg["n"] += 1
            agg["in_tokens"] += record["in_tokens"]
            agg["out_tokens"] += record["out_tokens"]
            agg["thinking_chars"] += record["thinking_chars"]
            agg["prompt_chars"] = max(agg["prompt_chars"], record["prompt_chars"])
        per_sample.append({
            "id": sample.id,
            "category": sample.category,
            "intent": result.intent,
            "stop_reason": result.stop_reason,
            "tool_calls": result.tool_calls,
            "total_ms": total_ms,
            "calls": records,
            "by_kind": by_kind,
        })
        line = " ".join(f"{k}={v['ms'] / 1000:.1f}s/{v['n']}次" for k, v in sorted(by_kind.items()))
        print(f"[{index}/{len(samples)}] {sample.id} {sample.category} "
              f"总计 {total_ms / 1000:.1f}s ｜ {line}", flush=True)

    grand = sum(item["total_ms"] for item in per_sample)
    print("\n=== 按调用类型汇总（全部样本）===")
    for kind in CALL_KINDS:
        v = summarize(per_sample).get(kind)
        if not v:
            continue
        print(f"{kind:7s} 合计 {v['ms'] / 1000:7.1f}s（占整轮 {v['ms'] / max(grand, 1) * 100:5.1f}%）"
              f"｜次数 {v['n']:3d}｜平均 {v['ms'] / max(v['n'], 1) / 1000:5.1f}s/次"
              f"｜输入 token {v['in_tokens']}｜输出 token {v['out_tokens']}"
              f"｜思考字符 {v['thinking_chars']}｜最长提示词 {v['prompt_chars']} 字符")
    print(f"整轮合计 {grand / 1000:.1f}s，样本 {len(per_sample)} 条，"
          f"平均 {grand / max(len(per_sample), 1) / 1000:.1f}s")

    print("\n=== 按样本类别汇总 ===")
    cats: dict[str, dict] = {}
    for item in per_sample:
        agg = cats.setdefault(item["category"], {"total_ms": 0, "n": 0,
                                                 "route_ms": 0, "step_ms": 0, "final_ms": 0})
        agg["total_ms"] += item["total_ms"]
        agg["n"] += 1
        for kind in ("route", "step", "final"):
            agg[f"{kind}_ms"] += item["by_kind"].get(kind, {}).get("ms", 0)
    for cat, v in sorted(cats.items(), key=lambda kv: -kv[1]["total_ms"] / kv[1]["n"]):
        n = v["n"]
        print(f"{cat:12s} n={n:2d} 单条均 {v['total_ms'] / n / 1000:5.1f}s"
              f"｜路由 {v['route_ms'] / n / 1000:5.1f}s（{v['route_ms'] / max(v['total_ms'], 1) * 100:4.1f}%）"
              f"步进 {v['step_ms'] / n / 1000:5.1f}s（{v['step_ms'] / max(v['total_ms'], 1) * 100:4.1f}%）"
              f"作答 {v['final_ms'] / n / 1000:5.1f}s（{v['final_ms'] / max(v['total_ms'], 1) * 100:4.1f}%）")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"total_ms": grand, "samples": per_sample}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"逐条记录已写到 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
