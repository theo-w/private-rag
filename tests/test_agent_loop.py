"""agentic 循环测试：意图路由、工具选择、步数与字符预算、失败降级。

全部用脚本化的决策器与替身工具，不依赖本机 Ollama。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional, Sequence

import pytest

import private_rag.agent.runtime as runtime_module
from private_rag.agent.planner import Planner, RouteDecision, StepDecision
from private_rag.agent.runtime import (
    AgentOptions,
    AgentRuntime,
    _catalog_entities,
    _data_catalog,
    build_agent_runtime,
    build_tool_registry,
)
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
# 每次生成收到的「已判定缺失项」，用于断言充分性判断真的传到了生成侧
final_missing: list[str] = []


@pytest.fixture(autouse=True)
def stub_final_answer(monkeypatch):
    """把最终回答生成换成替身，避免测试打到本机模型。"""
    final_calls.clear()
    final_missing.clear()

    def fake_final_answer(question, evidence, *, model=None, history=None, missing=""):
        final_calls.append(evidence)
        final_missing.append(missing)
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

    def test_intent_none_probes_kb_before_refusing(self, tmp_path):
        """none 不是终局判定：先做一次知识库检索，拒答必须建立在证据上。"""
        kb = RecordingTool(blocks=[], summary="没有匹配材料")
        planner = ScriptedPlanner(
            route=RouteDecision(intent="none", action={}, reason="与知识库无关"),
            decisions=[StepDecision(action="final", reason="查过了，材料里没有", answer="")],
        )
        result = make_runtime(tmp_path, [kb], planner).run("本月采购成本是多少")

        assert kb.calls == [{"query": "本月采购成本是多少"}]
        assert result.tool_calls == 1
        # 拒答理由从「根本没检索」变成「查过了没有证据」
        assert result.stop_reason == "no_evidence"
        assert result.answer == REFUSAL
        assert result.intent == "none"
        assert final_calls == []

    def test_none_probe_that_finds_evidence_answers_as_knowledge(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(
            route=RouteDecision(intent="none", action={}, reason="误判成与两侧都无关"),
            decisions=[StepDecision(action="final", reason="证据够了", answer="脚本答案")],
        )
        result = make_runtime(tmp_path, [kb], planner).run("这个项目有 iOS 客户端吗")

        assert len(kb.calls) == 1
        # 查到了东西就按实际作答依据报意图，none 只是路由那一刻的判断
        assert result.intent == "knowledge"
        assert result.stop_reason == "final"
        assert result.answer == "脚本答案"

    def test_stream_probes_kb_before_refusing_too(self, tmp_path):
        kb = RecordingTool(blocks=[], summary="没有匹配材料")
        planner = ScriptedPlanner(
            route=RouteDecision(intent="none", action={}, reason="与知识库无关"),
            decisions=[StepDecision(action="final", reason="查过了，材料里没有", answer="")],
        )
        events = list(make_runtime(tmp_path, [kb], planner).run_stream("本月采购成本是多少"))
        done = [event for event in events if event["event"] == "done"][-1]

        assert kb.calls == [{"query": "本月采购成本是多少"}]
        assert done["data"]["meta"]["stop_reason"] == "no_evidence"
        assert done["data"]["answer"].startswith(REFUSAL)
        assert [event for event in events if event["event"] == "token"] == []

    def test_none_probe_can_be_turned_off(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(route=RouteDecision(intent="none", action={}, reason="与知识库无关"))
        result = make_runtime(tmp_path, [kb], planner, none_probe=False).run("今天天气怎么样")

        assert kb.calls == []
        assert result.tool_calls == 0
        assert result.stop_reason == "no_retrieval"
        assert result.answer == REFUSAL

    def test_none_probe_needs_the_kb_tool(self, tmp_path):
        """只有 sql_query 时探测做不了（写不出可靠的问题查询），仍按直接拒答处理。"""
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(route=RouteDecision(intent="none", action={}, reason="外部事实"))
        result = make_runtime(tmp_path, [sql], planner).run("明天会下雨吗")

        assert sql.calls == []
        assert result.stop_reason == "no_retrieval"
        assert result.answer == REFUSAL

    def test_planner_failure_falls_back_to_heuristic(self, tmp_path):
        kb = RecordingTool()
        planner = ScriptedPlanner(route_error=RuntimeError("ollama down"))
        result = make_runtime(tmp_path, [kb], planner).run("增量入库怎么做")

        # 回退可接受，但底层原因必须留在轨迹里，否则事后查不出是超时还是 JSON 截断
        assert "决策模型不可用" in result.steps[0].observation
        assert "RuntimeError" in result.steps[0].observation
        assert "ollama down" in result.steps[0].observation
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

    # 配置拿不到时不再静默降级：日志要写清缺哪个文件、后果是什么、怎么修

    @staticmethod
    def runtime_records(caplog) -> list:
        """只看 runtime 自己打的日志，不被第三方库的输出干扰。"""
        return [r for r in caplog.records if r.name == "private_rag.agent.runtime"]

    def test_missing_default_config_warns_with_path_and_fix(self, tmp_path, monkeypatch, caplog):
        # 新克隆/新 worktree 的真实情形：没有 config/sources.yaml，app.py 传的路径就等于默认值
        missing = tmp_path / "sources.yaml"
        monkeypatch.setattr(runtime_module, "DEFAULT_SOURCES_CONFIG", missing)
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = build_agent_runtime(object(), sources_path=missing)
        assert agent.registry.names() == ["kb_search"]  # 行为不变，只是不再静默
        records = self.runtime_records(caplog)
        text = "\n".join(r.getMessage() for r in records)
        assert str(missing) in text  # 找不到的那个路径
        assert "sql_query 未注册" in text
        assert (
            "RAG_AGENT_SOURCES_CONFIG=config/sources.demo.yaml python3 scripts/serve.py" in text
        )
        # 只用文档知识库本来就不需要这份配置，所以是提示不是错误
        assert [r.levelno for r in records] == [logging.WARNING]

    def test_missing_explicit_config_is_logged_as_error(self, tmp_path, caplog):
        typo = tmp_path / "sources.yml"  # 路径是自己指的，指错了就是配错
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = build_agent_runtime(object(), sources_path=typo)
        assert agent.registry.names() == ["kb_search"]
        records = self.runtime_records(caplog)
        text = "\n".join(r.getMessage() for r in records)
        assert "指定的 sources 配置不存在" in text
        assert str(typo) in text
        assert max(r.levelno for r in records) == logging.ERROR

    def test_allow_sql_false_stays_silent(self, tmp_path, caplog):
        # 调用方明确不要 SQL，就别再拿配置缺失去吵
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = build_agent_runtime(
                object(), sources_path=tmp_path / "不存在.yaml", allow_sql=False
            )
        assert agent.registry.names() == ["kb_search"]
        assert self.runtime_records(caplog) == []

    def test_loaded_config_without_optin_explains_kb_only(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = self.agent(tmp_path, sources_yaml(make_db(tmp_path)))
        assert agent.registry.names() == ["kb_search"]
        assert "allow_runtime_query" in "\n".join(
            r.getMessage() for r in self.runtime_records(caplog)
        )

    def test_valid_optin_config_logs_no_missing_hint(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = self.agent(
                tmp_path,
                sources_yaml(make_db(tmp_path), opt_in="    allow_runtime_query: true\n"),
            )
        assert agent.registry.names() == ["kb_search", "sql_query"]  # 带配置时行为不变
        assert "未注册" not in "\n".join(r.getMessage() for r in self.runtime_records(caplog))

    def test_broken_config_names_path_and_consequence(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = self.agent(tmp_path, "sources:\n  - type: sql\n    dsn: sqlite:///x.db\n")
        assert agent.registry.names() == ["kb_search"]
        text = "\n".join(r.getMessage() for r in self.runtime_records(caplog))
        assert str(tmp_path / "sources.yaml") in text
        assert "sql_query 未注册" in text

    # 连不上库跟「配置没配好」是两回事，日志要分开说

    def test_unreachable_demo_db_points_at_init_script(self, tmp_path, caplog):
        # 新 worktree 的真实情形：data/sql/demo.db 被 gitignore 掉了，装配必然连不上
        missing = tmp_path / "sql" / "demo.db"
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            with pytest.raises(Exception):  # 语义不变：装配失败仍由调用方兜成 503
                self.agent(
                    tmp_path,
                    sources_yaml(missing, opt_in="    allow_runtime_query: true\n"),
                )
        text = "\n".join(r.getMessage() for r in self.runtime_records(caplog))
        assert "连不上 SQL 源" in text
        assert str(missing) in text
        assert "init_demo_db.py" in text

    def test_unreachable_db_that_exists_gives_generic_hint(self, tmp_path, caplog):
        # 路径存在但打不开（这里是个目录），只能给通用检查项，不该乱猜演示库
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            with pytest.raises(Exception):
                self.agent(
                    tmp_path, sources_yaml(tmp_path, opt_in="    allow_runtime_query: true\n")
                )
        text = "\n".join(r.getMessage() for r in self.runtime_records(caplog))
        assert "连不上 SQL 源" in text
        assert "demo.products" in text  # 说清是哪个源
        assert "init_demo_db" not in text

    def test_missing_sqlite_file_says_empty_db_will_be_created(self, tmp_path, caplog):
        # 目录在、文件不在：sqlite 会建出 0 字节空库，装配看着是成功的
        (tmp_path / "sql").mkdir()
        missing = tmp_path / "sql" / "demo.db"
        with caplog.at_level(logging.INFO, logger="private_rag.agent.runtime"):
            agent = self.agent(
                tmp_path,
                sources_yaml(missing, opt_in="    allow_runtime_query: true\n"),
            )
        assert agent.registry.names() == ["kb_search", "sql_query"]  # 语义不变，只是不再静默
        text = "\n".join(r.getMessage() for r in self.runtime_records(caplog))
        assert str(missing) in text
        assert "no such table" in text
        assert "init_demo_db.py" in text


class TestEvidenceSufficiency:
    """拒答策略：证据撑不起问题要的结论时，宁可不答。"""

    def _runtime(self, tmp_path, decision: StepDecision):
        planner = ScriptedPlanner(decisions=[decision])
        return make_runtime(tmp_path, [RecordingTool("kb_search")], planner), planner

    def test_insufficient_decision_refuses_without_generating(self, tmp_path):
        runtime, _ = self._runtime(
            tmp_path,
            StepDecision(
                action="final",
                sufficient=False,
                missing="products 表没有产地与联系方式字段",
                answer="根据现有数据，无法提供橙子的产地",
                reason="查过了，库里没这一列",
            ),
        )
        result = runtime.run("橙子的产地和供应商联系方式是什么？")

        assert result.answer.startswith(REFUSAL)
        # 缺什么都要写出来，否则用户不知道下一步该补什么
        assert "products 表没有产地与联系方式字段" in result.answer
        assert result.stop_reason == "insufficient_evidence"
        # 关键是没再打最终生成：模型拿不到「补一段」的机会
        assert final_calls == []

    def test_declared_gap_is_passed_into_final_generation(self, tmp_path):
        runtime, _ = self._runtime(
            tmp_path,
            StepDecision(action="final", sufficient=True, missing="保存方式", reason="价格有了"),
        )
        result = runtime.run("橙子多少钱，该怎么保存？")

        assert result.stop_reason == "final"
        assert final_missing == ["保存方式"]

    def test_no_gap_means_plain_final_prompt(self, tmp_path):
        # answer 留空才会走最终生成；此时没有缺口，传过去的 missing 就是空串
        runtime, _ = self._runtime(tmp_path, StepDecision(action="final", answer=""))

        assert runtime.run("问题").stop_reason == "final"
        assert final_missing == [""]

    def test_loop_without_final_decision_keeps_generating(self, tmp_path):
        # 步数耗尽时不该被充分性判断牵连，仍按原口径基于已有证据作答
        sql = RecordingTool("sql_query", data={"sql": "SELECT 1"})
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="database", action={"tool": "sql_query", "args": {"sql": "SELECT 1"}}, reason="要数值"
            ),
            decisions=[
                StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT 1"}, reason="再查"),
                StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT 1"}, reason="还查"),
            ],
        )
        runtime = make_runtime(tmp_path, [sql], planner, max_steps=1) if False else make_runtime(
            tmp_path, [sql], planner
        )
        result = runtime.run("水果价格", options=AgentOptions(max_steps=1))

        assert result.stop_reason == "max_steps"
        assert len(final_calls) == 1

    def test_stream_uses_the_same_refusal_rule(self, tmp_path):
        planner = ScriptedPlanner(
            decisions=[StepDecision(action="final", sufficient=False, missing="没有供应商表", reason="查不到")]
        )
        runtime = make_runtime(tmp_path, [RecordingTool("kb_search")], planner)

        events = list(runtime.run_stream("供应商电话是多少？"))
        done = [event for event in events if event["event"] == "done"][-1]

        assert done["data"]["answer"].startswith(REFUSAL)
        assert "没有供应商表" in done["data"]["answer"]
        assert done["data"]["meta"]["stop_reason"] == "insufficient_evidence"
        # 拒答不伪装成流式生成：没有 token 事件
        assert [event for event in events if event["event"] == "token"] == []


CATALOG_ROWS = [
    (1, "橙子", "水果", "橙子富含维生素 C，适合日常补充维 C；表皮较厚，常温可存放一周左右。", "常温阴凉", 12.5, "2026-01-10"),
    (2, "苹果", "水果", "苹果适合冷藏保存，可以延长保鲜时间；切开后容易氧化，建议尽快食用。", "冷藏 0-4℃", 8.0, "2026-01-12"),
    (3, "香蕉", "水果", "香蕉不适合放冰箱，低温会让表皮变黑、口感变差；建议常温阴凉处保存。", "常温阴凉", 6.5, "2026-01-15"),
    (4, "猕猴桃", "水果", "猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化。", "冷藏 0-4℃", 15.0, "2026-01-18"),
    (5, "蓝莓", "浆果", "蓝莓娇嫩易压，收到后应尽快食用，未洗的可以冷藏存放三四天。", "冷藏 0-4℃", 28.0, "2026-02-02"),
    (6, "牛油果", "热带果品", "牛油果硬的时候常温放两三天催熟，捏着发软就要当天吃。", "常温催熟后冷藏", 18.0, "2026-02-05"),
    (7, "核桃", "坚果", "核桃带壳干燥常温能放半年，剥壳后必须密封冷藏，受潮会变苦。", "密封干燥常温", 32.0, "2026-02-11"),
    (8, "腰果", "坚果", "腰果开封后务必密封并冷藏，敞放两周就会返软失去脆度。", "密封冷藏", 45.0, "2026-02-14"),
    (9, "桂圆干", "干货", "桂圆干未开封阴凉干燥处可放一年，开袋后尽快吃完，返潮容易发霉。", "阴凉干燥", 26.0, "2026-02-17"),
]


def make_catalog_db(tmp_path):
    """一张 9 行的商品表：实体名排在第 5~9 行，另有一列长说明、一列日期、一列数字。

    行序就是老目录看不到的那种情形：每列只取前 4 个值时，「腰果」永远进不了工具描述。
    """
    db = tmp_path / "catalog.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "CREATE TABLE products (id INTEGER, name TEXT, category TEXT, description TEXT, "
            "storage TEXT, price REAL, updated_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", CATALOG_ROWS
        )
        connection.commit()
    finally:
        connection.close()
    return f"sqlite:///{db}"


class TestCatalogEnumeration:
    """路由器只从工具描述里知道库里存着什么：实体名在后面行里时，老目录看不到它。"""

    def test_entities_beyond_the_first_rows_are_visible(self, tmp_path):
        catalog, terms = _data_catalog(make_catalog_db(tmp_path), ["products"])

        assert "腰果" in catalog
        assert "腰果" in terms

    def test_long_text_column_keeps_a_single_example(self, tmp_path):
        catalog, _ = _data_catalog(make_catalog_db(tmp_path), ["products"])

        assert catalog.count("存长文本") == 1
        assert "蓝莓娇嫩易压" not in catalog

    def test_number_and_date_columns_are_left_out(self, tmp_path):
        catalog, terms = _data_catalog(make_catalog_db(tmp_path), ["products"])

        assert "updated_at" not in catalog
        assert "2026-01-10" not in terms

    def test_entity_list_survives_a_tight_char_budget(self, tmp_path):
        """预算不够时先丢长文本例子，实体名单要保住。"""
        catalog, _ = _data_catalog(make_catalog_db(tmp_path), ["products"], max_chars=90)

        assert "腰果" in catalog
        assert "存长文本" not in catalog

    def test_disable_flag_restores_the_old_builder(self, tmp_path):
        """退回旧行为要连「每列只看前 4 个值」一起退，那才是当时误判的成因。"""
        catalog, terms = _data_catalog(make_catalog_db(tmp_path), ["products"], enumerate_values=False)

        assert "products 列(" in catalog
        assert "腰果" not in catalog
        assert "取值：橙子 / 苹果 / 香蕉 / 猕猴桃" in catalog
        assert "2026-01-10" in terms

    def test_samples_zero_still_skips_sampling(self, tmp_path):
        catalog, terms = _data_catalog(make_catalog_db(tmp_path), ["products"], max_samples=0)

        assert catalog == ""
        assert "name" in terms


class TestDataDrivenFallbackRoute:
    """模型不可用时的回退路由也要看得见库里有什么。"""

    def test_route_hints_come_from_the_sql_catalog(self, tmp_path):
        sql = RecordingTool("sql_query")
        sql.catalog_terms = ["香蕉", "description"]
        runtime = make_runtime(tmp_path, [RecordingTool("kb_search"), sql], ScriptedPlanner())

        assert runtime._route_hints(runtime.registry) == ["香蕉", "description"]

    def test_fallback_route_uses_catalog_entities(self, tmp_path):
        sql = RecordingTool("sql_query")
        sql.catalog_terms = ["香蕉"]
        planner = ScriptedPlanner(route_error=RuntimeError("模型不可用"))
        runtime = make_runtime(tmp_path, [RecordingTool("kb_search"), sql], planner)

        result = runtime.run("香蕉到底能不能放冰箱保存？")

        assert result.intent == "database"
        assert [step.tool for step in tool_steps(result)] == ["sql_query"]

    def test_doc_question_still_falls_back_to_kb(self, tmp_path):
        sql = RecordingTool("sql_query")
        sql.catalog_terms = ["香蕉"]
        planner = ScriptedPlanner(route_error=RuntimeError("模型不可用"))
        runtime = make_runtime(tmp_path, [RecordingTool("kb_search"), sql], planner)

        result = runtime.run("精排为什么始终用原问题？")

        assert result.intent == "knowledge"
        assert [step.tool for step in tool_steps(result)] == ["kb_search"]


class TestMixedGate:
    """修法 C：双侧查询门控——mixed 意图下任一侧零证据不许进 final。"""

    def test_gate_rejects_final_when_kb_side_empty(self, tmp_path):
        """kb_search 侧零证据时，门控强制调用 kb_search 而非接受 final。"""
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="mixed",
                action={"tool": "sql_query", "args": {"sql": "SELECT 1"}},
                reason="双侧取证",
            ),
            decisions=[
                # 模型选了 final，但 kb_search 侧没有证据
                StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，冷藏保存[1][2]"),
            ],
        )
        runtime = make_runtime(tmp_path, [kb, sql], planner)
        result = runtime.run("香蕉多少钱，怎么保存")

        # 门控拦截后强制查 kb_search，所以两个工具都调了
        assert len(sql.calls) == 1
        assert len(kb.calls) == 1
        assert result.tool_calls == 2
        # 门控步骤记录在轨迹里（kind=gate：循环没停，是被强制继续取证）
        gate_steps = [s for s in result.steps if s.kind == "gate"]
        assert len(gate_steps) == 1
        assert "门控拦截" in gate_steps[0].reason
        assert result.gate_intercepts == 1

    def test_gate_rejects_final_when_sql_side_empty(self, tmp_path):
        """sql_query 侧零证据时，门控强制调用 sql_query。"""
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="mixed",
                action={"tool": "kb_search", "args": {"query": "香蕉保存"}},
                reason="双侧取证",
            ),
            decisions=[
                # 模型选了 final，但 sql_query 侧没有证据
                StepDecision(action="final", reason="齐了", answer="冷藏保存[1]"),
            ],
        )
        runtime = make_runtime(tmp_path, [kb, sql], planner)
        result = runtime.run("香蕉多少钱，怎么保存")

        assert len(kb.calls) == 1
        assert len(sql.calls) == 1
        assert result.tool_calls == 2
        assert result.gate_intercepts == 1

    def test_gate_allows_final_when_both_sides_have_evidence(self, tmp_path):
        """两侧都有证据时，门控放行 final。"""
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="mixed",
                action={"tool": "kb_search", "args": {"query": "香蕉保存"}},
                reason="双侧取证",
            ),
            decisions=[
                StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT 1"}, reason="再查数值"),
                StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，冷藏保存[1][2]"),
            ],
        )
        runtime = make_runtime(tmp_path, [kb, sql], planner)
        result = runtime.run("香蕉多少钱，怎么保存")

        assert result.stop_reason == "final"
        assert result.tool_calls == 2
        # 没有门控拦截步骤
        gate_steps = [s for s in result.steps if s.kind == "gate"]
        assert len(gate_steps) == 0
        assert result.gate_intercepts == 0

    def test_gate_disabled_by_flag(self, tmp_path):
        """门控开关关掉时，即使一侧零证据也放行 final。"""
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="mixed",
                action={"tool": "sql_query", "args": {"sql": "SELECT 1"}},
                reason="双侧取证",
            ),
            decisions=[
                StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，冷藏保存[1][2]"),
            ],
        )
        runtime = make_runtime(tmp_path, [kb, sql], planner, mixed_gate=False)
        result = runtime.run("香蕉多少钱，怎么保存")

        # 门控关掉，final 直接通过，不强制查 kb_search
        assert len(sql.calls) == 1
        assert len(kb.calls) == 0
        assert result.tool_calls == 1
        assert result.stop_reason == "final"
        assert result.gate_intercepts == 0

    def test_gate_non_mixed_intent_ignored(self, tmp_path):
        """非 mixed 意图时，门控不生效。"""
        kb = RecordingTool("kb_search")
        planner = make_runtime(
            tmp_path,
            [kb],
            ScriptedPlanner(
                route=RouteDecision(
                    intent="knowledge",
                    action={"tool": "kb_search", "args": {"query": "问题"}},
                    reason="文档类",
                ),
                decisions=[StepDecision(action="final", reason="够了", answer="答案[1]")],
            ),
        )
        result = planner.run("问题")

        assert result.stop_reason == "final"
        assert result.tool_calls == 1

    def test_gate_in_stream(self, tmp_path):
        """run_stream 中门控行为一致。"""
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="mixed",
                action={"tool": "sql_query", "args": {"sql": "SELECT 1"}},
                reason="双侧取证",
            ),
            decisions=[
                StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，冷藏保存[1][2]"),
            ],
        )
        runtime = make_runtime(tmp_path, [kb, sql], planner)
        events = list(runtime.run_stream("香蕉多少钱，怎么保存"))

        # 门控拦截后强制查 kb_search
        step_events = [e for e in events if e["event"] == "step"]
        assert len(step_events) == 2  # sql_query + kb_search
        assert len(sql.calls) == 1
        assert len(kb.calls) == 1


class TestMixedGateSideTracking:
    """mixed 门控认「这一侧查过没有」的依据：真实证据块的标签里没有工具名，
    按标签文字认等于两侧永远零证据，每条 mixed 都会被反复逼到步数用尽。
    """

    # 生产链路里两个工具的块标签长这样：SQL 侧写表名，文档侧写引用标题
    SQL_BLOCKS = [
        ToolBlock(
            "SQL 查询（表 demo.products）",
            "name | price\n香蕉 | 3.5",
            Citation(2, "SQL 查询（表 demo.products）", "sql", "sqlite://", "demo.products"),
        )
    ]
    KB_BLOCKS = [
        ToolBlock("保存规范.md", "香蕉常温催熟后冷藏", Citation(1, "保存规范.md", "file", "u", "s"))
    ]

    def both_sides_ran(self, tmp_path, *, extra_final=False, tracking=None):
        """两侧都真的查过（块标签取生产链路的样子），模型随后选 final。"""
        kb = RecordingTool("kb_search", blocks=self.KB_BLOCKS)
        sql = RecordingTool("sql_query", blocks=self.SQL_BLOCKS)
        decisions = [
            StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT name FROM products"}, reason="查数值"),
            StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，常温催熟后冷藏[1][2]"),
        ]
        if extra_final:
            decisions.append(StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元[1][2]"))
        kwargs = {} if tracking is None else {"gate_side_tracking": tracking}
        runtime = make_runtime(
            tmp_path,
            [kb, sql],
            ScriptedPlanner(
                route=RouteDecision(
                    intent="mixed",
                    action={"tool": "kb_search", "args": {"query": "香蕉 保存"}},
                    reason="双侧取证",
                ),
                decisions=decisions,
            ),
            **kwargs,
        )
        return runtime.run("香蕉多少钱，怎么保存"), kb, sql

    def test_both_sides_ran_with_production_labels_passes(self, tmp_path):
        """两侧都查过，门控不该再逼第三次调用——改动前这里会连拦两次直到步数用尽。"""
        result, kb, sql = self.both_sides_ran(tmp_path)

        assert result.stop_reason == "final"
        assert result.tool_calls == 2
        assert len(kb.calls) == 1 and len(sql.calls) == 1
        assert result.gate_intercepts == 0
        assert [s.kind for s in result.steps].count("gate") == 0

    def test_label_matching_still_misfires_when_tracking_off(self, tmp_path):
        """退回老行为：同样两侧都查过，按标签文字认仍然判「一侧零证据」，
        每次 final 都被拦，一直逼到步数用尽——这就是旧报告里 mixed 样本 tools=4/gate=2 的来路。"""
        result, kb, sql = self.both_sides_ran(tmp_path, extra_final=True, tracking=False)

        assert result.gate_intercepts == 2
        assert len(kb.calls) == 3  # 每次都要 final 都被拦，一直逼到步数用尽
        assert result.stop_reason == "max_steps"

    def test_gate_forces_the_side_that_never_ran(self, tmp_path):
        """只查了文档侧时，门控逼一次 SQL 侧；那一侧跑完以后就放行 final。"""
        kb = RecordingTool("kb_search", blocks=self.KB_BLOCKS)
        sql = RecordingTool("sql_query", blocks=self.SQL_BLOCKS)
        runtime = make_runtime(
            tmp_path,
            [kb, sql],
            ScriptedPlanner(
                route=RouteDecision(
                    intent="mixed",
                    action={"tool": "kb_search", "args": {"query": "香蕉 保存"}},
                    reason="双侧取证",
                ),
                decisions=[
                    StepDecision(action="final", reason="够了", answer="常温催熟后冷藏[1]"),
                    StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元，常温催熟后冷藏[1][2]"),
                ],
            ),
        )
        result = runtime.run("香蕉多少钱，怎么保存")

        assert len(sql.calls) == 1 and len(kb.calls) == 1
        assert result.gate_intercepts == 1
        assert result.stop_reason == "final"

    def test_gate_forces_a_side_at_most_once(self, tmp_path):
        """SQL 侧每次都失败也只逼一次：问过了没问过了要分得开，反复逼只会把步数烧光。"""
        kb = RecordingTool("kb_search", blocks=self.KB_BLOCKS)
        sql = RecordingTool("sql_query", error="表不存在")
        runtime = make_runtime(
            tmp_path,
            [kb, sql],
            ScriptedPlanner(
                route=RouteDecision(
                    intent="mixed",
                    action={"tool": "kb_search", "args": {"query": "香蕉 保存"}},
                    reason="双侧取证",
                ),
                decisions=[
                    StepDecision(action="final", reason="够了", answer="常温催熟后冷藏[1]"),
                    StepDecision(action="final", reason="就这样", answer="常温催熟后冷藏[1]"),
                    StepDecision(action="final", reason="就这样", answer="常温催熟后冷藏[1]"),
                ],
            ),
        )
        result = runtime.run("香蕉多少钱，怎么保存")

        assert result.gate_intercepts == 1
        assert len(sql.calls) == 1
        assert result.stop_reason == "final"

    def test_gate_does_not_force_a_missing_tool(self, tmp_path):
        """没挂 sql_query 时不逼一个不存在的工具：白烧一步，且永远补不上。"""
        kb = RecordingTool("kb_search", blocks=self.KB_BLOCKS)
        runtime = make_runtime(
            tmp_path,
            [kb],
            ScriptedPlanner(
                route=RouteDecision(
                    intent="mixed",
                    action={"tool": "kb_search", "args": {"query": "香蕉 保存"}},
                    reason="双侧取证",
                ),
                decisions=[
                    StepDecision(action="final", reason="够了", answer="常温催熟后冷藏[1]"),
                ],
            ),
        )
        result = runtime.run("香蕉怎么保存")

        assert result.gate_intercepts == 0
        assert result.stop_reason == "final"
        assert result.tool_calls == 1

    def test_tracking_in_stream(self, tmp_path):
        """run_stream 里同样按执行记录认侧别。"""
        kb = RecordingTool("kb_search", blocks=self.KB_BLOCKS)
        sql = RecordingTool("sql_query", blocks=self.SQL_BLOCKS)
        runtime = make_runtime(
            tmp_path,
            [kb, sql],
            ScriptedPlanner(
                route=RouteDecision(
                    intent="mixed",
                    action={"tool": "kb_search", "args": {"query": "香蕉 保存"}},
                    reason="双侧取证",
                ),
                decisions=[
                    StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT 1"}, reason="查数值"),
                    StepDecision(action="final", reason="齐了", answer="香蕉 3.5 元[1][2]"),
                ],
            ),
        )
        events = list(runtime.run_stream("香蕉多少钱，怎么保存"))

        # 事件形如 {"event": ..., "data": ...}；门控拦截只写进轨迹，不发 step 事件
        step_events = [e for e in events if e["event"] == "step"]
        assert [e["data"]["tool"] for e in step_events] == ["kb_search", "sql_query"]
        meta = [e for e in events if e["event"] == "done"][0]["data"]["meta"]
        assert meta["gate_intercepts"] == 0
        assert meta["stop_reason"] == "final"


class TestKbMissSqlGate:
    """兜底门控：意图判成 knowledge、模型要说「证据不足」，而问题里的实体在库内取值里 → 拒答之前先查一次业务表。

    路由器只看得到工具描述，同一条提示词在温度 0 下仍会翻动，措辞修不干净；
    这条兜底只在「眼看要拒答」的路径上触发，不会把已经能答的问题改坏。
    """

    CATALOG = (
        "products.name 取值样例：橙子 / 酸奶；"
        "products.description 存长文本，例：酸奶全程冷藏，胀包就直接扔掉。"
    )

    def kb_planner(self, *decisions) -> ScriptedPlanner:
        return ScriptedPlanner(
            route=RouteDecision(
                intent="knowledge",
                action={"tool": "kb_search", "args": {"query": "酸奶胀包 能否饮用"}},
                reason="文档类问题",
            ),
            decisions=list(decisions),
        )

    def refusal(self) -> StepDecision:
        return StepDecision(
            action="final",
            sufficient=False,
            missing="证据中未包含酸奶胀包后是否可饮用的说明",
            answer="",
            reason="查过了，材料里没有",
        )

    def tools(self, tmp_path, catalog: str = CATALOG):
        kb = RecordingTool("kb_search")
        sql = RecordingTool("sql_query")
        sql.catalog = catalog
        return kb, sql

    def test_entities_come_from_the_sample_lines(self):
        assert _catalog_entities(self.CATALOG) == [
            ("橙子", "products", "name"),
            ("酸奶", "products", "name"),
        ]

    def test_queries_the_sql_tool_before_refusing(self, tmp_path):
        kb, sql = self.tools(tmp_path)
        runtime = make_runtime(tmp_path, [kb, sql], self.kb_planner(self.refusal()))
        result = runtime.run("酸奶胀包了还能喝吗？")

        # 强制的那次查询按实体过滤，不是整表扫：字符预算会被整表截掉靠后的记录
        assert sql.calls == [{"sql": "SELECT * FROM products WHERE name LIKE '%酸奶%' LIMIT 5"}]
        assert result.gate_intercepts == 1
        gate_steps = [s for s in result.steps if s.kind == "gate"]
        assert len(gate_steps) == 1
        assert "酸奶" in gate_steps[0].reason
        # 库里查到以后不再按证据不足拒答（脚本规划器第二次放行 final）
        assert result.stop_reason == "final"
        assert not result.answer.startswith(REFUSAL)

    def test_long_text_example_is_not_treated_as_an_entity(self, tmp_path):
        kb, sql = self.tools(tmp_path, catalog="products.description 存长文本，例：酸奶全程冷藏，胀包就直接扔掉。")
        result = make_runtime(tmp_path, [kb, sql], self.kb_planner(self.refusal())).run("酸奶胀包了还能喝吗？")

        assert sql.calls == []
        assert result.stop_reason == "insufficient_evidence"

    def test_question_without_a_catalog_entity_still_refuses(self, tmp_path):
        kb, sql = self.tools(tmp_path)
        planner = ScriptedPlanner(
            route=RouteDecision(
                intent="knowledge",
                action={"tool": "kb_search", "args": {"query": "精排 原问题"}},
                reason="文档类问题",
            ),
            decisions=[self.refusal()],
        )
        result = make_runtime(tmp_path, [kb, sql], planner).run("精排为什么始终用原问题？")

        assert sql.calls == []
        assert result.answer.startswith(REFUSAL)
        assert result.gate_intercepts == 0

    def test_forces_at_most_one_query(self, tmp_path):
        kb, sql = self.tools(tmp_path)
        result = make_runtime(
            tmp_path, [kb, sql], self.kb_planner(self.refusal(), self.refusal())
        ).run("酸奶胀包了还能喝吗？")

        assert len(sql.calls) == 1
        assert result.gate_intercepts == 1
        # 库里也没有那项事实：照原样拒答，不再反复查
        assert result.stop_reason == "insufficient_evidence"
        assert result.answer.startswith(REFUSAL)

    def test_no_second_look_when_the_sql_side_already_ran(self, tmp_path):
        kb, sql = self.tools(tmp_path)
        planner = self.kb_planner(
            StepDecision(action="tool", tool="sql_query", args={"sql": "SELECT 1"}, reason="再查一次"),
            self.refusal(),
        )
        result = make_runtime(tmp_path, [kb, sql], planner).run("酸奶胀包了还能喝吗？")

        assert len(sql.calls) == 1
        assert result.gate_intercepts == 0

    def test_disabled_by_flag(self, tmp_path):
        kb, sql = self.tools(tmp_path)
        runtime = make_runtime(
            tmp_path, [kb, sql], self.kb_planner(self.refusal()), kb_miss_sql_gate=False
        )
        result = runtime.run("酸奶胀包了还能喝吗？")

        assert sql.calls == []
        assert result.answer.startswith(REFUSAL)
        assert result.gate_intercepts == 0

    def test_gate_in_stream(self, tmp_path):
        kb, sql = self.tools(tmp_path)
        events = list(
            make_runtime(tmp_path, [kb, sql], self.kb_planner(self.refusal())).run_stream(
                "酸奶胀包了还能喝吗？"
            )
        )

        assert sql.calls == [{"sql": "SELECT * FROM products WHERE name LIKE '%酸奶%' LIMIT 5"}]
        step_events = [e for e in events if e["event"] == "step"]
        assert [e["data"]["tool"] for e in step_events if e["data"].get("tool")] == [
            "kb_search",
            "sql_query",
        ]


class TestSharedToolRegistryAssembly:
    """`build_tool_registry` 是 agentic 与 MCP 共用的装配：只认注册表，不经过 AgentRuntime。"""

    def registry(self, tmp_path, body: str, **kwargs):
        path = tmp_path / "sources.yaml"
        path.write_text(body, encoding="utf-8")
        return build_tool_registry(object(), sources_path=path, **kwargs)

    def test_optin_registers_sql_query(self, tmp_path):
        registry = self.registry(
            tmp_path, sources_yaml(make_db(tmp_path), opt_in="    allow_runtime_query: true\n")
        )
        assert registry.names() == ["kb_search", "sql_query"]
        assert registry.get("sql_query").allowed_tables == ["products"]

    def test_no_optin_means_kb_only(self, tmp_path):
        assert self.registry(tmp_path, sources_yaml(make_db(tmp_path))).names() == ["kb_search"]

    def test_allow_sql_false_skips_assembly(self, tmp_path):
        registry = self.registry(
            tmp_path,
            sources_yaml(make_db(tmp_path), opt_in="    allow_runtime_query: true\n"),
            allow_sql=False,
        )
        assert registry.names() == ["kb_search"]

    def test_missing_config_degrades_to_kb_only(self, tmp_path):
        registry = build_tool_registry(object(), sources_path=tmp_path / "不存在.yaml")
        assert registry.names() == ["kb_search"]

    def test_agent_runtime_shares_the_same_registry(self, tmp_path):
        body = sources_yaml(make_db(tmp_path), opt_in="    allow_runtime_query: true\n")
        path = tmp_path / "sources.yaml"
        path.write_text(body, encoding="utf-8")
        assert build_agent_runtime(object(), sources_path=path).registry.names() == build_tool_registry(
            object(), sources_path=path
        ).names()
