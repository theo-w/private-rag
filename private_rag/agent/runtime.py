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
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from sqlalchemy import create_engine, inspect as sa_inspect, make_url, text

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
    AGENT_CATALOG_ENUM,
    AGENT_CATALOG_SAMPLES,
    AGENT_CATALOG_SCAN_ROWS,
    AGENT_CONTEXT_CHARS,
    AGENT_GATE_SIDE_TRACKING,
    AGENT_LLM_MODEL,
    AGENT_MAX_STEPS,
    AGENT_MIXED_GATE,
    AGENT_NONE_PROBE,
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
from private_rag.sources import ConfigError, SqlSource, load_sources_config, normalize_sqlite_dsn

logger = logging.getLogger(__name__)

MAX_SQL_HINT_CHARS = 400

# 配置缺失时直接写进日志、可照抄执行的修复命令（路径相对仓库根目录）
SOURCES_FIX_COMMAND = (
    "RAG_AGENT_SOURCES_CONFIG=config/sources.demo.yaml python3 scripts/serve.py --port 8001"
)


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
    # 修法 C 门控拦截 final 的次数；评估用它算 gate_intercept_rate
    gate_intercepts: int = 0


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
# 长文本列在目录里只留这么多个例子：路由器需要知道「这一列存说明文字」，不需要读完整段落
CATALOG_LONG_VALUE_EXAMPLES = 1
# 旧行为每列只看前 4 行——改默认值之前就是这个数，退回旧行为时要连这个一起退，
# 否则「旧行为」其实只退了组织方式，取值条数还是新的
LEGACY_CATALOG_ROWS = 4
# 纯数字与日期时间不进目录也不进回退线索：问题不会用「2026-01-10」指认一条记录，
# 而它们会把目录的字符预算和线索名额占掉
_NUMBERISH_RE = re.compile(r"[\d\s.,:%+-]+")


def _is_numberish(value: str) -> bool:
    return bool(_NUMBERISH_RE.fullmatch(str(value or "")))


def _add_term(terms: list[str], term: str) -> None:
    if term and term not in terms:
        terms.append(term)


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


def _catalog_lines_for_table(
    connection: Any,
    table: str,
    columns: Sequence[dict],
    *,
    max_samples: int,
    scan_rows: int,
    enumerate_values: bool,
) -> tuple[list[str], list[str]]:
    """一张表的目录行与回退线索短词。

    两种组织方式：
    - 枚举（默认）：短取值（实体名、类别、储存方式这类）尽量列全，长文本列只留一个例子，
      数字与日期时间整列跳过。路由靠的就是「问题里的名词在不在库内取值里」，
      列全实体名比多抄一段说明文字有用得多。
    - 旧行为（``RAG_DISABLE_AGENT_CATALOG_ENUM=1``）：每列取前 ``max_samples`` 个值照抄，
      长文本也抄、数字日期也进目录。保留它是为了让前后对照能在同一进程里交替跑，
      也给换成大表时留一个退路。
    """
    lines: list[str] = []
    prose_lines: list[str] = []
    terms: list[str] = []
    for column in columns:
        name = str(column.get("name"))
        if not name or not _is_textual_column(column):
            continue
        values = _sample_values(
            connection,
            table,
            name,
            scan_rows if enumerate_values else min(max_samples, LEGACY_CATALOG_ROWS),
        )
        if not values:
            continue
        if not enumerate_values:
            shown = [
                value if len(value) <= CATALOG_VALUE_CHARS else value[:CATALOG_VALUE_CHARS] + "…"
                for value in values
            ]
            lines.append(f"{table}.{name} 取值：{' / '.join(shown)}")
            for value in values:
                if len(value) <= CATALOG_TERM_MAX_CHARS:
                    _add_term(terms, value)
            continue
        short = [
            value
            for value in values
            if len(value) <= CATALOG_TERM_MAX_CHARS and not _is_numberish(value)
        ]
        if short:
            shown = short[:max_samples]
            lines.append(f"{table}.{name} 取值样例：{' / '.join(shown)}")
            terms.extend(shown)
        long_values = [value for value in values if len(value) > CATALOG_TERM_MAX_CHARS]
        if long_values:
            example = long_values[0][:CATALOG_VALUE_CHARS]
            prose_lines.append(f"{table}.{name} 存长文本，例：{example}")
    # 长文本例子排在最后：字符预算不够时先牺牲它，实体名单要保住
    return lines + prose_lines[:CATALOG_LONG_VALUE_EXAMPLES], terms


