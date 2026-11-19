"""v5 父子块切分、父块扩展与多查询融合测试（不依赖 Ollama/Qdrant）。"""

from __future__ import annotations

import uuid

from private_rag.ingestion.chunking import (
    build_splitter,
    parent_point_id,
    split_parent_child,
)
from private_rag.models import SourceDocument
from private_rag.retrieval.hybrid import (
    RetrievedChunk,
    fuse_query_results,
    reciprocal_rank_fusion,
)
from private_rag.retrieval.lexical import LexicalHit
from private_rag.retrieval.parent import context_chars, expand_parents


def _document(text: str = "第一段内容。第二段内容。第三段内容。") -> SourceDocument:
    return SourceDocument(
        doc_id="doc-1",
        text=text,
        title="测试文档",
        source_type="file",
        source_uri="data/docs/test.md",
        source_key="file:data/docs/test.md",
    )


def _chunk(
    point_id: str,
    text: str = "子块内容",
    *,
    parent_id: str | None = None,
    parent_text: str | None = None,
    parent_index: int | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        point_id=point_id,
        text=text,
        metadata={"source_uri": "README.md"},
        parent_id=parent_id,
        parent_text=parent_text,
        parent_index=parent_index,
    )


def test_parent_point_id_is_stable_and_unique():
    first = parent_point_id("doc-1", "hash-1", 0)
    second = parent_point_id("doc-1", "hash-1", 0)
    other = parent_point_id("doc-1", "hash-1", 1)
    assert first == second
    assert first != other
    assert first == str(
        uuid.uuid5(uuid.NAMESPACE_URL, "doc-1:hash-1:parent:0")
    )


def test_split_parent_child_keeps_parent_child_relationships():
    document = _document()
    children = split_parent_child(
        document,
        parent_splitter=build_splitter(20, 0),
        child_splitter=build_splitter(8, 2),
    )

    assert children
    parent_ids = {child.parent_id for child in children}
    assert children[0].parent_count == len(parent_ids)
    assert len(parent_ids) == len({child.parent_index for child in children})

    for index in range(len(parent_ids)):
        group = [child for child in children if child.parent_index == index]
        assert group
        assert group[0].parent_chunk_index == 0
        assert [child.parent_chunk_index for child in group] == list(range(len(group)))
        for child in group:
            assert child.text in child.parent_text
            assert len(child.parent_text) <= 20
            assert child.parent_id == parent_point_id(
                document.doc_id,
                document.content_hash,
                index,
            )


def test_split_parent_child_is_deterministic_and_handles_empty_text():
    document = _document()
    first = split_parent_child(
        document,
        parent_splitter=build_splitter(20, 0),
        child_splitter=build_splitter(8, 2),
    )
    second = split_parent_child(
        document,
        parent_splitter=build_splitter(20, 0),
        child_splitter=build_splitter(8, 2),
    )
    assert [(child.text, child.parent_id) for child in first] == [
        (child.text, child.parent_id) for child in second
    ]

    assert split_parent_child(_document(" \n\n "), parent_splitter=build_splitter(20, 0)) == []


def test_expand_parents_deduplicates_and_uses_parent_text():
    parent_text = "父块完整内容。" * 10
    chunks = [
        _chunk("p1", "命中子块一", parent_id="parent-1", parent_text=parent_text, parent_index=0),
        _chunk("p2", "命中子块二", parent_id="parent-1", parent_text=parent_text, parent_index=0),
        _chunk("p3", "命中子块三", parent_id="parent-2", parent_text="另一个父块。", parent_index=1),
    ]

    expanded = expand_parents(chunks, max_chars=1000)

    assert [chunk.point_id for chunk in expanded] == ["p1", "p3"]
    assert expanded[0].text == parent_text
    assert expanded[0].snippet_text == "命中子块一"
    assert expanded[0].parent_id == "parent-1"
    assert context_chars(expanded) == len(parent_text) + len("另一个父块。")


def test_expand_parents_falls_back_to_child_and_truncates():
    parent_text = "父" * 100
    child = _chunk(
        "p1",
        "子" * 50,
        parent_id="parent-1",
        parent_text=parent_text,
        parent_index=0,
    )

    fallback = expand_parents([child], max_chars=60)
    assert fallback[0].text == "子" * 50
    assert fallback[0].snippet_text == "子" * 50

    truncated = expand_parents([child], max_chars=20)
    assert truncated[0].text == "子" * 20
    assert truncated[0].snippet_text == "子" * 50

    assert expand_parents([child], max_chars=0) == []


