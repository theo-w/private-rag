"""v7 agentic 循环：意图路由 -> 工具调用 -> 读观察 -> 再决策 -> 带引用作答。

三条边界：
1. 步数上限（AGENT_MAX_STEPS），防止模型反复调用工具停不下来；
2. 证据字符预算（AGENT_CONTEXT_CHARS），超限就停止取料、直接作答；
3. 每一步都写进 AgentTrace，落盘后可逐条回放「为什么这么选」。

模型不可用时不会整体失败：路由回退到关键词启发式，最终回答仍基于已收集证据生成，
一条证据都没有时按既有约定返回拒答文本。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from sqlalchemy import create_engine, inspect as sa_inspect, text

from private_rag.agent.planner import LlmPlanner, Planner, RouteDecision
from private_rag.agent.prompts import generate_final_answer, heuristic_route, stream_final_answer
from private_rag.agent.sql_guard import extract_tables
from private_rag.agent.tools import (
    KnowledgeSearchTool,
    SqlQueryTool,
    Tool,
    ToolBlock,
    ToolRegistry,
    ToolResult,
    render_evidence,
)
from private_rag.agent.trace import AgentStep, AgentTrace, new_trace_id
from private_rag.config import (
    AGENT_CATALOG_CHARS,
    AGENT_CATALOG_SAMPLES,
    AGENT_CONTEXT_CHARS,
    AGENT_LLM_MODEL,
    AGENT_MAX_STEPS,
    AGENT_SQL_ROWS,
    AGENT_TRACE_ENABLED,
    AGENT_TRACE_PATH,
    DEFAULT_SOURCES_CONFIG,
    TOP_K_RERANK,
)
from private_rag.generation import REFUSAL, History
from private_rag.ingestion.sql import redact_dsn
from private_rag.models import Citation
from private_rag.qa import RetrievalOptions
from private_rag.sources import ConfigError, SqlSource, load_sources_config

logger = logging.getLogger(__name__)

MAX_SQL_HINT_CHARS = 400


@dataclass
class AgentOptions:
    """一次 agentic 问答的选项；检索相关字段透传给 RagEngine。"""

    max_steps: int = AGENT_MAX_STEPS
    allow_sql: bool = True
    top_k: int = TOP_K_RERANK
    source_type: Optional[str] = None
    tags: Optional[list[str]] = None
    use_lexical: bool = True
    use_rerank: bool = True
    use_parent: bool = True
    query_rewrite: str = "off"

    def to_retrieval_options(self) -> RetrievalOptions:
        return RetrievalOptions(
            source_type=self.source_type,
            tags=self.tags,
            top_k=self.top_k,
            use_lexical=self.use_lexical,
            use_rerank=self.use_rerank,
            use_parent=self.use_parent,
            query_rewrite=self.query_rewrite,
        )


@dataclass
class AgentResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    intent: str = ""
    stop_reason: str = ""
    tool_calls: int = 0
    steps: list[AgentStep] = field(default_factory=list)
    trace_id: str = ""
    evidence_blocks: int = 0
    context_chars: int = 0
    elapsed_ms: int = 0
    trace: Optional[AgentTrace] = None
    contexts: list[str] = field(default_factory=list)  # 原始证据块文本列表，供评估等下游使用


def _evidence_chars(blocks: Sequence[ToolBlock]) -> int:
    return sum(len(block.text) + len(block.label) + 8 for block in blocks)


def _refusal_text(missing: str = "") -> str:
    """拒答文本；能定位到缺哪一项就写清缺什么，用户才知道该补什么。"""
    gap = " ".join(str(missing or "").split())
    return REFUSAL if not gap else f"{REFUSAL}\n缺少的信息：{gap[:200]}"


# 样例值送进模型时的单值长度上限：一整个描述段落塞进工具描述毫无价值
CATALOG_VALUE_CHARS = 60
CATALOG_TERM_MAX_CHARS = 24
CATALOG_TERMS_MAX = 40


def _is_textual_column(column: dict) -> bool:
    type_name = str(column.get("type") or "").lower()
    return any(word in type_name for word in ("char", "text", "clob", "string"))


def _sample_values(connection: Any, table: str, column: str, limit: int) -> list[str]:
    """取该列前 limit 个非空值。

    刻意不排序、不去重：数据库扫到 limit 行就停，换成真有数据的库也不会
    把「给路由看一眼」变成一次全表扫描。去重在这里按原顺序做。
    """
    sql = text(
        f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT {int(limit)}'
    )
    try:
        rows = list(connection.execute(sql).fetchmany(limit))
    except Exception:
        return []
    values: list[str] = []
    for row in rows:
        value = " ".join(str(row[0] or "").split())
        if value and value not in values:
            values.append(value)
    return values


def _data_catalog(
    dsn: str,
    tables: Sequence[str],
    *,
    max_samples: int = AGENT_CATALOG_SAMPLES,
    max_chars: int = AGENT_CATALOG_CHARS,
) -> tuple[str, list[str]]:
    """探测白名单表的列名与少量样例值，用于回答「问题要的事实到底在文档还是在表里」。

    返回值同时给两处用：目录文本进工具描述（模型看得到），短词列表进回退路由
    （模型不可用时，口语化问题也能靠库内实体名落到 sql_query）。
    探测失败就返回空，装配不该因为一个源库连不上而整体失败。
    """
    try:
        engine = create_engine(dsn)
    except Exception:
        return "", []
    parts: list[str] = []
    terms: list[str] = []
    try:
        inspector = sa_inspect(engine)
        with engine.connect() as connection:
            for table in tables:
                try:
                    if not inspector.has_table(table):
                        continue
                    columns = list(inspector.get_columns(table))
                except Exception:
                    continue
                names = [str(column.get("name")) for column in columns][:14]
                if not names:
                    continue
                parts.append(f"{table} 列({', '.join(names)})")
                for name in names:
                    if name not in terms:
                        terms.append(name)
                if max_samples <= 0:
                    continue
                for column in columns:
                    name = str(column.get("name"))
                    if not name or not _is_textual_column(column):
                        continue
                    values = _sample_values(connection, table, name, max_samples)
                    if not values:
                        continue
                    shown = [
                        value if len(value) <= CATALOG_VALUE_CHARS else value[:CATALOG_VALUE_CHARS] + "…"
                        for value in values
                    ]
                    parts.append(f"{table}.{name} 取值：{' / '.join(shown)}")
                    for value in values:
                        if len(value) <= CATALOG_TERM_MAX_CHARS and value not in terms:
                            terms.append(value)
                if len(";".join(parts)) >= max_chars:
                    break
    except Exception:
        return "", []
    finally:
        engine.dispose()

    kept: list[str] = []
    for part in parts:
        if kept and len(";".join(kept + [part])) > max_chars:
            break
        kept.append(part)
    return "；".join(kept), terms[:CATALOG_TERMS_MAX]


class AgentRuntime:
    """检索-行动循环的编排者。工具与决策器都可注入，便于测试与替换模型。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        planner: Optional[Planner] = None,
        model: str = AGENT_LLM_MODEL,
        trace_path: Path = AGENT_TRACE_PATH,
        trace_enabled: bool = AGENT_TRACE_ENABLED,
        max_context_chars: int = AGENT_CONTEXT_CHARS,
    ):
        self.registry = registry
        self.planner = planner
        self.model = model
        self.trace_path = Path(trace_path)
        self.trace_enabled = trace_enabled
        self.max_context_chars = max(1000, max_context_chars)

    def _available(self, allow_sql: bool) -> ToolRegistry:
        """按选项过滤工具；关掉 SQL 时模型根本看不到这个工具。"""
        if allow_sql:
            return self.registry
        # 按名字摘除而不是按类型：模型只认名字，任何自称 sql_query 的工具都该一并摘掉
        kept = [tool for tool in self.registry.tools() if tool.name != SqlQueryTool.name]
        return ToolRegistry(kept)

    def _route_hints(self, registry: ToolRegistry) -> list[str]:
        """回退路由可用的库内线索（列名与短实体值）。"""
        hints: list[str] = []
        for tool in registry.tools():
            for term in getattr(tool, "catalog_terms", []) or []:
                text_term = str(term).strip()
                if text_term and text_term not in hints:
                    hints.append(text_term)
        return hints

    def run(
        self,
        question: str,
        *,
        options: Optional[AgentOptions] = None,
        history: Optional[History] = None,
        request_id: str = "",
        session_id: str = "",
    ) -> AgentResult:
        options = options or AgentOptions()
        max_steps = max(1, int(options.max_steps))
        started = time.perf_counter()

        registry = self._available(options.allow_sql)
        specs = registry.describe()
        route_hints = self._route_hints(registry)
        planner = self.planner or LlmPlanner(self.model)

        trace = AgentTrace(
            trace_id=new_trace_id(),
            question=question,
            model=self.model,
            created_at=time.time(),
            request_id=request_id,
            session_id=session_id,
        )
        blocks: list[ToolBlock] = []
        observations: list[str] = []
        tool_calls = 0
        step_index = 0
        stop_reason = ""
        forced_answer = ""
        final_reason = "基于已收集的证据作答"
        # 规划器宣布作答时的充分性判断：insufficient 直接拒答，partial 把缺口带进最终提示词
        sufficient = True
        missing_part = ""

        def add_step(**kwargs: Any) -> AgentStep:
            nonlocal step_index
            step_index += 1
            step = AgentStep(index=step_index, **kwargs)
            trace.add(step)
            return step

        # 第一步：意图路由，顺带给出首个动作
        route_started = time.perf_counter()
        try:
            route = planner.route(question, specs, history)
            add_step(
                kind="route",
                tool=route.tool,
                args=dict(route.args),
                reason=route.reason or "模型给出路由",
                observation=f"intent={route.intent}",
                elapsed_ms=int((time.perf_counter() - route_started) * 1000),
            )
        except Exception as exc:
            intent, action, reason = heuristic_route(question, registry.names(), extra_hints=route_hints)
            route = RouteDecision(intent=intent, action=action, reason=reason, fallback=True)
            add_step(
                kind="route",
                tool=str(action.get("tool") or ""),
                args=dict(action.get("args") or {}),
                reason=reason,
                # 连底层原因一起记下：只留异常类名时，事后看不出是模型超时还是 JSON 被截断
                observation=trace.record_observation(
                    f"决策模型不可用，回退关键词路由：{type(exc).__name__}: {exc}"
                ),
                elapsed_ms=int((time.perf_counter() - route_started) * 1000),
            )
        intent = route.intent

        # 模型给了意图却没给出可用动作时，用启发式补一个，避免空跑一轮
        if intent != "none" and registry.get(route.tool) is None:
            intent, action, reason = heuristic_route(question, registry.names(), extra_hints=route_hints)
            route = RouteDecision(intent=intent, action=action, reason=reason, fallback=True)
            add_step(kind="stop", reason=reason, observation=f"改用 {route.tool or '无'}")
        if intent == "none" or not route.tool:
            stop_reason = "no_retrieval"

        pending: Optional[RouteDecision] = route if (intent != "none" and route.tool) else None

        # 行动循环：每一步要么执行工具，要么宣布可以作答。
        # 工具调用次数受 max_steps 约束；guard 兜住「反复给出无效工具名」的空转。
        guard = 0
        while stop_reason != "no_retrieval":
            guard += 1
            if tool_calls >= max_steps or guard > max_steps + 3:
                stop_reason = stop_reason or "max_steps"
                break

            if pending is not None:
                tool_name, args, reason = pending.tool, dict(pending.args), pending.reason
                pending = None
            else:
                try:
                    evidence_text, _ = render_evidence(blocks)
                    decision = planner.decide(
                        question,
                        specs,
                        evidence_text,
                        observations,
                        history,
                        steps_used=step_index,
                        max_steps=max_steps,
                    )
                except Exception as exc:
                    stop_reason = "planner_unavailable"
                    add_step(
                        kind="error",
                        ok=False,
                        error=f"步进决策失败：{type(exc).__name__}",
                        reason="无法继续规划",
                    )
                    break
                if decision.action == "final":
                    forced_answer = decision.answer.strip()
                    final_reason = decision.reason or "模型认为证据已足够"
                    sufficient = bool(decision.sufficient)
                    missing_part = decision.missing.strip()
                    stop_reason = "final"
                    break
                tool_name, args, reason = decision.tool, dict(decision.args), decision.reason

            tool = registry.get(tool_name)
            if tool is None:
                reason_text = f"工具 {tool_name or '未指定'} 不在可用列表，已跳过"
                observations.append(f"步骤{step_index + 1}：{reason_text}")
                add_step(kind="error", ok=False, tool=tool_name, args=args, reason=reason, error=reason_text)
                continue

            tool_started = time.perf_counter()
            try:
                result = tool.run(args)
            except Exception as exc:
                result = ToolResult(False, error=f"工具执行异常：{type(exc).__name__}")
            elapsed_ms = int((time.perf_counter() - tool_started) * 1000)

            tool_calls += 1
            blocks.extend(result.blocks)
            executed_sql = str(result.data.get("sql") or "")
            # 回放时要能看到护栏改写后真正跑出去的 SQL，而不是模型原本想跑的那条
            sql_hint = f"（实际执行 {executed_sql[:MAX_SQL_HINT_CHARS]}）" if executed_sql else ""
            observation = f"{tool.name}：{result.observation or '无返回'}{sql_hint}"
            observations.append(f"步骤{step_index + 1}：{observation}")
            add_step(
                kind="tool",
                tool=tool.name,
                args=args,
                reason=reason,
                ok=result.ok,
                observation=trace.record_observation(f"{result.observation or result.error}{sql_hint}"),
                rows=result.rows,
                evidence_blocks=len(blocks),
                elapsed_ms=elapsed_ms,
                error=result.error,
            )

            if _evidence_chars(blocks) >= self.max_context_chars:
                stop_reason = "context_budget"
                add_step(
                    kind="stop",
                    reason="证据字符达到预算上限",
                    observation=f"已收集 {len(blocks)} 段证据",
                )
                break

        evidence_text, citations = render_evidence(blocks)
        if not sufficient:
            # 问题要的那项事实不在证据里：按既有约定拒答，不再让模型补一段看着像的答案
            answer = _refusal_text(missing_part)
            stop_reason = "insufficient_evidence"
        elif forced_answer:
            answer = forced_answer
        elif not citations:
            answer = REFUSAL
            stop_reason = stop_reason or "no_evidence"
        else:
            try:
                answer = generate_final_answer(
                    question,
                    evidence_text,
                    model=self.model,
                    history=history,
                    missing=missing_part,
                )
            except Exception as exc:
                logger.warning("最终回答生成失败 trace_id=%s：%s", trace.trace_id, exc)
                answer = REFUSAL
                stop_reason = "generation_failed"

        add_step(kind="final", reason=final_reason, observation=trace.record_observation(answer))

        trace.intent = intent
        trace.answer = answer
        trace.citations = [asdict(citation) for citation in citations]
        trace.stop_reason = stop_reason or "completed"
        trace.tool_calls = tool_calls
        trace.elapsed_ms = int((time.perf_counter() - started) * 1000)

        if self.trace_enabled:
            try:
                trace.save(self.trace_path)
            except Exception as exc:
                logger.warning("决策轨迹落盘失败 trace_id=%s：%s", trace.trace_id, exc)

        contexts = [block.text for block in blocks]
        return AgentResult(
            answer=answer,
            citations=citations,
            intent=intent,
            stop_reason=trace.stop_reason,
            tool_calls=tool_calls,
            steps=list(trace.steps),
            trace_id=trace.trace_id,
            evidence_blocks=len(citations),
            context_chars=_evidence_chars(blocks),
            elapsed_ms=trace.elapsed_ms,
            trace=trace,
            contexts=contexts,
        )

    def run_stream(
        self,
        question: str,
        *,
        options: Optional[AgentOptions] = None,
        history: Optional[History] = None,
        request_id: str = "",
        session_id: str = "",
    ) -> Iterator[dict]:
        """SSE 流式 agentic 问答。yield 字典，每个有 'event' 和 'data' 键。

        事件顺序：session → route → step（多次）→ citations → token（多次）→ done
        """
        options = options or AgentOptions()
        max_steps = max(1, int(options.max_steps))
        started = time.perf_counter()

        registry = self._available(options.allow_sql)
        specs = registry.describe()
        route_hints = self._route_hints(registry)
        planner = self.planner or LlmPlanner(self.model)

        trace = AgentTrace(
            trace_id=new_trace_id(),
            question=question,
            model=self.model,
            created_at=time.time(),
            request_id=request_id,
            session_id=session_id,
        )
        blocks: list[ToolBlock] = []
        observations: list[str] = []
        tool_calls = 0
        step_index = 0
        stop_reason = ""
        forced_answer = ""
        final_reason = "基于已收集的证据作答"
        # 规划器宣布作答时的充分性判断：insufficient 直接拒答，partial 把缺口带进最终提示词
        sufficient = True
        missing_part = ""

        def add_step(**kwargs: Any) -> AgentStep:
            nonlocal step_index
            step_index += 1
            step = AgentStep(index=step_index, **kwargs)
            trace.add(step)
            return step

        def yield_event(event: str, data: Any) -> Iterator[dict]:
            yield {"event": event, "data": data}

        # session 事件
        yield from yield_event("session", {"session_id": session_id, "request_id": request_id})

        # 意图路由
        route_started = time.perf_counter()
        try:
            route = planner.route(question, specs, history)
            add_step(
                kind="route",
                tool=route.tool,
                args=dict(route.args),
                reason=route.reason or "模型给出路由",
                observation=f"intent={route.intent}",
                elapsed_ms=int((time.perf_counter() - route_started) * 1000),
            )
            yield from yield_event("route", {
                "intent": route.intent,
                "tool": route.tool,
                "reason": route.reason or "",
                "elapsed_ms": int((time.perf_counter() - route_started) * 1000),
            })
        except Exception as exc:
            intent, action, reason = heuristic_route(question, registry.names(), extra_hints=route_hints)
            route = RouteDecision(intent=intent, action=action, reason=reason, fallback=True)
            add_step(
                kind="route",
                tool=str(action.get("tool") or ""),
                args=dict(action.get("args") or {}),
                reason=reason,
                observation=trace.record_observation(
                    f"决策模型不可用，回退关键词路由：{type(exc).__name__}: {exc}"
                ),
                elapsed_ms=int((time.perf_counter() - route_started) * 1000),
            )
            yield from yield_event("route", {
                "intent": intent,
                "tool": action.get("tool", ""),
                "reason": reason,
                "elapsed_ms": int((time.perf_counter() - route_started) * 1000),
            })
        intent = route.intent

        if intent != "none" and registry.get(route.tool) is None:
            intent, action, reason = heuristic_route(question, registry.names(), extra_hints=route_hints)
            route = RouteDecision(intent=intent, action=action, reason=reason, fallback=True)
            add_step(kind="stop", reason=reason, observation=f"改用 {route.tool or '无'}")
        if intent == "none" or not route.tool:
            stop_reason = "no_retrieval"

        pending: Optional[RouteDecision] = route if (intent != "none" and route.tool) else None

        # 行动循环
        guard = 0
        while stop_reason != "no_retrieval":
            guard += 1
            if tool_calls >= max_steps or guard > max_steps + 3:
                stop_reason = stop_reason or "max_steps"
                break

            if pending is not None:
                tool_name, args, reason = pending.tool, dict(pending.args), pending.reason
                pending = None
            else:
                try:
                    evidence_text, _ = render_evidence(blocks)
                    decision = planner.decide(
                        question,
                        specs,
                        evidence_text,
                        observations,
                        history,
                        steps_used=step_index,
                        max_steps=max_steps,
                    )
                except Exception as exc:
                    stop_reason = "planner_unavailable"
                    add_step(
                        kind="error",
                        ok=False,
                        error=f"步进决策失败：{type(exc).__name__}",
                        reason="无法继续规划",
                    )
                    yield from yield_event("error", {
                        "code": "planner_error",
                        "message": f"步进决策失败：{exc}",
                        "request_id": request_id,
                    })
                    break
                if decision.action == "final":
                    forced_answer = decision.answer.strip()
                    final_reason = decision.reason or "模型认为证据已足够"
                    sufficient = bool(decision.sufficient)
                    missing_part = decision.missing.strip()
                    stop_reason = "final"
                    break
                tool_name, args, reason = decision.tool, dict(decision.args), decision.reason

            tool = registry.get(tool_name)
            if tool is None:
                reason_text = f"工具 {tool_name or '未指定'} 不在可用列表，已跳过"
                observations.append(f"步骤{step_index + 1}：{reason_text}")
                add_step(kind="error", ok=False, tool=tool_name, args=args, reason=reason, error=reason_text)
                continue

            tool_started = time.perf_counter()
            try:
                result = tool.run(args)
            except Exception as exc:
                result = ToolResult(False, error=f"工具执行异常：{type(exc).__name__}")
            elapsed_ms = int((time.perf_counter() - tool_started) * 1000)

            tool_calls += 1
            blocks.extend(result.blocks)
            executed_sql = str(result.data.get("sql") or "")
            sql_hint = f"（实际执行 {executed_sql[:MAX_SQL_HINT_CHARS]}）" if executed_sql else ""
            observation = f"{tool.name}：{result.observation or '无返回'}{sql_hint}"
            observations.append(f"步骤{step_index + 1}：{observation}")
            add_step(
                kind="tool",
                tool=tool.name,
                args=args,
                reason=reason,
                ok=result.ok,
                observation=trace.record_observation(f"{result.observation or result.error}{sql_hint}"),
                rows=result.rows,
                evidence_blocks=len(blocks),
                elapsed_ms=elapsed_ms,
                error=result.error,
            )
            yield from yield_event("step", {
                "index": step_index,
                "tool": tool.name,
                "args": args,
                "reason": reason,
                "ok": result.ok,
                "rows": result.rows,
                "evidence_blocks": len(blocks),
                "elapsed_ms": elapsed_ms,
            })

            if _evidence_chars(blocks) >= self.max_context_chars:
                stop_reason = "context_budget"
                add_step(
                    kind="stop",
                    reason="证据字符达到预算上限",
                    observation=f"已收集 {len(blocks)} 段证据",
                )
                break

        evidence_text, citations = render_evidence(blocks)
        yield from yield_event("citations", {
            "citations": [asdict(c) for c in citations],
            "retrieved": len(citations),
            "context_chars": _evidence_chars(blocks),
        })

        if not sufficient:
            answer = _refusal_text(missing_part)
            stop_reason = "insufficient_evidence"
        elif forced_answer:
            answer = forced_answer
        elif not citations:
            answer = REFUSAL
            stop_reason = stop_reason or "no_evidence"
        else:
            try:
                # 流式生成最终回答
                answer_parts: list[str] = []
                for token_text in stream_final_answer(
                    question,
                    evidence_text,
                    model=self.model,
                    history=history,
                    missing=missing_part,
                ):
                    answer_parts.append(token_text)
                    yield from yield_event("token", {"text": token_text})
                answer = "".join(answer_parts).strip() or REFUSAL
            except Exception as exc:
                logger.warning("最终回答生成失败 trace_id=%s：%s", trace.trace_id, exc)
                answer = REFUSAL
                stop_reason = "generation_failed"
                yield from yield_event("error", {
                    "code": "generation_error",
                    "message": str(exc),
                    "request_id": request_id,
                })

        add_step(kind="final", reason=final_reason, observation=trace.record_observation(answer))

        trace.intent = intent
        trace.answer = answer
        trace.citations = [asdict(citation) for citation in citations]
        trace.stop_reason = stop_reason or "completed"
        trace.tool_calls = tool_calls
        trace.elapsed_ms = int((time.perf_counter() - started) * 1000)

        if self.trace_enabled:
            try:
                trace.save(self.trace_path)
            except Exception as exc:
                logger.warning("决策轨迹落盘失败 trace_id=%s：%s", trace.trace_id, exc)

        meta = {
            "intent": intent,
            "stop_reason": trace.stop_reason,
            "tool_calls": tool_calls,
            "steps": step_index,
            "evidence_blocks": len(citations),
            "context_chars": _evidence_chars(blocks),
            "elapsed_ms": trace.elapsed_ms,
            "trace_id": trace.trace_id,
        }

        yield from yield_event("done", {
            "answer": answer,
            "citations": [asdict(c) for c in citations],
            "meta": meta,
            "request_id": request_id,
        })


