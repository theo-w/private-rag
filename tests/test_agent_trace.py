"""决策轨迹测试：落盘格式、按 trace_id 取回、坏行容错与回放渲染。"""

from __future__ import annotations

import json

from private_rag.agent.trace import (
    AgentStep,
    AgentTrace,
    format_trace,
    load_traces,
    new_trace_id,
)


def make_trace(trace_id: str = "aaaa1111bbbb", *, question: str = "最便宜的香蕉多少钱") -> AgentTrace:
    trace = AgentTrace(
        trace_id=trace_id,
        question=question,
        model="qwen3.8:27b-mlx",
        created_at=1_700_000_000.0,
        request_id="req-1",
        session_id="sess-1",
    )
    trace.add(
        AgentStep(
            index=1,
            kind="route",
            tool="sql_query",
            args={"sql": "SELECT name, price FROM products ORDER BY price"},
            reason="问的是价格，该查业务库",
            observation="intent=database",
            elapsed_ms=120,
        )
    )
    trace.add(
        AgentStep(
            index=2,
            kind="tool",
            tool="sql_query",
            reason="拿数值",
            ok=True,
            observation="查询返回 1 行",
            rows=1,
            evidence_blocks=1,
            elapsed_ms=45,
        )
    )
    trace.citations = [
        {
            "index": 1,
            "title": "demo 查询结果",
            "source_type": "sql",
            "source_uri": "demo#sql_query",
            "snippet": "name | price；首行：banana | 3.5",
            "page": None,
            "table": "demo",
            "score": None,
        }
    ]
    trace.intent = "database"
    trace.answer = "香蕉最便宜，3.5 元[1]。"
    trace.stop_reason = "final"
    trace.tool_calls = 1
    trace.elapsed_ms = 900
    return trace


def test_new_trace_id_shape():
    first = new_trace_id()
    assert len(first) == 12
    assert first.isalnum()
    assert first != new_trace_id()


def test_to_dict_drops_field_chars_and_roundtrips():
    payload = make_trace().to_dict()
    assert "field_chars" not in payload
    assert payload["steps"][0]["args"]["sql"].startswith("SELECT")
    assert json.loads(make_trace().to_json())["trace_id"] == "aaaa1111bbbb"


def test_record_observation_clips_long_text():
    trace = make_trace()
    trace.field_chars = 10
    clipped = trace.record_observation("一" * 50)
    assert len(clipped) == 11
    assert clipped.endswith("…")


def test_save_appends_one_line_per_request(tmp_path):
    path = tmp_path / "traces" / "agent.jsonl"
    make_trace("aaaa1111bbbb").save(path)
    make_trace("cccc2222dddd", question="怎么增量入库").save(path)

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["trace_id"] == "aaaa1111bbbb"


def test_load_recent_and_filter_by_trace_id(tmp_path):
    path = tmp_path / "agent.jsonl"
    for index in range(5):
        make_trace(f"id{index}").save(path)

    recent = load_traces(path, limit=2)
    assert [t["trace_id"] for t in recent] == ["id3", "id4"]

    one = load_traces(path, trace_id="id1")
    assert len(one) == 1
    assert one[0]["question"] == "最便宜的香蕉多少钱"


def test_load_tolerates_missing_file_and_bad_lines(tmp_path):
    assert load_traces(tmp_path / "不存在.jsonl") == []

    path = tmp_path / "agent.jsonl"
    path.write_text(
        "\n".join(
            [
                make_trace("good1").to_json(),
                "这不是 JSON",
                json.dumps([1, 2, 3]),
                "   ",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    records = load_traces(path)
    assert [r["trace_id"] for r in records] == ["good1"]


def test_format_trace_shows_reasoning_chain():
    text = format_trace(make_trace().to_dict())
    assert "trace_id: aaaa1111bbbb" in text
    assert "最便宜的香蕉多少钱" in text
    assert "意图: database" in text
    assert "决策过程：" in text
    assert "[1] route -> sql_query" in text
    assert "理由: 问的是价格，该查业务库" in text
    assert "观察: 查询返回 1 行" in text
    assert "返回行数: 1" in text
    assert "引用：" in text
    assert "[1] demo 查询结果（sql）" in text
    assert "表 demo" in text
    assert "回答: 香蕉最便宜，3.5 元[1]。" in text


def test_format_trace_marks_final_and_error_steps():
    trace = make_trace()
    trace.add(AgentStep(index=3, kind="error", tool="kb_search", ok=False, error="检索失败：RuntimeError"))
    trace.add(AgentStep(index=4, kind="final", reason="证据够了", observation="结论正文"))
    text = format_trace(trace.to_dict())
    assert "错误: 检索失败：RuntimeError" in text
    assert "结论: 结论正文" in text
    # final 步骤展示为「结论」而不是「观察」
    assert "观察: 结论正文" not in text


def test_format_trace_survives_minimal_record():
    text = format_trace({"trace_id": "x", "created_at": 0})
    assert "trace_id: x" in text
    assert "决策过程：" in text
