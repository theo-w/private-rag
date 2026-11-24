"""树索引（v8 第 4 项）测试：标题解析、章节构建、命中还原与检索接线。

全部用内存里的假数据，不调用模型、不联网、不打开真实 Qdrant。
"""

from __future__ import annotations

import pickle
from types import SimpleNamespace

from private_rag.agent.runtime import AgentRuntime
from private_rag.agent.tools import KnowledgeSearchTool, ToolRegistry
from private_rag.qa import RagEngine, RetrievalOptions, tree_enabled_for_intent
from private_rag.query_rewrite import RewriteResult
from private_rag.retrieval.hybrid import RetrievedChunk
from private_rag.retrieval.tree import (
    PATH_PREFIX,
    TreeIndex,
    attach_tree_context,
    build_from_store,
    build_index,
    locate_chunk,
    parse_heading,
    tree_context_for_chunk,
    window_around_hit,
)

DOC_TITLE = "测试文档"

# 三个父块：边界与切分器一致（标题行是硬边界），互不重叠
P_PARENT = "# 顶层标题\n开头铺垫，讲清楚整体流程。\n"
C_PARENT = (
    "## 小节一\n"
    "小节一的正文，里面有五个关键角色：甲、乙、丙、丁、戊。\n"
    "### 更深一层\n"
    "更深一层的内容，只属于这个小节。\n"
)
D_PARENT = "## 小节二\n小节二的正文，另一段材料。\n"


def _record(point_id: str, text: str, parent_id: str, parent_index: int) -> dict:
    return {
        "point_id": point_id,
        "text": text,
        "metadata": {"doc_id": "doc-1", "title": DOC_TITLE, "chunk_index": parent_index},
        "parent": {"id": parent_id, "index": parent_index, "text": PARENTS[parent_id]},
    }


PARENTS = {"p0": P_PARENT, "p1": C_PARENT, "p2": D_PARENT}

RECORDS = [
    _record("c_top", "开头铺垫，讲清楚整体流程。", "p0", 0),
    _record("c_mid", "小节一的正文，里面有五个关键角色：甲、乙、丙、丁、戊。", "p1", 1),
    _record("c_deep", "更深一层的内容，只属于这个小节。", "p1", 1),
    _record("c_two", "小节二的正文，另一段材料。", "p2", 2),
]


def _chunk(point_id: str, *, parent_id: str | None = None) -> RetrievedChunk:
    record = next(item for item in RECORDS if item["point_id"] == point_id)
    parent_text = PARENTS[parent_id] if parent_id else None
    return RetrievedChunk(
        point_id=point_id,
        text=record["text"],
        metadata={"source_uri": "data/docs/test.md", "doc_id": "doc-1"},
        parent_id=parent_id,
        parent_text=parent_text,
        snippet_text=record["text"],
    )


def _index() -> TreeIndex:
    return build_index(RECORDS)


# --------------------------------------------------------------------------- #
# 标题解析
# --------------------------------------------------------------------------- #


def test_parse_heading_reads_level_and_title():
    assert parse_heading("# 一级") == (1, "一级")
    assert parse_heading("   ### 三级带缩进   ") == (3, "三级带缩进")
    assert parse_heading("## 关闭式写法 ##") == (2, "关闭式写法")


def test_parse_heading_rejects_non_headings_and_seven_hashes():
    assert parse_heading("正文一行") is None
    assert parse_heading("#") is None
    assert parse_heading("####### 七级不算标题") is None
    assert parse_heading("使用 # 井号举例的正文") is None


# --------------------------------------------------------------------------- #
# 构建
# --------------------------------------------------------------------------- #


