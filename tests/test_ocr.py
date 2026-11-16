"""OCR 与表格提取模块测试（不依赖 Ollama/Qdrant）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from private_rag.ingestion.ocr import (
    OCRResult,
    _find_tables_on_image,
    _format_table,
    _import_ocr,
    _import_pdfplumber,
    extract_tables,
    ocr_image,
    ocr_page,
)


def test_ocr_result_default_empty():
    r = OCRResult()
    assert r.text == ""
    assert r.boxes == []


def test_import_ocr_returns_class():
    result = _import_ocr()
    # 本机已安装 rapidocr，应返回类而非 None
    assert result is not None


def test_import_pdfplumber_returns_module():
    result = _import_pdfplumber()
    # 本机已安装 pdfplumber
    assert result is not None


def test_ocr_image_no_backend():
    with patch("private_rag.ingestion.ocr._import_ocr", return_value=None):
        result = ocr_image(MagicMock())
        assert result.text == ""
        assert result.boxes == []


def test_ocr_image_backend_raises():
    mock_ocr = _import_ocr()
    assert mock_ocr is not None

    with patch("private_rag.ingestion.ocr._import_ocr") as mock_import:
        mock_import.return_value = MagicMock(side_effect=RuntimeError("模拟加载失败"))
        result = ocr_image(MagicMock())
        assert result.text == ""


def test_ocr_page_backend_raises():
    mock_page = MagicMock()
    mock_page.to_image.side_effect = RuntimeError("模拟渲染失败")

    with patch("private_rag.ingestion.ocr._import_ocr", return_value=None):
        result = ocr_page(mock_page)
        assert result.text == ""


def test_format_table_with_header():
    table_info = {
        "header": ["姓名", "年龄", "城市"],
        "rows": [
            ["张三", "28", "北京"],
            ["李四", "35", "上海"],
        ],
    }
    text = _format_table(table_info)
    assert text == "姓名,年龄,城市\n张三,28,北京\n李四,35,上海"


def test_format_table_no_header():
    table_info = {"header": [], "rows": [["a", "b"]]}
    text = _format_table(table_info)
    assert text == "a,b"


def test_format_table_empty():
    table_info = {"header": [], "rows": []}
    text = _format_table(table_info)
    assert text == ""


def test_extract_tables_no_backend():
    with patch("private_rag.ingestion.ocr._import_pdfplumber", return_value=None):
        result = extract_tables(MagicMock())
        assert result == []


def test_extract_tables_backend_raises():
    mock_page = MagicMock()
    mock_page.extract_tables.side_effect = RuntimeError("模拟提取失败")

    mock_pp = MagicMock()
    mock_pp.Page = MagicMock

    with patch("private_rag.ingestion.ocr._import_pdfplumber", return_value=mock_pp):
        result = extract_tables(mock_page)
        assert result == []


def test_find_tables_on_image_no_extract_method():
    mock_image = MagicMock()
    del mock_image.extract_tables  # 确保没有 extract_tables 方法

    mock_page = MagicMock()
    mock_page.to_image.return_value = mock_image

    result = _find_tables_on_image(mock_page)
    assert result == []


def test_find_tables_on_image_raises():
    mock_page = MagicMock()
    mock_page.to_image.side_effect = RuntimeError("模拟失败")

    result = _find_tables_on_image(mock_page)
    assert result == []
