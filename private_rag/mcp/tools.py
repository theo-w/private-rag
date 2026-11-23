"""MCP 工具层：`kb_search`（只检索）、`ask`（检索 + 作答）、`sql_query`（业务库只读查询）。

检索与问答直接复用 `RagEngine`——与 CLI / HTTP 完全同一条链路（查询改写 → 混合召回 →
精排 → 父块扩展 → 生成），不经过 agentic 循环：MCP 客户端要的是「一次调用给一批证据」，
不需要模型替它做多轮决策。

`sql_query` 包的是 agentic 那颗运行时查询工具，装配也共用 `build_tool_registry`，所以两个
入口的授权语义一致。它对外要过三重门，缺一门就不出现在 `tools/list`：启动命令显式带
`--allow-sql`（默认关，得由人在命令行上开）、`sources.yaml` 里对应 sql 源带
`allow_runtime_query: true`、`sql_guard` 的只读护栏照旧（单条 `SELECT`、表白名单、强制行数上限）。
入库/写库仍不暴露——读与写是两条不同的边界。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from private_rag.agent.tools import ToolBlock, citation_label
from private_rag.config import (
    AGENT_TOOL_CHARS,
    MAX_QUESTION_CHARS,
    TOP_K_RERANK,
)
from private_rag.generation import build_citations
from private_rag.mcp.evidence import (
    ASK_OUTPUT_SCHEMA,
    SEARCH_OUTPUT_SCHEMA,
    SQL_OUTPUT_SCHEMA,
    answer_text,
    citation_payload,
    evidence_citations,
    evidence_payload,
    evidence_text,
)
from private_rag.qa import RagEngine, RetrievalOptions

# 入参边界与 HTTP 请求模型保持一致，避免同一个仓库里两套口径
MAX_LIMIT = 10
MAX_TAGS = 10
MAX_TAG_CHARS = 50
MAX_SQL_CHARS = 2000
ALLOWED_SOURCE_TYPES = ("file", "web", "sql")

# 本地模式 Qdrant 的独占报错：把这个原因翻译成能照着做的下一步，客户端才不会只看到一句异常
STORAGE_LOCKED_HINT = (
    "知识库存储被其他进程占用：本地磁盘模式同一路径只允许一个进程打开。"
    "请先停掉 scripts/serve.py（或其他打开同一 storage/ 的进程），"
    "或用环境变量 RAG_STORAGE_DIR 把 MCP server 指向另一个目录。"
)
STORAGE_LOCKED_MARKERS = ("another instance", "already accessed", "already locked")

ENGINE_NOT_READY_HINT = "未注入检索引擎：这个工具面只能用于查看工具定义（--list-tools）"


@dataclass
class ToolOutcome:
    """一次工具调用的结果：文本给所有客户端读，载荷给支持 structuredContent 的客户端用。"""

    ok: bool
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _clip(value: str, limit: int) -> str:
    flat = str(value or "")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _is_storage_locked(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in STORAGE_LOCKED_MARKERS)


def failure_reason(exc: BaseException) -> str:
    """把底层异常翻译成面向客户端的一句话（不带连接信息，只带类型与原因）。启动失败也复用这句说明。"""
    if _is_storage_locked(exc):
        return STORAGE_LOCKED_HINT
    return f"{type(exc).__name__}：{_clip(str(exc), 200)}"


def _not_ready(engine: Any) -> str:
    """知识库未就绪时给出与 HTTP `/ready` 同源的原因；就绪则返回空串。"""
    try:
        readiness = engine.readiness()
    except Exception as exc:
        return f"知识库状态检查失败：{failure_reason(exc)}"
    if readiness.ready:
        return ""
    reasons = "；".join(readiness.reasons) or "知识库未就绪"
    warnings = "；".join(readiness.warnings)
    if warnings:
        reasons += f"（{warnings}）"
    return reasons


def _clean_text(args: dict[str, Any], key: str) -> tuple[str, str]:
    """必填文本参数；返回 (值, 错误说明)。"""
    value = args.get(key)
    if not isinstance(value, str):
        return "", f"参数 {key} 必须是字符串"
    value = value.strip()
    if not value:
        return "", f"参数 {key} 不能为空"
    if len(value) > MAX_QUESTION_CHARS:
        return "", f"参数 {key} 最多 {MAX_QUESTION_CHARS} 个字符"
    return value, ""


def _clean_limit(args: dict[str, Any], default: int) -> tuple[int, str]:
    value = args.get("limit")
    if value is None:
        return default, ""
    # bool 是 int 的子类，客户端传 true 时不能当成 1
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, "参数 limit 必须是整数"
    if not 1 <= value <= MAX_LIMIT:
        return 0, f"参数 limit 只能在 1–{MAX_LIMIT} 之间"
    return value, ""


def _clean_tags(args: dict[str, Any]) -> tuple[list[str], str]:
    """`tags`（数组）与 `tag`（单个）都接受：合并去重，命中任意一个即可。"""
    raw = args.get("tags")
    tags: list[str] = []
    if raw is not None:
        if not isinstance(raw, list):
            return [], "参数 tags 必须是字符串数组"
        if len(raw) > MAX_TAGS:
            return [], f"参数 tags 最多 {MAX_TAGS} 个"
        for item in raw:
            if not isinstance(item, str):
                return [], "参数 tags 的每一项必须是字符串"
            cleaned = item.strip()
            if not cleaned:
                continue
            if len(cleaned) > MAX_TAG_CHARS:
                return [], f"单个 tag 最多 {MAX_TAG_CHARS} 个字符"
            if cleaned not in tags:
                tags.append(cleaned)

    single = args.get("tag")
    if single is not None:
        if not isinstance(single, str):
            return [], "参数 tag 必须是字符串"
        cleaned = single.strip()
        if cleaned:
            if len(cleaned) > MAX_TAG_CHARS:
                return [], f"单个 tag 最多 {MAX_TAG_CHARS} 个字符"
            if cleaned not in tags:
                tags.append(cleaned)
    return tags, ""


def _clean_source_type(args: dict[str, Any]) -> tuple[Optional[str], str]:
    value = args.get("source_type")
    if value is None:
        return None, ""
    if not isinstance(value, str):
        return None, "参数 source_type 必须是字符串"
    value = value.strip()
    if not value:
        return None, ""
    if value not in ALLOWED_SOURCE_TYPES:
        return None, "参数 source_type 只能是 " + " / ".join(ALLOWED_SOURCE_TYPES)
    return value, ""


def _reject_unknown(args: dict[str, Any], allowed: Sequence[str]) -> str:
    unknown = [key for key in args if key not in allowed]
    if not unknown:
        return ""
    return "不支持的参数：" + "、".join(sorted(str(key) for key in unknown))


class McpTool:
    """MCP 工具基类：`name` / `description` / `title` 与两个 schema 会进 `tools/list`。"""

    name: str = ""
    description: str = ""
    title: str = ""
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    output_schema: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, args: dict[str, Any]) -> ToolOutcome:
        raise NotImplementedError

    def definition(self) -> dict[str, Any]:
        # readOnlyHint 是对外声明的边界：这两个工具都不会改动任何东西
        annotations = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
        if self.title:
            annotations["title"] = self.title
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "outputSchema": dict(self.output_schema),
            "annotations": annotations,
        }


class KbSearchTool(McpTool):
    """只检索：返回编号证据块与引用元数据，不打生成模型。"""

    name = "kb_search"
    title = "知识库检索"
    description = (
        "检索本机知识库，返回带编号的证据块与引用元数据（来源类型、标题、URI、页码/表名）。"
        "只读，不会改动知识库；资料类问题优先用它，需要成句回答时用 ask。"
    )

    def __init__(
        self,
        engine: Optional[RagEngine],
        *,
        base_options: Optional[RetrievalOptions] = None,
        default_limit: int = TOP_K_RERANK,
        max_chars: int = AGENT_TOOL_CHARS,
    ):
        self.engine = engine
        self.base_options = base_options or RetrievalOptions()
        self.default_limit = default_limit
        self.max_chars = max_chars
        self.input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": f"检索查询语句（最多 {MAX_QUESTION_CHARS} 字）"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": f"最多返回几段证据（1–{MAX_LIMIT}，默认 {default_limit}）",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"标签过滤，命中任意一个即可，最多 {MAX_TAGS} 个",
                },
                "tag": {"type": "string", "description": "单个标签，等价于 tags 里的一项"},
                "source_type": {
                    "type": "string",
                    "enum": list(ALLOWED_SOURCE_TYPES),
                    "description": "只检索指定来源类型：file / web / sql（sql 指已入库的行级文档）",
                },
            },
            "required": ["query"],
        }
        self.output_schema = SEARCH_OUTPUT_SCHEMA

    def run(self, args: dict[str, Any]) -> ToolOutcome:
        invalid = _reject_unknown(args, ("query", "limit", "tags", "tag", "source_type"))
        if invalid:
            return ToolOutcome(False, error=invalid)
        query, invalid = _clean_text(args, "query")
        if invalid:
            return ToolOutcome(False, error=invalid)
        limit, invalid = _clean_limit(args, self.default_limit)
        if invalid:
            return ToolOutcome(False, error=invalid)
        tags, invalid = _clean_tags(args)
        if invalid:
            return ToolOutcome(False, error=invalid)
        source_type, invalid = _clean_source_type(args)
        if invalid:
            return ToolOutcome(False, error=invalid)

        if self.engine is None:
            return ToolOutcome(False, error=ENGINE_NOT_READY_HINT)
        not_ready = _not_ready(self.engine)
        if not_ready:
            return ToolOutcome(False, error=f"知识库未就绪：{not_ready}")

        options = replace(
            self.base_options,
            top_k=limit,
            source_type=source_type,
            tags=tags or None,
        )
        try:
            trace = self.engine.retrieve_with_trace(query, options)
        except Exception as exc:
            return ToolOutcome(False, error=f"检索失败：{failure_reason(exc)}")

        chunks = list(trace.chunks)
        if not chunks:
            return ToolOutcome(
                True,
                text="知识库没有检索到相关内容。",
                payload={
                    "query": query,
                    "summary": "知识库没有检索到相关内容",
                    "evidence": [],
                    "citations": [],
                    "retrieved": 0,
                    "used_lexical": trace.used_lexical,
                    "used_rerank": trace.used_rerank,
                    "used_parent": trace.used_parent,
                    "query_rewrite": trace.query_rewrite,
                    "query_count": trace.query_count,
                    "context_chars": trace.context_chars,
                },
            )

        citations = build_citations(chunks)
        # 单段预算随段数缩小，保证所有证据加起来不超过一次调用的字符预算
        per_block = self.max_chars // max(1, len(chunks)) + 200
        blocks = [
            ToolBlock(
                label=citation_label(citation),
                text=_clip(chunk.text, per_block),
                citation=citation,
            )
            for chunk, citation in zip(chunks, citations)
        ]
        return ToolOutcome(
            True,
            text=evidence_text(blocks),
            payload={
                "query": query,
                "summary": f"检索到 {len(blocks)} 段资料",
                "evidence": evidence_payload(blocks),
                "citations": evidence_citations(blocks),
                "retrieved": len(blocks),
                "used_lexical": trace.used_lexical,
                "used_rerank": trace.used_rerank,
                "used_parent": trace.used_parent,
                "query_rewrite": trace.query_rewrite,
                "query_count": trace.query_count,
                "context_chars": trace.context_chars,
            },
        )


class AskTool(McpTool):
    """检索 + 作答：返回带 `[n]` 标注的回答与引用列表。"""

    name = "ask"
    title = "知识库问答"
    description = (
        "向本机知识库提问，返回带 [n] 编号标注的回答与引用列表。"
        "资料不足时返回固定拒答话术而不是编造；只读，不会改动知识库。"
        "单次调用是一轮独立问答（没有会话历史），多轮追问请自己带上上下文。"
    )

    def __init__(
        self,
        engine: Optional[RagEngine],
        *,
        base_options: Optional[RetrievalOptions] = None,
        default_limit: int = TOP_K_RERANK,
    ):
        self.engine = engine
        self.base_options = base_options or RetrievalOptions()
        self.default_limit = default_limit
        self.input_schema = {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": f"问题（最多 {MAX_QUESTION_CHARS} 字）"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": f"送入模型的上下文片段数（1–{MAX_LIMIT}，默认 {default_limit}）",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"标签过滤，命中任意一个即可，最多 {MAX_TAGS} 个",
                },
                "tag": {"type": "string", "description": "单个标签，等价于 tags 里的一项"},
                "source_type": {
                    "type": "string",
                    "enum": list(ALLOWED_SOURCE_TYPES),
                    "description": "只检索指定来源类型：file / web / sql（sql 指已入库的行级文档）",
                },
            },
            "required": ["question"],
        }
        self.output_schema = ASK_OUTPUT_SCHEMA

    def run(self, args: dict[str, Any]) -> ToolOutcome:
        invalid = _reject_unknown(args, ("question", "limit", "tags", "tag", "source_type"))
        if invalid:
            return ToolOutcome(False, error=invalid)
        question, invalid = _clean_text(args, "question")
        if invalid:
            return ToolOutcome(False, error=invalid)
        limit, invalid = _clean_limit(args, self.default_limit)
        if invalid:
            return ToolOutcome(False, error=invalid)
        tags, invalid = _clean_tags(args)
        if invalid:
            return ToolOutcome(False, error=invalid)
        source_type, invalid = _clean_source_type(args)
        if invalid:
            return ToolOutcome(False, error=invalid)

        if self.engine is None:
            return ToolOutcome(False, error=ENGINE_NOT_READY_HINT)
        not_ready = _not_ready(self.engine)
        if not_ready:
            return ToolOutcome(False, error=f"知识库未就绪：{not_ready}")

        options = replace(
            self.base_options,
            top_k=limit,
            source_type=source_type,
            tags=tags or None,
        )
        started = time.perf_counter()
        try:
            result = self.engine.answer(question, options=options)
        except Exception as exc:
            return ToolOutcome(False, error=f"问答失败：{failure_reason(exc)}")
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return ToolOutcome(
            True,
            text=answer_text(result.answer, result.citations),
            payload={
                "question": question,
                "answer": result.answer,
                "citations": [citation_payload(citation) for citation in result.citations],
                # 零证据时 generation 直接返回固定拒答话术，客户端靠这个字段区分「没资料」与「有资料」
                "no_evidence": result.retrieved == 0,
                "meta": {
                    "retrieved": result.retrieved,
                    "used_lexical": result.used_lexical,
                    "used_rerank": result.used_rerank,
                    "used_parent": result.used_parent,
                    "query_rewrite": result.query_rewrite,
                    "query_count": result.query_count,
                    "context_chars": result.context_chars,
                    "elapsed_ms": elapsed_ms,
                },
            },
        )


class SqlQueryTool(McpTool):
    """业务库只读查询：包 agentic 那颗运行时查询工具，护栏与授权语义照旧。

    包装而不是重写：`sql_guard` 的只读校验、表白名单、行数上限、护栏改写后的执行 SQL 都
    来自那颗工具本身，MCP 这边只做参数清洗和载荷整形。
    """

    name = "sql_query"
    title = "业务库只读查询"

    def __init__(self, tool: Any):
        self.tool = tool
        self.description = (
            getattr(tool, "description", "") or "只读查询业务数据库表"
        ) + "。写操作与白名单外的表会被直接拒绝。"
        self.input_schema = {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": f"单条 SELECT 语句（最多 {MAX_SQL_CHARS} 字），行数上限由服务端强制",
                },
            },
            "required": ["sql"],
        }
        self.output_schema = SQL_OUTPUT_SCHEMA

    def run(self, args: dict[str, Any]) -> ToolOutcome:
        invalid = _reject_unknown(args, ("sql",))
        if invalid:
            return ToolOutcome(False, error=invalid)
        raw = args.get("sql")
        if not isinstance(raw, str):
            return ToolOutcome(False, error="参数 sql 必须是字符串")
        sql = raw.strip()
        if not sql:
            return ToolOutcome(False, error="参数 sql 不能为空")
        if len(sql) > MAX_SQL_CHARS:
            return ToolOutcome(False, error=f"参数 sql 最多 {MAX_SQL_CHARS} 个字符")

        try:
            result = self.tool.run({"sql": sql})
        except Exception as exc:  # 连库失败之类不能让服务死掉
            return ToolOutcome(False, error=f"查询失败：{failure_reason(exc)}")
        if not result.ok:
            return ToolOutcome(False, error=f"查询失败：{result.error or '查询未成功'}")

        data = result.data or {}
        executed = str(data.get("sql") or sql)
        lines = [f"执行 SQL：{executed}"]
        if result.summary:
            lines.append(result.summary)
        lines.extend(result.warnings)
        sections = ["\n".join(lines)]
        body = evidence_text(result.blocks)
        if body:
            sections.append(body)

        return ToolOutcome(
            True,
            text="\n\n".join(sections),
            payload={
                "sql": executed,
                "summary": result.summary or "",
                "row_count": result.rows,
                "columns": list(data.get("columns") or []),
                "truncated": bool(data.get("truncated")),
                "warnings": list(result.warnings),
                "evidence": evidence_payload(result.blocks),
                "citations": evidence_citations(result.blocks),
            },
        )


def build_mcp_tools(
    engine: Optional[RagEngine],
    *,
    base_options: Optional[RetrievalOptions] = None,
    default_limit: int = TOP_K_RERANK,
    max_chars: int = AGENT_TOOL_CHARS,
    sql_tool: Optional[Any] = None,
) -> list[McpTool]:
    """装配对外工具面。`engine=None` 时只用于输出工具定义，调用会返回错误说明。

    `sql_tool` 传 agentic 装配出来的运行时查询工具（`build_tool_registry` 里的 `sql_query`），
    没传就只暴露两个知识库工具——`--allow-sql` 与 `allow_runtime_query` 两道门在入口
    `scripts/serve_mcp.py` 判，这里只按有没有工具决定暴不暴露。
    """
    tools: list[McpTool] = [
        KbSearchTool(engine, base_options=base_options, default_limit=default_limit, max_chars=max_chars),
        AskTool(engine, base_options=base_options, default_limit=default_limit),
    ]
    if sql_tool is not None:
        tools.append(SqlQueryTool(sql_tool))
    return tools
