"""在同一进程里交替跑 agentic 评估的不同设置，量耗时并逐条比对行为。

耗时对比不能各跑一轮：模型冷热与取证内容的差异能把方向测反（docs/ROADMAP.md 的 v8.3
就是这么翻过一次），所以前后设置要在同一个进程里交替跑，形如 A/B/A，第一轮作废。
质量指标与逐条行为（拒答、意图、停止原因、工具次数）在同一进程里顺带读出来，
省掉再跑一遍的时间。

用法（要打到本机 Ollama，串行跑；思考设置一轮 29 条约二十分钟，全量 40 条三轮约一小时）：
    python3 scripts/measure_thinking_ab.py --setting thinking_brief \
        --values off,on,off --prefix /tmp/ab_brief
    python3 scripts/measure_thinking_ab.py --setting catalog_enum \
        --dataset eval/v8_agentic_expanded.jsonl --values off,on,off --prefix /tmp/ab_catalog
只跑一部分样本时加 `--ids xd01,xd02`（与整轮对照时不要加，样本数变了指标均值就不可比）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# 环境变量必须先于 private_rag.config 导入
from private_rag.eval_env import EVAL_AGENT_SOURCES_CONFIG, configure_eval_env  # noqa: E402

configure_eval_env()

import private_rag.agent.prompts as prompts  # noqa: E402
import private_rag.agent.runtime as runtime_module  # noqa: E402

# scripts/evaluate.py 就是评估 CLI，这里按轮调用它的 main，报告的指标口径与平时跑评估完全一致
import evaluate  # noqa: E402

def _set_data_side_routing(value: str) -> None:
    """数据侧路由的两半一起开关：目录把库内实体列全（runtime 侧）+ 提示词第 10 条（prompts 侧）。

    两处是同一处改动，分开开关就不是在量这次要 ship 的行为。`off` 逐字回到改动前：
    目录退回每列只取前 4 个值（连字符预算一起回），提示词退回没有第 10 条。
    """
    on = value not in {"off", "0", "no"}
    setattr(runtime_module, "AGENT_CATALOG_ENUM", on)
    setattr(prompts, "AGENT_ROUTE_DATA_SIDE", on)


def _set_kb_miss_sql_gate(value: str) -> None:
    """拒答前的兜底查询：开=文档侧缺那项事实时先按库内实体查一次业务表，
    关=照原行为直接按证据不足拒答。只改模块属性，同一进程里交替才生效。
    """
    setattr(runtime_module, "AGENT_KB_MISS_SQL_GATE", value not in {"off", "0", "no"})


def _set_gate_side_tracking(value: str) -> None:
    """mixed 门控认「这一侧查过没有」的依据：开=看工具执行记录，
    关=退回改动前（拿证据块的标签文字找工具名，真实链路里恒为 False）。
    只改模块属性，同一进程里交替才生效。
    """
    setattr(runtime_module, "AGENT_GATE_SIDE_TRACKING", value not in {"off", "0", "no"})


def _set_kbmiss_evidence_priority(value: str) -> None:
    """兜底门控捞回的 SQL 记录是否排到证据最前。两轮都把门控钉在「开」，
    这样开关只改排列，不改「查不查」——否则量的就不是这一处改动。
    """
    setattr(runtime_module, "AGENT_KB_MISS_SQL_GATE", True)
    setattr(runtime_module, "AGENT_KBMISS_EVIDENCE_PRIORITY", value not in {"off", "0", "no"})


def _set_kbmiss_priority_bad_route(value: str) -> None:
    """同上，但把数据侧路由退回改动前（目录每列只取前 4 个值 + 提示词没有第 10 条），
    模拟「路由判错意图」的场景：那正是兜底门控存在的理由，也只有在这种场景里
    每一条数据侧样本都会被门控捞一次，排列改动的效果才量得满。
    """
    _set_data_side_routing("off")
    _set_kbmiss_evidence_priority(value)


# 可以交替的设置项。思考开关在 prompts 里是导入时抓的快照，必须改模块属性才生效
# （只改 config 不会被读到，见 scripts/check_generation_determinism.py 的同一处理）。
SETTINGS: dict[str, Callable[[str], None]] = {
    "thinking_brief": lambda value: setattr(
        prompts, "AGENT_THINKING_BRIEF", value not in {"off", "0", "no"}
    ),
    "planner_stages": lambda value: setattr(
        prompts, "AGENT_PLANNER_THINKING_STAGES",
        frozenset() if value == "off" else frozenset(value.split(",")),
    ),
    "answer_thinking": lambda value: setattr(prompts, "AGENT_ANSWER_THINKING", value),
    "data_side_routing": _set_data_side_routing,
    "kb_miss_sql_gate": _set_kb_miss_sql_gate,
    "gate_side_tracking": _set_gate_side_tracking,
    "kbmiss_evidence_priority": _set_kbmiss_evidence_priority,
    "kbmiss_priority_bad_route": _set_kbmiss_priority_bad_route,
}

# 对照时看的整轮指标：前四项是「不能变差」的门禁，其余是取证与引用的质量
GUARDRAILS = ("answer_rate", "source_hit", "refusal_accuracy", "both_sides_hit")
QUALITY = ("citation_rate", "citation_validity", "evidence_hit@3", "mrr@3",
           "context_precision@3", "n_errors")


def round_stats(json_path: Path) -> dict[str, Any]:
    """读一轮报告：整轮指标、单条均耗时，以及逐条行为指纹。"""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    config = next(iter(data["configs"].values()))
    per_sample_ms = [s["retrieval_ms"] + s["generation_ms"] for s in data["samples"]]
    behavior = {
        s["id"]: ("拒答" if s["refusal"] else "作答", s.get("intent") or "",
                  s.get("stop_reason") or "", s.get("tool_calls") or 0)
        for s in data["samples"]
    }
    return {
        "metrics": config["metrics"],
        "avg_ms": sum(per_sample_ms) / max(len(per_sample_ms), 1),
        "behavior": behavior,
    }


def report_deltas(reference: dict[str, Any], current: dict[str, Any], label: str) -> None:
    """打印某一轮与参照轮的指标差与逐条行为翻动。"""
    print(f"\n--- {label} 对参照轮 ---")
    for name in GUARDRAILS + QUALITY:
        base, now = reference["metrics"].get(name), current["metrics"].get(name)
        mark = "＝" if base == now else "≠"
        print(f"  {name:20s} {base} → {now}  {mark}")
    flips = [(sid, base, now) for sid, base in reference["behavior"].items()
             if (now := current["behavior"].get(sid)) != base]
    if not flips:
        print("  逐条行为（拒答/意图/停止原因/工具次数）没有翻动")
    for sid, base, now in flips:
        print(f"  {sid} {base} → {now}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同一进程交替跑多档思考设置的耗时对照")
    parser.add_argument("--setting", choices=sorted(SETTINGS), default="thinking_brief")
    parser.add_argument("--values", default="off,on,off",
                        help="按顺序跑的设置值，逗号分隔；建议 A/B/A")
    parser.add_argument("--dataset", default="eval/v7_agent_qa.jsonl")
    parser.add_argument("--ids", help="只跑指定样本 ID（逗号分隔）；整轮对照时别用，样本数变了均值不可比")
    parser.add_argument("--agent-sources", default=str(EVAL_AGENT_SOURCES_CONFIG))
    parser.add_argument("--prefix", default="/tmp/ab_thinking", help="报告文件名前缀")
    parser.add_argument("--keep-reports", action="store_true",
                        help="把报告写到 eval/reports 而不是临时目录")
    args = parser.parse_args(argv)

    values = [part.strip() for part in args.values.split(",") if part.strip()]
    out_dir = ROOT / "eval/reports" if args.keep_reports else Path("/tmp")
    rounds: list[dict[str, Any]] = []

    for index, value in enumerate(values, start=1):
        SETTINGS[args.setting](value)
        name = f"{args.prefix}_{index}_{value}"
        print(f"\n===== 第 {index} 轮：{args.setting}={value} =====", flush=True)
        cli = [
            "--dataset", args.dataset,
            "--configs", "agent",
            "--agent-sources", args.agent_sources,
            "--skip-ragas",
            "--max-workers", "1",
            "--output", str(out_dir / f"{name}.md"),
            "--json-output", str(out_dir / f"{name}.json"),
            "--run-note", f"同一进程交替对照 第{index}轮 {args.setting}={value}",
        ]
        if args.ids:
            cli += ["--ids", args.ids]
        evaluate.main(cli)
        rounds.append(round_stats(out_dir / f"{name}.json"))

    print("\n=== 单条均耗时（第一轮作废：冷启动要把生成与精排模型加载进来）===")
    for index, (value, stats) in enumerate(zip(values, rounds), start=1):
        note = "（第一轮，作废）" if index == 1 else ""
        print(f"  第 {index} 轮 {args.setting}={value:8s} {stats['avg_ms'] / 1000:6.1f}s{note}")

    reference = rounds[0]
    for index, (value, stats) in enumerate(zip(values, rounds), start=1):
        if index == 1:
            continue
        report_deltas(reference, stats, f"第 {index} 轮 {args.setting}={value}")
        if index > 2:
            # 只比暖轮：第 2 轮的上一轮是冷启动，差 40% 是冷热不是设置差别
            previous = rounds[index - 2]
            print(f"  耗时对上一轮（{values[index - 2]}）：{stats['avg_ms'] / 1000:.1f}s"
                  f" vs {previous['avg_ms'] / 1000:.1f}s"
                  f"（{(stats['avg_ms'] - previous['avg_ms']) / max(previous['avg_ms'], 1) * 100:+.1f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
