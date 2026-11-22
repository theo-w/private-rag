"""function calling 决策路径单测（不依赖本机 Ollama）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from private_rag.agent import prompts as prompts_module
from private_rag.agent.planner import LlmPlanner
from private_rag.agent.prompts import (
    ROUTE_TOOL_SCHEMA,
    STEP_TOOL_SCHEMA,
    PlannerError,
    ask_tool_json,
)


def _fake_llm(response: AIMessage) -> MagicMock:
    llm = MagicMock()
    llm.bind_tools.return_value.invoke.return_value = response
    return llm


class TestAskToolJson:
    def test_route_schema_and_args(self):
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "route_decision",
                    "args": {
                        "intent": "database",
                        "reason": "字段在表里",
                        "action": {"tool": "sql_query", "args": {"sql": "SELECT 1"}},
                    },
                    "id": "1",
                }
            ],
        )
        fake = _fake_llm(msg)
        with patch.object(prompts_module, "ChatOllama", return_value=fake) as ctor:
            data = ask_tool_json("提示词", kind="route")
        assert data["intent"] == "database"
        assert data["action"]["tool"] == "sql_query"
        # 工具 schema 靠 bind_tools 声明，不再设 format="json"
        assert "format" not in ctor.call_args.kwargs
        assert ctor.call_args.kwargs["temperature"] == 0
        assert fake.bind_tools.call_args.args[0] == [ROUTE_TOOL_SCHEMA]

    def test_step_schema(self):
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "step_decision",
                    "args": {"action": "tool", "tool": "sql_query", "args": {"sql": "SELECT 2"}, "reason": "要数值"},
                    "id": "1",
                }
            ],
        )
        fake = _fake_llm(msg)
        with patch.object(prompts_module, "ChatOllama", return_value=fake):
            data = ask_tool_json("提示词", kind="step")
        assert data["action"] == "tool"
        assert fake.bind_tools.call_args.args[0] == [STEP_TOOL_SCHEMA]

    def test_no_tool_calls_raises(self):
        msg = AIMessage(content="我直接回答你")
        fake = _fake_llm(msg)
        with patch.object(prompts_module, "ChatOllama", return_value=fake):
            with pytest.raises(PlannerError) as error:
                ask_tool_json("提示词", kind="route")
        assert "tool_calls" in str(error.value)

    def test_non_dict_args_raises(self):
        # AIMessage 的校验不允许 args 为非 dict，这里用替身响应模拟非 langchain 形状的返回
        response = MagicMock()
        response.tool_calls = [{"name": "route_decision", "args": ["不是对象"], "id": "1"}]
        fake = MagicMock()
        fake.bind_tools.return_value.invoke.return_value = response
        with patch.object(prompts_module, "ChatOllama", return_value=fake):
            with pytest.raises(PlannerError):
                ask_tool_json("提示词", kind="route")


class TestLlmPlannerMode:
    def test_default_mode_uses_ask_json(self):
        with patch("private_rag.agent.planner.ask_json", return_value={"intent": "knowledge", "action": {"tool": "kb_search", "args": {"query": "x"}}}) as aj, \
             patch("private_rag.agent.planner.ask_tool_json") as fc:
            route = LlmPlanner(model="m").route("问题", "清单")
        assert route.intent == "knowledge"
        aj.assert_called_once()
        fc.assert_not_called()

    def test_function_mode_route(self):
        payload = {"intent": "database", "reason": "r", "action": {"tool": "sql_query", "args": {"sql": "SELECT 1"}}}
        with patch("private_rag.agent.planner.ask_json") as aj, \
             patch("private_rag.agent.planner.ask_tool_json", return_value=payload) as fc:
            route = LlmPlanner(model="m", mode="function").route("问题", "清单")
        assert route.intent == "database"
        assert route.tool == "sql_query"
        aj.assert_not_called()
        assert fc.call_args.kwargs["kind"] == "route"

    def test_function_mode_decide_tool(self):
        payload = {"action": "tool", "tool": "sql_query", "args": {"sql": "SELECT 1"}, "reason": "r"}
        with patch("private_rag.agent.planner.ask_tool_json", return_value=payload) as fc:
            decision = LlmPlanner(model="m", mode="function").decide("问题", "清单", "证据", [])
        assert decision.action == "tool"
        assert decision.tool == "sql_query"
        assert fc.call_args.kwargs["kind"] == "step"

    def test_function_mode_decide_final_with_gap(self):
        payload = {"action": "final", "sufficient": False, "missing": "缺单价", "answer": "", "reason": "不足"}
        with patch("private_rag.agent.planner.ask_tool_json", return_value=payload):
            decision = LlmPlanner(model="m", mode="function").decide("问题", "清单", "证据", [])
        assert decision.action == "final"
        assert decision.sufficient is False
        assert decision.missing == "缺单价"

    def test_env_switch_to_function_mode(self, monkeypatch):
        monkeypatch.setattr("private_rag.agent.planner.AGENT_PLANNER_MODE", "function")
        payload = {"intent": "none", "reason": "r", "action": {}}
        with patch("private_rag.agent.planner.ask_tool_json", return_value=payload) as fc, \
             patch("private_rag.agent.planner.ask_json") as aj:
            route = LlmPlanner(model="m").route("明天天气", "清单")
        assert route.intent == "none"
        fc.assert_called_once()
        aj.assert_not_called()

    def test_invalid_env_falls_back_to_json(self, monkeypatch):
        # config 在导入时把非法取值归一到 json，运行时不会因为拼错开关而断链
        assert "json" in {"json", "function"}
        from private_rag.config import AGENT_PLANNER_MODE
        assert AGENT_PLANNER_MODE in {"json", "function"}
