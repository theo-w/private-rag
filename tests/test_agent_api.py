"""agentic 接口测试：/chat/agent 的响应结构、会话写入、参数校验与轨迹回放接口。

用注入的假引擎与假运行时，不碰 Qdrant 与本机 Ollama。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi.testclient import TestClient

from private_rag.agent.runtime import AgentResult
from private_rag.agent.trace import AgentStep, AgentTrace
from private_rag.api import app as app_module
from private_rag.api.app import create_app
from private_rag.models import Citation
from private_rag.qa import Readiness
from private_rag.sessions import SessionStore

TRACE_ID_PREFIX = "trace"


class FakeEngine:
    def __init__(self, ready: bool = True):
        self.ready = ready
        self.closed = False

    def readiness(self) -> Readiness:
        # 用真实的 Readiness，字段改名时测试会立刻报错而不是运行时 500
        return Readiness(
            ready=self.ready,
            vector_count=12 if self.ready else 0,
            lexical_available=True,
            reranker_available=True,
            reasons=[] if self.ready else ["知识库为空，请先运行 ingest"],
            warnings=[],
            collection_exists=self.ready,
        )

    def close(self) -> None:
        self.closed = True


class FakeRuntime:
    """实现 AgentRuntime 对接口暴露的那一个方法，返回一条完整轨迹。"""

    def __init__(self):
        self.calls: list[dict] = []

    def run(self, question, *, options=None, history=None, request_id="", session_id=""):
        self.calls.append(
            {
                "question": question,
                "options": options,
                "history": list(history or []),
                "request_id": request_id,
                "session_id": session_id,
            }
        )
        trace_id = f"{TRACE_ID_PREFIX}{len(self.calls):04d}"
        citation = Citation(
            index=1,
            title="demo 查询结果",
            source_type="sql",
            source_uri="demo#sql_query",
            snippet="name | price；首行：banana | 3.5",
            table="demo",
        )
        trace = AgentTrace(
            trace_id=trace_id,
            question=question,
            model="fake-model",
            created_at=1.0,
            intent="database",
            stop_reason="final",
            tool_calls=1,
            elapsed_ms=5,
            request_id=request_id,
            session_id=session_id,
        )
        trace.add(
            AgentStep(
                index=1,
                kind="route",
                tool="sql_query",
                args={"sql": "SELECT name, price FROM products"},
                reason="问的是价格",
                observation="intent=database",
            )
        )
        trace.add(
            AgentStep(
                index=2,
                kind="tool",
                tool="sql_query",
                reason="取数值",
                observation="查询返回 1 行",
                rows=1,
                evidence_blocks=1,
            )
        )
        trace.add(AgentStep(index=3, kind="final", reason="证据够了", observation="香蕉 3.5 元[1]"))
        trace.citations = [asdict(citation)]
        trace.answer = "香蕉 3.5 元[1]"
        return AgentResult(
            answer="香蕉 3.5 元[1]",
            citations=[citation],
            intent="database",
            stop_reason="final",
            tool_calls=1,
            steps=list(trace.steps),
            trace_id=trace_id,
            evidence_blocks=1,
            context_chars=30,
            elapsed_ms=5,
            trace=trace,
        )


def build_client(tmp_path=None, *, ready=True, with_agent=True):
    app = create_app(
        engine=FakeEngine(ready=ready),
        session_store=SessionStore(),
        agent_runtime=FakeRuntime() if with_agent else None,
        agent_sources_path=tmp_path / "no-sources.yaml" if tmp_path is not None else None,
    )
    if tmp_path is not None:
        app.state.agent_trace_path = tmp_path / "traces" / "agent.jsonl"
    return TestClient(app), app


def chat(client, question="最便宜的香蕉多少钱", **extra):
    return client.post("/api/v1/chat/agent", json={"question": question, **extra})


class TestAgentChat:
    def test_answer_meta_and_trace(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            response = chat(client)
        assert response.status_code == 200
        body = response.json()

        assert body["answer"] == "香蕉 3.5 元[1]"
        assert body["citations"][0]["source_type"] == "sql"
        assert body["citations"][0]["table"] == "demo"

        meta = body["meta"]
        assert meta["intent"] == "database"
        assert meta["stop_reason"] == "final"
        assert meta["tool_calls"] == 1
        assert meta["steps"] == 3
        assert meta["evidence_blocks"] == 1
        assert meta["context_chars"] == 30
        assert meta["history_turns"] == 0
        assert meta["trace_id"] == body["trace"]["trace_id"]

        steps = body["trace"]["steps"]
        assert [step["kind"] for step in steps] == ["route", "tool", "final"]
        assert steps[0]["args"] == {"sql": "SELECT name, price FROM products"}
        assert steps[1]["rows"] == 1

    def test_runtime_sees_question_and_defaults(self, tmp_path):
        client, app = build_client(tmp_path)
        with client:
            chat(client, "香蕉多少钱")
        call = app.state.agent_runtime.calls[0]
        assert call["question"] == "香蕉多少钱"
        assert call["options"].max_steps >= 1
        assert call["options"].allow_sql is True
        assert call["session_id"]

    def test_options_forwarded(self, tmp_path):
        client, app = build_client(tmp_path)
        with client:
            response = chat(client, max_steps=3, allow_sql=False, top_k=2, use_rerank=False)
        assert response.status_code == 200
        options = app.state.agent_runtime.calls[0]["options"]
        assert options.max_steps == 3
        assert options.allow_sql is False
        assert options.top_k == 2
        assert options.use_rerank is False

    def test_turn_saved_to_session(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            session_id = chat(client).json()["session_id"]
            detail = client.get(f"/api/v1/sessions/{session_id}").json()

        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][1]["content"] == "香蕉 3.5 元[1]"
        assert len(detail["messages"][1]["citations"]) == 1

    def test_history_forwarded_on_next_turn(self, tmp_path):
        client, app = build_client(tmp_path)
        with client:
            session_id = chat(client, "最便宜的香蕉多少钱").json()["session_id"]
            chat(client, "那应该怎么保存", session_id=session_id)

        second = app.state.agent_runtime.calls[1]
        assert second["history"] == [
            ("user", "最便宜的香蕉多少钱"),
            ("assistant", "香蕉 3.5 元[1]"),
        ]
        assert second["session_id"] == session_id

    def test_request_id_forwarded_and_echoed(self, tmp_path):
        client, app = build_client(tmp_path)
        with client:
            response = client.post(
                "/api/v1/chat/agent",
                json={"question": "香蕉多少钱"},
                headers={"X-Request-ID": "req-agent-1"},
            )
        assert response.headers["X-Request-ID"] == "req-agent-1"
        assert response.json()["request_id"] == "req-agent-1"
        assert app.state.agent_runtime.calls[0]["request_id"] == "req-agent-1"

    def test_explicit_session_id_used(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            session_id = client.post("/api/v1/sessions", json={"title": "采购"}).json()["session_id"]
            body = chat(client, session_id=session_id).json()
        assert body["session_id"] == session_id


class TestAgentChatValidation:
    def test_max_steps_out_of_range(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            assert chat(client, max_steps=0).status_code == 422
            assert chat(client, max_steps=9).status_code == 422

    def test_unknown_field_rejected(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            response = client.post("/api/v1/chat/agent", json={"question": "x", "no_such": 1})
        assert response.status_code == 422

    def test_blank_question_rejected(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            assert chat(client, "   ").status_code == 422

    def test_unknown_session(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            response = chat(client, session_id="sess_" + "0" * 32)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "session_not_found"
            # 格式不合法的 session_id 属于请求错误，按 422 返回
            assert chat(client, session_id="sess_short").status_code == 422

    def test_agent_assembly_failure_only_disables_agent(self, tmp_path, monkeypatch):
        # 有引擎时服务会自己装配 agentic 运行时；装配失败只让 /chat/agent 降级为 503
        def boom(*args, **kwargs):
            raise RuntimeError("sources 配置坏了")

        monkeypatch.setattr(app_module, "build_agent_runtime", boom)
        app = create_app(
            engine=FakeEngine(),
            session_store=SessionStore(),
            agent_sources_path=tmp_path / "no-sources.yaml",
        )
        client = TestClient(app)
        with client:
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
            assert app.state.agent_runtime is None
            response = chat(client)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "agent_unavailable"

    def test_knowledge_base_not_ready(self, tmp_path):
        client, _ = build_client(tmp_path, ready=False)
        with client:
            response = chat(client)
        assert response.status_code == 503
        body = response.json()["error"]
        assert body["code"] == "knowledge_base_not_ready"
        assert body["details"]["reasons"] == ["知识库为空，请先运行 ingest"]


class TestTraceEndpoints:
    def seed(self, tmp_path, *trace_ids):
        path = tmp_path / "traces" / "agent.jsonl"
        for index, trace_id in enumerate(trace_ids):
            trace = AgentTrace(
                trace_id=trace_id,
                question=f"问题{index}",
                model="fake-model",
                created_at=1_700_000_000.0 + index,
                intent="knowledge",
                stop_reason="final",
                tool_calls=1,
                elapsed_ms=10 + index,
            )
            trace.add(AgentStep(index=1, kind="route", tool="kb_search", reason="文档类问题"))
            trace.add(AgentStep(index=2, kind="final", reason="够了", observation=f"答案{index}"))
            trace.answer = f"答案{index}"
            trace.save(path)
        return path

    def test_service_info_advertises_endpoints(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            info = client.get("/").json()
        assert info["agent_chat"] == "/api/v1/chat/agent"
        assert info["traces"] == "/api/v1/traces"

    def test_openapi_documents_agent_paths(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/chat/agent" in paths
        assert "/api/v1/traces" in paths
        assert "/api/v1/traces/{trace_id}" in paths

    def test_list_empty_when_no_trace_file(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            body = client.get("/api/v1/traces").json()
        assert body["total"] == 0
        assert body["traces"] == []

    def test_list_newest_first(self, tmp_path):
        self.seed(tmp_path, "aaaa11", "bbbb22", "cccc33")
        client, _ = build_client(tmp_path)
        with client:
            body = client.get("/api/v1/traces").json()
        assert body["total"] == 3
        assert [t["trace_id"] for t in body["traces"]] == ["cccc33", "bbbb22", "aaaa11"]
        assert body["traces"][0]["question"] == "问题2"
        assert body["traces"][0]["intent"] == "knowledge"

    def test_list_limit_applied(self, tmp_path):
        self.seed(tmp_path, "aaaa11", "bbbb22", "cccc33")
        client, _ = build_client(tmp_path)
        with client:
            body = client.get("/api/v1/traces", params={"limit": 2}).json()
        assert [t["trace_id"] for t in body["traces"]] == ["cccc33", "bbbb22"]

    def test_list_limit_validated(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            assert client.get("/api/v1/traces", params={"limit": 0}).status_code == 422
            assert client.get("/api/v1/traces", params={"limit": 51}).status_code == 422

    def test_replay_returns_record_and_readable_text(self, tmp_path):
        self.seed(tmp_path, "aaaa11")
        client, _ = build_client(tmp_path)
        with client:
            response = client.get("/api/v1/traces/aaaa11")
        assert response.status_code == 200
        body = response.json()
        assert body["trace"]["trace_id"] == "aaaa11"
        assert body["trace"]["steps"][0]["tool"] == "kb_search"
        assert "决策过程：" in body["formatted"]
        assert "回答: 答案0" in body["formatted"]

    def test_replay_unknown_trace_id(self, tmp_path):
        self.seed(tmp_path, "aaaa11")
        client, _ = build_client(tmp_path)
        with client:
            response = client.get("/api/v1/traces/zzzz99")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "trace_not_found"

    def test_replay_rejects_malformed_trace_id(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            response = client.get("/api/v1/traces/bad-id!")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_trace_id"

    def test_replay_after_agent_chat(self, tmp_path):
        client, _ = build_client(tmp_path)
        with client:
            trace_id = chat(client).json()["meta"]["trace_id"]
            # 假运行时不写轨迹文件，这里模拟真实运行时落盘后再回放
            assert client.get(f"/api/v1/traces/{trace_id}").status_code == 404
            trace = AgentTrace(
                trace_id=trace_id,
                question="最便宜的香蕉多少钱",
                model="fake-model",
                created_at=1.0,
                intent="database",
                stop_reason="final",
                tool_calls=1,
            )
            trace.add(AgentStep(index=1, kind="route", tool="sql_query", reason="要数值"))
            trace.save(tmp_path / "traces" / "agent.jsonl")
            response = client.get(f"/api/v1/traces/{trace_id}")
        assert response.status_code == 200
        assert response.json()["trace"]["intent"] == "database"
