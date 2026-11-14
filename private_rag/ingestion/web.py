"""网页连接器：抓取 URL 并用 trafilatura 抽取正文与元数据。"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urldefrag

import requests
import trafilatura

from private_rag.config import WEB_USER_AGENT
from private_rag.models import SourceDocument
from private_rag.sources import WebSource


def _clean_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def _extract_metadata(html):
    """trafilatura 的 metadata 接口在不同版本参数略有差异，做一层兼容。"""
    try:
        return trafilatura.extract_metadata(html)
    except TypeError:
        return None


def fetch_web_document(url: str, timeout: float) -> SourceDocument:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": WEB_USER_AGENT},
    )
    response.raise_for_status()

    clean_url = _clean_url(url)
    # 直接传 bytes 给 trafilatura，由它按 HTML 声明/编码检测解码，避免 requests
    # 在缺少 charset 头时默认按 ISO-8859-1 解码导致中文乱码
    text = trafilatura.extract(
        response.content,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""
    metadata = _extract_metadata(response.content)

    title: Optional[str] = getattr(metadata, "title", None) if metadata else None
    date: Optional[str] = getattr(metadata, "date", None) if metadata else None
    sitename: Optional[str] = getattr(metadata, "sitename", None) if metadata else None

    if not text.strip():
        raise ValueError("未抽取到正文（可能是纯图片页或反爬页面）")

    return SourceDocument(
        doc_id=f"web:{clean_url}",
        text=text.strip(),
        title=title or clean_url,
        source_type="web",
        source_uri=clean_url,
        source_key="",  # 由调用方填入
        updated_at=date,
        tags=[],
        extra={"url": clean_url, "sitename": sitename},
    )


def load_web_documents(source: WebSource, source_key: str) -> tuple[list[SourceDocument], list[str]]:
    documents: list[SourceDocument] = []
    errors: list[str] = []
    for url in source.urls:
        try:
            document = fetch_web_document(url, source.timeout)
            document.source_key = source_key
            document.tags = list(source.tags)
            documents.append(document)
        except Exception as exc:
            errors.append(f"抓取失败 {url}: {exc}")
    return documents, errors
