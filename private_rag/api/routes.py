"""HTTP 路由：健康检查、会话管理与同步/流式问答。"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Iterator, Optional, Sequence

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from private_rag.agent.runtime import AgentRuntime
from private_rag.agent.trace import AgentTrace, format_trace, load_traces
from private_rag.api.errors import ApiError, request_id_of
from private_rag.api.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentMeta,
    AgentStepOut,
    AgentTraceOut,
    ChatMeta,
    ChatRequest,
    ChatResponse,
    EvalSummaryOut,
    HealthOut,
    ReadyCheckOut,
    ReadyOut,
    SessionCreateRequest,
    SessionDetailOut,
    SessionOut,
    TraceListOut,
    TraceReplayOut,
    TraceSummaryOut,
    citation_out,
    session_detail,
    session_summary,
)
from private_rag.config import AGENT_TRACE_PATH, API_PREFIX, APP_VERSION
from private_rag.eval_report import DEFAULT_EVAL_REPORT_PATH, load_eval_summary
from private_rag.models import Citation
from private_rag.qa import RagEngine
from private_rag.sessions import Session, SessionCapacityError, SessionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix=API_PREFIX)
ops_router = APIRouter()
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{6,32}$")


def get_engine(request: Request) -> RagEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise ApiError(503, "service_unavailable", "问答服务尚未初始化，请稍后重试")
    return engine


def get_sessions(request: Request) -> SessionStore:
    sessions = getattr(request.app.state, "session_store", None)
    if sessions is None:
        raise ApiError(503, "service_unavailable", "会话服务尚未初始化，请稍后重试")
    return sessions


def get_agent(request: Request) -> AgentRuntime:
    agent = getattr(request.app.state, "agent_runtime", None)
    if agent is None:
        raise ApiError(503, "agent_unavailable", "agentic 问答尚未初始化，请稍后重试")
    return agent


def _trace_path_of(request: Request) -> Path:
    return Path(getattr(request.app.state, "agent_trace_path", AGENT_TRACE_PATH))


def _agent_trace_out(trace: AgentTrace) -> AgentTraceOut:
    """把运行时轨迹转换成接口模型。"""
    return AgentTraceOut(
        trace_id=trace.trace_id,
        model=trace.model,
        intent=trace.intent,
        stop_reason=trace.stop_reason,
        tool_calls=trace.tool_calls,
        elapsed_ms=trace.elapsed_ms,
        steps=[AgentStepOut(**step.to_dict()) for step in trace.steps],
    )


def _require_ready(engine: RagEngine) -> None:
    readiness = engine.readiness()
    if readiness.ready:
        return
    raise ApiError(
        503,
        "knowledge_base_not_ready",
        "知识库未就绪，请先运行入库脚本",
        details={"reasons": readiness.reasons, "warnings": readiness.warnings},
    )


def _resolve_session(session_id: Optional[str], sessions: SessionStore) -> Session:
    if not session_id:
        try:
            return sessions.create()
        except SessionCapacityError as exc:
            raise ApiError(
                503,
                "session_capacity_reached",
                "会话数量已达上限，请稍后重试",
            ) from exc
    session = sessions.get(session_id)
    if session is None:
        raise ApiError(404, "session_not_found", "会话不存在或已过期")
    return session


def _chat_meta(
    *,
    retrieved: int,
    used_lexical: bool,
    used_rerank: bool,
    used_parent: bool,
    query_rewrite: str,
    query_count: int,
    context_chars: int,
    history_turns: int,
    elapsed_ms: int,
) -> ChatMeta:
    return ChatMeta(
        retrieved=retrieved,
        used_lexical=used_lexical,
        used_rerank=used_rerank,
        used_parent=used_parent,
        query_rewrite=query_rewrite,
        query_count=query_count,
        context_chars=context_chars,
        history_turns=history_turns,
        elapsed_ms=elapsed_ms,
    )


def _citations_payload(citations: Sequence[Citation]) -> list[dict]:
    return [citation_out(citation).model_dump() for citation in citations]


def _sse(event: str, data: dict) -> str:
    """SSE 帧；JSON 转义保证 token 内的换行不会破坏事件格式。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@ops_router.get("/health", response_model=HealthOut, summary="存活检查")
def health() -> HealthOut:
    return HealthOut(version=APP_VERSION)


@ops_router.get("/ready", response_model=ReadyOut, summary="知识库与检索组件就绪检查")
def ready(engine: RagEngine = Depends(get_engine)) -> JSONResponse:
    readiness = engine.readiness()
    body = ReadyOut(
        status="ready" if readiness.ready else "not_ready",
        ready=readiness.ready,
        checks=ReadyCheckOut(
            collection=readiness.collection_exists,
            vectors=readiness.vector_count,
            lexical=readiness.lexical_available,
            reranker=readiness.reranker_available,
        ),
        reasons=readiness.reasons,
        warnings=readiness.warnings,
    )
    return JSONResponse(
        status_code=200 if readiness.ready else 503,
        content=body.model_dump(),
    )