def _schema_hint(dsn: str, tables: Sequence[str], max_columns: int = 14) -> str:
    """探测白名单表的列名并写进工具描述，模型才写得出合法的 SQL。"""
    try:
        engine = create_engine(dsn)
    except Exception:
        return ""
    try:
        inspector = sa_inspect(engine)
        parts: list[str] = []
        for table in tables:
            try:
                if not inspector.has_table(table):
                    continue
                columns = [str(col["name"]) for col in inspector.get_columns(table)]
            except Exception:
                continue
            if columns:
                parts.append(f"{table}({', '.join(columns[:max_columns])})")
        return "；".join(parts)
    finally:
        engine.dispose()


def build_agent_runtime(
    engine: Any,
    *,
    sources_path: Optional[Path] = None,
    planner: Optional[Planner] = None,
    model: str = AGENT_LLM_MODEL,
    sql_rows: int = AGENT_SQL_ROWS,
    allow_sql: bool = True,
) -> AgentRuntime:
    """按 sources.yaml 装配工具：知识库检索始终可用，运行时 SQL 需显式开启。"""
    tools: list[Tool] = [KnowledgeSearchTool(engine)]

    path = Path(sources_path) if sources_path is not None else Path(DEFAULT_SOURCES_CONFIG)
    if allow_sql and path.exists():
        try:
            config = load_sources_config(path)
        except ConfigError as exc:
            logger.warning("读取 sources 配置失败，运行时 SQL 不可用：%s", exc)
        else:
            for source in config.sources:
                if not isinstance(source, SqlSource) or not source.allow_runtime_query:
                    continue
                tables = [str(name) for name in source.runtime_tables] or sorted(
                    extract_tables(source.query)
                )
                if not tables:
                    logger.warning("跳过 SQL 源 %s：无法确定可查表", source.label or redact_dsn(source.dsn))
                    continue
                label = source.label or redact_dsn(source.dsn)
                catalog, catalog_terms = _data_catalog(source.dsn, tables)
                tools.append(
                    SqlQueryTool(
                        dsn=source.dsn,
                        label=label,
                        allowed_tables=tables,
                        max_rows=sql_rows,
                        schema_hint=_schema_hint(source.dsn, tables),
                        catalog=catalog,
                        catalog_terms=catalog_terms,
                    )
                )

    return AgentRuntime(ToolRegistry(tools), planner=planner, model=model)
