"""把 ToolResult 转成 MCP 返回载荷：统一编号的证据块 + 带块级定位的引用列表。

编号口径与 agentic 循环里的 render_evidence 完全一致：只有带引用的证据块参与编号，
回答/客户端里的 [n] 与引用列表逐条对得上。引用在既有 Citation 字段之外附一个
locator（块级定位：source_uri + 块序号/片段偏移，或 SQL 的执行查询与行窗），
为后续引用定位器打基础。
"""

from __future__ import annotations

from typing import Any, Optional

from private_rag.agent.tools import ToolResult, citation_label, render_evidence
from private_rag.models import Citation


def citation_payload(citation: Citation, index: int, locator: Optional[dict[str, Any]]) -> dict[str, Any]:
    """一条引用的对外形态；None 字段直接省略，不占位。"""
    payload: dict[str, Any] = {
        "index": index,
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
        payload["locator"] = dict(locator)
    return payload


def result_payload(result: ToolResult) -> dict[str, Any]:
    """一次工具调用的结构化结果：evidence 与 citations 共用同一套编号。"""
    evidence: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    index = 1
    for block in result.blocks:
        if block.citation is not None:
            evidence.append({"index": index, "label": block.label, "text": block.text})
            citations.append(citation_payload(block.citation, index, block.locator))
            index += 1
            continue
        # 与 render_evidence 一致：无引用的块不参与编号
        evidence.append({"label": block.label, "text": block.text})
    payload: dict[str, Any] = {
        "summary": result.summary,
        "evidence": evidence,
        "citations": citations,
        "warnings": list(result.warnings),
        "data": dict(result.data),
    }
    if result.rows is not None:
        payload["rows"] = result.rows
    return payload


def result_text(result: ToolResult) -> str:
    """给只看文本内容的客户端（大多数 MCP 客户端先读 content[].text）的可读形态。"""
    sections: list[str] = []
    head_parts: list[str] = []
    if result.summary:
        head_parts.append(result.summary)
    if result.rows is not None:
        head_parts.append(f"行数 {result.rows}")
    head_parts.extend(f"警告：{warning}" for warning in result.warnings)
    if head_parts:
        sections.append("；".join(head_parts))

    numbered, citations = render_evidence(result.blocks)
    if numbered:
        sections.append(numbered)
    if citations:
        lines = [f"[{c.index}] {citation_label(c)}" for c in citations]
        sections.append("引用列表：\n" + "\n".join(lines))
    if not sections:
        return result.summary or "工具执行完成，没有证据内容"
    return "\n\n".join(sections)
