"""Agentic 决策的提示词与结构化输出解析。

决策一律用 JSON 结构化输出，而不是依赖模型的原生 function calling：
本项目的默认模型是本机 MLX 模型，工具调用格式支持并不稳定，
JSON 模式在任何模型上都可用，失败时还能回退到关键词启发式路由。
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from langchain_ollama import ChatOllama

from private_rag.config import AGENT_LLM_MODEL, AGENT_PLANNER_TOKENS, MAX_QUESTION_CHARS
from private_rag.generation import REFUSAL, History, render_history

# 明确指向「库里精确数据」的线索；LLM 不可用时用它决定首选工具
DATABASE_HINTS: tuple[str, ...] = (
    "多少",
    "几个",
    "几条",
    "多少个",
    "多少钱",
    "价格",
    "单价",
    "最贵",
    "最便宜",
    "总价",
    "合计",
    "总数",
    "统计",
    "平均",
    "排名",
    "排序",
    "前几",
    "最低",
    "最高",
    "低于",
    "超过",
    "大于",
    "小于",
    "库存",
    "销量",
    "销售额",
    "count",
    "sum",
    "avg",
    "select",
)

ROUTER_PROMPT = """你是检索路由器。判断下面这个问题该用哪个工具来取证，不要直接回答。

可用工具：
{tool_specs}

意图取值：
- knowledge：答案在知识库文档里（概念、流程、说明、结论）
- database：需要业务库里的精确数值（数量、价格、排名、统计）
- mixed：既要文档说明，也要库里的数值
- none：与知识库和数据库都无关，或信息不足以取证

只输出 JSON，格式为：
{{"intent": "knowledge", "reason": "一句话说明为什么", "action": {{"tool": "工具名", "args": {{"参数": "值"}}}}}}

要求：
1. action 必须是上面列出的工具之一，参数按该工具的示例填写；检索类查询要保留问题里的关键实体与限定条件。
2. intent 为 none 时 action 留空对象 {{}}。
3. reason 用一句中文说明判断依据，不要复述问题。

对话历史（仅用于理解指代）：
{history_section}用户问题：
{question}
"""

STEP_PROMPT = """你是检索行动规划器。基于已有证据决定「再调用一个工具」还是「就此作答」。

可用工具：
{tool_specs}

已收集的证据：
{evidence}

已执行的步骤：
{observations}

只输出 JSON，格式为：
{{"action": "tool", "tool": "工具名", "args": {{"参数": "值"}}, "reason": "一句话说明为什么还要再查"}}
或：
{{"action": "final", "answer": "最终回答"}}

要求：
1. 证据已足够回答就选 final，answer 用中文，并在相关句子后用 [编号] 标注依据；没有依据的部分不要编造。
2. 只有当缺少关键数据时才选 tool，并且必须换一个新角度（换查询词、换 SQL 条件），不要重复已做过的调用。
3. 只能使用上面列出的工具。
4. 这是第 {steps_used} 步，最多 {max_steps} 步；步数将尽时优先给出结论。

对话历史（仅用于理解指代）：
{history_section}用户问题：
{question}
"""

FINAL_PROMPT = """你是知识库问答助手，只根据下面带编号的证据回答，证据之外的事实一律不要写。
请在相关句子后用 [编号] 标注依据，例如 [1]；不同来源矛盾时说明矛盾，不要自行取舍。
如果证据不足以回答，直接回复【资料中没有相关信息】，并说明还缺什么。
{history_section}证据：
{evidence}

用户问题：
{question}
"""


class PlannerError(RuntimeError):
    """模型没有给出可用的决策 JSON。"""


def _content_text(content: Any) -> str:
    """兼容不同 provider 返回的字符串或 content blocks。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content or "")