def test_build_index_creates_nested_sections_with_paths():
    index = _index()

    assert index.stats() == {"docs": 1, "nodes": 5, "sections": 4, "chunks": 4}
    assert [node.level for node in index.nodes.values() if node.is_root] == [0]

    paths = {
        point_id: index.path_label(index.node_of_chunk(point_id))
        for point_id in ("c_top", "c_mid", "c_deep", "c_two")
    }
    assert paths["c_top"] == "测试文档 › 顶层标题"
    assert paths["c_mid"] == "测试文档 › 顶层标题 › 小节一"
    assert paths["c_two"] == "测试文档 › 顶层标题 › 小节二"
    assert paths["c_deep"] == "测试文档 › 顶层标题 › 小节一 › 更深一层"


def test_chunk_inside_subsection_gets_deeper_node():
    index = _index()
    node = index.node_of_chunk("c_deep")

    # 子块与父块同属 p1，但它的正文落在「更深一层」里，应当归到那一级
    assert node.level == 3
    assert index.path_label(node) == "测试文档 › 顶层标题 › 小节一 › 更深一层"
    assert "c_deep" in node.chunk_ids


def test_section_text_is_bounded_by_headings():
    index = _index()
    node = index.node_of_chunk("c_mid")

    assert node.text.startswith("## 小节一")
    assert "五个关键角色" in node.text
    # 片段到下一个标题为止：子章节的内容不混进来
    assert "更深一层的内容" not in node.text


def test_chunk_spanning_headings_falls_back_to_parent_section():
    spanning = RetrievedChunk(
        point_id="c_span",
        text="小节一的正文，里面有五个关键角色：甲、乙、丙、丁、戊。\n### 更深一层\n更深一层的内容",
        metadata={"doc_id": "doc-1"},
        parent_id="p1",
        parent_text=C_PARENT,
    )
    index = build_index(
        RECORDS
        + [
            {
                "point_id": "c_span",
                "text": spanning.text,
                "metadata": {"doc_id": "doc-1", "title": DOC_TITLE},
                "parent": {"id": "p1", "index": 1, "text": C_PARENT},
            }
        ]
    )

    # 归到父块起点所在的那一级，而不是更深一级
    assert index.path_label(index.node_of_chunk("c_span")) == "测试文档 › 顶层标题 › 小节一"
    # 该块的正文跨出了片段，还原不出上下文，查询时会退回父块文本
    assert tree_context_for_chunk(spanning, index, max_chars=2000) is None


def test_records_without_parent_fall_back_to_child_order():
    records = [
        {
            "point_id": f"o{i}",
            "text": text,
            "metadata": {"doc_id": "doc-2", "title": "无父块文档", "chunk_index": i},
            "parent": None,
        }
        for i, text in enumerate(["# 只有一个标题\n第一段。", "第二段。"])
    ]
    index = build_index(records)

    assert index.stats()["chunks"] == 2
    assert index.node_of_chunk("o0").title == "只有一个标题"
    # 第二个块没有标题行，仍在同一章节里
    assert index.node_of_chunk("o1").title == "只有一个标题"


def test_record_without_doc_id_becomes_its_own_document():
    index = build_index(
        [{"point_id": "x", "text": "# 只有一级\n没有 doc_id 的块", "metadata": {}, "parent": None}]
    )

    # 宁可让它自己成一个文档（路径退化成块 ID），也不要把块丢掉
    assert index.stats() == {"docs": 1, "nodes": 2, "sections": 1, "chunks": 1}
    assert index.node_of_chunk("x").doc_id == "x"


def test_build_from_store_reads_qdrant_payload_without_opening_it(monkeypatch, tmp_path):
    def fake_scroll_all(client, *, with_payload=True):
        assert with_payload is True
        for record in RECORDS:
            yield SimpleNamespace(
                id=record["point_id"],
                payload={
                    "page_content": record["text"],
                    "metadata": record["metadata"],
                    "parent": record["parent"],
                },
            )

    monkeypatch.setattr("private_rag.retrieval.tree.scroll_all", fake_scroll_all)
    path = tmp_path / "tree.pkl"

    nodes = build_from_store(object(), path)

    assert nodes == 5
    index = TreeIndex.load(path)
    assert index is not None
    assert index.stats()["chunks"] == 4


# --------------------------------------------------------------------------- #
# 落盘与读取
# --------------------------------------------------------------------------- #


