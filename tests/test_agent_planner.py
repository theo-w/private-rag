"""决策器测试：JSON 模式的输出预算与解析回退。

用假的 ChatOllama 替身捕获构造参数，不需要本机 Ollama。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import private_rag.agent.prompts as prompts_module
from private_rag.agent.planner import LlmPlanner
from private_rag.agent.prompts import PlannerError, build_final_prompt, build_route_prompt, heuristic_route, parse_decision
from private_rag.generation import REFUSAL
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


class TestRouterPromptDataAvailability:
    """路由提示词按「事实住在哪一侧」判意图，不按问法像不像查询。"""

    def test_rules_cover_plain_spoken_questions(self):
        prompt = build_route_prompt("香蕉能不能放冰箱", "工具清单", None)

        assert "回答所需的事实住在哪一侧" in prompt
        # 口语化、没有数字词的业务记录属性问题也必须查库——这是 v7.1 漏路由的直接原因
        assert "一个数字词都没有" in prompt
        assert "拿不准且 sql_query 可用时选 mixed" in prompt

    def test_multi_part_questions_must_pick_mixed(self):
        prompt = build_route_prompt("问题", "工具清单", None)

        assert "只要有一侧被漏掉就选 mixed" in prompt

    def test_examples_use_invented_tables_not_the_eval_corpus(self):
        # few-shot 里出现评估集的实体与表名，等于把答案喂给模型，指标就不干净了
        prompt = build_route_prompt("问题", "工具清单", None)
        for leaked in ("products", "香蕉", "猕猴桃", "橙子", "demo."):
            assert leaked not in prompt

    def test_none_example_is_out_of_scope_for_both_sides(self):
        prompt = build_route_prompt("问题", "工具清单", None)

        assert "既不在知识库文档里，也不在业务表中" in prompt

    def test_none_is_not_an_i_guess_we_cannot_find_it_verdict(self):
        # 收紧 none 准入：印象里没有数据不构成 none，这正是「判 none 就不检索」那条捷径的来源
        prompt = build_route_prompt("问题", "工具清单", None)

        assert "none 不等于「我猜查不到」" in prompt
        assert "有没有要查过才知道" in prompt
        assert "明确在本系统之外" in prompt


class TestSufficiencyParsing:
    def test_missing_field_defaults_to_sufficient(self):
        # 被截断的 JSON 或老格式不能被读成「证据不足」，否则整段 answers 变拒答
        action, payload, _, answer = parse_decision({"action": "final", "answer": "答"})

        assert action == "final"
        assert payload["sufficient"] is True
        assert payload["missing"] == ""

    def test_string_false_reads_as_insufficient(self):
        _, payload, _, _ = parse_decision(
            {"action": "final", "sufficient": "false", "missing": "  缺价格  ", "answer": ""}
        )

        assert payload["sufficient"] is False
        assert payload["missing"] == "缺价格"

    def test_prose_fallback_still_reads_as_sufficient(self):
        _, payload, _, answer = parse_decision({"answer": "只有正文"})

        assert payload["sufficient"] is True
        assert answer == "只有正文"

    def test_llm_planner_carries_sufficiency(self):
        FakeChatOllama.payload = json.dumps(
            {"action": "final", "sufficient": False, "missing": "库里没有该字段", "reason": "证据不足"},
            ensure_ascii=False,
        )

        decision = LlmPlanner(model="fake-model").decide("问题", "工具清单", "证据", [])

        assert decision.sufficient is False
        assert decision.missing == "库里没有该字段"


class TestFinalPromptGrounding:
    def test_grounding_rules_block_common_knowledge_and_absence_inference(self):
        prompt = build_final_prompt("问题", "证据", None)

        assert "通用常识" in prompt
        # 「查不到」被当成「不存在」是 v7.1 三条未拒答样本的共同成因
        assert "不等于「不存在」" in prompt
        assert REFUSAL in prompt

    def test_declared_gap_makes_the_sentinel_mandatory(self):
        prompt = build_final_prompt("问题", "证据", None, missing="保存方式")

        assert "已判定证据不足的部分：保存方式" in prompt
        assert "回答第一行必须是" in prompt

    def test_no_gap_adds_no_extra_demand(self):
        assert "已判定证据不足的部分" not in build_final_prompt("问题", "证据", None)


class TestHeuristicCatalogHints:
    def test_catalog_entity_routes_to_sql_without_number_words(self):
        intent, action, _ = heuristic_route(
            "香蕉能不能放冰箱", ["kb_search", "sql_query"], extra_hints=["香蕉"]
        )

        assert intent == "database"
        assert action["tool"] == "sql_query"

    def test_same_question_without_hints_goes_to_knowledge(self):
        intent, action, _ = heuristic_route("香蕉能不能放冰箱", ["kb_search", "sql_query"])

        assert intent == "knowledge"
        assert action["tool"] == "kb_search"

    def test_hints_are_case_insensitive(self):
        intent, _, _ = heuristic_route(
            "查一下 ORDERS 表", ["kb_search", "sql_query"], extra_hints=["orders"]
        )

        assert intent == "database"

    def test_blank_hints_are_ignored(self):
        intent, action, _ = heuristic_route("架构是什么", ["kb_search", "sql_query"], extra_hints=["", "  "])

        assert intent == "knowledge"
        assert action["tool"] == "kb_search"
