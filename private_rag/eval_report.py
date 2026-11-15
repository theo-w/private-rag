"""读取 v4 评估报告，生成供 API/演示页使用的只读摘要。

报告由 scripts/evaluate.py 生成；这里只提取指标，不暴露逐样本上下文、
答案正文或本机绝对路径。
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from private_rag.config import ROOT

logger = logging.getLogger(__name__)

DEFAULT_EVAL_REPORT_PATH = ROOT / "eval" / "reports" / "v4_report.json"

# 展示给演示页的确定性指标；键名与报告 JSON 保持一致。
DETERMINISTIC_METRICS = (
    "evidence_hit@3",
    "context_precision@3",
    "mrr@3",
    "answer_rate",
    "refusal_accuracy",
    "avg_latency_ms",
)

RAGAS_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


def _finite_number(value: Any) -> Optional[float]:
    """只接受有限数值，NaN/Infinity/布尔值一律视为缺失。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _summary_metric_entry(raw: Any) -> dict:
    entry = raw if isinstance(raw, dict) else {}
    return {
        "value": _finite_number(entry.get("value")),
        "coverage": _finite_number(entry.get("coverage")),
        "valid": entry.get("valid") if isinstance(entry.get("valid"), int) else None,
    }


def _config_summary(name: str, raw: Any) -> dict:
    data = raw if isinstance(raw, dict) else {}
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    ragas = data.get("ragas") if isinstance(data.get("ragas"), dict) else None

    summary: dict[str, Any] = {
        "name": name,
        "label": str(spec.get("label") or name),
        "use_lexical": bool(spec.get("use_lexical", False)),
        "use_rerank": bool(spec.get("use_rerank", False)),
        "metrics": {key: _finite_number(metrics.get(key)) for key in DETERMINISTIC_METRICS},
        "ragas": None,
        "ragas_total": None,
    }
    if ragas:
        raw_metrics = ragas.get("metrics") if isinstance(ragas.get("metrics"), dict) else {}
        summary["ragas_total"] = ragas.get("n") if isinstance(ragas.get("n"), int) else None
        summary["ragas"] = {
            key: _summary_metric_entry(raw_metrics.get(key)) for key in RAGAS_METRICS
        }
    return summary


def load_eval_summary(path: Optional[Path] = None) -> dict:
    """读取报告并返回稳定结构；报告缺失/损坏时只返回 available=false。"""
    report_path = Path(path) if path is not None else DEFAULT_EVAL_REPORT_PATH
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "reason": "missing"}
    except (OSError, json.JSONDecodeError):
        logger.warning("评估报告无法读取：%s", report_path)
        return {"available": False, "reason": "invalid"}

    if not isinstance(payload, dict):
        return {"available": False, "reason": "invalid"}

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    configs_raw = payload.get("configs") if isinstance(payload.get("configs"), dict) else {}
    names = meta.get("configs") if isinstance(meta.get("configs"), list) else list(configs_raw)
    if not names:
        return {"available": False, "reason": "invalid"}

    dataset = meta.get("dataset")
    summary = {
        "available": True,
        "reason": None,
        "generated_at": meta.get("generated_at"),
        "dataset_name": Path(str(dataset)).name if dataset else None,
        "dataset_total": meta.get("dataset_total"),
        "samples_answerable": meta.get("samples_answerable"),
        "samples_refusal": meta.get("samples_refusal"),
        "llm_model": meta.get("llm_model"),
        "judge_model": meta.get("judge_model"),
        "embed_model": meta.get("embed_model"),
        "duration_seconds": _finite_number(meta.get("duration_seconds")),
        "git_commit": meta.get("git_commit"),
        "configs": [
            _config_summary(str(name), configs_raw.get(str(name))) for name in names
        ],
    }
    return summary
