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

ROUTER_PROMPT = """你是检索路由器。判断下面这个问题该用哪个工具取证，不要直接回答。

可用工具：
{tool_specs}

判意图只看「回答所需的事实住在哪一侧」，不看问法像不像技术查询：
- knowledge：答案在知识库文档里（概念、流程、架构、说明、结论）
- database：答案在业务表的字段里——不只是数值，描述、备注、状态、时间这类文本字段同样算
- mixed：问题里有多个子问题，分别需要文档与业务表
- none：问题要的事实明确在本系统之外（实时信息、天气、新闻、外部网站内容、主观偏好），且工具描述的「库内数据」里没有对应实体或字段

硬性规则：
1. 问题问的是某条业务记录自身的属性（价格、库存、描述、备注、更新时间等），即使问得很口语、一个数字词都没有，也选 database。
2. 工具描述里的「库内数据」出现了问题指名的实体或字段时，先怀疑事实在表里，不要在文档里找。
3. 多个子问题逐个核对：只要有一侧被漏掉就选 mixed，并把缺口更大的一侧作为首个动作。
4. 拿不准且 sql_query 可用时选 mixed——多查一次的代价远小于漏查。
5. none 不等于「我猜查不到」：只有当问题要的事实明确属于本系统之外（实时信息、天气、新闻、外部网站内容、主观偏好）才选 none。只要问题指向某条业务记录、某个字段或本系统自身的行为与文档，即使你印象里没有这项数据，也必须先取证——有没有要查过才知道。
6. 「这个项目」「本系统」「我们这套系统」这类指代一律指本系统自身，它的说明文档就在知识库里；问到它的行为、组件、流程、数据从哪来，按第 2 条和第 5 条照常取证。没有对话历史绝不是选 none 的理由——历史只用来还原真正的省略，指代本系统时压根不需要历史。
7. action 必须是上面列出的工具之一，参数按该工具示例填写；检索类查询保留问题里的关键实体与限定条件；选 sql_query 时给出一条可执行的单条 SELECT。
8. intent 为 none 时 action 留空对象 {{}}。
9. reason 用一句中文说明判断依据，不要复述问题。

判断示例（下面出现的表与实体都是虚构的，只用来示范判断方法）：
问题：这批订单里的加急件都发货了吗？
{{"intent": "database", "reason": "问的是订单记录自身的状态字段，与文档无关", "action": {{"tool": "sql_query", "args": {{"sql": "SELECT id, status FROM orders WHERE level = '加急' LIMIT 20"}}}}}}
问题：系统的库位编号规则是什么？
{{"intent": "knowledge", "reason": "编号规则属于系统文档说明", "action": {{"tool": "kb_search", "args": {{"query": "库位编号规则"}}}}}}
问题：职级最高的员工是谁？公司的考勤制度又是怎么规定的？
{{"intent": "mixed", "reason": "职级要查员工表，考勤制度在制度文档，缺一不可", "action": {{"tool": "sql_query", "args": {{"sql": "SELECT name, level FROM employees ORDER BY level DESC LIMIT 5"}}}}}}
问题：工号 1024 的员工老家是哪个城市？
{{"intent": "database", "reason": "问的是员工记录自身的字段，字段里到底有没有这项要查过才知道，不能判 none", "action": {{"tool": "sql_query", "args": {{"sql": "SELECT home_city FROM employees WHERE id = 1024 LIMIT 1"}}}}}}
问题：我们这套内部工具的告警规则由哪个模块负责？
{{"intent": "knowledge", "reason": "问的是本系统自身的行为，说明文档在知识库里，没有对话历史不影响取证", "action": {{"tool": "kb_search", "args": {{"query": "告警规则 负责模块"}}}}}}
问题：明天上午北京会下雨吗？
{{"intent": "none", "reason": "天气既不在知识库文档里，也不在业务表中", "action": {{}}}}

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
{{"action": "final", "sufficient": true, "missing": "", "answer": "最终回答"}}

要求：
1. sufficient 表示「已收集的证据」能否支撑问题要的那项事实。只有「已收集的证据」段落里的内容算支撑，对话历史不算事实依据。不能支撑就填 false，并在 missing 里一句话写清缺哪一项，此时 answer 留空字符串——宁可拒答，不要硬凑。
2. 只缺问题的某一部分时 sufficient 填 true，把缺的那部分写进 missing，其余部分照常作答。
3. 「资料里没提到」不等于「不存在」：不得根据查不到就断言某事物不存在、没有或不可能，这类结论必须有明确写着它的证据。
4. 不要用通用常识补全证据里没有的事实。
5. 证据足够就选 final，answer 用中文，在相关句子后用 [编号] 标注依据。
6. 只有当缺少关键数据时才选 tool，并且必须换一个新角度（换查询词、换 SQL 条件），不要重复已做过的调用。
7. 只能使用上面列出的工具。
8. 这是第 {steps_used} 步，最多 {max_steps} 步；步数将尽时也要守住第 1 条——证据不足就选 final 并把 sufficient 填 false。
9. 问题里的指代词（它 / 这个 / 那一步 / 这一项）在对话历史里找不到先行词时，按本系统自身的行为与文档理解它，照常取证或作答；不得以「缺少上下文」「无法确定所指」为由停止取证，那不属于第 1 条说的证据不足。
10. 路由已判定需要两侧取证（intent=mixed）且步数未用尽时，如果任一侧还没有证据，不得选 final——必须至少各查一次。

对话历史（仅用于理解指代）：
{history_section}用户问题：
{question}
"""