def test_expand_parents_keeps_legacy_chunks_without_parent():
    chunks = [_chunk("p1", "旧子块一"), _chunk("p2", "旧子块二")]
    expanded = expand_parents(chunks, max_chars=100)
    assert [chunk.text for chunk in expanded] == ["旧子块一", "旧子块二"]
    assert [chunk.snippet_text for chunk in expanded] == ["旧子块一", "旧子块二"]
    assert all(chunk.parent_id is None for chunk in expanded)


def test_fuse_query_results_accumulates_rrf_and_preserves_parent():
    parent_text = "父块完整内容。"
    first = [
        _chunk("a"),
        _chunk("b", parent_id="parent-b", parent_text=parent_text, parent_index=0),
    ]
    second = [
        _chunk("b", parent_id="parent-b", parent_text=parent_text, parent_index=0),
        _chunk("c"),
    ]

    fused = fuse_query_results([first, second], top_k=3)

    assert [chunk.point_id for chunk in fused] == ["b", "a", "c"]
    assert fused[0].rrf_score > fused[1].rrf_score
    assert fused[0].parent_id == "parent-b"
    assert fused[0].parent_text == parent_text


def test_reciprocal_rank_fusion_preserves_parent_fields():
    dense = [
        _chunk("p1", "子块", parent_id="parent-1", parent_text="父块", parent_index=0)
    ]
    fused = reciprocal_rank_fusion(dense, [], top_k=3)
    assert fused[0].parent_id == "parent-1"
    assert fused[0].parent_text == "父块"

    lexical = [
        LexicalHit(
            point_id="p2",
            text="另一个子块",
            metadata={},
            score=1.0,
            parent={"id": "parent-2", "text": "另一个父块", "index": 1},
        )
    ]
    fused = reciprocal_rank_fusion([], lexical, top_k=3)
    assert fused[0].parent_id == "parent-2"
    assert fused[0].parent_text == "另一个父块"


def test_retrieved_chunk_from_payload_supports_legacy_and_parent():
    legacy = RetrievedChunk.from_payload(
        "p1",
        {"page_content": "旧子块", "metadata": {"source_uri": "README.md"}},
    )
    assert legacy.point_id == "p1"
    assert legacy.text == "旧子块"
    assert legacy.parent_id is None

    current = RetrievedChunk.from_payload(
        "p2",
        {
            "page_content": "子块",
            "metadata": {"source_uri": "README.md"},
            "parent": {"id": "parent-1", "text": "父块", "index": 0},
        },
        dense_score=0.9,
    )
    assert current.parent_id == "parent-1"
    assert current.parent_text == "父块"
    assert current.parent_index == 0
    assert current.dense_score == 0.9


def test_list_preserved_as_unit():
    """列表引导句与列表本体不应被截断到不同父块。"""
    intro = "这里有五个关键角色：\n"
    items = "\n".join(f"- 角色{i}：描述{i}" for i in range(1, 6))
    text = intro + items

    document = _document(text)
    children = split_parent_child(
        document,
        parent_splitter=build_splitter(200, 0),
        child_splitter=build_splitter(70, 10),
    )

    assert children
    # 整个列表应落在同一个父块内（不超过 200 字符）
    parent_texts = {child.parent_text for child in children}
    assert len(parent_texts) == 1
    parent_text = list(parent_texts)[0]
    # 父块应包含引导句和所有列表项
    assert "关键角色" in parent_text
    assert "角色5" in parent_text


def test_table_preserved_as_unit():
    """Markdown 表格应整体保留，不跨行截断。"""
    text = (
        "以下是产品列表：\n"
        "| 名称 | 价格 |\n"
        "|------|------|\n"
        "| 苹果 | 5 元 |\n"
        "| 香蕉 | 3 元 |\n"
        "| 橙子 | 8 元 |\n"
    )

    document = _document(text)
    children = split_parent_child(
        document,
        parent_splitter=build_splitter(200, 0),
        child_splitter=build_splitter(70, 10),
    )

    assert children
    parent_texts = {child.parent_text for child in children}
    assert len(parent_texts) == 1
    parent_text = list(parent_texts)[0]
    assert "苹果" in parent_text
    assert "橙子" in parent_text


def test_heading_forces_boundary():
    """标题应作为硬边界，切分点在标题处。"""
    text = (
        "# 第一章\n"
        "第一章内容。" * 20 + "\n\n"
        "# 第二章\n"
        "第二章内容。" * 20
    )

    document = _document(text)
    children = split_parent_child(
        document,
        parent_splitter=build_splitter(200, 0),
        child_splitter=build_splitter(70, 10),
    )

    assert children
    parent_texts = [child.parent_text for child in children]
    # 应至少有两个父块，分别以不同标题开头
    headings_found = set()
    for pt in parent_texts:
        if pt.startswith("# "):
            headings_found.add(pt.split("\n")[0])
    assert len(headings_found) >= 2
