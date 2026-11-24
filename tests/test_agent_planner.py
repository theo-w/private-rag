"""决策器测试：JSON 模式的输出预算与解析回退。

用假的 ChatOllama 替身捕获构造参数，不需要本机 Ollama。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import private_rag.agent.prompts as prompts_module
from private_rag import config
from private_rag.agent.planner import LlmPlanner
from private_rag.agent.prompts import (
    PlannerError,
    ask_json,
    build_final_prompt,
    build_route_prompt,
    build_step_prompt,
    generate_final_answer,
    heuristic_route,
    parse_decision,
    stream_final_answer,
)
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

    def stream(self, prompt):
        yield SimpleNamespace(content=FakeChatOllama.payload)


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


class TestPlannerThinkingStages:
    """思考按决策环节开关：默认只让路由思考，步进决策关掉。

    实测同一条提示词，路由开思考 40.4 秒 / 关思考 6.3 秒；全关时路由会把三条
    multi_step 样本误判成 mixed，双侧门控烧光步数并把证据上下文挤掉，最终拒答。
    """

    def test_route_keeps_thinking_by_default(self):
        FakeChatOllama.payload = route_payload()
        LlmPlanner(model="fake-model").route("问题", "工具清单")
        assert FakeChatOllama.calls[0]["reasoning"] is True

    def test_decide_turns_thinking_off_by_default(self):
        FakeChatOllama.payload = json.dumps(
            {"action": "final", "sufficient": True, "missing": "", "answer": "答案"},
            ensure_ascii=False,
        )
        LlmPlanner(model="fake-model").decide("问题", "工具清单", "证据", ["检索到 1 段"])
        assert FakeChatOllama.calls[0]["reasoning"] is False

    def test_switch_can_enable_step_thinking(self, monkeypatch):
        # RAG_AGENT_PLANNER_THINKING=all 时两个环节都保留思考
        monkeypatch.setattr(prompts_module, "AGENT_PLANNER_THINKING_STAGES", frozenset({"route", "step"}))
        FakeChatOllama.payload = route_payload()
        LlmPlanner(model="fake-model").decide("问题", "工具清单", "证据", ["检索到 1 段"])
        assert FakeChatOllama.calls[0]["reasoning"] is True

    def test_switch_can_turn_off_every_stage(self, monkeypatch):
        # RAG_AGENT_PLANNER_THINKING=off 时连路由也不思考（换取最快速度，接受路由判意更粗）
        monkeypatch.setattr(prompts_module, "AGENT_PLANNER_THINKING_STAGES", frozenset())
        FakeChatOllama.payload = route_payload()
        LlmPlanner(model="fake-model").route("问题", "工具清单")
        assert FakeChatOllama.calls[0]["reasoning"] is False

    def test_final_answer_is_not_affected_by_the_switch(self, monkeypatch):
        # 最终作答要写引用与缺证说明，思考保持模型默认，不由这个开关决定
        monkeypatch.setattr(prompts_module, "AGENT_PLANNER_THINKING_STAGES", frozenset())
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert "reasoning" not in FakeChatOllama.calls[0]

    def test_stage_value_parsing_falls_back_to_default(self, monkeypatch):
        from private_rag.config import _env_stages

        monkeypatch.setenv("RAG_AGENT_PLANNER_THINKING", "off")
        assert _env_stages("RAG_AGENT_PLANNER_THINKING", "route") == frozenset()
        monkeypatch.setenv("RAG_AGENT_PLANNER_THINKING", "1")
        assert _env_stages("RAG_AGENT_PLANNER_THINKING", "route") == frozenset({"route", "step"})
        monkeypatch.setenv("RAG_AGENT_PLANNER_THINKING", "step")
        assert _env_stages("RAG_AGENT_PLANNER_THINKING", "route") == frozenset({"step"})
        # 拼错的取值不静默失效：回退默认环节
        monkeypatch.setenv("RAG_AGENT_PLANNER_THINKING", "routeee")
        assert _env_stages("RAG_AGENT_PLANNER_THINKING", "route") == frozenset({"route"})


class TestAnswerThinkingSwitch:
    """最终作答的思考开关：默认 ``keep`` 保持模型默认，只有显式 on/off 才传 ``reasoning``。

    实测同一条证据文本 keep 与 on 都是 10.3 秒、off 是 2.95 秒，默认确实在思考；
    但关掉以后 29 条对照出现三条误拒，所以默认保持 ``keep``，只留开关。读数见 docs/ROADMAP.md 的 v8.2。
    """

    def test_default_keeps_model_default(self):
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert "reasoning" not in FakeChatOllama.calls[0]

    def test_off_passes_reasoning_false(self, monkeypatch):
        monkeypatch.setattr(prompts_module, "AGENT_ANSWER_THINKING", "off")
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert FakeChatOllama.calls[0]["reasoning"] is False

    def test_on_passes_reasoning_true(self, monkeypatch):
        monkeypatch.setattr(prompts_module, "AGENT_ANSWER_THINKING", "on")
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert FakeChatOllama.calls[0]["reasoning"] is True

    def test_explicit_argument_beats_config(self, monkeypatch):
        monkeypatch.setattr(prompts_module, "AGENT_ANSWER_THINKING", "off")
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据", thinking="keep")
        assert "reasoning" not in FakeChatOllama.calls[0]

    def test_stream_path_shares_the_switch(self, monkeypatch):
        # MCP 服务与 demo 的流式路径用同一个开关，不能只有非流式生效
        monkeypatch.setattr(prompts_module, "AGENT_ANSWER_THINKING", "off")
        FakeChatOllama.payload = "答案 [1]"
        assert list(stream_final_answer("问题", "[1] 证据")) == ["答案 [1]"]
        assert FakeChatOllama.calls[0]["reasoning"] is False

    def test_no_evidence_still_skips_the_model(self, monkeypatch):
        monkeypatch.setattr(prompts_module, "AGENT_ANSWER_THINKING", "off")
        assert generate_final_answer("问题", "   ") == REFUSAL
        assert FakeChatOllama.calls == []

    def test_value_parsing_falls_back_to_keep(self):
        from private_rag.config import _answer_thinking_mode

        assert _answer_thinking_mode("off") == "off"
        assert _answer_thinking_mode("1") == "on"
        assert _answer_thinking_mode("keep") == "keep"
        # 拼错的取值不静默失效：回退到保持模型默认
        assert _answer_thinking_mode("maybe") == "keep"
        assert _answer_thinking_mode("") == "keep"


class TestEvalDeterminismParams:
    """采样参数与 seed：运行时都不传（保持服务端默认），评估链路才钉死。

    钉死之前最终作答既不传 temperature 也不传 seed，同一条证据两次生成会给出不同答案，
    同配置的两次评估也就对不上（见 docs/ROADMAP.md 的 v8.3）。
    """

    def test_runtime_passes_neither(self, monkeypatch):
        monkeypatch.setattr(config, "AGENT_ANSWER_TEMPERATURE", None)
        monkeypatch.setattr(config, "LLM_SEED", None)
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert "temperature" not in FakeChatOllama.calls[0]
        assert "seed" not in FakeChatOllama.calls[0]
        # 决策调用本来传 temperature=0，没配 seed 时也不传
        FakeChatOllama.payload = json.dumps({"action": "final", "reason": "r"})
        ask_json("提示词", kind="step")
        assert FakeChatOllama.calls[1]["temperature"] == 0
        assert "seed" not in FakeChatOllama.calls[1]

    def test_pinned_seed_reaches_answer_and_decisions(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_SEED", 7)
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert FakeChatOllama.calls[0]["seed"] == 7
        # 流式路径与思考开关一样共用同一套参数
        assert list(stream_final_answer("问题", "[1] 证据")) == ["答案 [1]"]
        assert FakeChatOllama.calls[1]["seed"] == 7
        FakeChatOllama.payload = json.dumps({"action": "final", "reason": "r"})
        ask_json("提示词", kind="step")
        assert FakeChatOllama.calls[2]["seed"] == 7

    def test_pinned_answer_temperature_only_affects_answer(self, monkeypatch):
        monkeypatch.setattr(config, "AGENT_ANSWER_TEMPERATURE", 0.0)
        monkeypatch.setattr(config, "LLM_SEED", None)
        FakeChatOllama.payload = "答案 [1]"
        generate_final_answer("问题", "[1] 证据")
        assert FakeChatOllama.calls[0]["temperature"] == 0.0
        assert "seed" not in FakeChatOllama.calls[0]

    def test_optional_env_parsing(self, monkeypatch):
        from private_rag.config import _env_optional_float, _env_optional_int

        monkeypatch.delenv("RAG_X", raising=False)
        assert _env_optional_float("RAG_X") is None
        assert _env_optional_int("RAG_X") is None
        monkeypatch.setenv("RAG_X", "0")
        assert _env_optional_float("RAG_X") == 0.0
        assert _env_optional_int("RAG_X") == 0
        monkeypatch.setenv("RAG_X", "0.7")
        assert _env_optional_float("RAG_X") == 0.7
        # 非法值按「没设置」处理，不会把运行时行为改成某个猜出来的数
        assert _env_optional_int("RAG_X") is None

    def test_eval_env_pins_both(self, monkeypatch):
        import os

        from private_rag.eval_env import configure_eval_env

        # 两条链路的生成温度都要钉住：固定链路跑 full / none 时走的是 generation.py 的生成
        for name in (
            "RAG_AGENT_ANSWER_TEMPERATURE",
            "RAG_GENERATION_TEMPERATURE",
            "RAG_LLM_SEED",
        ):
            monkeypatch.delenv(name, raising=False)
        configure_eval_env()
        assert os.environ["RAG_AGENT_ANSWER_TEMPERATURE"] == "0"
        assert os.environ["RAG_GENERATION_TEMPERATURE"] == "0"
        assert os.environ["RAG_LLM_SEED"] == "7"

    def test_eval_env_keeps_exported_override(self, monkeypatch):
        import os

        from private_rag.eval_env import configure_eval_env

        # 想跑采样版可以把变量 export 掉，评估隔离不强行覆盖
        monkeypatch.setenv("RAG_AGENT_ANSWER_TEMPERATURE", "0.8")
        configure_eval_env()
        assert os.environ["RAG_AGENT_ANSWER_TEMPERATURE"] == "0.8"


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


class TestCoreferenceResolution:
    """「本项目/本系统」被当成缺少对话上下文，是 v7.2 那轮 m01 文档那一半没取证的直接成因。"""

    def test_router_prompt_resolves_self_reference_without_history(self):
        prompt = build_route_prompt("这个项目的引用列表从哪儿来", "工具清单", None)

        assert "「这个项目」「本系统」" in prompt
        assert "没有对话历史绝不是选 none 的理由" in prompt

    def test_step_prompt_forbids_missing_context_as_a_stop_reason(self):
        prompt = build_step_prompt("那它的保存方式呢", "工具清单", "证据", ["已查 1 次"], None)

        assert "不得以「缺少上下文」" in prompt
        # 指代不明不是证据不足，不能借第 1 条提前收工
        assert "不属于第 1 条说的证据不足" in prompt

    def test_final_prompt_denies_missing_history_as_a_refusal_reason(self):
        prompt = build_final_prompt("那它的保存方式呢", "证据", None)

        assert "「没有对话历史」不是拒答理由" in prompt

    def test_new_rules_do_not_leak_eval_corpus_entities(self):
        # 与路由提示词同一条底线：提示词里出现评估集的实体与表名，指标就不干净了
        for prompt in (
            build_route_prompt("问题", "工具清单", None),
            build_step_prompt("问题", "工具清单", "证据", [], None),
            build_final_prompt("问题", "证据", None),
        ):
            for leaked in ("products", "香蕉", "猕猴桃", "橙子", "demo.", "精排", "引用列表"):
                assert leaked not in prompt

    def test_step_prompt_limits_sufficient_to_collected_evidence(self):
        """修法 A：规则 1 限定 sufficient 只看已收集的证据，不含历史。"""
        prompt = build_step_prompt("问题", "工具清单", "证据", [], None)
        assert "只有「已收集的证据」段落里的内容算支撑" in prompt
        assert "对话历史不算事实依据" in prompt

    def test_step_prompt_forbids_final_without_both_sides(self):
        """修法 A：规则 10 禁止 mixed 查询在任一侧无证据时选 final。"""
        prompt = build_step_prompt("问题", "工具清单", "证据", [], None)
        assert "intent=mixed" in prompt
        assert "不得选 final" in prompt
        assert "至少各查一次" in prompt

    def test_final_prompt_requires_rechecking_history_facts(self):
        """修法 A：规则 7 要求历史里的事实必须先查证据。"""
        prompt = build_final_prompt("问题", "证据", None)
        assert "对话历史里的事实" in prompt
        assert "必须先查证据" in prompt
        assert "不能直接引用历史里的数据" in prompt

    def test_history_section_has_factual_disclaimer(self):
        """修法 A：_history_section() 加上固定链路的标注。"""
        from private_rag.agent.prompts import _history_section
        from private_rag.generation import History

        hist: History = [("user", "香蕉多少钱？"), ("assistant", "6.5 元")]
        result = _history_section(hist)
        assert "不能作为事实依据" in result
        assert "仅用于理解当前问题的指代" in result
