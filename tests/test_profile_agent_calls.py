"""耗时分解脚本的纯逻辑单测：不打模型、不联网，用假函数与假响应。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "profile_agent_calls.py"
_spec = importlib.util.spec_from_file_location("profile_agent_calls", _SCRIPT)
assert _spec and _spec.loader
profile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(profile)


def _reset() -> None:
    profile.RECORDS.clear()
    profile._CURRENT.clear()


def test_classify_names_first_decision_as_route():
    _reset()
    records = [profile.new_record("decision") for _ in range(3)]
    profile.classify(records, route_calls=1)
    assert [r["kind"] for r in records] == ["route", "step", "step"]


def test_timed_records_inner_ms_and_answer_length():
    _reset()

    def fake_ask(prompt, **kwargs):
        # 模拟最底层把耗时回填进当前调用
        profile._CURRENT[-1]["ms"] = 4200
        return {"action": "final", "answer": "答案正文"}

    wrapped = profile.timed("decision", fake_ask)
    out = wrapped("提示词")
    assert out["action"] == "final"
    assert profile.RECORDS[0]["ms"] == 4200
    assert profile.RECORDS[0]["out_chars"] == len("答案正文")
    assert profile._CURRENT == []


def test_timed_keeps_elapsed_on_failure():
    _reset()

    def boom(prompt, **kwargs):
        raise RuntimeError("模型不可用")

    try:
        profile.timed("decision", boom)("提示词")
    except RuntimeError:
        pass
    assert len(profile.RECORDS) == 1
    assert profile.RECORDS[0]["error"] == "RuntimeError"
    assert profile.RECORDS[0]["ms"] >= 0
    assert profile._CURRENT == []


def test_read_usage_reads_both_thinking_locations():
    class WithAttribute:
        usage_metadata = {"input_tokens": 120, "output_tokens": 30}
        reasoning_content = "先想一想"
        additional_kwargs = {}

    class WithKwargs:
        usage_metadata = None
        reasoning_content = ""
        additional_kwargs = {"reasoning": "另一种存放位置"}

    class Empty:
        pass

    assert profile.read_usage(WithAttribute()) == (120, 30, len("先想一想"))
    assert profile.read_usage(WithKwargs()) == (0, 0, len("另一种存放位置"))
    assert profile.read_usage(Empty()) == (0, 0, 0)


def test_summarize_adds_up_across_samples():
    _reset()
    per_sample = [
        {"category": "knowledge", "by_kind": {"route": {"ms": 1000, "n": 1, "in_tokens": 10,
                                                       "out_tokens": 5, "thinking_chars": 3,
                                                       "prompt_chars": 200}}},
        {"category": "mixed", "by_kind": {"route": {"ms": 2000, "n": 1, "in_tokens": 20,
                                                    "out_tokens": 7, "thinking_chars": 9,
                                                    "prompt_chars": 500},
                                          "step": {"ms": 300, "n": 2, "in_tokens": 1,
                                                   "out_tokens": 1, "thinking_chars": 0,
                                                   "prompt_chars": 100}}},
    ]
    agg = profile.summarize(per_sample)
    assert agg["route"]["ms"] == 3000
    assert agg["route"]["n"] == 2
    assert agg["route"]["samples"] == 2
    assert agg["route"]["prompt_chars"] == 500  # 取最长，不是求和
    assert agg["step"]["n"] == 2