def test_save_and_load_roundtrip(tmp_path):
    index = _index()
    path = tmp_path / "tree.pkl"
    index.save(path)

    loaded = TreeIndex.load(path)
    assert loaded is not None
    assert loaded.nodes == index.nodes
    assert loaded.chunk_node == index.chunk_node
    assert loaded.path_label(loaded.node_of_chunk("c_mid")) == index.path_label(
        index.node_of_chunk("c_mid")
    )


def test_load_returns_none_for_missing_version_mismatch_and_corrupt(tmp_path):
    assert TreeIndex.load(tmp_path / "absent.pkl") is None

    stale = tmp_path / "stale.pkl"
    stale.write_bytes(pickle.dumps({"version": TreeIndex.VERSION + 1, "nodes": {}, "chunk_node": {}}))
    assert TreeIndex.load(stale) is None

    broken = tmp_path / "broken.pkl"
    broken.write_bytes(b"not a pickle")
    assert TreeIndex.load(broken) is None

    wrong_type = tmp_path / "wrong.pkl"
    wrong_type.write_bytes(pickle.dumps({"version": TreeIndex.VERSION, "nodes": {"a": "b"}}))
    assert TreeIndex.load(wrong_type) is None


# --------------------------------------------------------------------------- #
# 定位与开窗
# --------------------------------------------------------------------------- #


def test_locate_chunk_ignores_whitespace_differences():
    section = "## 小节一\n小节一的正文，里面有五个关键角色。\n"
    hit = locate_chunk("小节一的正文，里面有\n五个关键角色。", section)

    assert hit is not None
    assert section[hit[0]:hit[1]].replace("\n", "") == "小节一的正文，里面有五个关键角色。"


def test_locate_chunk_returns_none_when_absent():
    assert locate_chunk("没有这句", "## 小节一\n别的正文") is None
    assert locate_chunk("", "## 小节一\n别的正文") is None


def test_window_around_hit_keeps_hit_inside():
    section = "A" * 100 + "[命中块]" + "B" * 100
    hit = (100, 105)

    window = window_around_hit(section, hit, max_chars=30)
    assert "[命中块]" in window
    assert len(window) <= 30


def test_window_borrows_unused_budget_from_blocked_side():
    section = "开头铺垫" + "命中块" + "后面的内容" * 10
    hit = (4, 7)

    # 命中块贴着开头，前面的额度让给后面
    window = window_around_hit(section, hit, max_chars=40)
    assert window.startswith("开头铺垫")
    assert len(window) > 3 + 2 + 10
    assert hit[0] >= section.find(window)
    assert hit[1] <= section.find(window) + len(window)


def test_window_with_zero_budget_is_empty():
    assert window_around_hit("内容", (0, 2), max_chars=0) == ""


# --------------------------------------------------------------------------- #
# 还原成章节上下文
# --------------------------------------------------------------------------- #


def test_tree_context_for_chunk_returns_label_and_section_text():
    context = tree_context_for_chunk(_chunk("c_mid"), _index(), max_chars=2000)

    assert context is not None
    assert context.label == "测试文档 › 顶层标题 › 小节一"
    assert "五个关键角色" in context.text


def test_tree_context_for_unknown_chunk_is_none():
    assert tree_context_for_chunk(_chunk("c_top"), build_index([]), max_chars=2000) is None


def test_attach_tree_context_prefixes_path_and_respects_budget():
    chunks = [_chunk("c_mid", parent_id="p1"), _chunk("c_two", parent_id="p2")]
    out = attach_tree_context(chunks, _index(), max_chars=6000)

    assert len(out) == 2
    assert out[0].text.startswith(PATH_PREFIX + "测试文档 › 顶层标题 › 小节一\n")
    assert out[1].text.startswith(PATH_PREFIX + "测试文档 › 顶层标题 › 小节二\n")
    assert out[0].tree_node_id == _index().node_of_chunk("c_mid").node_id
    # 引用摘要仍是命中的子块原文
    assert out[0].snippet_text == _chunk("c_mid").text
    assert sum(len(chunk.text) for chunk in out) <= 6000


