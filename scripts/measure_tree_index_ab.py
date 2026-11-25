"""在同一进程里交替开/关树索引跑 agentic 评估，量指标与逐条行为差别。

为什么要在同一个进程里交替跑：模型冷热能把方向测反（docs/ROADMAP.md 的 v8.3 就翻过一次），
所以按 off,on,off 顺序跑三轮，第一轮作废。指标口径与平时跑评估完全一致——
这里按轮调用 scripts/evaluate.py 的 main，只是每轮之前改一次开关。

开关是 `private_rag.config.TREE_INDEX_ENABLED` 的导入时快照，检索时读的是
`private_rag.qa` 里的那个名字，所以必须改模块属性才生效（只改环境变量不会被读到）。

用法（要打到本机 Ollama，串行跑；一条 20~35 秒，29 条三轮约 40 分钟）：
    python3 scripts/measure_tree_index_ab.py --values off,on,off --keep-reports
报告写在 `eval/reports/v8_5_tree_ab_{1,2,3}_{off,on,off}.md/.json`（默认前缀 `v8_5_tree_ab`）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# 环境变量必须先于 private_rag.config 导入
from private_rag.eval_env import (  # noqa: E402
    EVAL_AGENT_SOURCES_CONFIG,
    EVAL_TREE_INDEX_PATH,
    configure_eval_env,
)

configure_eval_env()

import private_rag.qa as qa  # noqa: E402

# scripts/evaluate.py 就是评估 CLI，这里按轮调用它的 main
import compare_eval_noise  # noqa: E402  # 同值轮次的噪声底比对（v8.3 口径）
import evaluate  # noqa: E402
from private_rag.retrieval.tree import PATH_PREFIX  # noqa: E402

# 对照时看的整轮指标：前四项是「不能变差」的门禁，其余是取证与引用的质量
GUARDRAILS = ("answer_rate", "source_hit", "refusal_accuracy", "both_sides_hit")
QUALITY = (
    "citation_rate",
    "citation_validity",
    "evidence_hit@3",
    "mrr@3",
    "context_precision@3",
    "n_errors",
)
# 这一路自己的读数：覆盖率与送进模型的上下文字符数。
# 不用 used_parent_rate——agentic 那一栏是配置里写的期望值，不是实际行为，看了会误读。
OWN = ("used_tree_rate", "avg_context_chars", "avg_retrieved")


def round_stats(json_path: Path) -> dict[str, Any]:
    """读一轮报告：整轮指标、单条均耗时、逐条行为指纹，以及章节路径出现次数。"""
    data = _read_json(json_path)
    config = next(iter(data["configs"].values()))
    per_sample_ms = [s["retrieval_ms"] + s["generation_ms"] for s in data["samples"]]
    behavior = {
        s["id"]: (
            "拒答" if s["refusal"] else "作答",
            s.get("intent") or "",
            s.get("stop_reason") or "",
            s.get("tool_calls") or 0,
        )
        for s in data["samples"]
    }
    path_lines = sum(
        text.count(PATH_PREFIX) for s in data["samples"] for text in s.get("contexts") or []
    )
    return {
        "metrics": config["metrics"],
        "avg_ms": sum(per_sample_ms) / max(len(per_sample_ms), 1),
        "behavior": behavior,
        "path_lines": path_lines,
        "samples_with_path": sum(
            1 for s in data["samples"] if any(PATH_PREFIX in t for t in s.get("contexts") or [])
        ),
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def report_deltas(reference: dict[str, Any], current: dict[str, Any], label: str) -> None:
    """打印某一轮与参照轮的指标差与逐条行为翻动。"""
    print(f"\n--- {label} 对参照轮 ---")
    for name in GUARDRAILS + QUALITY + OWN:
        base, now = reference["metrics"].get(name), current["metrics"].get(name)
        mark = "＝" if base == now else "≠"
        print(f"  {name:20s} {base} → {now}  {mark}")
    print(f"  章节路径行数        {reference['path_lines']} → {current['path_lines']}")
    print(f"  带章节路径的样本    {reference['samples_with_path']} → {current['samples_with_path']}")
    flips = [
        (sid, base, now)
        for sid, base in reference["behavior"].items()
        if (now := current["behavior"].get(sid)) != base
    ]
    if not flips:
        print("  逐条行为（拒答/意图/停止原因/工具次数）没有翻动")
    for sid, base, now in flips:
        print(f"  {sid} {base} → {now}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同一进程交替开关树索引的对照评估")
    parser.add_argument("--values", default="off,on,off",
                        help="按顺序跑的开关值，逗号分隔；建议 off,on,off")
    parser.add_argument("--dataset", default="eval/v7_agent_qa.jsonl")
    parser.add_argument("--agent-sources", default=str(EVAL_AGENT_SOURCES_CONFIG))
    parser.add_argument("--prefix", default="v8_5_tree_ab", help="报告文件名前缀")
    parser.add_argument("--keep-reports", action="store_true",
                        help="把报告写到 eval/reports 而不是临时目录")
    args = parser.parse_args(argv)

    values = [part.strip() for part in args.values.split(",") if part.strip()]
    out_dir = ROOT / "eval/reports" if args.keep_reports else Path("/tmp")
    if not Path(args.dataset).exists():
        print(f"❌ 评估集不存在：{args.dataset}")
        return 1
    if not EVAL_TREE_INDEX_PATH.exists():
        print(f"❌ 树索引不存在：{EVAL_TREE_INDEX_PATH}")
        print("   请先运行：python3 scripts/build_tree_index.py --eval")
        return 1

    rounds: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        enabled = value in {"on", "1", "true", "yes"}
        # 开关在检索时读 qa 模块里的这个名字，改它才对
        qa.TREE_INDEX_ENABLED = enabled
        name = f"{args.prefix}_{index}_{value}"
        print(f"\n===== 第 {index} 轮：tree_index={value} =====", flush=True)
        evaluate.main([
            "--dataset", args.dataset,
            "--configs", "agent",
            "--agent-sources", args.agent_sources,
            "--skip-ragas",
            "--max-workers", "1",
            "--output", str(out_dir / f"{name}.md"),
            "--json-output", str(out_dir / f"{name}.json"),
            "--run-note", f"树索引同一进程交替对照 第{index}轮 tree_index={value}",
        ])
        rounds.append(round_stats(out_dir / f"{name}.json"))

    print("\n=== 单条均耗时（第一轮作废：冷启动要把生成与精排模型加载进来）===")
    for index, (value, stats) in enumerate(zip(values, rounds), start=1):
        note = "（第一轮，作废）" if index == 1 else ""
        print(f"  第 {index} 轮 tree_index={value:6s} {stats['avg_ms'] / 1000:6.1f}s{note}")

    # 噪声底：同一个开关值的轮次两两比对（通常是两个 off 轮）。指标不一致就说明
    # 这一轮的变化不能全归给开关，比对脚本本身按 v8.3 口径判定并返回非 0
    same_value = [i for i, value in enumerate(values) if value == values[0]]
    if len(same_value) > 1:
        print("\n=== 同值轮次的噪声底（compare_eval_noise.py）===")
        compare_eval_noise.main(
            [str(out_dir / f"{args.prefix}_{i + 1}_{values[i]}.json") for i in same_value]
        )

    reference = rounds[0]
    for index, (value, stats) in enumerate(zip(values, rounds), start=1):
        if index == 1:
            continue
        report_deltas(reference, stats, f"第 {index} 轮 tree_index={value}")
        if index > 2:
            # 只比暖轮：第 2 轮的上一轮是冷启动，差 40% 是冷热不是开关差别
            previous = rounds[index - 2]
            print(
                f"  耗时对上一轮（{values[index - 2]}）：{stats['avg_ms'] / 1000:.1f}s"
                f" vs {previous['avg_ms'] / 1000:.1f}s"
                f"（{(stats['avg_ms'] - previous['avg_ms']) / max(previous['avg_ms'], 1) * 100:+.1f}%）"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
