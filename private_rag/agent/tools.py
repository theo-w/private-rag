"""工具层：把知识库检索与运行时 SQL 包装成 agentic 循环可调用的动作。

每个工具返回 ToolResult：blocks 是可作为证据的证据块（带来源），
summary 是给模型看的一句话结论，error 非空表示这次调用失败——
失败也是有用的观察，模型可以据此换一条 SQL 或改用知识库检索。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from sqlalchemy import create_engine, text

from private_rag.agent.sql_guard import SqlGuardError, sanitize_sql
from private_rag.config import AGENT_SQL_ROWS, AGENT_TOOL_CHARS, TOP_K_RERANK
from private_rag.generation import build_citations
from private_rag.models import Citation
from private_rag.qa import RetrievalOptions


def _clip(text: str, limit: int) -> str:
    flat = str(text or "")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def citation_label(citation: Citation) -> str:
    """证据块顶部的来源说明，与引用展示的信息一致。"""
    parts = [citation.source_type, citation.title]
    if citation.source_uri:
        parts.append(citation.source_uri)
    if citation.page is not None:
        parts.append(f"第 {citation.page} 页")
    if citation.table:
        parts.append(f"表 {citation.table}")
    return " | ".join(part for part in parts if part)


@dataclass
class ToolBlock:
    """一段可作为证据的材料；citation 为 None 表示该段材料无法溯源。"""

    label: str
    text: str
    citation: Optional[Citation] = None


@dataclass
class ToolResult:
    ok: bool
    summary: str = ""
    blocks: list[ToolBlock] = field(default_factory=list)
    rows: Optional[int] = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    # 写进轨迹的调试信息（SQL 文本、检索统计等），不送进模型上下文
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def observation(self) -> str:
        """回灌给模型的观察文本。"""
        parts = [self.summary] if self.summary else []
        if self.error:
            parts.append(f"失败原因：{self.error}")
        parts.extend(f"· {warning}" for warning in self.warnings)
        return "；".join(part for part in parts if part)


class Tool:
    """工具基类；name 与 description 会进入决策提示词。"""

    name: str = ""
    description: str = ""
    args_hint: str = "{}"

    def run(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def spec(self) -> str:
        return f"- {self.name}: {self.description} 参数示例 {self.args_hint}"


class KnowledgeSearchTool(Tool):
    """复用 RagEngine 的完整检索链路（向量 + BM25 + 精排 + 父子块）。"""

    name = "kb_search"
    description = "检索本地知识库资料，适合概念、流程、文档说明类问题"
    args_hint = '{"query": "检索用的查询语句"}'

    def __init__(
        self,
        engine: Any,
        *,
        base_options: Optional[RetrievalOptions] = None,
        top_k: int = TOP_K_RERANK,
        max_chars: int = AGENT_TOOL_CHARS,
    ):
        self.engine = engine
        self.base_options = base_options or RetrievalOptions()
        self.top_k = top_k
        self.max_chars = max_chars
        # 路由判出的意图，由 agentic 循环在路由之后写进来。树索引只在纯知识意图上启用，
        # 而注册表是在路由之前装配的，所以只能在循环里补。
        self.intent = ""

    def set_intent(self, intent: str) -> None:
        self.intent = str(intent or "")

    def run(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return ToolResult(False, error="query 不能为空")

        options = replace(self.base_options, top_k=self.top_k, intent=self.intent)
        try:
            trace = self.engine.retrieve_with_trace(query, options)
        except Exception as exc:  # 检索失败要作为观察回灌，而不是让整个请求崩掉
            return ToolResult(False, error=f"检索失败：{type(exc).__name__}")

        chunks = list(trace.chunks)
        if not chunks:
            return ToolResult(
                True,
                summary="知识库没有检索到相关内容",
                data={"queries": list(trace.queries), "context_chars": trace.context_chars},
            )

        citations = build_citations(chunks)
        blocks = [
            ToolBlock(
                label=citation_label(citation),
                text=_clip(chunk.text, self.max_chars // max(1, len(chunks)) + 200),
                citation=citation,
            )
            for chunk, citation in zip(chunks, citations)
        ]
        return ToolResult(
            True,
            summary=f"检索到 {len(blocks)} 段资料",
            blocks=blocks,
            data={
                "queries": list(trace.queries),
                "used_parent": trace.used_parent,
                "used_rerank": trace.used_rerank,
                "used_tree": bool(getattr(trace, "used_tree", False)),
                "context_chars": trace.context_chars,
            },
        )


class SqlQueryTool(Tool):
    """运行时只读查询业务库。表白名单 + 行数上限 + 只读校验由 sql_guard 负责。"""

    name = "sql_query"
    args_hint = '{"sql": "SELECT name, price FROM products WHERE price < 10"}'

    def __init__(
        self,
        *,
        dsn: str,
        label: str,
        allowed_tables: Sequence[str],
        max_rows: int = AGENT_SQL_ROWS,
        max_chars: int = AGENT_TOOL_CHARS,
        schema_hint: str = "",
        catalog: str = "",
        catalog_terms: Sequence[str] = (),
    ):
        self.dsn = dsn
        self.label = label
        self.allowed_tables = [str(name) for name in allowed_tables]
        self.max_rows = max_rows
        self.max_chars = max_chars
        self.schema_hint = schema_hint
        self.catalog = catalog
        # 路由回退时可当关键词线索的短词（实体值、列名），由装配方给出
        self.catalog_terms = [str(t).strip() for t in catalog_terms if str(t).strip()]
        self.description = self._build_description()

    def _build_description(self) -> str:
        base = (
            f"只读查询业务数据库表（{('、'.join(self.allowed_tables)) or '未配置'}），"
            "适合精确的数量、价格、排名、统计类问题；只允许单条 SELECT，自动限制行数"
        )
        if self.schema_hint:
            base += f"。可查列：{self.schema_hint}"
        if self.catalog:
            # 路由器只看得到工具描述：库里到底存着什么，得写在这里才可能判准意图
            base += f"。库内数据：{self.catalog}"
        return base

    def fallback_sql(self) -> str:
        """回退路由只知道「该查库」时用的兜底查询：列出白名单首表。"""
        if not self.allowed_tables:
            return ""
        return f"SELECT * FROM {self.allowed_tables[0]}"

    def run(self, args: dict[str, Any]) -> ToolResult:
        raw = str(args.get("sql") or args.get("query") or "").strip()
        warnings: list[str] = []
        if not raw:
            raw = self.fallback_sql()
            if not raw:
                return ToolResult(False, error="没有给出 SQL，也没有配置可查的表")
            warnings.append("模型未给出 SQL，改为列出白名单表的前若干行")

        try:
            guarded, guard_warnings = sanitize_sql(
                raw, allowed_tables=self.allowed_tables, max_rows=self.max_rows
            )
            warnings.extend(guard_warnings)
        except SqlGuardError as exc:
            return ToolResult(False, error=str(exc), data={"sql": _clip(raw, 300)})

        try:
            engine = create_engine(self.dsn, pool_pre_ping=True)
        except ModuleNotFoundError as exc:
            return ToolResult(False, error=f"缺少数据库驱动（{exc.name}）")
        except Exception:
            return ToolResult(False, error="无法连接数据库")

        try:
            with engine.connect() as connection:
                result = connection.execute(text(guarded))
                columns = list(result.keys())
                rows = [list(row) for row in result.fetchmany(self.max_rows + 1)]
        except Exception as exc:
            # 数据库报错常含列名/表名，对模型有用，但不含连接信息
            return ToolResult(
                False,
                error=f"SQL 执行失败：{_clip(exc, 300)}",
                data={"sql": guarded},
            )
        finally:
            engine.dispose()

        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        if not columns:
            return ToolResult(
                True,
                summary="查询没有返回列",
                rows=0,
                warnings=warnings,
                data={"sql": guarded},
            )

        lines = [" | ".join(columns)]
        for row in rows:
            lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
        body = "\n".join(lines)

        snippet = " | ".join(columns)
        if rows:
            first = " | ".join("" if cell is None else str(cell) for cell in rows[0])
            snippet = f"{snippet}；首行：{first}"

        citation = Citation(
            index=1,
            title=f"{self.label} 查询结果",
            source_type="sql",
            source_uri=f"{self.label}#sql_query",
            snippet=_clip(" ".join(snippet.split()), 120),
            table=self.label,
        )
        summary = f"查询返回 {len(rows)} 行"
        if truncated:
            summary += f"（已达上限 {self.max_rows} 行，可能还有更多）"

        return ToolResult(
            True,
            summary=summary,
            blocks=[ToolBlock(label=f"SQL 查询（表 {self.label}）", text=_clip(body, self.max_chars), citation=citation)],
            rows=len(rows),
            warnings=warnings,
            data={"sql": guarded, "columns": columns, "truncated": truncated},
        )


class ToolRegistry:
    """按名字管理可用工具；未启用的工具不会出现在提示词里，模型也就选不到。"""

    def __init__(self, tools: Sequence[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("工具必须有 name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        if not isinstance(name, str):
            return None
        return self._tools.get(name.strip())

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def describe(self) -> str:
        if not self._tools:
            return "（当前没有可用工具）"
        return "\n".join(tool.spec() for tool in self._tools.values())


def render_evidence(
    blocks: Sequence[ToolBlock],
    *,
    start_index: int = 1,
) -> tuple[str, list[Citation]]:
    """把各工具的证据块统一编号，保证回答里的 [n] 与引用列表一一对应。"""
    numbered: list[str] = []
    citations: list[Citation] = []
    index = start_index
    for block in blocks:
        if block.citation is not None:
            citations.append(replace(block.citation, index=index))
            numbered.append(f"[{index}] (来源: {block.label})\n{block.text}")
            index += 1
            continue
        numbered.append(f"[无引用] (来源: {block.label})\n{block.text}")
    return "\n\n".join(numbered), citations
