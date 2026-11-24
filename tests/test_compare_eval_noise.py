"""噪声底比对脚本的单测（不依赖模型与网络，用临时 JSON 报告）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compare_eval_noise.py"
_spec = importlib.util.spec_from_file_location("compare_eval_noise", _SCRIPT)
assert _spec and _spec.loader
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def _write_round(tmp_path: Path, name: str, *, answer_rate: float, answer: str) -> str:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "configs": {
                    "full": {
                        "metrics": {
                            "answer_rate": answer_rate,
                            "source_hit": 1.0,
                            "both_sides_hit": float("nan"),
                            "avg_latency_ms": 10000,
                        }
                    }
                },
                "samples": [
                    {
                        "id": "k01",
                        "refusal": False,
                        "intent": None,
                        "stop_reason": None,
                        "tool_calls": None,
                        "context_chars": 100,
                        "retrieval_ms": 1000,
                        "generation_ms": 9000,
                        "answer": answer,
                        "metrics": {"source_hit": 1.0, "evidence_hit@3": 1.0},
                    }
                ],
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    return str(path)


def test_nan_counts_as_not_applicable(tmp_path, capsys):
    # 固定链路没挂运行时 SQL 源时 both_sides_hit 整列是 NaN，不能报成「轮轮不一致」
    a = _write_round(tmp_path, "a.json", answer_rate=1.0, answer="同一答案")
    b = _write_round(tmp_path, "b.json", answer_rate=1.0, answer="同一答案")
    assert compare.main([a, b]) == 0
    out = capsys.readouterr().out
    assert "both_sides_hit: 不适用" in out
    assert "answer_rate: 一致" in out


def test_real_metric_change_is_reported(tmp_path, capsys):
    a = _write_round(tmp_path, "a.json", answer_rate=1.0, answer="同一答案")
    b = _write_round(tmp_path, "b.json", answer_rate=0.5, answer="同一答案")
    assert compare.main([a, b]) == 1
    assert "answer_rate: 不一致" in capsys.readouterr().out
