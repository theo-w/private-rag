"""OCR 与表格提取模块测试（不依赖 Ollama/Qdrant）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from private_rag.ingestion.ocr import (
    OCRResult,
    _format_table,
    _import_ocr,
    _import_pdfplumber,
    extract_tables,
    ocr_image,
    ocr_page,
    render_page,
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


def test_ocr_image_parses_rapidocr_result():
    """回归测试：按 rapidocr 真实返回结构解析（result 是三元组，elapse 是耗时列表）。

    早期实现把第二个返回值当边界框列表排序，对 float 取下标抛
    "'float' object is not subscriptable"，每条文本又固定取 result[0]，
    导致扫描件 OCR 静默返回空文本。
    """
    from PIL import Image

    box_line1 = [[67.0, 68.0], [316.0, 68.0], [316.0, 95.0], [67.0, 95.0]]
    box_line2 = [[67.0, 140.0], [600.0, 140.0], [600.0, 167.0], [67.0, 167.0]]
    fake_result = [
        [box_line2, "第二行内容", 0.91],
        [box_line1, "第一行内容", 0.98],
    ]
    fake_engine = MagicMock(return_value=(fake_result, [0.09, 0.004, 0.18]))
    # _import_ocr 返回的是 OCR 类，ocr_image 内部还要再实例化一次
    fake_cls = MagicMock(return_value=fake_engine)

    with patch("private_rag.ingestion.ocr._import_ocr", return_value=fake_cls):
        result = ocr_image(Image.new("RGB", (100, 80), "white"))

    # 按 y 排序，且每行用自己的文本
    assert result.text == "第一行内容\n第二行内容"
    assert [b[1] for b in result.boxes] == ["第一行内容", "第二行内容"]
    assert result.boxes[0][2] == 0.98


def test_ocr_image_empty_result():
    """无识别结果时返回空。"""
    from PIL import Image

    fake_engine = MagicMock(return_value=(None, [0.01, 0.0, 0.0]))
    fake_cls = MagicMock(return_value=fake_engine)
    with patch("private_rag.ingestion.ocr._import_ocr", return_value=fake_cls):
        result = ocr_image(Image.new("RGB", (50, 50), "white"))

    # 断言引擎真的被调用过，避免"异常被吞掉"造成的假通过
    assert fake_engine.called
    assert result.text == ""
    assert result.boxes == []


def test_render_page_uses_high_resolution(tmp_path):
    """回归测试：OCR 渲染分辨率必须高于 pdfplumber 默认的 72 dpi。"""
    from PIL import Image
    import pdfplumber

    pdf_path = tmp_path / "hi.pdf"
    Image.new("RGB", (200, 120), "white").save(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        image = render_page(page)
        low = page.to_image(resolution=72).original

    assert image.size[0] > low.size[0]


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


def test_render_page_unwraps_page_image(tmp_path):
    """回归测试：render_page 必须交出 PIL Image，而不是 PageImage 包装对象。

    pdfplumber 的 page.to_image() 返回 PageImage，直接 np.array() 会得到形状 ()
    的 0 维对象，rapidocr 会以 "ndim(0) of the img is not in [2, 3]" 拒收，
    导致扫描件 OCR 静默返回空文本。
    """
    from PIL import Image
    import numpy as np
    import pdfplumber

    pdf_path = tmp_path / "render.pdf"
    Image.new("RGB", (200, 120), "white").save(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        image = render_page(pdf.pages[0])

    assert hasattr(image, "size")
    array = np.array(image)
    assert array.ndim in (2, 3)


def test_ocr_page_passes_pil_image_to_ocr_image():
    """回归测试：传给 ocr_image 的必须是 PIL Image（有 .size）。"""
    from PIL import Image

    wrapper = MagicMock()
    wrapper.to_image.return_value.original = Image.new("RGB", (60, 40), "white")
    wrapper.to_image.return_value.to_pil = None  # 走 .original 分支

    with patch("private_rag.ingestion.ocr.ocr_image") as mock_ocr_image:
        mock_ocr_image.return_value = OCRResult(text="识别结果")
        result = ocr_page(wrapper)

    passed = mock_ocr_image.call_args[0][0]
    assert isinstance(passed, Image.Image)
    assert result.text == "识别结果"
