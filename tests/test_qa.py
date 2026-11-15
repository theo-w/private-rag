"""共享问答引擎测试：readiness、检索选项、同步与流式编排。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from private_rag.models import Citation
from private_rag.qa import QaEvent, RagEngine, RetrievalOptions
from private_rag.query_rewrite import RewriteResult
from private_rag.retrieval.hybrid import RetrievedChunk


class FakeClient:
    def __init__(self, exists: bool = True, vectors: int = 3, fail: bool = False):
        self.exists = exists
        self.vectors = vectors
        self.fail = fail
        self.closed = False

    def collection_exists(self, name):
        if self.fail:
            raise RuntimeError("qdrant down")
        return self.exists

    def count(self, name):
        return SimpleNamespace(count=self.vectors)

    def close(self):
        self.closed = True


class FakeReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, question, chunks, top_k):
        self.calls.append((question, len(chunks), top_k))
        for index, chunk in enumerate(chunks):
            chunk.rerank_score = float(len(chunks) - index)
        return sorted(chunks, key=lambda c: c.rerank_score or 0.0, reverse=True)[:top_k]


def _chunk(index: int) -> RetrievedChunk:
    return RetrievedChunk(
        point_id=f"p{index}",
        text=f"第 {index} 段内容。",
        metadata={
            "title": f"文档{index}",
            "source_type": "file",
            "source_uri": f"data/docs/{index}.md",
            "doc_id": f"doc-{index}",
        },
    )


def _parent_chunk(index: int, parent_text: str) -> RetrievedChunk:
    chunk = _chunk(index)
    chunk.parent_id = f"parent-{index}"
    chunk.parent_text = parent_text
    chunk.parent_index = index
    return chunk


def _engine(client=None, lexical=None, reranker=None) -> RagEngine:
    return RagEngine(client or FakeClient(), object(), lexical, reranker)


def test_readiness_reports_missing_collection_and_degradations():
    readiness = _engine(
        client=FakeClient(exists=False, vectors=0),
        lexical=None,
        reranker=None,
    ).readiness()
    assert readiness.ready is False
    assert "集合不存在" in readiness.reasons[0]
    assert readiness.lexical_available is False
    assert readiness.reranker_available is False
    assert len(readiness.warnings) == 2


def test_readiness_reports_empty_and_healthy_store():
    empty = _engine(client=FakeClient(vectors=0)).readiness()
    assert empty.ready is False
    assert "知识库为空" in empty.reasons[0]

    ready = _engine(client=FakeClient(vectors=3), lexical=object(), reranker=object()).readiness()
    assert ready.ready is True
    assert ready.vector_count == 3
    assert ready.warnings == []


def test_readiness_handles_backend_errors():
    readiness = _engine(client=FakeClient(fail=True)).readiness()
    assert readiness.ready is False
    assert "知识库访问失败" in readiness.reasons[0]


def test_answer_uses_retrieval_options_rerank_and_history(monkeypatch):
    captured = {}

    class FakeRetriever:
        def __init__(self, client, embedding, lexical, **kwargs):
            captured["lexical"] = lexical
            captured["kwargs"] = kwargs

        def retrieve(self, question):
            captured["question"] = question
            return [_chunk(1), _chunk(2)]

    def fake_generate(question, chunks, model, history):
        captured["generate"] = (question, len(chunks), model, history)
        return "回答", [Citation(1, "文档1", "file", "data/docs/1.md", "片段")]

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr("private_rag.qa.generate_answer", fake_generate)

    reranker = FakeReranker()
    engine = _engine(lexical=object(), reranker=reranker)
    result = engine.answer(
        "问题",
        options=RetrievalOptions(source_type="file", tags=["x"], top_k=1),
        history=[("user", "前文"), ("assistant", "前答")],
    )

    assert captured["kwargs"] == {"source_type": "file", "tags": ["x"]}
    assert captured["question"] == "问题"
    assert captured["generate"] == ("问题", 1, engine.llm_model, [("user", "前文"), ("assistant", "前答")])
    assert reranker.calls == [("问题", 2, 1)]
    assert result.answer == "回答"
    assert result.retrieved == 1
    assert result.used_lexical is True
    assert result.used_rerank is True


def test_answer_degrades_to_dense_and_fusion_when_components_missing(monkeypatch):
    captured = {}

    class FakeRetriever:
        def __init__(self, client, embedding, lexical, **kwargs):
            captured["lexical"] = lexical

        def retrieve(self, question):
            return [_chunk(1), _chunk(2), _chunk(3)]

    def fake_generate(question, chunks, model, history):
        return "回答", []

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr("private_rag.qa.generate_answer", fake_generate)

    engine = _engine(lexical=object(), reranker=None)
    result = engine.answer(
        "问题",
        options=RetrievalOptions(top_k=2, use_lexical=False, use_rerank=True),
    )
    assert captured["lexical"] is None
    assert result.retrieved == 2
    assert result.used_lexical is False
    assert result.used_rerank is False


def test_stream_emits_citations_tokens_and_done(monkeypatch):
    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            return [_chunk(1)]

    def fake_stream(question, chunks, model, history):
        assert question == "问题"
        assert chunks[0].point_id == "p1"
        yield "苹"
        yield "果"

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr("private_rag.qa.stream_answer", fake_stream)

    engine = _engine(lexical=object(), reranker=None)
    events = list(engine.stream("问题", options=RetrievalOptions(use_rerank=False)))

    assert [event.kind for event in events] == ["citations", "token", "token", "done"]
    assert events[0].retrieved == 1
    assert events[0].citations[0].title == "文档1"
    assert events[0].used_lexical is True
    assert events[0].used_rerank is False
    assert [event.text for event in events[1:3]] == ["苹", "果"]
    assert events[-1].text == "苹果"
    assert events[-1].citations[0].title == "文档1"


def test_stream_without_candidates_refuses_without_llm(monkeypatch):
    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            return []

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    engine = _engine(lexical=None, reranker=None)
    events = list(engine.stream("库外问题"))

    assert [event.kind for event in events] == ["citations", "token", "done"]
    assert events[0].citations == []
    assert events[1].text == "【资料中没有相关信息】"
    assert events[2].text == "【资料中没有相关信息】"


def test_retrieve_with_trace_uses_multiple_queries_and_original_for_rerank(monkeypatch):
    queries = []

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            queries.append(question)
            if question == "原问题":
                return [_parent_chunk(1, "父块一")]
            return [_parent_chunk(1, "父块一"), _parent_chunk(2, "父块二")]

    def fake_rewrite(question, mode, *, model):
        assert mode == "multi"
        return RewriteResult([question, "改写查询"])

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr("private_rag.qa.rewrite_queries", fake_rewrite)

    reranker = FakeReranker()
    engine = _engine(lexical=object(), reranker=reranker)
    trace = engine.retrieve_with_trace(
        "原问题",
        RetrievalOptions(query_rewrite="multi", top_k=2, use_parent=True),
    )

    assert queries == ["原问题", "改写查询"]
    assert reranker.calls == [("原问题", 2, 2)]
    assert [chunk.point_id for chunk in trace.chunks] == ["p1", "p2"]
    assert trace.used_parent is True
    assert trace.query_rewrite == "multi"
    assert trace.query_count == 2
    assert trace.queries == ["原问题", "改写查询"]
    assert trace.context_chars == len("父块一") + len("父块二")


def test_retrieve_with_trace_reports_rewrite_fallback(monkeypatch):
    queries = []

    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            queries.append(question)
            return [_chunk(1)]

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr(
        "private_rag.qa.rewrite_queries",
        lambda question, mode, *, model: RewriteResult(
            [question], fallback=True, reason="模拟改写失败"
        ),
    )

    engine = _engine(lexical=None, reranker=None)
    trace = engine.retrieve_with_trace(
        "问题",
        RetrievalOptions(query_rewrite="hyde", use_parent=False, use_rerank=False),
    )

    assert queries == ["问题"]
    assert trace.query_count == 1
    assert trace.rewrite_fallback is True
    assert trace.rewrite_reason == "模拟改写失败"
    assert [chunk.text for chunk in trace.chunks] == ["第 1 段内容。"]
    assert trace.context_chars == len("第 1 段内容。")


def test_retrieve_without_parent_keeps_child_text_and_snippet(monkeypatch):
    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            return [_parent_chunk(1, "父块完整文本"), _parent_chunk(2, "另一个父块")]

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)

    engine = _engine(lexical=None, reranker=None)
    trace = engine.retrieve_with_trace(
        "问题",
        RetrievalOptions(use_parent=False, use_rerank=False, top_k=2),
    )

    assert trace.used_parent is False
    assert [chunk.text for chunk in trace.chunks] == ["第 1 段内容。", "第 2 段内容。"]
    assert [chunk.snippet_text for chunk in trace.chunks] == [
        "第 1 段内容。",
        "第 2 段内容。",
    ]
    assert trace.context_chars == len("第 1 段内容。") + len("第 2 段内容。")


def test_answer_reports_parent_and_rewrite_metadata(monkeypatch):
    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            return [_parent_chunk(1, "父块完整文本")]

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr(
        "private_rag.qa.rewrite_queries",
        lambda question, mode, *, model: RewriteResult([question, "改写查询"]),
    )
    monkeypatch.setattr(
        "private_rag.qa.generate_answer",
        lambda question, chunks, model, history: (
            "回答",
            [Citation(1, "文档1", "file", "data/docs/1.md", "片段")],
        ),
    )

    engine = _engine(lexical=object(), reranker=None)
    result = engine.answer(
        "问题",
        options=RetrievalOptions(query_rewrite="multi", use_rerank=False),
    )

    assert result.answer == "回答"
    assert result.retrieved == 1
    assert result.used_parent is True
    assert result.query_rewrite == "multi"
    assert result.query_count == 2
    assert result.context_chars == len("父块完整文本")


def test_stream_events_carry_parent_and_query_metadata(monkeypatch):
    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            return [_parent_chunk(1, "父块完整文本")]

    def fake_stream(question, chunks, model, history):
        assert chunks[0].text == "父块完整文本"
        yield "回答"

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr("private_rag.qa.stream_answer", fake_stream)

    engine = _engine(lexical=None, reranker=None)
    events = list(engine.stream("问题", options=RetrievalOptions(use_rerank=False)))

    citations = events[0]
    assert citations.kind == "citations"
    assert citations.used_parent is True
    assert citations.query_count == 1
    assert citations.context_chars == len("父块完整文本")
    assert events[-1].kind == "done"
    assert events[-1].used_parent is True
    assert events[-1].context_chars == len("父块完整文本")


def test_engine_close_closes_client():
    client = FakeClient()
    engine = _engine(client=client)
    engine.close()
    assert client.closed is True


def test_stream_events_have_flags_and_retrieved():
    event = QaEvent("done", text="x", retrieved=2, used_lexical=True, used_rerank=True)
    assert event.kind == "done"
    assert event.retrieved == 2
