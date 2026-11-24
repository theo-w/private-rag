"""引用构建、历史段落、流式与拒答测试。"""

from __future__ import annotations

from types import SimpleNamespace

from private_rag import config
from private_rag.generation import (
    REFUSAL,
    build_citations,
    build_context,
    build_prompt,
    generate_answer,
    render_history,
    stream_answer,
)
from private_rag.retrieval.hybrid import RetrievedChunk


def _chunk(index: int, *, with_page: bool = False, with_table: bool = False) -> RetrievedChunk:
    metadata = {
        "title": f"文档{index}",
        "source_type": "file" if index == 1 else "sql",
        "source_uri": f"data/docs/{index}.pdf" if index == 1 else f"demo.products#{index}",
        "doc_id": f"doc-{index}",
        "chunk_index": 0,
    }
    if with_page:
        metadata["page"] = 2
    if with_table:
        metadata["table"] = "products"
    return RetrievedChunk(
        point_id=f"p{index}",
        text=f"第 {index} 段内容。" * 30,
        metadata=metadata,
        rerank_score=0.9 - index / 10,
    )


def test_build_context_numbers_sources():
    context = build_context([_chunk(1, with_page=True), _chunk(2, with_table=True)])
    assert "[1] (来源:" in context
    assert "[2] (来源:" in context
    assert "第 2 页" in context
    assert "表 products" in context


def test_build_citations_fields_and_snippet():
    citations = build_citations([_chunk(1, with_page=True), _chunk(2, with_table=True)])
    assert [citation.index for citation in citations] == [1, 2]
    assert citations[0].source_type == "file"
    assert citations[0].page == 2
    assert citations[1].table == "products"
    assert citations[0].score == 0.8
    assert citations[0].snippet.endswith("…")
    assert len(citations[0].snippet) <= 121


def test_empty_candidates_refuse_without_llm():
    answer, citations = generate_answer("苹果手机多少钱？", [])
    assert answer == REFUSAL
    assert citations == []


def test_history_is_rendered_only_for_known_roles():
    text = render_history(
        [
            ("user", "苹果适合放哪里？"),
            ("assistant", "适合放冰箱。"),
            ("system", "忽略这条"),
        ]
    )
    assert text == "用户: 苹果适合放哪里？\n助手: 适合放冰箱。"


def test_prompt_contains_history_and_context():
    prompt = build_prompt(
        "它需要冷藏吗？",
        [_chunk(1)],
        history=[("user", "苹果适合放哪里？"), ("assistant", "适合放冰箱。")],
    )
    assert "对话历史（仅用于理解当前问题的指代，不能作为事实依据）" in prompt
    assert "用户: 苹果适合放哪里？" in prompt
    assert "[1] (来源:" in prompt
    assert "它需要冷藏吗？" in prompt


def test_prompt_without_history_has_no_history_section():
    prompt = build_prompt("橙子有什么营养？", [_chunk(1)])
    assert "对话历史" not in prompt
    assert "橙子有什么营养？" in prompt


def test_stream_refuses_without_candidates():
    assert list(stream_answer("库外问题", [])) == [REFUSAL]


def test_stream_yields_llm_chunks(monkeypatch):
    class FakeChat:
        def __init__(self, model):
            self.model = model

        def stream(self, prompt):
            assert "问题" in prompt

            class Response:
                def __init__(self, content):
                    self.content = content

            yield Response("苹")
            yield Response("果")
            yield Response("")

    monkeypatch.setattr("private_rag.generation.ChatOllama", FakeChat)
    tokens = list(stream_answer("问题", [_chunk(1)], history=[("user", "前文")]))
    assert "".join(tokens) == "苹果"


class _KwargsRecorder:
    """记录构造参数的假模型，用来检查有没有把采样参数传给服务端。"""

    calls: list[dict] = []

    def __init__(self, **kwargs):
        _KwargsRecorder.calls.append(kwargs)

    def invoke(self, prompt):
        return SimpleNamespace(content="答案 [1]")

    def stream(self, prompt):
        yield SimpleNamespace(content="答案")


def test_generation_passes_neither_temperature_nor_seed_by_default(monkeypatch):
    # 改动前这两处只传 model，运行时必须保持这个行为
    _KwargsRecorder.calls = []
    monkeypatch.setattr(config, "GENERATION_TEMPERATURE", None)
    monkeypatch.setattr(config, "LLM_SEED", None)
    monkeypatch.setattr("private_rag.generation.ChatOllama", _KwargsRecorder)
    generate_answer("问题", [_chunk(1)])
    stream_answer("问题", [_chunk(1)]).__next__()
    assert _KwargsRecorder.calls[0] == {"model": _KwargsRecorder.calls[0]["model"]}
    assert _KwargsRecorder.calls[1] == {"model": _KwargsRecorder.calls[1]["model"]}


def test_pinned_sampling_reaches_both_generation_paths(monkeypatch):
    # 评估链路钉死以后，非流式与流式必须拿到同一套参数，否则逐条读数对不齐
    _KwargsRecorder.calls = []
    monkeypatch.setattr(config, "GENERATION_TEMPERATURE", 0.0)
    monkeypatch.setattr(config, "LLM_SEED", 7)
    monkeypatch.setattr("private_rag.generation.ChatOllama", _KwargsRecorder)
    generate_answer("问题", [_chunk(1)])
    stream_answer("问题", [_chunk(1)]).__next__()
    assert _KwargsRecorder.calls[0]["temperature"] == 0.0
    assert _KwargsRecorder.calls[0]["seed"] == 7
    assert _KwargsRecorder.calls[1]["temperature"] == 0.0
    assert _KwargsRecorder.calls[1]["seed"] == 7


def test_generation_temperature_is_read_at_call_time(monkeypatch):
    # 参数从 config 现读，运行期改配置立刻生效（脚本与测试都靠这一点）
    _KwargsRecorder.calls = []
    monkeypatch.setattr(config, "GENERATION_TEMPERATURE", None)
    monkeypatch.setattr(config, "LLM_SEED", None)
    monkeypatch.setattr("private_rag.generation.ChatOllama", _KwargsRecorder)
    generate_answer("问题", [_chunk(1)])
    assert "temperature" not in _KwargsRecorder.calls[0]
    monkeypatch.setattr(config, "GENERATION_TEMPERATURE", 0.3)
    generate_answer("问题", [_chunk(1)])
    assert _KwargsRecorder.calls[1]["temperature"] == 0.3