FINAL_PROMPT = """你是知识库问答助手，只根据下面带编号的证据回答。

接地与拒答规则（必须遵守）：
1. 证据之外的事实一句都不要写，包括通用常识、行业惯例、你自己掌握的背景知识。
2. 「证据里没提到」不等于「不存在」；除非证据明确写了，不要断言某事物不存在、没有或做不到。
3. 问题要的那项事实证据里没有，就把【资料中没有相关信息】放在回答第一行，再用一句话说明已查到什么、还缺什么。
4. 问题包含多个子问题时逐个处理：有证据的部分照常作答并标注 [编号]，缺证据的那一部分以【资料中没有相关信息】开头说明。
5. 相关句子后用 [编号] 标注依据，例如 [1]；不同来源矛盾时说明矛盾，不要自行取舍。
6. 「没有对话历史」不是拒答理由：指代本系统自身的提问按证据作答，只有证据里确实没有那项事实才按第 3 条拒答。
7. 对话历史里的事实（如上一轮回答中的价格、时间、数量等）要写进回答必须先查证据——不能直接引用历史里的数据。
{missing_rule}{history_section}证据：
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
    return f"对话历史（仅用于理解当前问题的指代，不能作为事实依据）：\n{text}\n\n"


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
    *,
    missing: str = "",
) -> str:
    # 规划器已判定某一子问题缺证据时，把缺口写进提示词：哨兵从「可选」变成硬要求
    missing_rule = ""
    gap = " ".join(str(missing or "").split())
    if gap:
        missing_rule = (
            f"本轮已判定证据不足的部分：{gap[:200]}\n"
            "上面第 3、4 条对这一部分是硬要求：回答第一行必须是【资料中没有相关信息】，"
            "说明该项缺失，不得用别的内容替代。\n"
        )
    return FINAL_PROMPT.format(
        missing_rule=missing_rule,
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
    missing: str = "",
) -> str:
    """基于已收集的证据生成最终回答；没有证据时不调用模型，直接拒答。"""
    if not evidence.strip():
        return REFUSAL
    llm = ChatOllama(model=model, num_ctx=8192, keep_alive="10m")
    response = llm.invoke(build_final_prompt(question, evidence, history, missing=missing))
    text = _content_text(response.content).strip()
    return text or REFUSAL


def stream_final_answer(
    question: str,
    evidence: str,
    *,
    model: str = AGENT_LLM_MODEL,
    history: Optional[History] = None,
    missing: str = "",
) -> "Iterator[str]":
    """基于已收集的证据流式生成最终回答；没有证据时直接 yield 拒答。"""
    from typing import Iterator

    if not evidence.strip():
        yield REFUSAL
        return
    llm = ChatOllama(model=model, num_ctx=8192, keep_alive="10m")
    prompt = build_final_prompt(question, evidence, history, missing=missing)
    for response in llm.stream(prompt):
        text = _content_text(response.content).strip()
        if text:
            yield text


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


# 模型把 sufficient 写成字符串时的假值写法；缺字段一律按「足够」处理，
# 被截断的 JSON 与老格式因此维持原有行为，不会因为读不到字段就整段拒答。
_FALSE_WORDS = {"false", "no", "0", "off", "否", "假", "不足", "不够"}


def _sufficiency(data: dict) -> dict:
    """取 (sufficient, missing)：证据是否足以支撑问题要的结论，以及缺哪一项。"""
    raw = data.get("sufficient")
    if raw is None:
        sufficient = True
    elif isinstance(raw, str):
        sufficient = raw.strip().lower() not in _FALSE_WORDS
    else:
        sufficient = bool(raw)
    missing = " ".join(str(data.get("missing") or "").split())[:200]
    return {"sufficient": sufficient, "missing": missing}


def parse_decision(data: dict) -> tuple[str, dict, str, str]:
    """从步进 JSON 中取 (action, payload, reason, answer)。"""
    action = str(data.get("action") or "").strip().lower()
    if action not in {"tool", "final"}:
        # 有的模型会直接回一段文本，当成最终回答处理
        answer = " ".join(str(data.get("answer") or "").split())
        return (
            ("final", {"sufficient": True, "missing": ""}, "", answer)
            if answer
            else ("final", {"sufficient": True, "missing": ""}, "", "")
        )
    if action == "final":
        answer = str(data.get("answer") or "").strip()
        reason = " ".join(str(data.get("reason") or "").split())[:300]
        return "final", _sufficiency(data), reason, answer
    tool = str(data.get("tool") or "").strip()
    args = _clean_args(data.get("args"))
    reason = " ".join(str(data.get("reason") or "").split())[:300]
    return "tool", {"tool": tool, "args": args}, reason, ""


def heuristic_route(
    question: str,
    tool_names: Sequence[str],
    *,
    extra_hints: Sequence[str] = (),
) -> tuple[str, dict, str]:
    """模型不可用时的回退路由：靠关键词在知识库与数据库之间选一条路。

    `extra_hints` 传库内数据目录里的实体与列名，口语化问题（「香蕉能不能放冰箱」）
    也能落到 sql_query，不再依赖「多少/排名」这类数字词。
    """
    text = (question or "").lower()
    hints = list(DATABASE_HINTS) + [str(hint).lower() for hint in extra_hints if str(hint).strip()]
    hits = [hint for hint in hints if hint and hint in text]
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
