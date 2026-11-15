"""v4 评估 CLI：策略对比 + 确定性指标 + Ragas + 报告。

用法：
    python scripts/evaluate.py --validate-only
    python scripts/evaluate.py --configs full --limit 3 \
        --ragas-configs full --ragas-limit 2 \
        --output /tmp/v4_smoke.md --json-output /tmp/v4_smoke.json
    python scripts/evaluate.py --configs dense,hybrid,full --ragas-configs dense,full
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须先设置环境变量，再导入 private_rag.config / qa / evaluation
from private_rag.eval_env import EVAL_CACHE_DIR, configure_eval_env  # noqa: E402

configure_eval_env()

from private_rag.config import EMB_MODEL, LLM_MODEL, ROOT  # noqa: E402
from private_rag.evaluation import (  # noqa: E402
    CONFIGS,
    DEFAULT_DATASET,
    RAGAS_DISPLAY_NAMES,
    DatasetError,
    build_report,
    collect_results,
    dataset_fingerprint,
    load_dataset,
    render_markdown,
    run_ragas_for_config,
    validate_distribution,
)
from private_rag.qa import build_engine  # noqa: E402


def report_path(value: Path | str) -> str:
    """报告只记录相对项目根的路径，避免把本机绝对路径写进公开仓库。"""
    path = Path(value).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 private-rag v4 检索策略评估")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="JSONL 评估集")
    parser.add_argument(
        "--configs",
        default="dense,hybrid,full",
        help="逗号分隔的策略配置：dense,hybrid,full",
    )
    parser.add_argument("--limit", type=int, help="只跑前 N 条样本（冒烟用）")
    parser.add_argument("--ids", help="只跑指定样本 ID，逗号分隔，例如 q01,q02")
    parser.add_argument("--skip-ragas", action="store_true", help="只跑确定性指标")
    parser.add_argument(
        "--ragas-configs",
        default="dense,full",
        help="逗号分隔：对哪些配置运行 Ragas（默认 dense,full）",
    )
    parser.add_argument("--ragas-limit", type=int, help="每个配置只评估前 N 条（子集）")
    parser.add_argument("--judge-model", default=LLM_MODEL, help="Ragas judge 模型")
    parser.add_argument("--strictness", type=int, default=3, help="AnswerRelevancy strictness")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Ragas 并发数（本地 Ollama 默认单并发，建议保持 1）",
    )
    parser.add_argument("--cache-dir", default=str(EVAL_CACHE_DIR), help="Ragas 缓存目录")
    parser.add_argument("--no-cache", action="store_true", help="关闭 Ragas 磁盘缓存")
    parser.add_argument(
        "--output",
        default=str(ROOT / "eval" / "reports" / "v4_report.md"),
        help="Markdown 报告路径",
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT / "eval" / "reports" / "v4_report.json"),
        help="JSON 报告路径",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验评估集，不加载 Ollama/Qdrant",
    )
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须是正整数")
    if args.ragas_limit is not None and args.ragas_limit <= 0:
        parser.error("--ragas-limit 必须是正整数")
    if args.max_workers <= 0:
        parser.error("--max-workers 必须是正整数")
    if args.limit is not None and args.ids:
        parser.error("--limit 与 --ids 不能同时使用")
    return args


def split_names(raw: str) -> list[str]:
    names = [item.strip() for item in raw.split(",") if item.strip()]
    deduped: list[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    return deduped


def select_samples(samples, args: argparse.Namespace):
    if args.ids:
        wanted = split_names(args.ids)
        missing = [sample_id for sample_id in wanted if sample_id not in {s.id for s in samples}]
        if missing:
            raise SystemExit(f"❌ 未知样本 ID：{missing}")
        return [sample for sample in samples if sample.id in set(wanted)]
    if args.limit is not None:
        return samples[: args.limit]
    return samples


def git_commit() -> str | None:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return output or None
    except Exception:
        return None


def print_summary(report: dict) -> None:
    print("\n=== 汇总 ===")
    for name, config in report["configs"].items():
        metrics = config["metrics"]
        print(
            f"[{name}] evidence_hit@3={metrics['evidence_hit@3']} "
            f"context_precision@3={metrics['context_precision@3']} "
            f"source_hit={metrics['source_hit']} "
            f"answer_rate={metrics['answer_rate']} "
            f"refusal_accuracy={metrics['refusal_accuracy']} "
            f"latency={metrics['avg_latency_ms']}ms"
        )
        ragas = config.get("ragas")
        if ragas:
            values = {
                metric: ragas["metrics"][metric]["value"] for metric in RAGAS_DISPLAY_NAMES
            }
            print(f"  ragas({ragas['n']} 条): {values}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        all_samples = load_dataset(args.dataset)
    except DatasetError as exc:
        print(f"❌ 评估集校验失败：{exc}")
        return 1

    distribution_errors = validate_distribution(all_samples)
    if distribution_errors:
        print("❌ 评估集分布不符合 v4 约定：")
        for error in distribution_errors:
            print(f"  - {error}")
        return 1

    if args.validate_only:
        categories = Counter(sample.category for sample in all_samples)
        answerable = sum(1 for sample in all_samples if sample.answerable)
        print(
            f"✅ 评估集有效：{len(all_samples)} 条"
            f"（{answerable} 可回答 + {len(all_samples) - answerable} 应拒答）"
        )
        print(f"   类别：{dict(categories)}")
        print(f"   SHA256：{dataset_fingerprint(args.dataset)}")
        return 0

    config_names = split_names(args.configs)
    unknown_configs = [name for name in config_names if name not in CONFIGS]
    if unknown_configs or not config_names:
        print(f"❌ 未知配置：{unknown_configs or '(空)'}，可选 {list(CONFIGS)}")
        return 1

    ragas_configs: list[str] = []
    if not args.skip_ragas:
        requested = split_names(args.ragas_configs)
        unknown = [name for name in requested if name not in CONFIGS]
        if unknown:
            print(f"❌ 未知 Ragas 配置：{unknown}，可选 {list(CONFIGS)}")
            return 1
        ragas_configs = [name for name in requested if name in config_names]
        skipped = [name for name in requested if name not in config_names]
        if skipped:
            print(f"⚠️ 这些配置本次不运行，已跳过 Ragas：{skipped}")

    samples = select_samples(all_samples, args)
    if not samples:
        print("❌ 没有选中任何样本")
        return 1

    print(
        f"📋 评估集：{len(all_samples)} 条，本次运行 {len(samples)} 条；"
        f"配置：{config_names}；Ragas：{ragas_configs or '关闭'}"
    )

    engine = build_engine(use_rerank="full" in config_names)
    try:
        readiness = engine.readiness()
        if not readiness.ready:
            print("❌ 评估知识库未就绪：" + "；".join(readiness.reasons))
            print("   请先运行：python3 scripts/prepare_eval_kb.py --recreate")
            return 2
        print(
            f"✅ 评估库：{readiness.vector_count} 个向量；"
            f"BM25={'有' if readiness.lexical_available else '无'}；"
            f"reranker={'有' if readiness.reranker_available else '无'}"
        )
        if readiness.warnings:
            for warning in readiness.warnings:
                print(f"⚠️ {warning}")

        started = time.perf_counter()
        results = collect_results(
            engine,
            samples,
            [CONFIGS[name] for name in config_names],
            model=LLM_MODEL,
            progress=lambda message: print(message, flush=True),
        )
        ragas_errors: dict[str, str] = {}
        for name in ragas_configs:
            try:
                run_ragas_for_config(
                    results,
                    name,
                    judge_model=args.judge_model,
                    embed_model=EMB_MODEL,
                    strictness=args.strictness,
                    max_workers=args.max_workers,
                    cache_dir=None if args.no_cache else args.cache_dir,
                    limit=args.ragas_limit,
                    progress=lambda message: print(message, flush=True),
                )
            except Exception as exc:
                ragas_errors[name] = f"{type(exc).__name__}: {exc}"
                print(f"❌ Ragas {name} 失败：{ragas_errors[name]}")
        duration = time.perf_counter() - started
    finally:
        engine.close()

    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": report_path(args.dataset),
        "dataset_sha256": dataset_fingerprint(args.dataset),
        "dataset_total": len(all_samples),
        "samples_total": len(samples),
        "samples_answerable": sum(1 for sample in samples if sample.answerable),
        "samples_refusal": sum(1 for sample in samples if not sample.answerable),
        "sample_ids": [sample.id for sample in samples],
        "categories": dict(Counter(sample.category for sample in samples)),
        "configs": config_names,
        "llm_model": LLM_MODEL,
        "judge_model": args.judge_model,
        "embed_model": EMB_MODEL,
        "ragas": {
            "enabled": bool(ragas_configs),
            "configs": ragas_configs,
            "limit": args.ragas_limit,
            "metrics": list(RAGAS_DISPLAY_NAMES),
            "strictness": args.strictness,
            "max_workers": args.max_workers,
            "cache": not args.no_cache,
            "cache_dir": None
            if args.no_cache
            else report_path(args.cache_dir),
        },
        "ragas_errors": ragas_errors,
        "duration_seconds": round(duration, 2),
        "git_commit": git_commit(),
    }
    report = build_report(meta, samples, results)

    output = Path(args.output)
    json_output = Path(args.json_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(report), encoding="utf-8")
    json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print_summary(report)
    print(f"\n📝 Markdown：{output}")
    print(f"📝 JSON：{json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
