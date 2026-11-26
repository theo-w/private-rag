"""MCP 工具返回的载荷形态：编号证据块 + 引用列表（检索类带块级定位）。

编号口径直接复用 agentic 循环的 `render_evidence`，所以外部客户端看到的 `[n]`
与知识库里那条证据的对应关系，跟模型在 agentic 链路里看到的完全一致：
只有带引用的证据块参与编号，引用列表里也不会有对不上号的条目。

两种形态共用同一套编号：
- `evidence_text` / `answer_text`：给只看 `content[].text` 的客户端（多数客户端先读文本）。
- `evidence_payload` / `citation_payload`：给支持 `structuredContent` 的客户端。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from private_rag.agent.tools import ToolBlock, citation_label, render_evidence
from private_rag.config import CITATION_LOCATOR
from private_rag.models import Citation

# 引用的对外形态：index 与证据块编号同源，其余字段就是既有 Citation 的字段
_CITATION_PROPERTIES: dict[str, Any] = {
    "index": {"type": "integer", "description": "引用编号，与回答/证据块里的 [n] 一致"},
    "title": {"type": "string"},
    "source_type": {"type": "string", "description": "file / web / sql"},
    "source_uri": {"type": "string", "description": "文件路径、URL 或 label#主键"},
    "snippet": {"type": "string", "description": "命中片段的摘要（截断到 120 字）"},
    "page": {"type": "integer"},
    "table": {"type": "string"},
    "score": {"type": "number", "description": "reranker 分数，未精排时不出现"},
}

# 块级定位：检索块说「出自哪一块、命中片段在返回文本的哪个位置」
CHUNK_LOCATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "块级定位：这条引用出自哪一块、命中片段在返回文本里的起始位置。"
        "取不到的键不出现；chunk_id 按内容哈希算，内容一改就换 id。"
    ),
    "properties": {
        "chunk_id": {"type": "string", "description": "块身份（入库时按 doc_id:内容哈希:块序号算）"},
        "doc_id": {"type": "string", "description": "命中的子块所属文档"},
        "chunk_index": {"type": "integer", "description": "子块在文档里的序号"},
        "chunk_count": {"type": "integer", "description": "该文档的总块数"},
        "parent_id": {"type": "string", "description": "父块扩展时那段长文本所属的父块"},
        "page": {"type": "integer"},
        "table": {"type": "string"},
        "snippet_offset": {
            "type": "integer",
            "description": "命中片段在 evidence.text 里的起始字符位置，可直接用来高亮",
        },
    },
}

# SQL 侧没有「块」，定位就是「哪条查询、取了哪些行」
SQL_LOCATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "这次查询的定位：护栏改写后执行的 SQL、表名与行数。",
    "properties": {
        "source_uri": {"type": "string", "description": "label#sql_query"},
        "table": {"type": "string"},
        "sql": {"type": "string", "description": "护栏改写后真正执行的 SQL"},
        "row_count": {"type": "integer"},
        "truncated": {"type": "boolean"},
    },
}


def citation_item_schema(locator_schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """引用项的 schema；带定位的工具才多出 `locator` 这一个键。"""
    properties = dict(_CITATION_PROPERTIES)
    if locator_schema is not None:
        properties["locator"] = locator_schema
    return {
        "type": "object",
        "properties": properties,
        "required": ["index", "title", "source_type", "source_uri"],
    }


def citation_payload(citation: Citation, locator: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """一条引用的对外形态；没有的字段直接省略，不用 null 占位。"""
    payload: dict[str, Any] = {
        "index": citation.index,
        "title": citation.title,
        "source_type": citation.source_type,
        "source_uri": citation.source_uri,
        "snippet": citation.snippet,
    }
    if citation.page is not None:
        payload["page"] = citation.page
    if citation.table:
        payload["table"] = citation.table
    if citation.score is not None:
        payload["score"] = citation.score
    if locator:
        payload["locator"] = locator
    return payload


def citation_lines(citations: Sequence[Citation]) -> str:
    """引用列表的可读形态：`[n] 来源类型 | 标题 | URI | 页码/表名`。"""
    if not citations:
        return ""
    return "引用列表：\n" + "\n".join(f"[{c.index}] {citation_label(c)}" for c in citations)


def _block_entry(block: ToolBlock, *, index: Optional[int] = None) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if index is not None:
        entry["index"] = index
    entry["label"] = block.label
    entry["text"] = block.text
    if block.locator:
        entry["locator"] = block.locator
    return entry


def evidence_payload(blocks: Sequence[ToolBlock]) -> list[dict[str, Any]]:
    """证据块列表；编号规则与 `render_evidence` 一致（无引用的块不参与编号）。"""
    out: list[dict[str, Any]] = []
    index = 1
    for block in blocks:
        if block.citation is not None:
            out.append(_block_entry(block, index=index))
            index += 1
            continue
        out.append(_block_entry(block))
    return out


def evidence_citations(blocks: Sequence[ToolBlock]) -> list[dict[str, Any]]:
    """与 `evidence_payload` 同源的引用列表（定位信息也同源，都来自 block.locator）。"""
    _, citations = render_evidence(blocks)
    located = [block.locator for block in blocks if block.citation is not None]
    return [
        citation_payload(citation, locator)
        for citation, locator in zip(citations, located)
    ]


def evidence_text(blocks: Sequence[ToolBlock]) -> str:
    """`[n] (来源: …)\\n正文` 的编号证据块，后面接引用列表。"""
    numbered, citations = render_evidence(blocks)
    sections = [numbered] if numbered else []
    lines = citation_lines(citations)
    if lines:
        sections.append(lines)
    return "\n\n".join(sections)


def answer_text(answer: str, citations: Sequence[Citation]) -> str:
    """回答正文 + 引用列表；客户端不需要解析 `[n]` 也能看到每条来源。"""
    sections = [answer] if answer else []
    lines = citation_lines(citations)
    if lines:
        sections.append(lines)
    return "\n\n".join(sections)


_EVIDENCE_PROPERTIES: dict[str, Any] = {
    "index": {"type": "integer", "description": "证据块编号；无引用的块没有该字段"},
    "label": {"type": "string", "description": "这一段的来源说明"},
    "text": {"type": "string", "description": "证据正文（按总字符预算截断）"},
}


def evidence_list_schema(locator_schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """证据块列表的 schema；带定位的工具才多出 `locator` 这一个键。"""
    properties = dict(_EVIDENCE_PROPERTIES)
    if locator_schema is not None:
        properties["locator"] = locator_schema
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": ["label", "text"],
        },
    }


def sql_output_schema() -> dict[str, Any]:
    # 开关关闭时这份定义逐字等于今天：`tools/list` 是对外契约的一部分
    locator = SQL_LOCATOR_SCHEMA if CITATION_LOCATOR else None
    return {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "护栏改写后真正执行的 SQL（带行数上限）"},
            "summary": {"type": "string", "description": "一句话结论，例如「查询返回 3 行」"},
            "row_count": {"type": "integer"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "truncated": {"type": "boolean", "description": "是否撞上行数上限"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "evidence": evidence_list_schema(locator),
            "citations": {"type": "array", "items": citation_item_schema(locator)},
        },
        "required": ["summary", "row_count", "citations"],
    }


def search_output_schema() -> dict[str, Any]:
    locator = CHUNK_LOCATOR_SCHEMA if CITATION_LOCATOR else None
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "summary": {"type": "string", "description": "一句话结论，例如「检索到 3 段资料」"},
            "evidence": evidence_list_schema(locator),
            "citations": {"type": "array", "items": citation_item_schema(locator)},
            "retrieved": {"type": "integer", "description": "实际返回的证据块数"},
            "used_lexical": {"type": "boolean"},
            "used_rerank": {"type": "boolean"},
            "used_parent": {"type": "boolean"},
            "query_rewrite": {"type": "string"},
            "query_count": {"type": "integer"},
            "context_chars": {"type": "integer"},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "evidence", "citations"],
    }


def ask_output_schema() -> dict[str, Any]:
    """`ask` 的引用来自生成阶段、没有证据块，所以定义里不出现 `locator`。"""
    return {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "answer": {"type": "string", "description": "回答正文，句子后带 [n] 标注"},
            "citations": {"type": "array", "items": citation_item_schema(None)},
            "no_evidence": {
                "type": "boolean",
                "description": "true 表示一条证据都没检索到，回答是固定拒答话术",
            },
            "meta": {
                "type": "object",
                "properties": {
                    "retrieved": {"type": "integer"},
                    "used_lexical": {"type": "boolean"},
                    "used_rerank": {"type": "boolean"},
                    "used_parent": {"type": "boolean"},
                    "query_rewrite": {"type": "string"},
                    "query_count": {"type": "integer"},
                    "context_chars": {"type": "integer"},
                    "elapsed_ms": {"type": "integer"},
                },
            },
        },
        "required": ["answer", "citations"],
    }
