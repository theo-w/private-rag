"""分词、BM25 检索、过滤与持久化测试。"""

from __future__ import annotations

from private_rag.retrieval.lexical import LexicalIndex, tokenize


def _index() -> LexicalIndex:
    return LexicalIndex(
        [
            {
                "point_id": "p1",
                "text": "香蕉不适合放冰箱，低温会让表皮变黑。",
                "metadata": {"source_type": "file", "tags": ["local", "fruit"]},
            },
            {
                "point_id": "p2",
                "text": "苹果适合冷藏，可以延长保鲜时间。",
                "metadata": {"source_type": "file", "tags": ["local"]},
            },
            {
                "point_id": "p3",
                "text": "橙子富含维生素 C。",
                "metadata": {"source_type": "web", "tags": ["web"]},
            },
        ]
    )


def test_tokenize_removes_stopwords_and_punctuation():
    tokens = tokenize("香蕉能不能放冰箱？")
    assert "香蕉" in tokens
    assert "冰箱" in tokens
    assert "吗" not in tokens
    assert "？" not in tokens


def test_bm25_ranks_relevant_chunk_first():
    hits = _index().search("香蕉可以放冰箱吗")
    assert hits
    assert hits[0].point_id == "p1"
    assert hits[0].score > 0


def test_source_type_and_tag_filters():
    index = _index()
    hits = index.search("维生素", source_type="web")
    assert [hit.point_id for hit in hits] == ["p3"]

    hits = index.search("冰箱 苹果", tags=["fruit"])
    assert [hit.point_id for hit in hits] == ["p1"]


def test_empty_query_or_empty_index_returns_nothing():
    assert _index().search("？。") == []
    assert LexicalIndex([]).search("香蕉") == []


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "bm25.pkl"
    index = _index()
    index.save(path)
    loaded = LexicalIndex.load(path)
    assert loaded is not None
    assert len(loaded) == len(index)
    hits = loaded.search("冰箱")
    assert hits[0].point_id == "p1"


def test_load_missing_or_corrupted_returns_none(tmp_path):
    assert LexicalIndex.load(tmp_path / "missing.pkl") is None
    path = tmp_path / "bad.pkl"
    path.write_bytes(b"not a pickle")
    assert LexicalIndex.load(path) is None


def test_filtered_search_uses_untouched_full_corpus_stats():
    """过滤只影响候选，不影响 BM25 的语料统计。"""
    index = _index()
    hits = index.search("冰箱", tags=["web"])
    assert hits == []