def _data_catalog(
    dsn: str,
    tables: Sequence[str],
    *,
    max_samples: Optional[int] = None,
    max_chars: Optional[int] = None,
    scan_rows: Optional[int] = None,
    enumerate_values: Optional[bool] = None,
) -> tuple[str, list[str]]:
    """探测白名单表的列名与库内取值，用于回答「问题要的事实到底在文档还是在表里」。

    返回值同时给两处用：目录文本进工具描述（模型看得到），短词列表进回退路由
    （模型不可用时，口语化问题也能靠库内实体名落到 sql_query）。
    探测失败就返回空，装配不该因为一个源库连不上而整体失败。

    预算是「扫多少行」而不是「取多少个值」：只扫前几行的话，实体名在后面行里的记录
    永远进不了目录，路由器会按第 2 条把这类问题判成 knowledge（v8.7 扩充集 8 条数据侧
    样本全部误判，路由器当时的理由就是「name 取值不含蓝莓」）。
    仍然有上限：每列只扫前 ``scan_rows`` 行，更大的表里这些行以外的实体看不到，
    那种情形要靠路由提示词里的「拿不准就选 mixed」兜底，不是靠目录列全。
    """
    # 默认值在调用时读模块属性，而不是写进参数默认值：参数默认值在导入时就定死了，
    # 同一进程里换设置（前后对照脚本要这么干）不会生效。
    if max_samples is None:
        max_samples = AGENT_CATALOG_SAMPLES
    if max_chars is None:
        max_chars = AGENT_CATALOG_CHARS
    if scan_rows is None:
        scan_rows = AGENT_CATALOG_SCAN_ROWS
    if enumerate_values is None:
        enumerate_values = AGENT_CATALOG_ENUM

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
                for name in names:
                    _add_term(terms, name)
                if max_samples <= 0:
                    continue
                if not enumerate_values:
                    # 旧行为把列名再抄一遍（当时没有「取值样例」这层信息，靠它说明表结构）
                    parts.append(f"{table} 列({', '.join(names)})")
                # 枚举模式下不再重复列名：工具描述里的「可查列」已经写了同一张表的列
                lines, table_terms = _catalog_lines_for_table(
                    connection,
                    table,
                    columns,
                    max_samples=max_samples,
                    scan_rows=scan_rows,
                    enumerate_values=enumerate_values,
                )
                parts.extend(lines)
                for term in table_terms:
                    _add_term(terms, term)
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
        none_probe: bool = AGENT_NONE_PROBE,
        intent: str = "",
        mixed_gate: bool = AGENT_MIXED_GATE,
        gate_side_tracking: Optional[bool] = None,
    ):
        self.registry = registry
        self.planner = planner
        self.model = model
        self.trace_path = Path(trace_path)
        self.trace_enabled = trace_enabled
        self.max_context_chars = max(1000, max_context_chars)
        # 路由器判 none 时是否先做一次验证检索（关掉就退回判 none 直接拒答）
        self.none_probe = bool(none_probe)
        # 修法 C：双侧查询门控——mixed 意图下代码级禁止一侧零证据就选 final
        self.intent = str(intent or "")
        self.mixed_gate = bool(mixed_gate)
        # mixed 门控认侧别的依据，同上：在装配时才读模块设置，同一进程里换设置才生效
        self.gate_side_tracking = (
            AGENT_GATE_SIDE_TRACKING if gate_side_tracking is None else bool(gate_side_tracking)
        )

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

    def _has_evidence_for(self, blocks: list[ToolBlock], tool_name: str) -> bool:
        """按证据块的标签文字认「这一侧查过没有」——只在门控退回老行为时走这条。

        真实链路里这块认不出来：`sql_query` 的块标签是「SQL 查询（表 demo.products）」，
        `kb_search` 的块标签是引用标题（文档名或页码），两处都不含工具名，子串匹配恒为 False。
        也就是说老行为等于「两侧永远零证据」，每条 mixed 都被反复逼到步数用尽。
        默认路径改看工具执行记录（`AGENT_GATE_SIDE_TRACKING`），这里留着是为了做前后对照。
        """
        label = str(tool_name)
        for block in blocks:
            if label in (block.label or "") or label in (block.text or ""):
                return True
        return False

    def _none_probe(
        self, registry: ToolRegistry, question: str, intent: str
    ) -> Optional[RouteDecision]:
        """把路由器判成的 none 变成一次待验证检索。

        none 只是模型「猜查不到」，而拒答的正当理由只有「查过了，证据里没有」。所以知识库可用时
        先拿原问题检索一次：查到东西照常作答，查不到再拒答，`stop_reason` 也从 `no_retrieval`
        变成 `no_evidence`。知识库不可用或显式关掉探测时，仍走原来的直接拒答。
        """
        if not self.none_probe or not str(question or "").strip():
            return None
        if registry.get(KnowledgeSearchTool.name) is None:
            return None
        return RouteDecision(
            intent=intent,
            action={"tool": KnowledgeSearchTool.name, "args": {"query": str(question).strip()}},
            reason="路由判 none，先检索验证一次再决定是否拒答",
        )

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
        # 是否由 none 探测检索带出来的证据，用于如实回报最终按哪条路作答
        probed_none = False

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
        self.intent = intent  # 修法 C：门控需要
        gate_intercepts = 0
        # 两侧各查过没有——两道门控都看这两个标记，而不是 `_has_evidence_for`：
        # 真实证据块的标签是「SQL 查询（表 …）」与文档标题，里面没有工具名，
        # 按标签认等于「两侧永远零证据」，那个判断只对测试替身成立
        kb_ran = False
        sql_ran = False

        # 模型给了意图却没给出可用动作时，用启发式补一个，避免空跑一轮
        if intent != "none" and registry.get(route.tool) is None:
            intent, action, reason = heuristic_route(question, registry.names(), extra_hints=route_hints)
            route = RouteDecision(intent=intent, action=action, reason=reason, fallback=True)
            add_step(kind="stop", reason=reason, observation=f"改用 {route.tool or '无'}")
        unverified = intent == "none" or not route.tool
        probe = self._none_probe(registry, question, intent) if unverified else None
        if probe is not None:
            # 不采信没查过的 none：先按探测动作检索一轮，拒答要建立在证据上
            probed_none = True
            pending = probe
        elif unverified:
            stop_reason = "no_retrieval"
            pending = None
        else:
            pending = route

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
                    # 修法 C：双侧查询门控——mixed 意图下有一侧没查过就不许进 final
                    if self.mixed_gate and self.intent == "mixed":
                        if self.gate_side_tracking:
                            kb_has, sql_has = kb_ran, sql_ran
                        else:
                            # 老行为：拿证据块的标签文字找工具名，真实链路里恒为 False
                            kb_has = self._has_evidence_for(blocks, KnowledgeSearchTool.name)
                            sql_has = self._has_evidence_for(blocks, SqlQueryTool.name)
                        missing_tool = ""
                        if not kb_has or not sql_has:
                            missing_tool = KnowledgeSearchTool.name if not kb_has else SqlQueryTool.name
                            if self.gate_side_tracking and registry.get(missing_tool) is None:
                                # 这一侧压根没挂工具（例如关掉了 SQL），逼它只会白烧一步
                                missing_tool = ""
                        if missing_tool:
                            observations.append(
                                f"步骤{step_index + 1}：门控拦截 final——intent=mixed，"
                                f"{missing_tool} 侧还没查过，强制继续取证"
                            )
                            gate_intercepts += 1
                            add_step(
                                kind="gate",
                                reason=f"门控拦截：{missing_tool} 侧没查过",
                                observation=f"intent=mixed，{missing_tool} 侧还没查过",
                            )
                            # 强制下一次循环调用缺失的工具；那一侧跑完以后就算查过了
                            pending = RouteDecision(
                                intent=self.intent,
                                action={"tool": missing_tool, "args": {}},
                                reason=f"修法 C 门控：{missing_tool} 侧没查过，强制取证",
                            )
                            continue

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
            # 「这一侧查过了」看的是工具跑过没有，而不是有没有返回内容：查过但为空也算查过，
            # 再逼一次只会把步数烧光，而「库里确实没有这项事实」本身就是可以作答或拒答的依据
            if tool.name == SqlQueryTool.name:
                sql_ran = True
            elif tool.name == KnowledgeSearchTool.name:
                kb_ran = True
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
        if probed_none and citations:
            # none 只是路由那一刻的判断：查到了东西就按实际作答依据报意图
            intent = "knowledge"
        if not sufficient:
            # 问题要的那项事实不在证据里：按既有约定拒答，不再让模型补一段看着像的答案
            answer = _refusal_text(missing_part)
            stop_reason = "insufficient_evidence"
        elif forced_answer:
            answer = forced_answer
        elif not citations:
            answer = REFUSAL
            if probed_none:
                # none 探测查过了仍没有证据：这是有据的拒答，与压根没检索的 no_retrieval 区分开
                stop_reason = "no_evidence"
            else:
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
            gate_intercepts=gate_intercepts,
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
        # 是否由 none 探测检索带出来的证据，用于如实回报最终按哪条路作答
        probed_none = False

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
        self.intent = intent  # 修法 C：门控需要
        gate_intercepts = 0
        # 两侧各查过没有——两道门控都看这两个标记，而不是 `_has_evidence_for`：
        # 真实证据块的标签是「SQL 查询（表 …）」与文档标题，里面没有工具名，
        # 按标签认等于「两侧永远零证据」，那个判断只对测试替身成立
        kb_ran = False
        sql_ran = False

        if intent != "none" and registry.get(route.tool) is None:
            intent, action, reason = heuristic_route(question, registry.names(), extra_hints=route_hints)
            route = RouteDecision(intent=intent, action=action, reason=reason, fallback=True)
            add_step(kind="stop", reason=reason, observation=f"改用 {route.tool or '无'}")
        unverified = intent == "none" or not route.tool
        probe = self._none_probe(registry, question, intent) if unverified else None
        if probe is not None:
            # 不采信没查过的 none：先按探测动作检索一轮，拒答要建立在证据上
            probed_none = True
            pending = probe
        elif unverified:
            stop_reason = "no_retrieval"
            pending = None
        else:
            pending = route

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
                    # 修法 C：双侧查询门控——mixed 意图下有一侧没查过就不许进 final
                    if self.mixed_gate and self.intent == "mixed":
                        if self.gate_side_tracking:
                            kb_has, sql_has = kb_ran, sql_ran
                        else:
                            # 老行为：拿证据块的标签文字找工具名，真实链路里恒为 False
                            kb_has = self._has_evidence_for(blocks, KnowledgeSearchTool.name)
                            sql_has = self._has_evidence_for(blocks, SqlQueryTool.name)
                        missing_tool = ""
                        if not kb_has or not sql_has:
                            missing_tool = KnowledgeSearchTool.name if not kb_has else SqlQueryTool.name
                            if self.gate_side_tracking and registry.get(missing_tool) is None:
                                # 这一侧压根没挂工具（例如关掉了 SQL），逼它只会白烧一步
                                missing_tool = ""
                        if missing_tool:
                            observations.append(
                                f"步骤{step_index + 1}：门控拦截 final——intent=mixed，"
                                f"{missing_tool} 侧还没查过，强制继续取证"
                            )
                            gate_intercepts += 1
                            add_step(
                                kind="gate",
                                reason=f"门控拦截：{missing_tool} 侧没查过",
                                observation=f"intent=mixed，{missing_tool} 侧还没查过",
                            )
                            # 强制下一次循环调用缺失的工具；那一侧跑完以后就算查过了
                            pending = RouteDecision(
                                intent=self.intent,
                                action={"tool": missing_tool, "args": {}},
                                reason=f"修法 C 门控：{missing_tool} 侧没查过，强制取证",
                            )
                            continue

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
            # 「这一侧查过了」看的是工具跑过没有，而不是有没有返回内容：查过但为空也算查过，
            # 再逼一次只会把步数烧光，而「库里确实没有这项事实」本身就是可以作答或拒答的依据
            if tool.name == SqlQueryTool.name:
                sql_ran = True
            elif tool.name == KnowledgeSearchTool.name:
                kb_ran = True
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

        if probed_none and citations:
            # none 只是路由那一刻的判断：查到了东西就按实际作答依据报意图
            intent = "knowledge"
        if not sufficient:
            answer = _refusal_text(missing_part)
            stop_reason = "insufficient_evidence"
        elif forced_answer:
            answer = forced_answer
        elif not citations:
            answer = REFUSAL
            if probed_none:
                # none 探测查过了仍没有证据：这是有据的拒答，与压根没检索的 no_retrieval 区分开
                stop_reason = "no_evidence"
            else:
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
            "gate_intercepts": gate_intercepts,
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


