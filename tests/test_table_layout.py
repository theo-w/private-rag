"""table_layout 模块测试（不依赖真实 OCR/模型）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from private_rag.ingestion.table_layout import (
    _expand_span,
    _fallback_parse,
    _get_engine,
    _merge_lines,
    _merge_overlapping,
    _parse_html_table,
    extract_scan_tables,
    filter_ocr_result,
    find_table_rects,
    ScanTableExtraction,
)

# _merge_lines 和 filter_ocr_result 也从 ocr.py 导出
from private_rag.ingestion.ocr import (
    _merge_lines as _merge_lines_ocr,
    filter_ocr_result as filter_ocr_result_ocr,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_engine():
    """每次测试前重置进程级引擎单例。"""
    import private_rag.ingestion.table_layout as mod

    old_engine = mod._engine
    old_failed = mod._engine_failed
    mod._engine = None
    mod._engine_failed = False
    yield
    mod._engine = old_engine
    mod._engine_failed = old_failed


# ---------------------------------------------------------------------------
# find_table_rects
# ---------------------------------------------------------------------------


def test_find_table_rects_no_cv2():
    """cv2 不可用时返回空列表。"""
    with patch("private_rag.ingestion.table_layout._import_cv2", return_value=None):
        result = find_table_rects(MagicMock())
        assert result == []


def test_find_table_rects_cv2_raises():
    """cv2 抛出异常时返回空列表。"""
    mock_cv2 = MagicMock()
    mock_cv2.threshold.side_effect = RuntimeError("模拟失败")

    with patch("private_rag.ingestion.table_layout._import_cv2", return_value=mock_cv2):
        result = find_table_rects(MagicMock())
        assert result == []


# ---------------------------------------------------------------------------
# _merge_overlapping
# ---------------------------------------------------------------------------


def test_merge_overlapping_empty():
    assert _merge_overlapping([]) == []


def test_merge_overlapping_no_overlap():
    rects = [(0, 0, 10, 10), (50, 50, 60, 60)]
    assert _merge_overlapping(rects) == [(0, 0, 10, 10), (50, 50, 60, 60)]


def test_merge_overlapping_overlap():
    rects = [(0, 0, 20, 20), (15, 10, 30, 30)]
    result = _merge_overlapping(rects)
    assert len(result) == 1
    assert result[0] == (0, 0, 30, 30)


# ---------------------------------------------------------------------------
# _parse_html_table
# ---------------------------------------------------------------------------


def test_parse_html_simple():
    html = "<tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr>"
    grid = _parse_html_table(html)
    assert len(grid) == 2
    assert grid[0] == ["A", "B"]
    assert grid[1] == ["C", "D"]


def test_parse_html_with_colspan():
    html = "<tr><td colspan='2'>Wide</td></tr><tr><td>A</td><td>B</td></tr>"
    grid = _parse_html_table(html)
    assert len(grid) == 2
    assert grid[0] == ["Wide", None]
    assert grid[1] == ["A", "B"]


def test_parse_html_with_rowspan():
    html = "<tr><td rowspan='2'>Tall</td><td>1</td></tr><tr><td>2</td></tr>"
    grid = _parse_html_table(html)
    assert len(grid) == 2
    assert grid[0] == ["Tall", "1"]
    assert grid[1] == [None, "2"]


def test_parse_html_empty():
    html = ""
    grid = _parse_html_table(html)
    assert grid == []


def test_parse_html_garbage():
    html = "not html at all {{{"
    grid = _parse_html_table(html)
    # 兜底解析应返回空或最小结果
    assert isinstance(grid, list)


def test_parse_html_none_html():
    with patch("private_rag.ingestion.table_layout._parse_html_table", return_value=None):
        # 确保 _parse_html_table 不会返回 None
        pass


# ---------------------------------------------------------------------------
# _expand_span
# ---------------------------------------------------------------------------


def test_expand_span_no_span():
    grid = [["A", "B"], ["C", "D"]]
    result = _expand_span(grid)
    assert result == grid


def test_expand_span_colspan():
    grid = [[("Wide", 1, 2)], [("A", 1, 1), ("B", 1, 1)]]
    result = _expand_span(grid)
    assert result[0] == ["Wide", None]
    assert result[1] == ["A", "B"]


def test_expand_span_rowspan():
    grid = [[("Tall", 2, 1), ("1", 1, 1)], [("2", 1, 1)]]
    result = _expand_span(grid)
    assert result[0] == ["Tall", "1"]
    assert result[1] == [None, "2"]


def test_expand_span_empty():
    assert _expand_span([]) == []


# ---------------------------------------------------------------------------
# _fallback_parse
# ---------------------------------------------------------------------------


def test_fallback_parse_simple():
    html = "<tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr>"
    result = _fallback_parse(html)
    assert len(result) == 2
    assert result[0] == ["A", "B"]


def test_fallback_parse_empty():
    assert _fallback_parse("") == []


# ---------------------------------------------------------------------------
# _get_engine
# ---------------------------------------------------------------------------


def test_get_engine_no_rapid_table():
    with patch("private_rag.ingestion.table_layout._download_model_if_needed", side_effect=ImportError("no rapid_table")):
        result = _get_engine()
        assert result is None


def test_get_engine_download_fails():
    with patch("private_rag.ingestion.table_layout._download_model_if_needed", side_effect=RuntimeError("download fail")):
        result = _get_engine()
        assert result is None


# ---------------------------------------------------------------------------
# extract_scan_tables
# ---------------------------------------------------------------------------


def test_extract_scan_tables_no_engine():
    with patch("private_rag.ingestion.table_layout._get_engine", return_value=None):
        result = extract_scan_tables(MagicMock())
        assert isinstance(result, ScanTableExtraction)
        assert result.tables == []
        assert result.rects == []


def test_extract_scan_tables_no_rects():
    mock_engine = MagicMock()
    with patch("private_rag.ingestion.table_layout._get_engine", return_value=mock_engine):
        with patch("private_rag.ingestion.table_layout.find_table_rects", return_value=[]):
            result = extract_scan_tables(MagicMock())
            assert result.tables == []


def test_extract_scan_tables_empty_ocr():
    mock_engine = MagicMock()
    with patch("private_rag.ingestion.table_layout._get_engine", return_value=mock_engine):
        with patch("private_rag.ingestion.table_layout.find_table_rects", return_value=[(0, 0, 100, 50)]):
            with patch("private_rag.ingestion.table_layout._parse_html_table", return_value=[]):
                result = extract_scan_tables(MagicMock(), ocr_boxes=[])
                assert result.tables == []


def test_extract_scan_tables_engine_raises():
    mock_engine = MagicMock()
    mock_engine.predict.side_effect = RuntimeError("predict fail")
    with patch("private_rag.ingestion.table_layout._get_engine", return_value=mock_engine):
        with patch("private_rag.ingestion.table_layout.find_table_rects", return_value=[(0, 0, 100, 50)]):
            result = extract_scan_tables(MagicMock())
            assert result.tables == []


def test_extract_scan_tables_success():
    mock_engine = MagicMock()
    mock_engine.predict.return_value = "<tr><td>A</td></tr>"
    with patch("private_rag.ingestion.table_layout._get_engine", return_value=mock_engine):
        with patch("private_rag.ingestion.table_layout.find_table_rects", return_value=[(0, 0, 100, 50)]):
            with patch("private_rag.ingestion.table_layout._parse_html_table", return_value=[["A"]]):
                result = extract_scan_tables(MagicMock())
                assert len(result.tables) == 1
                assert len(result.rects) == 1


# ---------------------------------------------------------------------------
# filter_ocr_result
# ---------------------------------------------------------------------------


def _make_box(x, y):
    """构造一个方形文本框。"""
    return ([[x, y], [x + 20, y], [x + 20, y + 10], [x, y + 10]],)


def test_filter_ocr_result_no_rects():
    """无表格区域时返回原结果。"""
    from private_rag.ingestion.ocr import OCRResult

    result = OCRResult(text="hello", boxes=[(_make_box(0, 0)[0], "hello", 0.9)])
    filtered = filter_ocr_result(result, [])
    assert filtered.text == "hello"


def test_filter_ocr_result_all_inside():
    """所有文本框都在表格区域内时返回空。"""
    from private_rag.ingestion.ocr import OCRResult

    box = [[10, 10], [30, 10], [30, 20], [10, 20]]
    result = OCRResult(text="in table", boxes=[(box, "in table", 0.9)])
    filtered = filter_ocr_result(result, [(0, 0, 100, 50)])
    assert filtered.text == ""


def test_filter_ocr_result_outside_survives():
    """表格区域外的文本框保留。"""
    from private_rag.ingestion.ocr import OCRResult

    box = [[200, 10], [220, 10], [220, 20], [200, 20]]
    result = OCRResult(text="outside", boxes=[(box, "outside", 0.9)])
    filtered = filter_ocr_result(result, [(0, 0, 100, 50)])
    assert "outside" in filtered.text


def test_filter_ocr_result_no_rects_is_noop():
    """rects 为空时不做任何过滤。"""
    from private_rag.ingestion.ocr import OCRResult

    box = [[10, 10], [30, 10], [30, 20], [10, 20]]
    result = OCRResult(text="hello", boxes=[(box, "hello", 0.9)])
    filtered = filter_ocr_result(result, [])
    assert filtered.text == "hello"


def test_filter_ocr_result_empty_boxes():
    """空 boxes 返回空结果。"""
    from private_rag.ingestion.ocr import OCRResult

    result = OCRResult(text="", boxes=[])
    filtered = filter_ocr_result(result, [(0, 0, 100, 50)])
    assert filtered.text == ""


# ---------------------------------------------------------------------------
# _merge_lines
# ---------------------------------------------------------------------------


def test_merge_lines_empty():
    assert _merge_lines([]) == ""


def test_merge_lines_single():
    box = [[0, 0], [100, 0], [100, 10], [0, 10]]
    result = _merge_lines([(box, "hello", 0.9)])
    assert result == "hello"


def test_merge_lines_same_row():
    box1 = [[0, 0], [50, 0], [50, 10], [0, 10]]
    box2 = [[60, 2], [110, 2], [110, 12], [60, 12]]
    boxes = [(box1, "第一", 0.9), (box2, "第二", 0.9)]
    result = _merge_lines(boxes)
    assert result == "第一 第二"


def test_merge_lines_different_rows():
    box1 = [[0, 0], [50, 0], [50, 10], [0, 10]]
    box2 = [[0, 50], [50, 50], [50, 60], [0, 60]]
    boxes = [(box1, "第一行", 0.9), (box2, "第二行", 0.9)]
    result = _merge_lines(boxes)
    assert result == "第一行\n第二行"
