"""内存多轮会话：独立历史、TTL、容量上限与历史裁剪。"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from private_rag.config import (
    HISTORY_MESSAGE_CHARS,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    MAX_SESSION_MESSAGES,
    MAX_SESSIONS,
    MAX_STORED_MESSAGE_CHARS,
    SESSION_TTL_SECONDS,
)
from private_rag.models import Citation

SESSION_ID_PATTERN = r"^sess_[0-9a-f]{32}$"
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)


class SessionCapacityError(RuntimeError):
    """会话数量达到上限且没有可淘汰的空闲会话。"""


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def is_valid_session_id(value: str) -> bool:
    return bool(_SESSION_ID_RE.match(value))


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


@dataclass
class Message:
    role: str  # user / assistant
    content: str
    created_at: float
    citations: list[Citation] = field(default_factory=list)


class Session:
    """单个会话；`lock` 用于串行化同一会话的并发问答。"""

    def __init__(
        self,
        session_id: str,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_messages: int = MAX_SESSION_MESSAGES,
        max_stored_message_chars: int = MAX_STORED_MESSAGE_CHARS,
        title: Optional[str] = None,
    ):
        now = clock()
        self.session_id = session_id
        self.title = (title or "").strip() or None
        self.created_at = now
        self.last_active_at = now
        self.ttl_seconds = ttl_seconds
        self.max_messages = max(2, max_messages - max_messages % 2)
        self.max_stored_message_chars = max_stored_message_chars
        self.clock = clock
        self.messages: list[Message] = []
        self.lock = threading.Lock()

    @property
    def expires_at(self) -> float:
        return self.last_active_at + self.ttl_seconds

    def touch(self) -> None:
        self.last_active_at = self.clock()

    def add_exchange(
        self,
        question: str,
        answer: str,
        citations: Optional[list[Citation]] = None,
    ) -> None:
        """成对写入本轮问答；调用方应持有会话锁。"""
        now = self.clock()
        if not self.title:
            self.title = _truncate(" ".join(question.split()), 40)

        user = Message(
            role="user",
            content=_truncate(question, self.max_stored_message_chars),
            created_at=now,
        )
        assistant = Message(
            role="assistant",
            content=_truncate(answer, self.max_stored_message_chars),
            created_at=now,
            citations=list(citations or []),
        )
        # 一次性替换列表，避免读取方看到只写了一半的问答
        messages = self.messages + [user, assistant]
        if len(messages) > self.max_messages:
            messages = messages[-self.max_messages :]
        self.messages = messages
        self.touch()

    def history_for_prompt(
        self,
        max_turns: int = MAX_HISTORY_TURNS,
        max_chars: int = MAX_HISTORY_CHARS,
        message_chars: int = HISTORY_MESSAGE_CHARS,
    ) -> list[tuple[str, str]]:
        """返回最近完整轮次，按字符预算从新到旧裁剪，结果按时间正序。"""
        if max_turns <= 0 or max_chars <= 0 or message_chars <= 0:
            return []

        pairs: list[tuple[Message, Message]] = []
        index = len(self.messages)
        while index >= 2 and len(pairs) < max_turns:
            user = self.messages[index - 2]
            assistant = self.messages[index - 1]
            if user.role == "user" and assistant.role == "assistant":
                pairs.append((user, assistant))
                index -= 2
            else:
                index -= 1

        selected: list[tuple[str, str]] = []
        remaining = max_chars
        # pairs 是“从新到旧”，预算也从新到旧计算
        for user, assistant in pairs:
            user_text = _truncate(" ".join(user.content.split()), message_chars)
            answer_text = _truncate(" ".join(assistant.content.split()), message_chars)
            cost = len(user_text) + len(answer_text)
            if cost <= remaining:
                selected.append((user_text, answer_text))
                remaining -= cost
                continue
            if not selected and remaining >= 40:
                # 只有最新一轮且预算不足：按剩余预算截断，保证至少带一点上下文
                user_budget = max(20, remaining // 2)
                answer_budget = remaining - user_budget
                if user_budget >= 10 and answer_budget >= 10:
                    selected.append(
                        (
                            _truncate(user_text, user_budget),
                            _truncate(answer_text, answer_budget),
                        )
                    )
            break

        selected.reverse()
        history: list[tuple[str, str]] = []
        for user_text, answer_text in selected:
            history.append(("user", user_text))
            history.append(("assistant", answer_text))
        return history

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "message_count": len(self.messages),
        }

    def detail(self) -> dict:
        data = self.summary()
        data["messages"] = [
            {
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
                "citations": [asdict(citation) for citation in message.citations],
            }
            for message in self.messages
        ]
        return data


class SessionStore:
    """进程内会话表；通过锁保证并发安全，容量与过期时间有上限。"""

    def __init__(
        self,
        *,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
        max_messages: int = MAX_SESSION_MESSAGES,
        max_stored_message_chars: int = MAX_STORED_MESSAGE_CHARS,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = new_session_id,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self.max_stored_message_chars = max_stored_message_chars
        self.clock = clock
        self.id_factory = id_factory
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def _is_expired(self, session: Session) -> bool:
        return self.clock() - session.last_active_at >= self.ttl_seconds

    def _prune_expired_locked(self) -> None:
        for session_id, session in list(self._sessions.items()):
            if not self._is_expired(session):
                continue
            # 正在处理请求的会话先保留，避免聊天中途被清理
            if not session.lock.acquire(blocking=False):
                continue
            try:
                if self._is_expired(session):
                    self._sessions.pop(session_id, None)
            finally:
                session.lock.release()

    def _evict_one_locked(self) -> None:
        candidates = []
        for session_id, session in self._sessions.items():
            if session.lock.acquire(blocking=False):
                session.lock.release()
                candidates.append((session.last_active_at, session_id))
        if not candidates:
            raise SessionCapacityError("会话数量已达上限且当前没有空闲会话")
        _, session_id = min(candidates)
        self._sessions.pop(session_id, None)

    def create(self, title: Optional[str] = None) -> Session:
        with self._lock:
            self._prune_expired_locked()
            if len(self._sessions) >= self.max_sessions:
                self._evict_one_locked()
            session = Session(
                self.id_factory(),
                clock=self.clock,
                ttl_seconds=self.ttl_seconds,
                max_messages=self.max_messages,
                max_stored_message_chars=self.max_stored_message_chars,
                title=title,
            )
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            self._prune_expired_locked()
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._is_expired(session):
                self._sessions.pop(session_id, None)
                return None
            session.touch()
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