def _parse_payload(text: str) -> dict:
    """解析 JSON；模型偶尔在 JSON 前后加说明文字，尽量取出对象。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出中没有 JSON 对象")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型输出的 JSON 不是对象")
    return data


def _history_section(history: Optional[History]) -> str:
    text = render_history(history)
    if not text:
        return "（无）\n\n"
    return f"{text}\n\n"


def ask_json(
    prompt: str, *, model: str = AGENT_LLM_MODEL, num_predict: int = AGENT_PLANNER_TOKENS
) -> dict:
    """向模型要一段 JSON；解析失败抛 PlannerError。"""
    llm = ChatOllama(
        model=model,
        temperature=0,
        format="json",
        num_ctx=8192,
        num_predict=num_predict,
        disable_streaming=True,
        keep_alive="10m",
    )
    response = llm.invoke(prompt)
    try:
        return _parse_payload(_content_text(response.content))
    except Exception as exc:
        raise PlannerError(f"{type(exc).__name__}: {exc}") from exc


def build_route_prompt(
    question: str,
    tool_specs: str,
    history: Optional[History] = None,
) -> str:
    return ROUTER_PROMPT.format(
        tool_specs=tool_specs,
        history_section=_history_section(history),
        question=question[:MAX_QUESTION_CHARS],
    )


def build_step_prompt(
    question: str,
    tool_specs: str,
    evidence: str,
    observations: Sequence[str],
    history: Optional[History] = None,
    *,
    steps_used: int = 1,
    max_steps: int = 4,
) -> str:
    log = "\n".join(observations) if observations else "（还没有执行任何步骤）"
    return STEP_PROMPT.format(
        tool_specs=tool_specs,
        evidence=evidence or "（还没有收集到证据）",
        observations=log,
        history_section=_history_section(history),
        question=question[:MAX_QUESTION_CHARS],
        steps_used=steps_used,
        max_steps=max_steps,
    )


def build_final_prompt(
    question: str,
    evidence: str,
    history: Optional[History] = None,
) -> str:
    return FINAL_PROMPT.format(
        history_section=_history_section(history),
        evidence=evidence or "（没有收集到任何证据）",
        question=question[:MAX_QUESTION_CHARS],
    )


def generate_final_answer(
    question: str,
    evidence: str,
    *,
    model: str = AGENT_LLM_MODEL,
    history: Optional[History] = None,
) -> str:
    """基于已收集的证据生成最终回答；没有证据时不调用模型，直接拒答。"""
    if not evidence.strip():
        return REFUSAL
    llm = ChatOllama(model=model, num_ctx=8192, keep_alive="10m")
    response = llm.invoke(build_final_prompt(question, evidence, history))
    text = _content_text(response.content).strip()
    return text or REFUSAL


def _clean_args(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[str(key)] = item
    return cleaned


def parse_route(data: dict) -> tuple[str, dict, str]:
    """从路由 JSON 中取 (intent, action, reason)；intent 归一到四个取值。"""
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in {"knowledge", "database", "mixed", "none"}:
        intent = "knowledge"
    action = data.get("action")
    if not isinstance(action, dict):
        action = {}
    tool = str(action.get("tool") or "").strip()
    args = _clean_args(action.get("args"))
    reason = " ".join(str(data.get("reason") or "").split())[:300]
    return intent, {"tool": tool, "args": args}, reason


def parse_decision(data: dict) -> tuple[str, dict, str, str]:
    """从步进 JSON 中取 (action, payload, reason, answer)。"""
    action = str(data.get("action") or "").strip().lower()
    if action not in {"tool", "final"}:
        # 有的模型会直接回一段文本，当成最终回答处理
        answer = " ".join(str(data.get("answer") or "").split())
        return ("final", {}, "", answer) if answer else ("final", {}, "", "")
    if action == "final":
        answer = str(data.get("answer") or "").strip()
        reason = " ".join(str(data.get("reason") or "").split())[:300]
        return "final", {}, reason, answer
    tool = str(data.get("tool") or "").strip()
    args = _clean_args(data.get("args"))
    reason = " ".join(str(data.get("reason") or "").split())[:300]
    return "tool", {"tool": tool, "args": args}, reason, ""


def heuristic_route(question: str, tool_names: Sequence[str]) -> tuple[str, dict, str]:
    """模型不可用时的回退路由：靠关键词在知识库与数据库之间选一条路。"""
    text = (question or "").lower()
    hits = [hint for hint in DATABASE_HINTS if hint in text]
    names = list(tool_names)
    sql_available = "sql_query" in names

    if sql_available and hits:
        # 回退路由写不出具体 SQL，交给工具自己的兜底查询
        return (
            "database",
            {"tool": "sql_query", "args": {}},
            f"命中数据类线索（{hits[0]}），改查业务库",
        )
    if "kb_search" in names:
        return "knowledge", {"tool": "kb_search", "args": {"query": question}}, "回退为知识库检索"
    if sql_available:
        return "database", {"tool": "sql_query", "args": {}}, "回退为数据库查询"
    return "none", {}, "没有可用工具"
