"""查询改写：多查询扩展与 HyDE，只用于检索，不进入最终上下文。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_ollama import ChatOllama

from private_rag.config import LLM_MODEL
from private_rag.llm_params import seed_kwargs

REWRITE_MODES = ("off", "multi", "hyde")
MAX_VARIANTS = 2
MAX_VARIANT_CHARS = 300
MAX_HYDE_CHARS = 400

REWRITE_PROMPT = """你是检索查询改写助手。根据用户问题生成检索用的改写结果，不要回答用户问题。
要求：
1. queries：2 个措辞不同、语义等价的检索查询，保留关键实体与限定条件，每个不超过 300 字。
2. hyde：一段 200 字以内、可能出现在知识库文档里的说明性文字；不要编造具体数字、型号或链接。
只输出 JSON，格式为：{{"queries": ["查询一", "查询二"], "hyde": "假想文档段落"}}
用户问题：
{question}
"""


@dataclass
class RewriteResult:
    queries: list[str]
    fallback: bool = False
    reason: str = ""


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


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _variant_key(text: str) -> str:
    return " ".join(text.split()).lower()


def _variant_queries(data: dict, question: str) -> list[str]:
    raw = data.get("queries")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    variants: list[str] = []
    seen = {_variant_key(question)}
    for item in raw:
        text = _clean_text(item, MAX_VARIANT_CHARS)
        key = _variant_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        variants.append(text)
        if len(variants) >= MAX_VARIANTS:
            break
    return variants


def _hyde_text(data: dict) -> str:
    for key in ("hyde", "hypothetical_document", "document"):
        text = _clean_text(data.get(key), MAX_HYDE_CHARS)
        if text:
            return text
    return ""


def rewrite_queries(
    question: str,
    mode: str = "off",
    *,
    model: str = LLM_MODEL,
) -> RewriteResult:
    """按模式生成检索查询；任何失败都回退为原问题，不影响主链路。"""
    if mode not in REWRITE_MODES:
        raise ValueError(f"未知查询改写模式：{mode}")
    if mode == "off":
        return RewriteResult([question])

    try:
        llm = ChatOllama(
            model=model,
            temperature=0,
            format="json",
            num_ctx=4096,
            num_predict=512,
            disable_streaming=True,
            keep_alive="10m",
            **seed_kwargs(),
        )
        response = llm.invoke(REWRITE_PROMPT.format(question=question))
        data = _parse_payload(_content_text(response.content))

        if mode == "multi":
            variants = _variant_queries(data, question)
            if not variants:
                return RewriteResult(
                    [question], fallback=True, reason="模型没有给出可用的改写查询"
                )
            return RewriteResult([question, *variants])

        hyde = _hyde_text(data)
        if not hyde:
            return RewriteResult(
                [question], fallback=True, reason="模型没有给出可用的 HyDE 文本"
            )
        return RewriteResult([question, hyde])
    except Exception as exc:
        # 不把问题正文写入错误信息，避免日志/报告泄露用户输入
        reason = f"{type(exc).__name__}: {exc}"[:200]
        return RewriteResult([question], fallback=True, reason=reason)
