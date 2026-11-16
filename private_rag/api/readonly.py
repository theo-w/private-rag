"""只读演示服务：把已入库的语料与引用暴露出来，不调用任何模型。

设计边界：
- 数据来自 `scripts/build_demo_snapshot.py` 产出的快照，服务本身不连 Qdrant、不装
  torch/Ollama，因此可以跑在小内存实例上（例如 Render 免费档）。
- 检索只用 BM25 关键词召回，与线上混合检索的关键词那一路共用同一套分词
  （`retrieval.tokenizer`），语义向量那一路在没有模型的部署里无法复现。
- 不提供问答：`/api/v1/chat*` 明确返回 generation_disabled，避免假装能回答。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from private_rag.config import API_PREFIX, APP_VERSION, MAX_QUESTION_CHARS, ROOT
from private_rag.retrieval.tokenizer import tokenize

DEFAULT_SNAPSHOT_PATH = ROOT / "data" / "demo_snapshot.json"
_PAGE_PATH = Path(__file__).resolve().parent / "static" / "demo-readonly.html"

SNIPPET_CHARS = 120
DEFAULT_TOP_K = 5
MAX_TOP_K = 20


class SnapshotError(RuntimeError):
    """快照缺失或结构不对。"""


class SearchRequest(BaseModel):
    """只读检索请求；字段与问答请求保持兼容。"""

    model_config = {"extra": "forbid"}

    query: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    source_type: Optional[str] = Field(default=None, description="按来源类型过滤")
    tags: Optional[list[str]] = Field(default=None, description="按标签过滤")


@dataclass
class Snapshot:
    """内存中的只读语料。"""

    app_version: str
    mode: str
    retrieval: str
    source_config: str
    stats: dict[str, Any]
    documents: list[dict[str, Any]]
    built_at: float

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def scanned_count(self) -> int:
        return int(self.stats.get("scanned_documents", 0))

    @property
    def table_count(self) -> int:
        return int(self.stats.get("table_documents", 0))


def load_snapshot(path: Path = DEFAULT_SNAPSHOT_PATH) -> Snapshot:
    """读取快照 JSON；缺文件时抛错，让 /ready 报未就绪而不是静默空库。"""

    if not Path(path).exists():
        raise SnapshotError(
            f"快照不存在：{path}；请先运行 python scripts/build_demo_snapshot.py"
        )
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"快照 JSON 解析失败：{exc}") from exc

    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise SnapshotError("快照里没有 documents，请重新生成")
    return Snapshot(
        app_version=str(payload.get("app_version", "")),
        mode=str(payload.get("mode", "readonly")),
        retrieval=str(payload.get("retrieval", "bm25")),
        source_config=str(payload.get("source_config", "")),
        stats=dict(payload.get("stats") or {}),
        documents=documents,
        built_at=Path(path).stat().st_mtime,
    )


def _snippet(text: str) -> str:
    snippet = " ".join((text or "").split())
    return snippet[:SNIPPET_CHARS] + "…" if len(snippet) > SNIPPET_CHARS else snippet


def _citation(index: int, document: dict[str, Any], score: float) -> dict[str, Any]:
    """组装引用条目，字段与线上 CitationOut 保持一致。"""

    return {
        "index": index,
        "title": str(document.get("title") or document.get("doc_id") or "未知来源"),
        "source_type": str(document.get("source_type") or "unknown"),
        "source_uri": str(document.get("source_uri") or ""),
        "snippet": _snippet(str(document.get("text") or "")),
        "page": document.get("page"),
        "table": document.get("table"),
        "score": round(float(score), 4),
        "scanned": bool(document.get("scanned")),
        "tags": list(document.get("tags") or []),
    }


def _matches(document: dict[str, Any], source_type: Optional[str],
             tags: Optional[list[str]]) -> bool:
    if source_type and document.get("source_type") != source_type:
        return False
    if tags:
        if not set(document.get("tags") or []).intersection(tags):
            return False
    return True


class ReadonlyIndex:
    """快照上的 BM25 索引；与 LexicalIndex 同算法，但不依赖 Qdrant。"""

    def __init__(self, snapshot: Snapshot):
        from rank_bm25 import BM25Okapi

        self.snapshot = snapshot
        self.tokens = [tokenize(str(document.get("text") or ""))
                       for document in snapshot.documents]
        self.bm25 = BM25Okapi(self.tokens) if self.tokens else None

    def search(self, query: str, top_k: int = DEFAULT_TOP_K,
               source_type: Optional[str] = None,
               tags: Optional[list[str]] = None) -> tuple[list[dict[str, Any]], int]:
        query_tokens = tokenize(query)
        if not query_tokens or self.bm25 is None:
            return [], 0

        scores = self.bm25.get_scores(query_tokens)
        scored = []
        for score, document in zip(scores, self.snapshot.documents):
            # 与词法索引一致：零重叠不打分、不进入候选
            if score <= 0 or not _matches(document, source_type, tags):
                continue
            scored.append((float(score), document))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("doc_id") or "")))
        selected = scored[:top_k]
        citations = [
            _citation(index, document, score)
            for index, (score, document) in enumerate(selected, start=1)
        ]
        return citations, len(scored)


def _error(code: str, message: str, status: int, request: Optional[Request] = None,
           details: Optional[dict[str, Any]] = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": code, "message": message}
    if details:
        payload["details"] = details
    request_id = getattr(getattr(request, "state", None), "request_id", "")
    if request_id:
        payload["request_id"] = request_id
    return JSONResponse(status_code=status, content=payload)


def create_readonly_app(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
    snapshot: Optional[Snapshot] = None,
    token: Optional[str] = None,
) -> FastAPI:
    """创建只读服务。token 非空时，写接口要求 X-Demo-Token 与之一致。"""

    app = FastAPI(
        title="private-rag 只读演示",
        version=APP_VERSION,
        description=(
            "只读知识库演示：浏览已入库的文档与引用，BM25 关键词检索。"
            "此部署不加载模型，因此不提供问答。"
        ),
    )
    app.state.snapshot_path = Path(snapshot_path)
    app.state.token = token if token is not None else os.getenv("RAG_DEMO_TOKEN", "").strip()

    try:
        app.state.index = ReadonlyIndex(snapshot or load_snapshot(app.state.snapshot_path))
        app.state.load_error = ""
    except SnapshotError as exc:
        app.state.index = None
        app.state.load_error = str(exc)

    @app.middleware("http")
    async def request_id_and_token(request: Request, call_next):
        import uuid

        request.state.request_id = (request.headers.get("X-Request-ID", "").strip()
                                    or uuid.uuid4().hex[:12])
        protected = request.url.path.startswith(API_PREFIX)
        expected = app.state.token
        if protected and expected:
            provided = request.headers.get("X-Demo-Token", "").strip()
            if provided != expected:
                return _error("unauthorized", "缺少或错误的 X-Demo-Token", 401, request)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Readonly"] = "1"
        duration_ms = int((time.perf_counter() - started) * 1000)
        return response

    @app.get("/", include_in_schema=False)
    def service_info() -> dict[str, Any]:
        return {
            "service": "private-rag-readonly",
            "version": APP_VERSION,
            "mode": "readonly",
            "models": "off",
            "demo": "/demo",
            "docs": "/docs",
            "api_prefix": API_PREFIX,
            "note": "只读演示：可浏览语料与检索引用，不提供问答。",
        }

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": APP_VERSION, "mode": "readonly"}

    @app.get("/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        index = app.state.index
        checks = {
            "snapshot": index is not None,
            "documents": index.snapshot.document_count if index else 0,
            "scanned_documents": index.snapshot.scanned_count if index else 0,
            "table_documents": index.snapshot.table_count if index else 0,
            "lexical": index is not None,
            "embedding_model": False,
            "reranker": False,
            "llm": False,
        }
        warnings = []
        if index is not None:
            warnings = [
                "只读部署不加载模型：语义向量召回、精排与问答均不可用",
                "检索为快照内 BM25，索引粒度为页/表/行",
            ]
        reasons = [app.state.load_error] if index is None else []
        payload = {
            "status": "ready" if index else "not_ready",
            "ready": index is not None,
            "mode": "readonly",
            "checks": checks,
            "reasons": [reason for reason in reasons if reason],
            "warnings": warnings,
        }
        return JSONResponse(status_code=200 if index else 503, content=payload)

    @app.get(f"{API_PREFIX}/documents", summary="列出快照中的文档")
    def documents(request: Request, offset: int = 0, limit: int = 50) -> JSONResponse:
        index = app.state.index
        if index is None:
            return _error("snapshot_unavailable", app.state.load_error, 503, request)
        offset = max(0, offset)
        limit = max(1, min(limit, 200))
        items = [
            {
                "doc_id": document.get("doc_id"),
                "title": document.get("title"),
                "source_type": document.get("source_type"),
                "source_uri": document.get("source_uri"),
                "page": document.get("page"),
                "table": document.get("table"),
                "scanned": bool(document.get("scanned")),
                "tags": list(document.get("tags") or []),
                "char_count": document.get("char_count"),
            }
            for document in index.snapshot.documents[offset:offset + limit]
        ]
        return JSONResponse(content={
            "total": index.snapshot.document_count,
            "offset": offset,
            "limit": limit,
            "documents": items,
        })

    # doc_id 里带 ":" 与 "#page=N"，放进路径会被当成 fragment 截断，因此用查询参数
    @app.get(f"{API_PREFIX}/document", summary="读取单个文档全文")
    def document_detail(doc_id: str, request: Request) -> JSONResponse:
        index = app.state.index
        if index is None:
            return _error("snapshot_unavailable", app.state.load_error, 503, request)
        for document in index.snapshot.documents:
            if document.get("doc_id") == doc_id:
                return JSONResponse(content=document)
        return _error("document_not_found", f"快照中没有文档：{doc_id}", 404, request)

    @app.post(f"{API_PREFIX}/search", summary="BM25 关键词检索（只读）")
    def search(payload: SearchRequest, request: Request) -> JSONResponse:
        index = app.state.index
        if index is None:
            return _error("snapshot_unavailable", app.state.load_error, 503, request)
        citations, retrieved = index.search(
            payload.query,
            top_k=payload.top_k,
            source_type=payload.source_type,
            tags=payload.tags,
        )
        return JSONResponse(content={
            "query": payload.query,
            "tokens": tokenize(payload.query),
            "retrieved": retrieved,
            "citations": citations,
            "meta": {
                "mode": "readonly",
                "retrieval": "bm25",
                "used_parent": False,
                "query_rewrite": "off",
                "documents": index.snapshot.document_count,
                "answer": "disabled",
            },
        })

    def _disabled(request: Request) -> JSONResponse:
        return _error(
            "generation_disabled",
            "只读部署未加载模型，不提供问答；可调用 POST %s/search 查看检索与引用。" % API_PREFIX,
            409,
            request,
        )

    @app.post(f"{API_PREFIX}/chat", summary="问答（只读部署已关闭）")
    def chat(request: Request) -> JSONResponse:
        return _disabled(request)

    @app.post(f"{API_PREFIX}/chat/stream", summary="流式问答（只读部署已关闭）")
    def chat_stream(request: Request) -> JSONResponse:
        return _disabled(request)

    @app.get("/demo", include_in_schema=False)
    def demo_page() -> FileResponse:
        return FileResponse(str(_PAGE_PATH), media_type="text/html")

    return app


def create_app(**kwargs: Any) -> FastAPI:  # pragma: no cover - 兼容 uvicorn 工厂写法
    return create_readonly_app(**kwargs)
