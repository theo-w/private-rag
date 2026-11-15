"""查询改写测试：模式、限长去重、解析容错与回退（不依赖 Ollama）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from private_rag import query_rewrite
from private_rag.query_rewrite import (
    MAX_HYDE_CHARS,
    MAX_VARIANT_CHARS,
    rewrite_queries,
)


class FakeChat:
    """记录构造参数与提示词；每次构造都读取控制器预设的响应。"""

    instances: list["FakeChat"] = []
    next_response: object = "{}"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.prompts: list[str] = []
        self.response = FakeChat.next_response
        FakeChat.instances.append(self)

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(content=self.response)


@pytest.fixture
def fake_chat(monkeypatch):
    FakeChat.instances.clear()
    FakeChat.next_response = "{}"
    monkeypatch.setattr(query_rewrite, "ChatOllama", FakeChat)
    return FakeChat


def test_off_mode_does_not_call_llm(fake_chat):
    result = rewrite_queries("问题", "off")
    assert result.queries == ["问题"]
    assert result.fallback is False
    assert fake_chat.instances == []


def test_unknown_mode_is_rejected_before_llm(fake_chat):
    with pytest.raises(ValueError, match="未知查询改写模式"):
        rewrite_queries("问题", "magic")
    assert fake_chat.instances == []


def test_multi_mode_deduplicates_limits_length_and_keeps_question(fake_chat):
    fake_chat.next_response = (
        "好的，结果如下："
        '{"queries": ["' + "长" * 400 + '", "苹果如何保存", " 苹果  如何保存 ", '
        '"苹果 怎么 存放", "苹果怎么保存？"], "hyde": "无关内容"}'
        " 以上。"
    )
    result = rewrite_queries("苹果怎么保存？", "multi", model="test-model")

    assert fake_chat.instances
    chat = fake_chat.instances[-1]
    assert chat.kwargs["model"] == "test-model"
    assert chat.kwargs["temperature"] == 0
    assert chat.kwargs["format"] == "json"
    assert chat.kwargs["disable_streaming"] is True
    assert "苹果怎么保存？" in chat.prompts[-1]

    assert result.fallback is False
    assert result.queries[0] == "苹果怎么保存？"
    assert len(result.queries[1]) == MAX_VARIANT_CHARS
    assert result.queries[2] == "苹果如何保存"
    assert len(result.queries) == 3


def test_multi_mode_returns_fallback_without_variants(fake_chat):
    fake_chat.next_response = '{"queries": [], "hyde": "x"}'
    result = rewrite_queries("问题", "multi")

    assert result.queries == ["问题"]
    assert result.fallback is True
    assert "改写查询" in result.reason


def test_multi_mode_accepts_string_query_and_content_blocks(fake_chat):
    fake_chat.next_response = [{"text": '{"queries": "另一种问法"}'}]
    result = rewrite_queries("问题", "multi")

    assert result.queries == ["问题", "另一种问法"]
    assert result.fallback is False


def test_hyde_mode_uses_alias_key_and_truncates(fake_chat):
    fake_chat.next_response = (
        '{"queries": [], "hypothetical_document": "' + "文" * 500 + '"}'
    )
    result = rewrite_queries("问题", "hyde")

    assert result.queries[0] == "问题"
    assert len(result.queries[1]) == MAX_HYDE_CHARS
    assert result.fallback is False


def test_hyde_mode_falls_back_when_text_missing(fake_chat):
    fake_chat.next_response = '{"queries": ["查询"], "hyde": "   "}'
    result = rewrite_queries("问题", "hyde")

    assert result.queries == ["问题"]
    assert result.fallback is True
    assert "HyDE" in result.reason


def test_llm_error_falls_back_without_leaking_question(fake_chat):
    fake_chat.next_response = RuntimeError("模拟改写失败")
    result = rewrite_queries("机密问题", "multi")

    assert result.queries == ["机密问题"]
    assert result.fallback is True
    assert "RuntimeError" in result.reason
    assert "机密问题" not in result.reason
    assert len(result.reason) <= 200


def test_invalid_json_falls_back(fake_chat):
    fake_chat.next_response = "模型没有按 JSON 输出"
    result = rewrite_queries("问题", "multi")

    assert result.queries == ["问题"]
    assert result.fallback is True


def test_non_object_json_falls_back(fake_chat):
    fake_chat.next_response = '["查询一", "查询二"]'
    result = rewrite_queries("问题", "hyde")

    assert result.queries == ["问题"]
    assert result.fallback is True