def _log_missing_sources(path: Path, *, explicit: bool) -> None:
    """sources 配置拿不到时把话说全：缺哪个文件、后果是什么、怎么修。

    默认路径缺失只记 WARNING——只用文档知识库的用法本来就不需要这份配置，服务该照常起；
    用户显式指定了路径却找不到，那是配错了，记 ERROR。两种情况都不让启动失败。
    """
    head = (
        f"指定的 sources 配置不存在：{path}"
        if explicit
        else f"未找到默认 sources 配置：{path}（仓库默认不带这个文件，需要从模板复制）"
    )
    (logger.error if explicit else logger.warning)(
        "agentic 查库能力不可用，sql_query 未注册：%s\n"
        "        后果：agentic 问答只有 kb_search，判到查库意图也拿不到业务数据\n"
        "        只用文档知识库可忽略本提示（服务照常起，/health 仍可探活）；需要查库任选一种：\n"
        f"            {SOURCES_FIX_COMMAND}\n"
        f"            cp config/sources.example.yaml {path}",
        head,
    )


def _missing_sqlite_file(dsn: str) -> Optional[Path]:
    """DSN 指向本机的 SQLite 文件而文件不在时返回该路径，其他情况返回 None。

    演示库 data/sql/demo.db 已 gitignore，新克隆与新 worktree 里必然不存在，
    这是「连不上库」最常见的成因，值得单独指出来。
    """
    try:
        url = make_url(dsn)
    except Exception:
        return None
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database).expanduser()
    if not path.is_absolute():
        # 用配置层同一个解析规则，免得从别处启动时这里指向另一个文件
        path = Path(normalize_sqlite_dsn(dsn)[len("sqlite:///"):]).expanduser()
    return None if path.exists() else path


