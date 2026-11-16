"""PDF 加载器测试（不依赖 Ollama/Qdrant）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from private_rag.ingestion.files import (
    _is_scanned,
    _load_pdf_with_pdfplumber,
    _load_pdf_with_pypdf,
    load_file_documents,
)
from private_rag.sources import FileSource


def _make_mock_pdfplumber(pages):
    """构造一个可以打桩的 pdfplumber 替身：pdfplumber.open(path) 返回 pages 的上下文管理器。"""
    mock_pp = MagicMock()
    mock_pdf_obj = MagicMock()
    mock_pdf_obj.pages = pages
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_pdf_obj)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_pp.open = MagicMock(return_value=ctx)
    return mock_pp


# --- _is_scanned ---


def test_is_scanned_empty():
    assert _is_scanned("") is True
    assert _is_scanned("   ") is True


def test_is_scanned_text_not_scanned():
    assert _is_scanned("这是一段中文文本。") is False
    assert _is_scanned("This is English text.") is False


def test_is_scanned_scanned_low_chinese_ratio():
    # 大量无意义字符（纯标点/噪声），没有 CJK 或拉丁字母
    text = "！·…—（）【】《》" * 50
    assert _is_scanned(text) is True


# --- _load_pdf_with_pypdf (fallback) ---


def test_load_pdf_fallback_to_pypdf():
    """pdfplumber 不可用时回退到 PyPDFLoader。"""
    mock_page = MagicMock()
    mock_page.metadata = {"page": 0}
    mock_page.page_content = "测试文本内容。"

    mock_loader = MagicMock()
    mock_loader.load.return_value = [mock_page]

    with patch("private_rag.ingestion.files._try_import_pdfplumber", return_value=None):
        with patch("private_rag.ingestion.files.PyPDFLoader", return_value=mock_loader):
            docs, errors = _load_pdf_with_pypdf(
                Path("/tmp/test.pdf"),
                "data/docs/test.pdf",
                "file:/tmp/test.pdf",
                "2026-01-01T00:00:00",
                ["test"],
            )

    assert len(docs) == 1
    assert docs[0].text == "测试文本内容。"
    assert docs[0].extra["page"] == 1
    assert docs[0].extra["format"] == "pdf"
    assert errors == []


def test_load_pdf_fallback_empty_page():
    """PyPDFLoader 返回空页面时跳过。"""
    mock_page = MagicMock()
    mock_page.metadata = {"page": 0}
    mock_page.page_content = ""

    mock_loader = MagicMock()
    mock_loader.load.return_value = [mock_page]

    with patch("private_rag.ingestion.files._try_import_pdfplumber", return_value=None):
        with patch("private_rag.ingestion.files.PyPDFLoader", return_value=mock_loader):
            docs, errors = _load_pdf_with_pypdf(
                Path("/tmp/test.pdf"),
                "data/docs/test.pdf",
                "file:/tmp/test.pdf",
                "2026-01-01T00:00:00",
                [],
            )

    assert len(docs) == 0
    assert errors == []


# --- _load_pdf_with_pdfplumber ---


def test_load_pdf_text_only():
    """纯文本 PDF（无表格）正常加载。"""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "这是一段中文文本。"
    mock_page.extract_tables.return_value = []

    mock_pp = _make_mock_pdfplumber([mock_page])

    with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
        docs, errors = _load_pdf_with_pdfplumber(
            Path("/tmp/test.pdf"),
            "data/docs/test.pdf",
            "file:/tmp/test.pdf",
            "2026-01-01T00:00:00",
            ["test"],
        )

    assert len(docs) == 1
    assert docs[0].text == "这是一段中文文本。"
    assert docs[0].extra["page"] == 1
    assert docs[0].extra["scanned"] is False
    assert errors == []


def test_load_pdf_with_table():
    """文本 PDF 包含表格，表格作为独立文档入库。"""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "正文内容。"
    # pdfplumber extract_tables 返回 list of rows，每行是 tuple of cells
    # extract_tables 返回 list of tables，每个 table 是 list of rows
    mock_page.extract_tables.return_value = [
        [["姓名", "年龄"], ["张三", "28"]],
    ]

    mock_pp = _make_mock_pdfplumber([mock_page])

    with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
        docs, errors = _load_pdf_with_pdfplumber(
            Path("/tmp/test.pdf"),
            "data/docs/test.pdf",
            "file:/tmp/test.pdf",
            "2026-01-01T00:00:00",
            ["test"],
        )

    assert len(docs) == 2  # 正文 + 表格
    assert docs[0].extra["table"] == "test_table_1"
    assert docs[0].extra["page"] == 1
    assert docs[1].extra["page"] == 1
    assert "姓名" in docs[0].text
    assert "张三" in docs[0].text
    assert errors == []


def test_load_pdf_scanned_with_ocr():
    """扫描件 PDF 使用 OCR 提取文本。"""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""  # 扫描件无文本

    mock_ocr_result = MagicMock()
    mock_ocr_result.text = "OCR 识别结果。"

    mock_pp = _make_mock_pdfplumber([mock_page])

    with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
        with patch("private_rag.ingestion.ocr.ocr_page", return_value=mock_ocr_result):
            docs, errors = _load_pdf_with_pdfplumber(
                Path("/tmp/test.pdf"),
                "data/docs/test.pdf",
                "file:/tmp/test.pdf",
                "2026-01-01T00:00:00",
                ["test"],
            )

    assert len(docs) == 1
    assert docs[0].text == "OCR 识别结果。"
    assert docs[0].extra["scanned"] is True


def test_load_pdf_scanned_no_ocr_backend():
    """扫描件但无 OCR 后端时跳过该页。"""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""

    mock_pp = _make_mock_pdfplumber([mock_page])

    with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
        with patch("private_rag.ingestion.ocr._import_ocr", return_value=None):
            docs, errors = _load_pdf_with_pdfplumber(
                Path("/tmp/test.pdf"),
                "data/docs/test.pdf",
                "file:/tmp/test.pdf",
                "2026-01-01T00:00:00",
                ["test"],
            )

    assert len(docs) == 0


def test_load_pdf_multiple_pages():
    """多页 PDF 每页独立生成文档。"""
    mock_page1 = MagicMock()
    mock_page1.extract_text.return_value = "第一页内容。"
    mock_page1.extract_tables.return_value = []

    mock_page2 = MagicMock()
    mock_page2.extract_text.return_value = "第二页内容。"
    mock_page2.extract_tables.return_value = []

    mock_pp = _make_mock_pdfplumber([mock_page1, mock_page2])

    with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
        docs, errors = _load_pdf_with_pdfplumber(
            Path("/tmp/test.pdf"),
            "data/docs/test.pdf",
            "file:/tmp/test.pdf",
            "2026-01-01T00:00:00",
            ["test"],
        )

    assert len(docs) == 2
    assert docs[0].extra["page"] == 1
    assert docs[1].extra["page"] == 2


def test_load_pdf_empty_table_skipped():
    """空表格不生成文档。"""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "正文。"
    mock_page.extract_tables.return_value = [[None, None], [None, None]]

    mock_pp = _make_mock_pdfplumber([mock_page])

    with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
        docs, errors = _load_pdf_with_pdfplumber(
            Path("/tmp/test.pdf"),
            "data/docs/test.pdf",
            "file:/tmp/test.pdf",
            "2026-01-01T00:00:00",
            ["test"],
        )

    assert len(docs) == 1  # 只有正文，没有表格


def test_load_pdf_pdfplumber_import_fails():
    """pdfplumber 不可用时回退到 PyPDFLoader。"""
    mock_page = MagicMock()
    mock_page.metadata = {"page": 0}
    mock_page.page_content = "回退文本。"

    mock_loader = MagicMock()
    mock_loader.load.return_value = [mock_page]

    with patch("private_rag.ingestion.files._try_import_pdfplumber", return_value=None):
        with patch("private_rag.ingestion.files.PyPDFLoader", return_value=mock_loader):
            docs, errors = _load_pdf_with_pypdf(
                Path("/tmp/test.pdf"),
                "data/docs/test.pdf",
                "file:/tmp/test.pdf",
                "2026-01-01T00:00:00",
                [],
            )

    assert len(docs) == 1
    assert docs[0].text == "回退文本。"


# --- load_file_documents (integration) ---


def test_load_file_documents_txt(tmp_path):
    """TXT 文件正常加载。"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("这是一段测试文本。", encoding="utf-8")

    config = FileSource(path=str(tmp_path), tags=["test"])
    from private_rag.sources import source_key_for

    key = source_key_for(config)
    docs, errors = load_file_documents(config, key)

    assert len(docs) == 1
    assert docs[0].text == "这是一段测试文本。"
    assert docs[0].source_type == "file"


def test_load_file_documents_pdf_fallback(tmp_path):
    """PDF 文件在 pdfplumber 不可用时回退。"""
    # 创建一个空的 PDF 文件（仅用于测试回退逻辑）
    test_file = tmp_path / "test.pdf"
    test_file.write_bytes(b"%PDF-1.4\n")

    mock_page = MagicMock()
    mock_page.metadata = {"page": 0}
    mock_page.page_content = "回退文本。"

    mock_loader = MagicMock()
    mock_loader.load.return_value = [mock_page]

    with patch("private_rag.ingestion.files._try_import_pdfplumber", return_value=None):
        with patch("private_rag.ingestion.files.PyPDFLoader", return_value=mock_loader):
            config = FileSource(path=str(test_file), tags=["test"])
            from private_rag.sources import source_key_for

            key = source_key_for(config)
            docs, errors = load_file_documents(config, key)

    assert len(docs) == 1
    assert "回退文本" in docs[0].text
