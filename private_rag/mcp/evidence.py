"""MCP 工具返回的载荷形态：编号证据块 + 引用列表。

编号口径直接复用 agentic 循环的 `render_evidence`，所以外部客户端看到的 `[n]`
与知识库里那条证据的对应关系，跟模型在 agentic 链路里看到的完全一致：
只有带引用的证据块参与编号，引用列表里也不会有对不上号的条目。

两种形态共用同一套编号：
- `evidence_text` / `answer_text`：给只看 `content[].text` 的客户端（多数客户端先读文本）。
- `evidence_payload` / `citation_payload`：给支持 `structuredContent` 的客户端。
"""

from __future__ import annotations

from typing import Any, Sequence

from private_rag.agent.tools import ToolBlock, citation_label, render_evidence
from private_rag.models import Citation

# 引用的对外形态：index 与证据块编号同源，其余字段就是既有 Citation 的字段
CITATION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "description": "引用编号，与回答/证据块里的 [n] 一致"},
        "title": {"type": "string"},
        "source_type": {"type": "string", "description": "file / web / sql"},
        "source_uri": {"type": "string", "description": "文件路径、URL 或 label#主键"},
        "snippet": {"type": "string", "description": "命中片段的摘要（截断到 120 字）"},
        "page": {"type": "integer"},
        "table": {"type": "string"},
        "score": {"type": "number", "description": "reranker 分数，未精排时不出现"},
    },
    "required": ["index", "title", "source_type", "source_uri"],
}


def citation_payload(citation: Citation) -> dict[str, Any]:
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
    return payload


def citation_lines(citations: Sequence[Citation]) -> str:
    """引用列表的可读形态：`[n] 来源类型 | 标题 | URI | 页码/表名`。"""
    if not citations:
        return ""
    return "引用列表：\n" + "\n".join(f"[{c.index}] {citation_label(c)}" for c in citations)


def evidence_payload(blocks: Sequence[ToolBlock]) -> list[dict[str, Any]]:
    """证据块列表；编号规则与 `render_evidence` 一致（无引用的块不参与编号）。"""
    out: list[dict[str, Any]] = []
    index = 1
    for block in blocks:
        if block.citation is not None:
            out.append({"index": index, "label": block.label, "text": block.text})
            index += 1
            continue
        out.append({"label": block.label, "text": block.text})
    return out


def evidence_citations(blocks: Sequence[ToolBlock]) -> list[dict[str, Any]]:
    """与 `evidence_payload` 同源的引用列表。"""
    _, citations = render_evidence(blocks)
    return [citation_payload(citation) for citation in citations]


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


EVIDENCE_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "证据块编号；无引用的块没有该字段"},
            "label": {"type": "string", "description": "这一段的来源说明"},
            "text": {"type": "string", "description": "证据正文（按总字符预算截断）"},
        },
        "required": ["label", "text"],
    },
}

SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "summary": {"type": "string", "description": "一句话结论，例如「检索到 3 段资料」"},
        "evidence": EVIDENCE_LIST_SCHEMA,
        "citations": {"type": "array", "items": CITATION_ITEM_SCHEMA},
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

ASK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "answer": {"type": "string", "description": "回答正文，句子后带 [n] 标注"},
        "citations": {"type": "array", "items": CITATION_ITEM_SCHEMA},
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