def _sqlite_db_hint(path: Optional[Path]) -> list[str]:
    """SQLite 文件不在时给具体线索；演示库这种直接点名初始化脚本。"""
    if path is None:
        return ["        请确认数据库服务是否在运行、DSN 与账号是否正确"]
    lines = [f"        SQLite 文件不存在：{path}"]
    if path.name == "demo.db" and path.parent.name == "sql":
        lines.append(
            "        演示库不入库（data/sql/*.db 在 .gitignore 里），先生成再启动："
            "python3 scripts/init_demo_db.py"
        )
    else:
        lines.append("        请确认这个 SQLite 文件的路径是否正确")
    return lines


def _log_missing_sqlite_db(label: str, dsn: str, path: Path) -> None:
    """文件不在但连接会静默建出空库，这一层不拦，问题会推迟到运行时才露。

    sqlite 对「目录在、文件不在」的 DSN 会创建 0 字节空库，于是装配看着成功，
    工具描述里的表却一张都不存在，模型只能反复撞上 no such table。这里在连库前说破。
    """
    logger.error(
        "SQL 源 %s（DSN %s）连库前就查不到数据库文件\n%s\n"
        "        后果：sqlite 会静默建出一个 0 字节空库，sql_query 照常注册，"
        "但表一张都没有，运行时查询必然报 no such table\n"
        "        本次装配不中断，/chat/agent 仍在，只是查库类问题答不对",
        label,
        redact_dsn(dsn),
        "\n".join(_sqlite_db_hint(path)),
    )


