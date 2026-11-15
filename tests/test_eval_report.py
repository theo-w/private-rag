"""评估报告摘要与只读 API 测试。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from private_rag.api.app import create_app
from private_rag.eval_report import load_eval_summary


def make_report() -> dict:
    return {
        "meta": {
            "generated_at": "2026-11-15T13:59:57+08:00",
            "dataset": "/Users/someone/private-rag/eval/project_rag_qa.jsonl",
            "dataset_total": 40,
            "samples_answerable": 36,
            "samples_refusal": 4,
            "configs": ["dense", "hybrid", "full"],
            "llm_model": "qwen3.8:27b-mlx",
            "judge_model": "qwen2.5:7b-instruct",
            "embed_model": "bge-m3",
            "duration_seconds": 3801.14,
            "git_commit": "a6ab3ba3db9dcca84c730051c347a765aa905bc1",
        },
        "configs": {
            "dense": {
                "spec": {"label": "仅向量召回", "use_lexical": False, "use_rerank": False},
                "metrics": {
                    "evidence_hit@3": 0.5833333333333334,
                    "context_precision@3": 0.2037037037037036,
                    "mrr@3": 0.42592592592592593,
                    "answer_rate": 0.8888888888888888,
                    "refusal_accuracy": 1.0,
                    "avg_latency_ms": 11077.94281442499,
                },
                "ragas": {
                    "n": 36,
                    "metrics": {
                        "faithfulness": {
                            "value": float("nan"),
                            "valid": 35,
                            "coverage": 0.9722222222222222,
                        },
                        "answer_relevancy": {
                            "value": 0.6951071387737889,
                            "valid": 36,
                            "coverage": 1.0,
                        },
                        "context_precision": {
                            "value": 0.7708333332802081,
                            "valid": 36,
                            "coverage": 1.0,
                        },
                        "context_recall": {
                            "value": 0.838,
                            "valid": 36,
                            "coverage": 1.0,
                        },
                    },
                },
            },
            "hybrid": {
                "spec": {"label": "向量 + BM25 + RRF", "use_lexical": True, "use_rerank": False},
                "metrics": {"evidence_hit@3": 0.8055555555555556},
                "ragas": None,
            },
            "full": {
                "spec": {
                    "label": "混合召回 + 精排（v2/v3 默认基线）",
                    "use_lexical": True,
                    "use_rerank": True,
                },
                "metrics": {"evidence_hit@3": 0.9166666666666666},
                "ragas": {
                    "n": 36,
                    "metrics": {
                        "faithfulness": {
                            "value": 0.9361331569664905,
                            "valid": 36,
                            "coverage": 1.0,
                        }
                    },
                },
            },
        },
    }


def test_load_summary_extracts_metrics_without_absolute_path(tmp_path):
    path = tmp_path / "v4_report.json"
    path.write_text(json.dumps(make_report(), ensure_ascii=False), encoding="utf-8")

    summary = load_eval_summary(path)

    assert summary["available"] is True
    assert summary["dataset_name"] == "project_rag_qa.jsonl"
    assert "/Users/someone" not in json.dumps(summary, ensure_ascii=False)
    assert [item["name"] for item in summary["configs"]] == ["dense", "hybrid", "full"]
    assert summary["configs"][1]["ragas"] is None
    assert "基线" in summary["configs"][2]["label"]

    dense = summary["configs"][0]
    # NaN 不写进 API，统一显示为缺失值。
    assert dense["ragas"]["faithfulness"]["value"] is None
    assert dense["ragas"]["faithfulness"]["coverage"] == 0.9722222222222222
    assert dense["ragas"]["faithfulness"]["valid"] == 35
    assert dense["ragas_total"] == 36
    assert dense["metrics"]["avg_latency_ms"] == 11077.94281442499


def test_load_summary_handles_missing_and_invalid_report(tmp_path):
    missing = load_eval_summary(tmp_path / "missing.json")
    assert missing == {"available": False, "reason": "missing"}

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_eval_summary(broken) == {"available": False, "reason": "invalid"}

    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert load_eval_summary(empty) == {"available": False, "reason": "invalid"}


def test_eval_summary_api_reads_report(tmp_path):
    path = tmp_path / "v4_report.json"
    path.write_text(json.dumps(make_report(), ensure_ascii=False), encoding="utf-8")
    app = create_app(engine=object(), eval_report_path=path)

    with TestClient(app) as client:
        response = client.get("/api/v1/eval/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["dataset_total"] == 40
    assert [item["name"] for item in body["configs"]] == ["dense", "hybrid", "full"]
    assert body["configs"][0]["ragas"]["faithfulness"]["value"] is None


def test_eval_summary_api_without_report(tmp_path):
    app = create_app(engine=object(), eval_report_path=tmp_path / "missing.json")

    with TestClient(app) as client:
        response = client.get("/api/v1/eval/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "missing"
    assert body["configs"] == []