@router.get("/eval/summary", response_model=EvalSummaryOut, summary="v4 评估摘要")
def get_eval_summary(request: Request) -> EvalSummaryOut:
    # 报告是离线生成的只读产物；缺失或损坏时返回 available=false，不影响问答服务。
    path = getattr(request.app.state, "eval_report_path", DEFAULT_EVAL_REPORT_PATH)
    return EvalSummaryOut(**load_eval_summary(path))


@router.post("/sessions", response_model=SessionOut, status_code=201, summary="创建会话")
def create_session(
    payload: Optional[SessionCreateRequest] = Body(default=None),
    sessions: SessionStore = Depends(get_sessions),
) -> SessionOut:
    title = payload.title if payload else None
    try:
        session = sessions.create(title=title)
    except SessionCapacityError as exc:
        raise ApiError(
            503,
            "session_capacity_reached",
            "会话数量已达上限，请稍后重试",
        ) from exc
    return session_summary(session)


@router.get("/sessions/{session_id}", response_model=SessionDetailOut, summary="会话详情")
def get_session(
    session_id: str,
    sessions: SessionStore = Depends(get_sessions),
) -> SessionDetailOut:
    session = sessions.get(session_id)
    if session is None:
        raise ApiError(404, "session_not_found", "会话不存在或已过期")
    return session_detail(session)


@router.delete("/sessions/{session_id}", status_code=204, summary="删除会话")
def delete_session(
    session_id: str,
    sessions: SessionStore = Depends(get_sessions),
) -> Response:
    if not sessions.delete(session_id):
        raise ApiError(404, "session_not_found", "会话不存在或已过期")
    return Response(status_code=204)


@router.post("/chat", response_model=ChatResponse, summary="同步问答")
def chat(
    payload: ChatRequest,
    request: Request,
    engine: RagEngine = Depends(get_engine),
    sessions: SessionStore = Depends(get_sessions),
) -> ChatResponse:
    _require_ready(engine)
    session = _resolve_session(payload.session_id, sessions)
    options = payload.to_options()

    started = time.perf_counter()
    with session.lock:
        history = session.history_for_prompt()
        history_turns = len(history) // 2
        result = engine.answer(payload.question, options=options, history=history)
        session.add_exchange(payload.question, result.answer, result.citations)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

    return ChatResponse(
        session_id=session.session_id,
        answer=result.answer,
        citations=[citation_out(citation) for citation in result.citations],
        meta=_chat_meta(
            retrieved=result.retrieved,
            used_lexical=result.used_lexical,
            used_rerank=result.used_rerank,
            used_parent=result.used_parent,
            query_rewrite=result.query_rewrite,
            query_count=result.query_count,
            context_chars=result.context_chars,
            history_turns=history_turns,
            elapsed_ms=elapsed_ms,
        ),
        request_id=request_id_of(request),
    )


