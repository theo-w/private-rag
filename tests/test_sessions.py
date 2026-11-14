"""会话存储测试：TTL、容量、历史裁剪与多会话隔离。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from private_rag.models import Citation
from private_rag.sessions import (
    Session,
    SessionCapacityError,
    SessionStore,
    is_valid_session_id,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _citation(index: int = 1) -> Citation:
    return Citation(
        index=index,
        title=f"文档{index}",
        source_type="file",
        source_uri=f"data/docs/{index}.md",
        snippet="片段",
    )


def test_create_get_delete_and_validation():
    store = SessionStore()
    session = store.create(title="测试会话")
    assert is_valid_session_id(session.session_id)
    assert store.get(session.session_id) is session
    assert store.delete(session.session_id) is True
    assert store.get(session.session_id) is None
    assert store.delete(session.session_id) is False
    assert not is_valid_session_id("bad-id")


def test_expired_session_is_removed():
    clock = FakeClock()
    store = SessionStore(ttl_seconds=10, clock=clock)
    session = store.create()
    clock.advance(9)
    assert store.get(session.session_id) is session
    # get 会 touch 会话，因此再空闲 11 秒才超过 TTL
    clock.advance(11)
    assert store.get(session.session_id) is None
    assert len(store) == 0


def test_capacity_evicts_idle_and_reports_busy():
    store = SessionStore(max_sessions=1)
    first = store.create()
    second = store.create()
    assert store.get(first.session_id) is None
    assert store.get(second.session_id) is second

    busy = store.create()
    acquired = busy.lock.acquire(timeout=0.1)
    assert acquired
    try:
        with pytest.raises(SessionCapacityError):
            store.create()
    finally:
        busy.lock.release()


def test_add_exchange_sets_title_and_bounds_messages():
    clock = FakeClock()
    session = Session(
        "sess_" + "0" * 32,
        clock=clock,
        max_messages=4,
        max_stored_message_chars=20,
    )
    session.add_exchange("第一个问题是什么？", "第一个回答。", [_citation(1)])
    assert session.title == "第一个问题是什么？"
    assert [message.role for message in session.messages] == ["user", "assistant"]
    assert session.messages[1].citations[0].title == "文档1"

    for index in range(2, 5):
        session.add_exchange(f"问题{index}", f"回答{index}")
    # 最多保留 4 条消息，即最近两轮
    assert len(session.messages) == 4
    assert session.messages[0].content == "问题3"
    assert session.messages[-1].content == "回答4"

    long_answer = "很长的回答" * 20
    session.add_exchange("长问题", long_answer)
    stored = session.messages[-1].content
    assert len(stored) <= 20
    assert stored.endswith("…")


def test_history_keeps_last_turns_in_order():
    clock = FakeClock()
    session = Session("sess_" + "1" * 32, clock=clock, max_messages=20)
    for index in range(5):
        session.add_exchange(f"问题{index}", f"回答{index}")

    history = session.history_for_prompt(max_turns=2, max_chars=10_000, message_chars=100)
    assert history == [
        ("user", "问题3"),
        ("assistant", "回答3"),
        ("user", "问题4"),
        ("assistant", "回答4"),
    ]


def test_history_respects_char_budget():
    clock = FakeClock()
    session = Session("sess_" + "2" * 32, clock=clock, max_messages=20)
    session.add_exchange("旧问题" * 50, "旧回答" * 50)
    session.add_exchange("新问题", "新回答")

    history = session.history_for_prompt(max_turns=5, max_chars=20, message_chars=100)
    # 预算不足时只保留最新一轮，并截断到 20 字符以内
    assert len(history) == 2
    assert history[0][0] == "user"
    assert len(history[0][1]) + len(history[1][1]) <= 20


def test_history_empty_budget_and_detail():
    session = Session("sess_" + "3" * 32)
    assert session.history_for_prompt(max_turns=1, max_chars=0) == []
    session.add_exchange("问题", "回答", [_citation(2)])
    detail = session.detail()
    assert detail["session_id"] == session.session_id
    assert detail["message_count"] == 2
    assert detail["messages"][1]["citations"][0]["index"] == 2
    assert detail["expires_at"] > detail["created_at"]


def test_sessions_are_isolated_and_parallel_creates_are_unique():
    store = SessionStore(max_sessions=100)

    def create_and_chat(index: int) -> str:
        session = store.create()
        session.add_exchange(f"问题{index}", f"回答{index}")
        return session.session_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create_and_chat, range(20)))
    assert len(set(ids)) == 20
    assert len(store) == 20

    first = store.get(ids[0])
    second = store.get(ids[1])
    assert first is not None and second is not None
    assert first.messages[0].content == "问题0"
    assert second.messages[0].content == "问题1"


def test_session_lock_serializes_chat_access():
    session = Session("sess_" + "4" * 32)
    order: list[str] = []
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        barrier.wait()
        with session.lock:
            order.append(f"{name}-start")
            session.add_exchange(name, "回答")

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(order) == 2
    assert session.messages[0].role == "user"
    assert session.messages[1].role == "assistant"
