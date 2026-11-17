"""agentic 循环测试：意图路由、工具选择、步数与字符预算、失败降级。

全部用脚本化的决策器与替身工具，不依赖本机 Ollama。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional, Sequence

import pytest

import private_rag.agent.runtime as runtime_module
from private_rag.agent.planner import Planner, RouteDecision, StepDecision
from private_rag.agent.runtime import AgentOptions, AgentRuntime, build_agent_runtime
from private_rag.agent.tools import (
    SqlQueryTool,
    Tool,
    ToolBlock,
    ToolRegistry,
    ToolResult,
)
from private_rag.agent.trace import load_traces
from private_rag.generation import REFUSAL
from private_rag.models import Citation


# 记录最终回答生成被调用的次数与证据。用模块级列表而不是 fixture 的返回值，
# 本项目的 pytest 不会把 autouse fixture 的返回值注入到测试参数里。
final_calls: list[str] = []


@pytest.fixture(autouse=True)
def stub_final_answer(monkeypatch):
    """把最终回答生成换成替身，避免测试打到本机模型。"""
    final_calls.clear()

    def fake_final_answer(question, evidence, *, model=None, history=None):
        final_calls.append(evidence)
        return "基于证据的答案[1]"

    monkeypatch.setattr(runtime_module, "generate_final_answer", fake_final_answer)


class RecordingTool(Tool):
    def __init__(
        self,
        name: str = "kb_search",
        *,
        blocks: Optional[Sequence[ToolBlock]] = None,
        summary: str = "拿到 1 段材料",
        error: str = "",
        raises: Optional[Exception] = None,
        data: Optional[dict[str, Any]] = None,
    ):
        self.name = name
        self.description = f"{name} 的说明"
        self.calls: list[dict[str, Any]] = []
        self._blocks = list(
            blocks
            if blocks is not None
            else [ToolBlock(f"{name} 来源", "材料正文", Citation(1, f"{name} 来源", "file", "u", "s"))]
        )
        self._summary = summary
        self._error = error
        self._raises = raises
        self._data = data or {}

    def run(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(dict(args))
        if self._raises:
            raise self._raises
        if self._error:
            return ToolResult(False, error=self._error, data=dict(self._data))
        return ToolResult(True, summary=self._summary, blocks=list(self._blocks), data=dict(self._data))


class ScriptedPlanner(Planner):
    def __init__(
        self,
        route: Optional[RouteDecision] = None,
        decisions: Optional[Sequence[StepDecision]] = None,
        route_error: Optional[Exception] = None,
    ):
        self.route_decision = route or RouteDecision(
            intent="knowledge",
            action={"tool": "kb_search", "args": {"query": "增量入库"}},
            reason="文档类问题",
        )
        self.decisions = list(decisions or [])
        self.route_error = route_error
        self.specs_seen: list[str] = []
        self.decide_calls = 0

    def route(self, question, tool_specs, history=None) -> RouteDecision:
        self.specs_seen.append(tool_specs)
        if self.route_error:
            raise self.route_error
        return self.route_decision

    def decide(
        self,
        question,
        tool_specs,
        evidence,
        observations,
        history=None,
        *,
        steps_used: int = 1,
        max_steps: int = 4,
    ) -> StepDecision:
        self.specs_seen.append(tool_specs)
        self.decide_calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return StepDecision(action="final", reason="证据够了", answer="脚本答案")


def make_runtime(
    tmp_path,
    tools: Sequence[Tool],
    planner: Planner,
    **kwargs,
) -> AgentRuntime:
    return AgentRuntime(
        ToolRegistry(list(tools)),
        planner=planner,
        model="fake-model",
        trace_path=tmp_path / "traces" / "agent.jsonl",
        **kwargs,
    )


def tool_steps(result) -> list:
    return [step for step in result.steps if step.kind == "tool"]


class TestRouting:
    def test_database_intent_routes_to_sql_tool(self, tmp_path):
        sql = RecordingTool("sql_query", data={"sql": "SELECT name FROM products LIMIT 20"})
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="database",
                action={"tool": "sql_query", "args": {"sql": "SELECT name FROM products"}},
                reason="问的是价格",
            ),
            decisions=[StepDecision(action="final", reason="一行就够", answer="香蕉 3.5 元[1]")],
        )
        result = make_runtime(tmp_path, [RecordingTool(), sql], planner).run("最便宜的香蕉多少钱")

        assert result.intent == "database"
        assert sql.calls == [{"sql": "SELECT name FROM products"}]
        assert result.stop_reason == "final"
        assert result.answer == "香蕉 3.5 元[1]"
        assert final_calls == []  # 模型直接给了答案，不必再走一次生成
        assert result.steps[0].kind == "route"
        assert "实际执行 SELECT name FROM products LIMIT 20" in tool_steps(result)[0].observation

    def test_mixed_intent_uses_both_tools(self, tmp_path):
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="mixed",
                action={"tool": "kb_search", "args": {"query": "香蕉保存"}},
                reason="既要价格也要做法",
            ),
            decisions=[
                StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT 1 FROM products"}, reason="再查数值"),
                StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，冷藏保存[1][2]"),
            ],
        )
        result = make_runtime(tmp_path, [kb, sql], planner).run("最便宜的香蕉多少钱，怎么保存")

        assert kb.calls == [{"query": "香蕉保存"}]
        assert len(sql.calls) == 1
        assert result.tool_calls == 2
        assert result.intent == "mixed"
        # 两个工具的证据统一编号，回答里的 [n] 才能对上引用
        assert [c.index for c in result.citations] == [1, 2]
        assert result.evidence_blocks == 2

    def test_intent_none_skips_retrieval(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(
            route=RouteDecision(intent="none", action={}, reason="与知识库无关")
        )
        result = make_runtime(tmp_path, [kb], planner).run("今天天气怎么样")

        assert kb.calls == []
        assert result.tool_calls == 0
        assert result.stop_reason == "no_retrieval"
        assert result.answer == REFUSAL
        assert final_calls == []

    def test_planner_failure_falls_back_to_heuristic(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(route_error=RuntimeError("ollama down"))
        result = make_runtime(tmp_path, [kb], planner).run("增量入库怎么做")

        assert "决策模型不可用" in result.steps[0].observation
        assert len(kb.calls) == 1
        assert result.answer == "脚本答案"
        assert result.tool_calls == 1


class TestLoopLimits:
    def test_max_steps_caps_tool_calls(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(
            decisions=[StepDecision(action="tool", tool="kb_search", args={"query": "再查"})] * 10
        )
        agent = make_runtime(tmp_path, [kb], planner)
        result = agent.run("继续查", options=AgentOptions(max_steps=2))

        assert len(kb.calls) == 2
        assert result.stop_reason == "max_steps"
        assert planner.decide_calls == 1  # 第二次进循环时已达上限，直接停
        assert result.answer == "基于证据的答案[1]"

    def test_unknown_tool_name_does_not_spin(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(
            decisions=[StepDecision(action="tool", tool="no_such_tool", reason="模型幻觉")] * 10
        )
        result = make_runtime(tmp_path, [kb], planner).run("问题", options=AgentOptions(max_steps=2))

        errors = [step for step in result.steps if step.kind == "error"]
        assert len(errors) >= 1
        assert "不在可用列表" in errors[0].error
        assert len(kb.calls) == 1  # 只有路由那一次真执行了工具
        assert planner.decide_calls <= 5  # guard 兜住空转
        assert result.stop_reason == "max_steps"

    def test_context_budget_stops_collecting(self, tmp_path):
        big = ToolBlock("来源", "字" * 4000, Citation(1, "来源", "file", "u", "s"))
        kb = RecordingTool(blocks=[big])
        planner = ScriptedPlanner(
            decisions=[StepDecision(action="tool", tool="kb_search", args={"query": "再来一段"})] * 5
        )
        agent = make_runtime(tmp_path, [kb], planner, max_context_chars=1000)
        result = agent.run("问题")

        assert len(kb.calls) == 1
        assert planner.decide_calls == 0  # 预算已满，不再问模型
        assert result.stop_reason == "context_budget"
        assert result.context_chars >= 1000
        assert [s.kind for s in result.steps].count("stop") == 1


class TestFailureHandling:
    def test_tool_exception_becomes_error_step(self, tmp_path):
        kb = RecordingTool(raises=RuntimeError("boom"))
        planner = ScriptedPlanner()
        result = make_runtime(tmp_path, [kb], planner).run("问题")

        step = tool_steps(result)[0]
        assert step.ok is False
        assert step.error == "工具执行异常：RuntimeError"
        assert result.tool_calls == 1
        assert result.answer == "脚本答案"

    def test_failed_search_observation_reaches_planner(self, tmp_path):
        kb = RecordingTool(error="检索失败：RuntimeError")
        planner = ScriptedPlanner()
        result = make_runtime(tmp_path, [kb], planner).run("问题")

        assert "检索失败" in tool_steps(result)[0].observation
        assert result.answer == "脚本答案"

    def test_no_evidence_refuses(self, tmp_path):
        kb = RecordingTool(blocks=[])
        planner = ScriptedPlanner(
            decisions=[StepDecision(action="final", reason="查不到东西", answer="")]
        )
        result = make_runtime(tmp_path, [kb], planner).run("问题")

        # final 没给答案且没有引用时，按既有约定返回拒答文本
        assert result.answer == REFUSAL
        assert final_calls == []

    def test_generation_failure_degrades_to_refusal(self, tmp_path, monkeypatch):
        def boom(question, evidence, *, model=None, history=None):
            raise RuntimeError("model gone")

        monkeypatch.setattr(runtime_module, "generate_final_answer", boom)
        # final 不给现成答案，才会走到「基于已收集证据再生成一次」这一步
        planner = ScriptedPlanner(decisions=[StepDecision(action="final", reason="齐了", answer="")])
        result = make_runtime(tmp_path, [RecordingTool()], planner).run("问题")

        assert result.answer == REFUSAL
        assert result.stop_reason == "generation_failed"

    def test_sql_guard_rejection_surfaces_as_observation(self, tmp_path):
        db = tmp_path / "demo.db"
        connection = sqlite3.connect(db)
        try:
            connection.execute("CREATE TABLE products (name TEXT, price REAL)")
            connection.execute("INSERT INTO products VALUES ('banana', 3.5)")
            connection.commit()
        finally:
            connection.close()

        tool = SqlQueryTool(dsn=f"sqlite:///{db}", label="demo", allowed_tables=["products"])
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="database",
                action={"tool": "sql_query", "args": {"sql": "DROP TABLE products"}},
                reason="想删表",
            ),
            decisions=[StepDecision(action="final", reason="被拦下了", answer="无法给出该结果")],
        )
        result = make_runtime(tmp_path, [tool], planner).run("删掉商品表")

        step = tool_steps(result)[0]
        assert step.ok is False
        assert "只允许 SELECT" in step.error
        assert result.citations == []
        assert result.answer == "无法给出该结果"

    def test_real_sql_result_becomes_cited_evidence(self, tmp_path):
        db = tmp_path / "demo.db"
        connection = sqlite3.connect(db)
        try:
            connection.execute("CREATE TABLE products (name TEXT, price REAL)")
            connection.execute("INSERT INTO products VALUES ('banana', 3.5)")
            connection.commit()
        finally:
            connection.close()

        tool = SqlQueryTool(dsn=f"sqlite:///{db}", label="demo", allowed_tables=["products"])
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="database",
                action={"tool": "sql_query", "args": {"sql": "SELECT name, price FROM products"}},
                reason="查价格",
            )
        )
        result = make_runtime(tmp_path, [tool], planner).run("香蕉多少钱")

        assert result.tool_calls == 1
        assert [c.source_type for c in result.citations] == ["sql"]
        assert result.citations[0].table == "demo"
        assert result.answer == "脚本答案"
        assert "banana" in result.trace.citations[0]["snippet"]


class TestToolAvailability:
    def test_allow_sql_false_hides_sql_tool(self, tmp_path):
        kb = RecordingTool()
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="database",
                action={"tool": "sql_query", "args": {"sql": "SELECT 1 FROM products"}},
                reason="模型坚持查库",
            )
        )
        result = make_runtime(tmp_path, [kb, sql], planner).run(
            "香蕉多少钱", options=AgentOptions(allow_sql=False)
        )

        assert all("sql_query" not in specs for specs in planner.specs_seen)
        assert sql.calls == []
        assert len(kb.calls) == 1  # 退回到唯一可用的知识库检索
        assert result.tool_calls == 1

    def test_registry_without_tools_still_answers(self, tmp_path):
        agent = AgentRuntime(
            ToolRegistry(),
            planner=ScriptedPlanner(),
            trace_path=tmp_path / "agent.jsonl",
        )
        result = agent.run("问题")

        assert result.tool_calls == 0
        assert result.answer == REFUSAL
        assert "没有可用工具" in result.steps[0].observation or result.steps[0].kind == "route"


class TestTraceWiring:
    def test_trace_saved_and_replayable(self, tmp_path):
        path = tmp_path / "traces" / "agent.jsonl"
        agent = AgentRuntime(
            ToolRegistry([RecordingTool()]),
            planner=ScriptedPlanner(),
            model="fake-model",
            trace_path=path,
        )
        result = agent.run("增量入库怎么做")

        records = load_traces(path, trace_id=result.trace_id)
        assert len(records) == 1
        record = records[0]
        assert record["question"] == "增量入库怎么做"
        assert record["model"] == "fake-model"
        assert [step["kind"] for step in record["steps"]][-1] == "final"
        assert record["citations"][0]["title"] == "kb_search 来源"

    def test_trace_disabled_writes_nothing(self, tmp_path):
        path = tmp_path / "traces" / "agent.jsonl"
        agent = AgentRuntime(
            ToolRegistry([RecordingTool()]),
            planner=ScriptedPlanner(),
            trace_path=path,
            trace_enabled=False,
        )
        agent.run("问题")
        assert not path.exists()

    def test_options_forward_retrieval_settings(self):
        options = AgentOptions(
            top_k=2,
            source_type="file",
            tags=["运维"],
            use_lexical=False,
            query_rewrite="hyde",
        )
        retrieval = options.to_retrieval_options()
        assert retrieval.top_k == 2
        assert retrieval.source_type == "file"
        assert retrieval.tags == ["运维"]
        assert retrieval.use_lexical is False
        assert retrieval.query_rewrite == "hyde"


def make_db(tmp_path):
    db = tmp_path / "demo.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("CREATE TABLE products (id INTEGER, name TEXT)")
        connection.execute("CREATE TABLE orders (id INTEGER, product_id INTEGER)")
        connection.commit()
    finally:
        connection.close()
    return db


def sources_yaml(db, *, opt_in: str = "") -> str:
    return (
        "sources:\n"
        "  - type: sql\n"
        f"    dsn: sqlite:///{db}\n"
        "    query: SELECT id, name FROM products ORDER BY id\n"
        "    id_column: id\n"
        "    title_column: name\n"
        "    content_columns: [name]\n"
        "    label: demo.products\n"
        "    tags: [business]\n"
        f"{opt_in}"
    )


class TestRuntimeAssembly:
    """运行时 SQL 只有在 sources 配置里显式授权时才挂上工具。"""

    def agent(self, tmp_path, body: str, **kwargs):
        path = tmp_path / "sources.yaml"
        path.write_text(body, encoding="utf-8")
        return build_agent_runtime(object(), sources_path=path, **kwargs)

    def test_no_optin_means_kb_only(self, tmp_path):
        agent = self.agent(tmp_path, sources_yaml(make_db(tmp_path)))
        assert agent.registry.names() == ["kb_search"]

    def test_optin_derives_tables_from_query(self, tmp_path):
        agent = self.agent(
            tmp_path, sources_yaml(make_db(tmp_path), opt_in="    allow_runtime_query: true\n")
        )
        assert agent.registry.names() == ["kb_search", "sql_query"]
        tool = agent.registry.get("sql_query")
        assert tool.allowed_tables == ["products"]  # 从 query 的 FROM 推导
        assert tool.label == "demo.products"
        # 把真实列名写进工具描述，模型才写得出能跑通的 SQL
        assert "products(id, name)" in tool.description

    def test_runtime_tables_take_precedence(self, tmp_path):
        agent = self.agent(
            tmp_path,
            sources_yaml(
                make_db(tmp_path),
                opt_in="    allow_runtime_query: true\n    runtime_tables: [products, orders]\n",
            ),
        )
        assert agent.registry.get("sql_query").allowed_tables == ["products", "orders"]

    def test_allow_sql_argument_skips_assembly(self, tmp_path):
        agent = self.agent(
            tmp_path,
            sources_yaml(make_db(tmp_path), opt_in="    allow_runtime_query: true\n"),
            allow_sql=False,
        )
        assert agent.registry.names() == ["kb_search"]

    def test_missing_config_degrades_to_kb_only(self, tmp_path):
        agent = build_agent_runtime(
            object(), sources_path=tmp_path / "不存在.yaml"
        )
        assert agent.registry.names() == ["kb_search"]

    def test_invalid_config_degrades_to_kb_only(self, tmp_path):
        agent = self.agent(tmp_path, "sources:\n  - type: sql\n    dsn: sqlite:///x.db\n")
        assert agent.registry.names() == ["kb_search"]

    def test_empty_sources_degrades_to_kb_only(self, tmp_path):
        agent = self.agent(tmp_path, "sources: []\n")
        assert agent.registry.names() == ["kb_search"]

    def test_planner_and_model_defaults_passed_through(self, tmp_path):
        agent = build_agent_runtime(object(), sources_path=tmp_path / "none.yaml", model="自定义模型")
        assert agent.model == "自定义模型"
        assert agent.planner is None  # 留空表示运行时按 LlmPlanner 走
