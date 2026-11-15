"""API 层测试：同步/流式问答、会话隔离、错误格式与并发。"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from private_rag.api.app import create_app
from private_rag.models import Citation
from private_rag.qa import QaEvent, QaResult, Readiness
from private_rag.sessions import SessionStore


class FakeEngine:
    """不加载 Ollama/Qdrant 的引擎替身，记录 API 传入的选项与历史。"""

    def __init__(self, *, ready: bool = True, fail_answer: bool = False, fail_stream: bool = False):
        self.ready = ready
        self.fail_answer = fail_answer
        self.fail_stream = fail_stream
        self.delay = 0.0
        self.calls: list[tuple] = []

    def readiness(self) -> Readiness:
        return Readiness(
            ready=self.ready,
            vector_count=1 if self.ready else 0,
            lexical_available=True,
            reranker_available=True,
            reasons=[] if self.ready else ["知识库为空，请先运行 ingest"],
            warnings=[],
            collection_exists=self.ready,
        )

    def _citation(self) -> Citation:
        return Citation(
            index=1,
            title="文档A",
            source_type="file",
            source_uri="data/docs/a.md",
            snippet="片段内容",
            score=0.9,
        )

    def answer(self, question, *, options=None, history=None):
        self.calls.append(("answer", question, options, list(history or [])))
        if self.delay:
            time.sleep(self.delay)
        if self.fail_answer:
            raise RuntimeError("模拟生成失败")
        return QaResult(
            answer=f"回答:{question}",
            citations=[self._citation()],
            retrieved=1,
            used_lexical=bool(options.use_lexical if options else True),
            used_rerank=bool(options.use_rerank if options else True),
            used_parent=bool(options.use_parent if options else True),
            query_rewrite=options.query_rewrite if options else "off",
            query_count=2 if options and options.query_rewrite != "off" else 1,
            context_chars=12,
        )

    def stream(self, question, *, options=None, history=None):
        self.calls.append(("stream", question, options, list(history or [])))
        if self.fail_stream:
            raise RuntimeError("模拟流式失败")
        citation = self._citation()
        used_parent = bool(options.use_parent if options else True)
        query_rewrite = options.query_rewrite if options else "off"
        query_count = 2 if options and options.query_rewrite != "off" else 1
        yield QaEvent(
            "citations",
            citations=[citation],
            retrieved=1,
            used_lexical=True,
            used_rerank=True,
            used_parent=used_parent,
            query_rewrite=query_rewrite,
            query_count=query_count,
            context_chars=12,
        )
        if self.delay:
            time.sleep(self.delay)
        yield QaEvent("token", text="流式", retrieved=1)
        yield QaEvent("token", text=f"回答:{question}", retrieved=1)
        yield QaEvent(
            "done",
            text=f"流式回答:{question}",
            citations=[citation],
            retrieved=1,
            used_lexical=True,
            used_rerank=True,
            used_parent=used_parent,
            query_rewrite=query_rewrite,
            query_count=query_count,
            context_chars=12,
        )


class ApiHarness:
    def __init__(self, engine: FakeEngine):
        self.engine = engine
        self.sessions = SessionStore(max_sessions=100)
        self.app = create_app(engine=engine, session_store=self.sessions)
        self.client = TestClient(self.app)

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *args):
        return self.client.__exit__(*args)


@pytest.fixture
def api():
    with ApiHarness(FakeEngine()) as harness:
        yield harness


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        if name:
            events.append((name, json.loads("\n".join(data_lines))))
    return events


def test_health_root_and_request_id(api):
    response = api.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.5.0"}
    assert response.headers["X-Request-ID"]

    root = api.client.get("/", headers={"X-Request-ID": "trace-abc.123"})
    assert root.status_code == 200
    assert root.json()["api_prefix"] == "/api/v1"
    assert root.headers["X-Request-ID"] == "trace-abc.123"

    replaced = api.client.get("/health", headers={"X-Request-ID": "bad id!"})
    assert replaced.headers["X-Request-ID"] != "bad id!"


def test_demo_page_and_root_link(api):
    response = api.client.get("/demo")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert 'id="chat-form"' in response.text
    assert 'id="top-k"' in response.text
    assert 'id="use-parent"' in response.text
    assert 'id="query-rewrite"' in response.text
    assert "file://" in response.text
    assert 'id="eval-panel"' in response.text

    root = api.client.get("/")
    assert root.json()["demo"] == "/demo"


def test_ready_reports_components_and_unready_state(api):
    response = api.client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "collection": True,
        "vectors": 1,
        "lexical": True,
        "reranker": True,
    }
    assert body["reasons"] == []

    api.engine.ready = False
    unready = api.client.get("/ready")
    assert unready.status_code == 503
    assert unready.json()["status"] == "not_ready"
    assert "知识库为空" in unready.json()["reasons"][0]


def test_chat_creates_session_and_persists_full_exchange(api):
    response = api.client.post("/api/v1/chat", json={"question": "苹果怎么保存？"})
    assert response.status_code == 200
    body = response.json()
    session_id = body["session_id"]
    assert session_id.startswith("sess_")
    assert body["answer"] == "回答:苹果怎么保存？"
    assert body["citations"][0]["title"] == "文档A"
    assert body["citations"][0]["score"] == 0.9
    assert body["meta"]["retrieved"] == 1
    assert body["meta"]["history_turns"] == 0
    assert body["request_id"] == response.headers["X-Request-ID"]

    detail = api.client.get(f"/api/v1/sessions/{session_id}").json()
    assert detail["title"] == "苹果怎么保存？"
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"][0]["title"] == "文档A"

    second = api.client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "question": "它需要冷藏吗？"},
    ).json()
    assert second["meta"]["history_turns"] == 1
    history = api.engine.calls[-1][3]
    assert history == [("user", "苹果怎么保存？"), ("assistant", "回答:苹果怎么保存？")]


def test_chat_options_and_filters_reach_engine(api):
    response = api.client.post(
        "/api/v1/chat",
        json={
            "question": "过滤问题",
            "top_k": 2,
            "source_type": "file",
            "tags": [" a ", "a", "b"],
            "use_lexical": False,
            "use_rerank": False,
            "use_parent": False,
            "query_rewrite": "multi",
        },
    )
    assert response.status_code == 200
    options = api.engine.calls[-1][2]
    assert options.source_type == "file"
    assert options.tags == ["a", "b"]
    assert options.top_k == 2
    assert options.use_lexical is False
    assert options.use_rerank is False
    assert options.use_parent is False
    assert options.query_rewrite == "multi"
    assert response.json()["meta"]["used_parent"] is False
    assert response.json()["meta"]["query_rewrite"] == "multi"
    assert response.json()["meta"]["query_count"] == 2
    assert response.json()["meta"]["context_chars"] == 12


def test_sessions_crud_and_unknown_session(api):
    created = api.client.post("/api/v1/sessions", json={"title": "测试会话"})
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert created.json()["title"] == "测试会话"

    no_body = api.client.post("/api/v1/sessions")
    assert no_body.status_code == 201

    detail = api.client.get(f"/api/v1/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["message_count"] == 0

    deleted = api.client.delete(f"/api/v1/sessions/{session_id}")
    assert deleted.status_code == 204
    assert api.client.get(f"/api/v1/sessions/{session_id}").status_code == 404
    assert api.client.delete(f"/api/v1/sessions/{session_id}").status_code == 404

    unknown_chat = api.client.post(
        "/api/v1/chat",
        json={"session_id": "sess_" + "0" * 32, "question": "你好"},
    )
    assert unknown_chat.status_code == 404
    assert unknown_chat.json()["error"]["code"] == "session_not_found"


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   "},
        {"question": "x" * 2001},
        {"question": "x", "session_id": "abc"},
        {"question": "x", "top_k": 0},
        {"question": "x", "top_k": 11},
        {"question": "x", "source_type": "pdf"},
        {"question": "x", "tags": [f"t{index}" for index in range(11)]},
        {"question": "x", "query_rewrite": "invalid"},
        {"question": "x", "unknown": True},
    ],
)
def test_invalid_requests_return_unified_422(api, payload):
    response = api.client.post("/api/v1/chat", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert error["details"]


def test_unknown_route_and_method_return_unified_errors(api):
    missing = api.client.get("/api/v1/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    wrong_method = api.client.get("/api/v1/chat")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


def test_chat_returns_503_when_knowledge_base_not_ready(api):
    api.engine.ready = False
    response = api.client.post("/api/v1/chat", json={"question": "苹果"})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "knowledge_base_not_ready"
    assert "知识库为空" in error["details"]["reasons"][0]
    assert len(api.sessions) == 0

    stream = api.client.post("/api/v1/chat/stream", json={"question": "苹果"})
    assert stream.status_code == 503
    assert stream.headers["content-type"].startswith("application/json")


def test_stream_emits_events_and_persists_session(api):
    response = api.client.post("/api/v1/chat/stream", json={"question": "流式问题"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["X-Request-ID"]

    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["session", "citations", "token", "token", "done"]
    session_id = events[0][1]["session_id"]
    assert events[1][1]["retrieved"] == 1
    assert events[1][1]["citations"][0]["title"] == "文档A"
    assert events[1][1]["used_parent"] is True
    assert events[1][1]["query_rewrite"] == "off"
    assert events[1][1]["query_count"] == 1
    assert events[1][1]["context_chars"] == 12
    assert events[-1][1]["answer"] == "流式回答:流式问题"
    assert events[-1][1]["meta"]["history_turns"] == 0
    assert events[-1][1]["meta"]["used_parent"] is True

    detail = api.client.get(f"/api/v1/sessions/{session_id}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]

    second = parse_sse(
        api.client.post(
            "/api/v1/chat/stream",
            json={"session_id": session_id, "question": "继续问"},
        ).text
    )
    assert second[-1][1]["meta"]["history_turns"] == 1
    assert api.engine.calls[-1][3] == [
        ("user", "流式问题"),
        ("assistant", "流式回答:流式问题"),
    ]


def test_stream_failure_emits_error_event_and_discards_exchange():
    engine = FakeEngine(fail_stream=True)
    with ApiHarness(engine) as api:
        response = api.client.post("/api/v1/chat/stream", json={"question": "失败问题"})
        assert response.status_code == 200

        events = parse_sse(response.text)
        assert [name for name, _ in events] == ["session", "error"]
        error = events[-1][1]
        assert error["code"] == "generation_failed"
        assert error["request_id"] == response.headers["X-Request-ID"]

        session = api.sessions.get(events[0][1]["session_id"])
        assert session is not None
        assert session.messages == []


def test_unexpected_sync_failure_returns_500_with_request_id():
    engine = FakeEngine(fail_answer=True)
    app = create_app(engine=engine, session_store=SessionStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/chat", json={"question": "失败问题"})
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_engine_startup_failure_starts_degraded(monkeypatch):
    def fail_build(**kwargs):
        raise RuntimeError("模拟初始化失败")

    monkeypatch.setattr("private_rag.api.app.build_engine", fail_build)
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "service_unavailable"
        chat = client.post("/api/v1/chat", json={"question": "苹果"})
        assert chat.status_code == 503
        assert chat.json()["error"]["code"] == "service_unavailable"


def test_session_capacity_returns_503(api):
    api.sessions.max_sessions = 1
    holder = api.sessions.create(title="占用中")
    holder.lock.acquire()
    try:
        response = api.client.post("/api/v1/chat", json={"question": "容量问题"})
    finally:
        holder.lock.release()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "session_capacity_reached"


def test_concurrent_same_session_requests_are_serialized(api):
    created = api.client.post("/api/v1/sessions", json={}).json()
    session_id = created["session_id"]
    api.engine.delay = 0.05

    def ask(question: str):
        return api.client.post(
            "/api/v1/chat",
            json={"session_id": session_id, "question": question},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result() for future in (pool.submit(ask, "问题一"), pool.submit(ask, "问题二"))]

    assert all(response.status_code == 200 for response in responses)
    session = api.sessions.get(session_id)
    assert len(session.messages) == 4
    histories = [
        call[3]
        for call in api.engine.calls
        if call[0] == "answer" and call[1] in {"问题一", "问题二"}
    ]
    assert sorted(len(history) // 2 for history in histories) == [0, 1]


def test_concurrent_sessions_do_not_share_history(api):
    first = api.client.post("/api/v1/sessions", json={}).json()["session_id"]
    second = api.client.post("/api/v1/sessions", json={}).json()["session_id"]
    api.engine.delay = 0.05

    def ask(session_id: str, question: str):
        return api.client.post(
            "/api/v1/chat",
            json={"session_id": session_id, "question": question},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [
            future.result()
            for future in (
                pool.submit(ask, first, "会话一的问题"),
                pool.submit(ask, second, "会话二的问题"),
            )
        ]

    assert all(response.status_code == 200 for response in responses)
    assert len(api.sessions.get(first).messages) == 2
    assert len(api.sessions.get(second).messages) == 2

    follow_up = api.client.post(
        "/api/v1/chat",
        json={"session_id": first, "question": "会话一的追问"},
    ).json()
    assert follow_up["meta"]["history_turns"] == 1
    assert api.engine.calls[-1][3] == [
        ("user", "会话一的问题"),
        ("assistant", "回答:会话一的问题"),
    ]
