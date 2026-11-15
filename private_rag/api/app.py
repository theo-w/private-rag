"""FastAPI 应用工厂、请求 ID 中间件与进程生命周期。"""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from private_rag.api.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from private_rag.api.routes import ops_router, router
from private_rag.config import API_PREFIX, APP_VERSION
from private_rag.eval_report import DEFAULT_EVAL_REPORT_PATH
from private_rag.qa import RagEngine, build_engine
from private_rag.sessions import SessionStore

logger = logging.getLogger(__name__)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DEMO_PAGE = Path(__file__).resolve().parent / "static" / "demo.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 测试可注入 fake engine；生产在启动时才加载 Qdrant/BM25/reranker
    if app.state.engine is None:
        try:
            app.state.engine = build_engine(use_rerank=app.state.use_rerank)
            app.state.owns_engine = True
        except Exception:
            # 启动失败不直接退出：/health 仍可探活，/ready 与 /chat 返回 503
            logger.exception("初始化问答引擎失败，服务将以未就绪状态启动")
            app.state.engine = None
            app.state.owns_engine = False
    if app.state.session_store is None:
        app.state.session_store = SessionStore()

    logger.info("private-rag API 启动，版本 %s", APP_VERSION)
    try:
        yield
    finally:
        if app.state.owns_engine and app.state.engine is not None:
            try:
                app.state.engine.close()
            except Exception:
                logger.exception("关闭问答引擎失败")
            app.state.owns_engine = False
        logger.info("private-rag API 已关闭")


def create_app(
    *,
    engine: Optional[RagEngine] = None,
    session_store: Optional[SessionStore] = None,
    use_rerank: Optional[bool] = None,
    eval_report_path: Optional[Path] = None,
) -> FastAPI:
    app = FastAPI(
        title="private-rag API",
        version=APP_VERSION,
        description=(
            "本地知识库 RAG 服务：多源入库、BM25 + 向量混合召回、重排、"
            "引用溯源、多轮会话与 SSE 流式输出。"
        ),
        lifespan=lifespan,
    )
    # 这里只做轻量状态初始化；重资源在 lifespan 中加载
    app.state.engine = engine
    app.state.session_store = session_store
    app.state.owns_engine = False
    app.state.use_rerank = use_rerank
    app.state.eval_report_path = (
        Path(eval_report_path) if eval_report_path is not None else DEFAULT_EVAL_REPORT_PATH
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "").strip()
        request_id = incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # 只记录路径、状态码与耗时，不记录问题正文
        logger.info(
            "%s %s status=%s request_id=%s duration_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            request_id,
            int((time.perf_counter() - started) * 1000),
        )
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/", include_in_schema=False)
    def service_info() -> dict:
        return {
            "service": "private-rag",
            "version": APP_VERSION,
            "api_prefix": API_PREFIX,
            "docs": "/docs",
            "demo": "/demo",
            "health": "/health",
            "ready": "/ready",
        }

    @app.get("/demo", include_in_schema=False)
    def demo_page() -> FileResponse:
        # 单文件演示页：与 API 同源，直接调用真实接口，不引入前端构建步骤。
        # 不缓存 HTML，避免页面仍在运行旧版内联脚本（例如新增面板后一直“加载中”）。
        return FileResponse(
            _DEMO_PAGE,
            media_type="text/html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    app.include_router(ops_router)
    app.include_router(router)
    return app


app = create_app()
