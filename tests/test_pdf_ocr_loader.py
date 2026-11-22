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
    from PIL import Image
    import os

    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""  # 扫描件无文本

    mock_image = Image.new("RGB", (200, 120), "white")
    mock_page.to_image.return_value = MagicMock(
        original=mock_image, to_pil=None
    )

    mock_ocr_result = MagicMock()
    mock_ocr_result.text = "OCR 识别结果。"
    mock_ocr_result.boxes = []

    mock_pp = _make_mock_pdfplumber([mock_page])

    # 关闭版面表格提取，避免测试依赖 rapid-table
    old_env = os.environ.get("RAG_DISABLE_SCAN_TABLE")
    os.environ["RAG_DISABLE_SCAN_TABLE"] = "1"
    try:
        with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
            with patch("private_rag.ingestion.ocr.ocr_image", return_value=mock_ocr_result):
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
    finally:
        if old_env is not None:
            os.environ["RAG_DISABLE_SCAN_TABLE"] = old_env
        else:
            os.environ.pop("RAG_DISABLE_SCAN_TABLE", None)


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


# --- 联动单测：扫描件 + 版面表格 + 正文过滤 ---


def test_load_pdf_scanned_with_scan_tables_and_filter(tmp_path):
    """端到端测试：扫描件页面同时有 OCR 正文和版面表格。

    验证：
    1. 表格作为独立 SourceDocument 入库
    2. 正文文本不包含表格区域内容（被 filter_ocr_result 过滤）
    3. 页码和 scanned 标记正确
    """
    from PIL import Image
    import os

    # 模拟扫描件页面：无文本，需要 OCR
    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""
    mock_page.extract_tables.return_value = []

    # 模拟渲染图像
    mock_image = Image.new("RGB", (400, 300), "white")
    mock_page.to_image.return_value = MagicMock(
        original=mock_image, to_pil=None
    )

    # 模拟 OCR 结果：包含正文和表格区域的文本框
    # 表格区域：(50, 50) 到 (200, 150)
    # 表格内文本框：中心在 (75, 75) → 应被过滤
    # 表格外文本框：中心在 (300, 250) → 应保留
    table_box = [[60, 60], [90, 60], [90, 90], [60, 90]]  # 中心 (75, 75)
    outside_box = [[280, 240], [320, 240], [320, 270], [280, 270]]  # 中心 (300, 255)

    mock_ocr_result = MagicMock()
    mock_ocr_result.text = "表格内容\n正文内容"
    mock_ocr_result.boxes = [
        (table_box, "表格内容", 0.95),
        (outside_box, "正文内容", 0.92),
    ]

    mock_pp = _make_mock_pdfplumber([mock_page])

    # 模拟版面表格提取结果（带 rects 用于过滤）
    mock_scan_result = MagicMock()
    mock_scan_result.tables = [
        [["姓名", "年龄"], ["张三", "28"]],
    ]
    mock_scan_result.rects = [(50, 50, 200, 150)]

    # 开启版面表格提取
    old_env = os.environ.get("RAG_DISABLE_SCAN_TABLE")
    os.environ.pop("RAG_DISABLE_SCAN_TABLE", None)
    try:
        with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
            with patch("private_rag.ingestion.ocr.ocr_image", return_value=mock_ocr_result):
                with patch(
                    "private_rag.ingestion.table_layout.extract_scan_tables",
                    return_value=mock_scan_result,
                ):
                    docs, errors = _load_pdf_with_pdfplumber(
                        Path("/tmp/scanned.pdf"),
                        "data/docs/scanned.pdf",
                        "file:/tmp/scanned.pdf",
                        "2026-01-01T00:00:00",
                        ["test"],
                    )

        # 应该有 2 个文档：表格 + 正文
        assert len(docs) == 2
        # 表格文档
        assert docs[0].extra["table"] == "scanned_table_1"
        assert docs[0].extra["scanned"] is True
        assert "姓名" in docs[0].text
        # 正文文档：表格内容应被过滤
        assert docs[1].extra["page"] == 1
        assert docs[1].extra["scanned"] is True
        assert "正文内容" in docs[1].text
        assert "表格内容" not in docs[1].text
    finally:
        if old_env is not None:
            os.environ["RAG_DISABLE_SCAN_TABLE"] = old_env
        else:
            os.environ.pop("RAG_DISABLE_SCAN_TABLE", None)


def test_load_pdf_scanned_with_table_document(tmp_path):
    """端到端测试：扫描件页面有版面表格，表格作为独立文档入库。"""
    from PIL import Image
    import os

    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""
    mock_page.extract_tables.return_value = []

    mock_image = Image.new("RGB", (400, 300), "white")
    mock_page.to_image.return_value = MagicMock(
        original=mock_image, to_pil=None
    )

    # OCR 结果：只有表格区域外的正文
    outside_box = [[280, 240], [320, 240], [320, 270], [280, 270]]
    mock_ocr_result = MagicMock()
    mock_ocr_result.text = "正文内容"
    mock_ocr_result.boxes = [(outside_box, "正文内容", 0.92)]

    mock_pp = _make_mock_pdfplumber([mock_page])

    # 模拟版面表格提取结果
    mock_scan_result = MagicMock()
    mock_scan_result.tables = [
        [["姓名", "年龄"], ["张三", "28"]],
    ]
    mock_scan_result.rects = [(50, 50, 200, 150)]

    old_env = os.environ.get("RAG_DISABLE_SCAN_TABLE")
    os.environ.pop("RAG_DISABLE_SCAN_TABLE", None)  # 开启版面表格
    try:
        with patch("private_rag.ingestion.files.pdfplumber", mock_pp):
            with patch("private_rag.ingestion.ocr.ocr_image", return_value=mock_ocr_result):
                with patch(
                    "private_rag.ingestion.table_layout.extract_scan_tables",
                    return_value=mock_scan_result,
                ):
                    docs, errors = _load_pdf_with_pdfplumber(
                        Path("/tmp/scanned.pdf"),
                        "data/docs/scanned.pdf",
                        "file:/tmp/scanned.pdf",
                        "2026-01-01T00:00:00",
                        ["test"],
                    )

        # 应该有 2 个文档：表格 + 正文
        assert len(docs) == 2
        # 表格文档
        assert docs[0].extra["table"] == "scanned_table_1"
        assert docs[0].extra["scanned"] is True
        assert "姓名" in docs[0].text
        assert "张三" in docs[0].text
        # 正文文档
        assert docs[1].extra["page"] == 1
        assert docs[1].extra["scanned"] is True
        assert "正文内容" in docs[1].text
    finally:
        if old_env is not None:
            os.environ["RAG_DISABLE_SCAN_TABLE"] = old_env
        else:
            os.environ.pop("RAG_DISABLE_SCAN_TABLE", None)
