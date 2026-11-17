"""决策器测试：JSON 模式的输出预算与解析回退。

用假的 ChatOllama 替身捕获构造参数，不需要本机 Ollama。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import private_rag.agent.prompts as prompts_module
from private_rag.agent.planner import LlmPlanner
from private_rag.agent.prompts import PlannerError
from private_rag.config import AGENT_PLANNER_TOKENS


class FakeChatOllama:
    """记录构造参数并返回预设内容。"""

    calls: list[dict] = []
    payload: str = ""

    def __init__(self, **kwargs):
        FakeChatOllama.calls.append(kwargs)

    def invoke(self, prompt):
        return SimpleNamespace(content=FakeChatOllama.payload)


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    FakeChatOllama.calls.clear()
    FakeChatOllama.payload = ""
    monkeypatch.setattr(prompts_module, "ChatOllama", FakeChatOllama)


def route_payload() -> str:
    return json.dumps(
        {
            "intent": "knowledge",
            "action": {"tool": "kb_search", "args": {"query": "香蕉怎么保存"}},
            "reason": "储存方法属于文档内容",
        },
        ensure_ascii=False,
    )


class TestPlannerTokenBudget:
    """预算给小时，推理型模型只输出思考内容、正文为空，路由会整段回退。"""

    def test_route_uses_the_configured_budget(self):
        FakeChatOllama.payload = route_payload()
        route = LlmPlanner(model="fake-model").route("香蕉怎么保存", "工具清单")

        assert FakeChatOllama.calls[0]["num_predict"] == AGENT_PLANNER_TOKENS
        assert route.intent == "knowledge"
        assert route.tool == "kb_search"
        assert route.args == {"query": "香蕉怎么保存"}

    def test_decide_uses_the_configured_budget(self):
        FakeChatOllama.payload = json.dumps(
            {
                "action": "tool",
                "tool": "sql_query",
                "args": {"sql": "SELECT name FROM products"},
                "reason": "要数值",
            },
            ensure_ascii=False,
        )
        decision = LlmPlanner(model="fake-model").decide("问题", "工具清单", "证据", ["检索到 1 段"])

        assert FakeChatOllama.calls[0]["num_predict"] == AGENT_PLANNER_TOKENS
        assert decision.action == "tool"
        assert decision.tool == "sql_query"

    def test_budget_leaves_room_for_reasoning_tokens(self):
        # 本机实跑时 400 token 的返回长度是 0，整段预算被思考内容吃掉
        assert AGENT_PLANNER_TOKENS > 1000

    def test_empty_output_raises_planner_error(self):
        FakeChatOllama.payload = ""
        with pytest.raises(PlannerError) as error:
            LlmPlanner(model="fake-model").route("问题", "工具清单")
        # 回退可接受，但原因要能从异常里读出来
        assert "没有 JSON 对象" in str(error.value)

    def test_json_wrapped_in_prose_still_parses(self):
        FakeChatOllama.payload = '好的：{"intent": "database", "action": {"tool": "sql_query"}} 以上'
        route = LlmPlanner(model="fake-model").route("最便宜的香蕉多少钱", "工具清单")
        assert route.intent == "database"
        assert route.tool == "sql_query"
