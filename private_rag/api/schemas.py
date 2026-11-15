"""API 请求与响应模型。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from private_rag.config import MAX_QUESTION_CHARS, TOP_K_RERANK
from private_rag.models import Citation
from private_rag.sessions import SESSION_ID_PATTERN, Session

MAX_TAGS = 10
MAX_TAG_CHARS = 50


def to_iso(timestamp: float) -> str:
    """把秒级时间戳转成带时区的 ISO 字符串。"""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def citation_out(citation: Citation) -> "CitationOut":
    return CitationOut(**asdict(citation))


class CitationOut(BaseModel):
    index: int
    title: str
    source_type: str
    source_uri: str
    snippet: str
    page: Optional[int] = None
    table: Optional[str] = None
    score: Optional[float] = None


class ChatRequest(BaseModel):
    """一次问答请求；省略 session_id 表示自动创建新会话。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="用户问题",
    )
    session_id: Optional[str] = Field(
        default=None,
        pattern=SESSION_ID_PATTERN,
        description="会话 ID；省略时自动创建",
    )
    top_k: int = Field(default=TOP_K_RERANK, ge=1, le=10, description="最终送入 LLM 的片段数")
    source_type: Optional[Literal["file", "web", "sql"]] = Field(
        default=None,
        description="只检索指定来源类型",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        max_length=MAX_TAGS,
        description=f"标签过滤，最多 {MAX_TAGS} 个，命中任意一个即可",
    )
    use_lexical: bool = Field(default=True, description="是否启用 BM25 混合召回")
    use_rerank: bool = Field(default=True, description="是否启用 reranker 精排")
    use_parent: bool = Field(
        default=True,
        description="是否把命中的子块扩展为父块上下文",
    )
    query_rewrite: Literal["off", "multi", "hyde"] = Field(
        default="off",
        description="查询改写模式：off / multi（多查询）/ hyde（假想文档）",
    )

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question 不能为空")
        return value

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        cleaned: list[str] = []
        for tag in value:
            tag = tag.strip()
            if not tag:
                continue
            if len(tag) > MAX_TAG_CHARS:
                raise ValueError(f"单个 tag 最多 {MAX_TAG_CHARS} 个字符")
            if tag not in cleaned:
                cleaned.append(tag)
        return cleaned or None

    def to_options(self) -> "RetrievalOptions":
        # 延迟导入，避免 schemas 在导入时就牵出检索/生成依赖
        from private_rag.qa import RetrievalOptions

        return RetrievalOptions(
            source_type=self.source_type,
            tags=self.tags,
            top_k=self.top_k,
            use_lexical=self.use_lexical,
            use_rerank=self.use_rerank,
            use_parent=self.use_parent,
            query_rewrite=self.query_rewrite,
        )


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=100, description="会话标题")

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ChatMeta(BaseModel):
    retrieved: int
    used_lexical: bool
    used_rerank: bool
    used_parent: bool
    query_rewrite: str
    query_count: int
    context_chars: int
    history_turns: int
    elapsed_ms: int


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[CitationOut]
    meta: ChatMeta
    request_id: str


class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: str
    citations: list[CitationOut] = Field(default_factory=list)


class SessionOut(BaseModel):
    session_id: str
    title: Optional[str] = None
    created_at: str
    expires_at: str
    message_count: int


class SessionDetailOut(SessionOut):
    messages: list[MessageOut] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class ReadyCheckOut(BaseModel):
    collection: bool
    vectors: int
    lexical: bool
    reranker: bool


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    ready: bool
    checks: ReadyCheckOut
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvalMetricOut(BaseModel):
    value: Optional[float] = None
    coverage: Optional[float] = None
    valid: Optional[int] = None


class EvalConfigOut(BaseModel):
    name: str
    label: str
    use_lexical: bool
    use_rerank: bool
    # 键为 evidence_hit@3 / mrr@3 等确定性指标名
    metrics: dict[str, Optional[float]] = Field(default_factory=dict)
    ragas: Optional[dict[str, EvalMetricOut]] = None
    ragas_total: Optional[int] = None


class EvalSummaryOut(BaseModel):
    available: bool
    reason: Optional[str] = None
    generated_at: Optional[str] = None
    dataset_name: Optional[str] = None
    dataset_total: Optional[int] = None
    samples_answerable: Optional[int] = None
    samples_refusal: Optional[int] = None
    llm_model: Optional[str] = None
    judge_model: Optional[str] = None
    embed_model: Optional[str] = None
    duration_seconds: Optional[float] = None
    git_commit: Optional[str] = None
    configs: list[EvalConfigOut] = Field(default_factory=list)


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorInfo


def session_summary(session: Session) -> SessionOut:
    data = session.summary()
    return SessionOut(
        session_id=data["session_id"],
        title=data["title"],
        created_at=to_iso(data["created_at"]),
        expires_at=to_iso(data["expires_at"]),
        message_count=data["message_count"],
    )


def session_detail(session: Session) -> SessionDetailOut:
    data = session.detail()
    return SessionDetailOut(
        session_id=data["session_id"],
        title=data["title"],
        created_at=to_iso(data["created_at"]),
        expires_at=to_iso(data["expires_at"]),
        message_count=data["message_count"],
        messages=[
            MessageOut(
                role=message["role"],
                content=message["content"],
                created_at=to_iso(message["created_at"]),
                citations=[CitationOut(**citation) for citation in message["citations"]],
            )
            for message in data["messages"]
        ],
    )