@router.post("/chat/stream", summary="SSE 流式问答")
def chat_stream(
    payload: ChatRequest,
    request: Request,
    engine: RagEngine = Depends(get_engine),
    sessions: SessionStore = Depends(get_sessions),
) -> StreamingResponse:
    # 响应头发出前仍可返回普通 JSON 错误；发出后只能通过 error 事件报告
    _require_ready(engine)
    session = _resolve_session(payload.session_id, sessions)
    options = payload.to_options()
    request_id = request_id_of(request)

    def event_stream() -> Iterator[str]:
        started = time.perf_counter()
        answer_parts: list[str] = []
        citations: list[Citation] = []
        retrieved = 0
        used_lexical = False
        used_rerank = False
        used_parent = False
        query_rewrite = "off"
        query_count = 1
        context_chars = 0
        history_turns = 0

        yield _sse(
            "session",
            {"session_id": session.session_id, "request_id": request_id},
        )
        try:
            with session.lock:
                history = session.history_for_prompt()
                history_turns = len(history) // 2
                for event in engine.stream(
                    payload.question,
                    options=options,
                    history=history,
                ):
                    if event.kind == "citations":
                        citations = list(event.citations)
                        retrieved = event.retrieved
                        used_lexical = event.used_lexical
                        used_rerank = event.used_rerank
                        used_parent = event.used_parent
                        query_rewrite = event.query_rewrite
                        query_count = event.query_count
                        context_chars = event.context_chars
                        yield _sse(
                            "citations",
                            {
                                "citations": _citations_payload(citations),
                                "retrieved": retrieved,
                                "used_parent": used_parent,
                                "query_rewrite": query_rewrite,
                                "query_count": query_count,
                                "context_chars": context_chars,
                            },
                        )
                    elif event.kind == "token":
                        answer_parts.append(event.text)
                        yield _sse("token", {"text": event.text})
                    elif event.kind == "done":
                        answer = event.text or "".join(answer_parts)
                        citations = list(event.citations) or citations
                        retrieved = event.retrieved or retrieved
                        used_lexical = event.used_lexical
                        used_rerank = event.used_rerank
                        used_parent = event.used_parent
                        query_rewrite = event.query_rewrite
                        query_count = event.query_count
                        context_chars = event.context_chars
                        elapsed_ms = int((time.perf_counter() - started) * 1000)
                        session.add_exchange(payload.question, answer, citations)
                        meta = _chat_meta(
                            retrieved=retrieved,
                            used_lexical=used_lexical,
                            used_rerank=used_rerank,
                            used_parent=used_parent,
                            query_rewrite=query_rewrite,
                            query_count=query_count,
                            context_chars=context_chars,
                            history_turns=history_turns,
                            elapsed_ms=elapsed_ms,
                        )
                        yield _sse(
                            "done",
                            {
                                "session_id": session.session_id,
                                "answer": answer,
                                "citations": _citations_payload(citations),
                                "meta": meta.model_dump(),
                                "request_id": request_id,
                            },
                        )
        except Exception:
            # 流式响应头已发出，无法再改状态码；客户端断开不会走到这里
            logger.exception(
                "流式问答失败 session_id=%s request_id=%s",
                session.session_id,
                request_id,
            )
            yield _sse(
                "error",
                {
                    "code": "generation_failed",
                    "message": "生成回答失败，请稍后重试",
                    "request_id": request_id,
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/agent", response_model=AgentChatResponse, summary="Agentic 问答（意图路由 + 工具调用 + 决策轨迹）")
def chat_agent(
    payload: AgentChatRequest,
    request: Request,
    engine: RagEngine = Depends(get_engine),
    sessions: SessionStore = Depends(get_sessions),
    agent: AgentRuntime = Depends(get_agent),
) -> AgentChatResponse:
    _require_ready(engine)
    session = _resolve_session(payload.session_id, sessions)
    options = payload.to_agent_options()
    request_id = request_id_of(request)

    with session.lock:
        history = session.history_for_prompt()
        history_turns = len(history) // 2
        # 计时在循环内部完成（含工具调用），这里不再重复计算
        result = agent.run(
            payload.question,
            options=options,
            history=history,
            request_id=request_id,
            session_id=session.session_id,
        )
        session.add_exchange(payload.question, result.answer, result.citations)

    return AgentChatResponse(
        session_id=session.session_id,
        answer=result.answer,
        citations=[citation_out(citation) for citation in result.citations],
        meta=AgentMeta(
            intent=result.intent,
            stop_reason=result.stop_reason,
            tool_calls=result.tool_calls,
            steps=len(result.steps),
            evidence_blocks=result.evidence_blocks,
            context_chars=result.context_chars,
            history_turns=history_turns,
            elapsed_ms=result.elapsed_ms,
            trace_id=result.trace_id,
        ),
        trace=_agent_trace_out(result.trace),
        request_id=request_id,
    )


@router.get("/traces", response_model=TraceListOut, summary="最近的 agentic 决策轨迹")
def list_traces(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50, description="返回最近多少条"),
) -> TraceListOut:
    records = load_traces(_trace_path_of(request), limit=limit)
    summaries = [
        TraceSummaryOut(
            trace_id=str(record.get("trace_id") or ""),
            question=str(record.get("question") or ""),
            intent=str(record.get("intent") or ""),
            stop_reason=str(record.get("stop_reason") or ""),
            tool_calls=int(record.get("tool_calls") or 0),
            elapsed_ms=int(record.get("elapsed_ms") or 0),
            created_at=float(record.get("created_at") or 0.0),
        )
        # 轨迹文件是追加写入，展示时让最新的排在前面
        for record in reversed(records)
    ]
    return TraceListOut(path=str(_trace_path_of(request)), total=len(summaries), traces=summaries)


@router.get("/traces/{trace_id}", response_model=TraceReplayOut, summary="回放一条决策轨迹")
def replay_trace(trace_id: str, request: Request) -> TraceReplayOut:
    if not _TRACE_ID_PATTERN.fullmatch(trace_id):
        raise ApiError(422, "invalid_trace_id", "trace_id 只能是 6-32 位字母或数字")
    records = load_traces(_trace_path_of(request), trace_id=trace_id)
    if not records:
        raise ApiError(404, "trace_not_found", "轨迹不存在，可能已被清理或换过存储路径")
    record = records[-1]
    return TraceReplayOut(trace=record, formatted=format_trace(record))
