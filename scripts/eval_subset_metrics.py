#!/usr/bin/env python3
"""从一份评估报告里按 id 子集重新算指标，给出「全集」与「可比子集」两套读数。

为什么要这个脚本：评估集扩充以后，一次跑出来的均值分母变了，全集数字不能和旧报告
横向比；但报告 JSON 里逐条读数都在，把旧集那些 id 挑出来用同一套 `aggregate_results`
再聚合一次，就得到与旧报告同口径的子集读数，新旧可以直接对。

跑法：
    python3 scripts/eval_subset_metrics.py eval/reports/xxx.json \
        --ids-file eval/v7_agent_qa.jsonl --ids-file eval/v8_agentic_expanded.jsonl
    python3 scripts/eval_subset_metrics.py eval/reports/xxx.json \
        --ids-file eval/v7_agent_qa.jsonl --baseline eval/reports/old.json

离线：只读报告 JSON 与数据集文件，不打模型、不联网、不开 Qdrant。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.evaluation import SampleResult, aggregate_results

# 与 eval/README 的读数表同口径，顺序也照它排
QUALITY_METRICS = (
    "evidence_hit@3",
    "evidence_recall@3",
    "context_precision@3",
    "mrr@3",
    "source_hit",
    "both_sides_hit",
    "answer_rate",
    "refusal_accuracy",
    "citation_rate",
    "citation_validity",
)
BEHAVIOR_METRICS = (
    "avg_latency_ms",
    "avg_retrieval_ms",
    "avg_generation_ms",
    "avg_retrieved",
    "avg_context_chars",
    "avg_query_count",
    "avg_tool_calls",
    "avg_tool_calls_knowledge",
    "avg_tool_calls_database",
    "avg_tool_calls_mixed",
    "dual_tool_rate",
    "gate_intercept_rate",
    "n_answerable",
    "n_refusal",
    "n_errors",
)


def _result_from_dict(data: dict[str, Any]) -> SampleResult:
    """把报告里的一条读数还原成 SampleResult（`_result_to_dict` 的逆操作）。"""
    return SampleResult(
        sample_id=str(data["id"]),
        config=str(data.get("config") or ""),
        category=str(data.get("category") or ""),
        answerable=bool(data.get("answerable")),
        question=str(data.get("question") or ""),
        reference=str(data.get("reference") or ""),
        evidence=list(data.get("evidence") or []),
        expected_source=str(data.get("expected_source") or ""),
        answer=str(data.get("answer") or ""),
        contexts=list(data.get("contexts") or []),
        citations=list(data.get("citations") or []),
        source_uris=list(data.get("source_uris") or []),
        used_lexical=bool(data.get("used_lexical")),
        used_rerank=bool(data.get("used_rerank")),
        used_parent=bool(data.get("used_parent")),
        query_rewrite=str(data.get("query_rewrite") or "off"),
        query_count=int(data.get("query_count") or 1),
        context_chars=int(data.get("context_chars") or 0),
        rewrite_fallback=bool(data.get("rewrite_fallback")),
        rewrite_reason=str(data.get("rewrite_reason") or ""),
        requested_rerank=bool(data.get("requested_rerank")),
        retrieved=int(data.get("retrieved") or 0),
        refusal=bool(data.get("refusal")),
        retrieval_ms=float(data.get("retrieval_ms") or 0.0),
        generation_ms=float(data.get("generation_ms") or 0.0),
        generation_cached=bool(data.get("generation_cached")),
        error=data.get("error"),
        metrics=dict(data.get("metrics") or {}),
        ragas=data.get("ragas"),
        intent=str(data.get("intent") or ""),
        stop_reason=str(data.get("stop_reason") or ""),
        tool_calls=data.get("tool_calls"),
        tool_steps=data.get("tool_steps"),
        tool_names=frozenset(data.get("tool_names") or []),
        gate_intercepts=int(data.get("gate_intercepts") or 0),
        trace_id=str(data.get("trace_id") or ""),
        history_turns=data.get("history_turns"),
        expected_sources=list(data.get("expected_sources") or []),
    )


def load_report(path: Path) -> tuple[dict[str, Any], list[SampleResult]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    samples = [_result_from_dict(item) for item in report.get("samples", [])]
    return report, samples


def ids_from_dataset(path: Path) -> list[str]:
    """从 JSONL 数据集取 id 清单（不校验，只要 id）。"""
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample_id = json.loads(line).get("id")
        if sample_id:
            ids.append(str(sample_id))
    return ids


def fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.3f}"


def compare(
    metrics: dict[str, Any],
    baseline: dict[str, Any],
    n: int,
    keys: tuple[str, ...],
) -> list[str]:
    """逐项比较，并把差值换算成「几条样本」——项目口径是变化不足 1 条不当真。"""
    lines: list[str] = []
    for key in keys:
        now, before = metrics.get(key), baseline.get(key)
        if now is None or before is None:
            continue
        delta = now - before
        # 比率指标的 1 条 = 1/n，换算成条数才能套用噪声底口径
        samples_equiv = delta * n if isinstance(now, float) and now <= 1.0 else delta
        mark = "不足 1 条，不当真" if abs(samples_equiv) < 1.0 else f"{samples_equiv:+.2f} 条"
        lines.append(f"  {key:<26} {fmt(before)} → {fmt(now)}  {delta:+.3f}  {mark}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 id 子集重算评估报告指标（离线）")
    parser.add_argument("report", help="报告 JSON 路径")
    parser.add_argument("--config", default="agent", help="配置名，默认 agent")
    parser.add_argument(
        "--ids-file",
        action="append",
        default=[],
        help="从 JSONL 数据集取 id（可重复，多个文件的 id 取并集）",
    )
    parser.add_argument("--ids", default="", help="逗号分隔的 id 清单")
    parser.add_argument("--baseline", default="", help="另一份报告，按同一子集逐项对比")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"❌ 报告不存在：{report_path}")
        return 1
    report, results = load_report(report_path)
    config_results = [item for item in results if item.config == args.config]
    if not config_results:
        print(f"❌ 报告里没有配置 {args.config!r}（现有：{sorted({i.config for i in results})}）")
        return 1

    subset_ids: list[str] = []
    for dataset in args.ids_file:
        path = Path(dataset)
        if not path.is_file():
            print(f"❌ 数据集不存在：{path}")
            return 1
        subset_ids.extend(ids_from_dataset(path))
    subset_ids.extend(item.strip() for item in args.ids.split(",") if item.strip())
    subset_ids = list(dict.fromkeys(subset_ids))

    meta = report.get("meta") or {}
    print(f"=== 报告：{report_path.name}（配置 {args.config}）===")
    print(
        f"出处 git_commit={meta.get('git_commit') or '—'} dirty={meta.get('git_dirty')}"
        f"｜数据集={','.join(str(d) for d in meta.get('datasets') or []) or '—'}"
    )
    if meta.get("run_note"):
        print(f"run_note：{meta['run_note']}")

    full = aggregate_results(config_results)
    print(f"\n全集 {len(config_results)} 条：")
    for key in QUALITY_METRICS + BEHAVIOR_METRICS:
        print(f"  {key:<26} {fmt(full.get(key))}")

    if subset_ids:
        picked = [item for item in config_results if item.sample_id in subset_ids]
        missing = [sid for sid in subset_ids if sid not in {item.sample_id for item in picked}]
        print(f"\n子集（可比口径）{len(picked)} 条：")
        if missing:
            print(f"  ⚠️ 报告里没有这些 id：{', '.join(missing)}")
        subset = aggregate_results(picked)
        for key in QUALITY_METRICS + BEHAVIOR_METRICS:
            print(f"  {key:<26} {fmt(subset.get(key))}")
        if args.baseline:
            baseline_path = Path(args.baseline)
            if not baseline_path.is_file():
                print(f"❌ 基线报告不存在：{baseline_path}")
                return 1
            _, baseline_results = load_report(baseline_path)
            baseline_picked = [
                item for item in baseline_results
                if item.config == args.config and item.sample_id in subset_ids
            ]
            print(f"\n与 {baseline_path.name} 同子集对比（{len(baseline_picked)} 条）：")
            baseline = aggregate_results(baseline_picked)
            print("  质量指标：")
            print("\n".join(compare(subset, baseline, len(picked), QUALITY_METRICS)))
            print("  行为指标：")
            print("\n".join(compare(subset, baseline, len(picked), BEHAVIOR_METRICS)))
            print("  逐条差异（判定相关的键）：")
            by_id = {item.sample_id: item for item in baseline_picked}
            diffs = 0
            for item in picked:
                before = by_id.get(item.sample_id)
                if before is None:
                    continue
                changed = [
                    key
                    for key in QUALITY_METRICS
                    if item.metrics.get(key) != before.metrics.get(key)
                ]
                flags = []
                if item.refusal != before.refusal:
                    flags.append(f"refusal {before.refusal}→{item.refusal}")
                if item.intent != before.intent:
                    flags.append(f"intent {before.intent or '—'}→{item.intent or '—'}")
                if item.stop_reason != before.stop_reason:
                    flags.append(f"stop {before.stop_reason or '—'}→{item.stop_reason or '—'}")
                if changed or flags:
                    diffs += 1
                    print(f"    {item.sample_id}: {', '.join(changed + flags)}")
            if not diffs:
                print("    （逐条一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