def test_attach_tree_context_dedupes_same_section():
    first = _chunk("c_mid", parent_id="p1")
    # 同一章节里的另一块，内容已被第一段的窗口覆盖
    inside = RetrievedChunk(
        point_id="c_inside",
        text="小节一的正文",
        metadata={"doc_id": "doc-1"},
        parent_id="p1",
        parent_text=C_PARENT,
        snippet_text="小节一的正文",
    )
    index = build_index(
        RECORDS
        + [
            {
                "point_id": "c_inside",
                "text": "小节一的正文",
                "metadata": {"doc_id": "doc-1", "title": DOC_TITLE},
                "parent": {"id": "p1", "index": 1, "text": C_PARENT},
            }
        ]
    )

    out = attach_tree_context([first, inside], index, max_chars=6000)

    assert len(out) == 1
    assert out[0].point_id == "c_mid"


def test_attach_tree_context_falls_back_to_parent_text_when_unlocatable():
    spanning = RetrievedChunk(
        point_id="c_span",
        text="小节一的正文\n### 更深一层\n更深一层的内容",
        metadata={"doc_id": "doc-1"},
        parent_id="p1",
        parent_text=C_PARENT,
        snippet_text="小节一的正文",
    )

    out = attach_tree_context([spanning], _index(), max_chars=6000)

    assert len(out) == 1
    assert out[0].text == C_PARENT
    assert out[0].tree_node_id is None
    assert out[0].snippet_text == "小节一的正文"


def test_attach_tree_context_truncates_to_remaining_budget():
    chunks = [_chunk("c_mid", parent_id="p1"), _chunk("c_two", parent_id="p2")]

    out = attach_tree_context(chunks, _index(), max_chars=30)

    assert len(out) >= 1
    assert sum(len(chunk.text) for chunk in out) <= 30


# --------------------------------------------------------------------------- #
# 检索链路接线
# --------------------------------------------------------------------------- #


class FakeClient:
    def collection_exists(self, name):
        return True

    def count(self, name):
        return SimpleNamespace(count=4)

    def close(self):
        pass


def _engine(tree: TreeIndex | None) -> RagEngine:
    return RagEngine(FakeClient(), object(), None, None, tree)


def _patch_retriever(monkeypatch, chunks):
    class FakeRetriever:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve(self, question):
            return list(chunks)

    monkeypatch.setattr("private_rag.qa.HybridRetriever", FakeRetriever)
    monkeypatch.setattr(
        "private_rag.qa.rewrite_queries",
        lambda question, mode, *, model: RewriteResult([question]),
    )


def test_tree_path_is_off_by_default_and_keeps_parent_expansion(monkeypatch):
    _patch_retriever(monkeypatch, [_chunk("c_mid", parent_id="p1")])
    engine = _engine(_index())

    trace = engine.retrieve_with_trace("问题", RetrievalOptions(use_rerank=False))

    assert trace.used_tree is False
    assert trace.used_parent is True
    assert PATH_PREFIX not in trace.chunks[0].text
    assert trace.chunks[0].text == C_PARENT


def test_tree_path_used_when_option_is_on(monkeypatch):
    _patch_retriever(
        monkeypatch, [_chunk("c_mid", parent_id="p1"), _chunk("c_two", parent_id="p2")]
    )
    engine = _engine(_index())

    trace = engine.retrieve_with_trace(
        "问题", RetrievalOptions(use_rerank=False, use_tree=True, top_k=2)
    )

    assert trace.used_tree is True
    assert trace.used_parent is False
    assert trace.tree_sections == 2
    assert trace.chunks[0].text.startswith(PATH_PREFIX)
    assert trace.context_chars == sum(len(chunk.text) for chunk in trace.chunks)