def _log_unreachable_sql_source(label: str, dsn: str, exc: Exception) -> None:
    """连不上 SQL 源时把下一步说清，别让它看起来跟配置没配好一模一样。

    只加日志，异常照原样抛出去：装配失败仍然只让 /chat/agent 返回 503，
    要不要继续降级由调用方决定，这里不替它改语义。
    """
    detail = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
    lines = [
        f"连不上 SQL 源 {label}（DSN {redact_dsn(dsn)}）：{detail}",
        "        后果：agentic 运行时装配中断，/chat/agent 会返回 503；kb_search 链路与 /health 不受影响",
    ]
    lines.extend(_sqlite_db_hint(_missing_sqlite_file(dsn)))
    logger.error("\n".join(lines))


def build_tool_registry(
    engine: Any,
    *,
    sources_path: Optional[Path] = None,
    sql_rows: int = AGENT_SQL_ROWS,
    allow_sql: bool = True,
) -> ToolRegistry:
    """按 sources.yaml 装配工具：知识库检索始终可用，运行时 SQL 需显式开启。

    拿不到配置（文件不存在或写坏了）只降级成 kb_search 并打日志，不抛异常：
    只有文档知识库的用法本来就没有这份配置，不该因此起不了服务。

    agentic 循环（`build_agent_runtime`）与 MCP server（`scripts/serve_mcp.py`）共用这份
    装配：授权门、表白名单、有界采样、缺配置降级这几套语义只有一处实现，两个入口不会漂移。
    """
    tools: list[Tool] = [KnowledgeSearchTool(engine)]

    path = Path(sources_path) if sources_path is not None else Path(DEFAULT_SOURCES_CONFIG)
    # app.py 传进来的路径本来就等于默认值，所以「是不是用户指定的」只能看有没有偏离默认
    explicit = sources_path is not None and path != Path(DEFAULT_SOURCES_CONFIG)
    if not allow_sql:
        pass  # 调用方明确不要 SQL，不该拿配置缺失去吵
    elif not path.exists():
        # 以前这里直接跳过，日志只剩「已装配工具：kb_search」，使用者以为系统不支持查库
        _log_missing_sources(path, explicit=explicit)
    else:
        try:
            config = load_sources_config(path)
        except ConfigError as exc:
            logger.error(
                "sources 配置读取失败，sql_query 未注册、agentic 无法查库：%s ｜ 配置路径：%s",
                exc,
                path,
            )
        else:
            registered = 0
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
                missing_db = _missing_sqlite_file(source.dsn)
                if missing_db is not None:
                    # 连下去只会静默建出空库，等到运行时只剩 no such table
                    _log_missing_sqlite_db(label, source.dsn, missing_db)
                try:
                    catalog, catalog_terms = _data_catalog(source.dsn, tables)
                    schema_hint = _schema_hint(source.dsn, tables)
                except Exception as exc:
                    # 以前这里只留下一串 sqlalchemy 的栈，看起来跟配置没配好一模一样
                    _log_unreachable_sql_source(label, source.dsn, exc)
                    raise
                tools.append(
                    SqlQueryTool(
                        dsn=source.dsn,
                        label=label,
                        allowed_tables=tables,
                        max_rows=sql_rows,
                        schema_hint=schema_hint,
                        catalog=catalog,
                        catalog_terms=catalog_terms,
                    )
                )
                registered += 1
            if registered == 0:
                # 配置在、但一个 sql 源都没授权，也要说清为什么只有 kb_search
                logger.info(
                    "sources 配置 %s 里没有 allow_runtime_query: true 的 sql 源，"
                    "agentic 只注册 kb_search；需要查库请给对应 sql 源加这个开关",
                    path,
                )

    return ToolRegistry(tools)


def build_agent_runtime(
    engine: Any,
    *,
    sources_path: Optional[Path] = None,
    planner: Optional[Planner] = None,
    model: str = AGENT_LLM_MODEL,
    sql_rows: int = AGENT_SQL_ROWS,
    allow_sql: bool = True,
) -> AgentRuntime:
    """装配 agentic 运行时：工具注册表见 `build_tool_registry`，决策器与模型在这里挂上。"""
    registry = build_tool_registry(
        engine, sources_path=sources_path, sql_rows=sql_rows, allow_sql=allow_sql
    )
    return AgentRuntime(registry, planner=planner, model=model)