def test_tree_switch_can_be_flipped_in_process(monkeypatch):
    _patch_retriever(monkeypatch, [_chunk("c_mid", parent_id="p1")])
    engine = _engine(_index())
    options = RetrievalOptions(use_rerank=False)

    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", True)
    on = engine.retrieve_with_trace("问题", options)
    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", False)
    off = engine.retrieve_with_trace("问题", options)

    assert on.used_tree is True
    assert off.used_tree is False
    assert off.chunks[0].text == C_PARENT


def test_tree_option_falls_back_to_parent_when_index_missing(monkeypatch):
    _patch_retriever(monkeypatch, [_chunk("c_mid", parent_id="p1")])
    engine = _engine(None)

    trace = engine.retrieve_with_trace("问题", RetrievalOptions(use_rerank=False, use_tree=True))

    assert trace.used_tree is False
    assert trace.used_parent is True
    assert trace.tree_sections == 0


def test_readiness_warns_only_when_switch_is_on_but_index_missing(monkeypatch):
    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", True)
    assert any("树索引" in w for w in _engine(None).readiness().warnings)
    assert not any("树索引" in w for w in _engine(_index()).readiness().warnings)

    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", False)
    assert not any("树索引" in w for w in _engine(None).readiness().warnings)


def test_tree_intent_gate_only_allows_knowledge(monkeypatch):
    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", True)
    assert tree_enabled_for_intent("knowledge") is True
    assert tree_enabled_for_intent("") is True  # 经典链路没有意图，按知识处理
    assert tree_enabled_for_intent("mixed") is False
    assert tree_enabled_for_intent("database") is False
    assert tree_enabled_for_intent("none") is False

    # 退回按意图收窄之前的行为：任何意图都启用
    monkeypatch.setattr("private_rag.qa.TREE_KNOWLEDGE_ONLY", False)
    assert tree_enabled_for_intent("mixed") is True

    # 总开关关着的时候，意图再怎么判都不启用
    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", False)
    assert tree_enabled_for_intent("knowledge") is False


def test_mixed_intent_keeps_parent_expansion(monkeypatch):
    _patch_retriever(monkeypatch, [_chunk("c_mid", parent_id="p1")])
    engine = _engine(_index())
    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", True)

    trace = engine.retrieve_with_trace(
        "问题", RetrievalOptions(use_rerank=False, intent="mixed")
    )

    assert trace.used_tree is False
    assert trace.used_parent is True
    assert trace.chunks[0].text == C_PARENT


def test_knowledge_intent_uses_tree_without_explicit_option(monkeypatch):
    _patch_retriever(monkeypatch, [_chunk("c_mid", parent_id="p1")])
    engine = _engine(_index())
    monkeypatch.setattr("private_rag.qa.TREE_INDEX_ENABLED", True)

    trace = engine.retrieve_with_trace(
        "问题", RetrievalOptions(use_rerank=False, intent="knowledge")
    )

    assert trace.used_tree is True
    assert trace.chunks[0].text.startswith(PATH_PREFIX)


def test_kb_search_tool_passes_its_intent_into_retrieval():
    captured: dict = {}

    class FakeEngine:
        def retrieve_with_trace(self, query, options):
            captured["intent"] = options.intent
            return SimpleNamespace(
                chunks=[], queries=[query], context_chars=0,
                used_parent=False, used_rerank=False, used_tree=False,
            )

    tool = KnowledgeSearchTool(FakeEngine())
    assert tool.intent == ""  # 装配时还没有意图
    tool.set_intent("mixed")
    tool.run({"query": "问题"})
    assert captured["intent"] == "mixed"


def test_runtime_syncs_intent_only_onto_tools_that_accept_it():
    class TakesIntent:
        name = "kb_search"

        def __init__(self) -> None:
            self.intent = None

        def set_intent(self, intent: str) -> None:
            self.intent = intent

    class PlainTool:
        name = "sql_query"

    kb = TakesIntent()
    runtime = AgentRuntime(ToolRegistry([kb, PlainTool()]))
    runtime._sync_tool_intent(ToolRegistry([kb, PlainTool()]), "knowledge")

    assert kb.intent == "knowledge"
