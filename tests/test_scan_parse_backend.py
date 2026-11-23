"""扫描件解析后端替身的测试（全部打桩，不装第三方解析器、不起子进程）。"""

from __future__ import annotations

import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "parse_drivers"))

from _grid import cells_to_grid, html_to_grid, markdown_table_to_grid  # noqa: E402
from private_rag import config  # noqa: E402
from private_rag.ingestion.files import (  # noqa: E402
    _build_scan_backend,
    _load_pdf_with_pdfplumber,
    load_file_documents,
)
from private_rag.ingestion.table_layout import ScanTableExtraction  # noqa: E402
from private_rag.ingestion.scan_parse import (  # noqa: E402
    SCAN_PARSERS,
    ScanBackend,
    ScanBackendError,
    ScanPageParse,
    build_scan_backend,
    cache_key,
    normalize_grid,
    parse_payload,
    read_cache,
    write_cache,
)
from private_rag.sources import FileSource  # noqa: E402

RELATIVE = "eval/fixtures/scan.pdf"


def payload(backend: str = "docling", pages=None) -> dict:
    if pages is None:
        pages = [
            {
                "page": 1,
                "text": "正文一行",
                "tables": [{"rows": [["产品", "数量"], ["键盘", "12"]]}],
                "notes": [],
            }
        ]
    return {"backend": backend, "elapsed_ms": 1234, "pages": pages, "warnings": []}


def backend_with(pages: dict) -> ScanBackend:
    return ScanBackend(name="docling", pages=pages, backend_ms=100)


def mock_pdfplumber(page_count: int):
    """pdfplumber 替身：每页 extract_text 返回空串 → 判为扫描件。"""
    pages = []
    for _ in range(page_count):
        page = MagicMock()
        page.extract_text = MagicMock(return_value="")
        pages.append(page)
    pdf_obj = MagicMock()
    pdf_obj.pages = pages
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=pdf_obj)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_pp = MagicMock()
    mock_pp.open = MagicMock(return_value=ctx)
    return mock_pp


@contextmanager
def stub_selfbuilt_ocr(text: str):
    """把自建链路打桩成「只出正文、没有表格」，用于验回退（四个补丁要同时生效）。"""
    ocr_result = MagicMock()
    ocr_result.text = text
    ocr_result.boxes = []
    filtered = MagicMock()
    filtered.text = text
    with ExitStack() as stack:
        stack.enter_context(patch("private_rag.ingestion.ocr.ocr_image", return_value=ocr_result))
        stack.enter_context(patch("private_rag.ingestion.ocr.render_page", return_value=None))
        stack.enter_context(
            patch("private_rag.ingestion.table_layout.extract_scan_tables",
                  return_value=ScanTableExtraction())
        )
        stack.enter_context(patch("private_rag.ingestion.ocr.filter_ocr_result",
                                  return_value=filtered))
        yield


def load_with_backend(tmp_path: Path, backend, page_count: int = 1):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with patch("private_rag.ingestion.files._build_scan_backend", return_value=backend):
        with patch("private_rag.ingestion.files.pdfplumber", mock_pdfplumber(page_count)):
            return _load_pdf_with_pdfplumber(
                pdf, RELATIVE, "file:scan", "2026-01-01T00:00:00", ["eval"]
            )


# --------------------------------------------------------------------------- #
# 驱动输出契约
# --------------------------------------------------------------------------- #


def test_parse_payload_reads_pages_and_tables():
    built = parse_payload(payload(), "docling")
    assert built.name == "docling"
    assert built.backend_ms == 1234
    assert built.page_count == 1
    page = built.parse_page(1)
    assert page.text == "正文一行"
    assert page.tables == [[["产品", "数量"], ["键盘", "12"]]]
    assert built.parse_page(2) is None


def test_parse_payload_rejects_wrong_backend():
    with pytest.raises(ScanBackendError, match="自报后端"):
        parse_payload(payload("mineru"), "docling")


def test_parse_payload_rejects_empty_pages():
    with pytest.raises(ScanBackendError, match="没有给出任何页"):
        parse_payload(payload(pages=[]), "docling")


def test_parse_payload_rejects_bad_page_number():
    with pytest.raises(ScanBackendError, match="页号非法"):
        parse_payload(payload(pages=[{"page": 0, "text": "x", "tables": []}]), "docling")


def test_parse_payload_rejects_non_string_text():
    with pytest.raises(ScanBackendError, match="text 不是字符串"):
        parse_payload(payload(pages=[{"page": 1, "text": None, "tables": []}]), "docling")


def test_parse_payload_accepts_bare_row_grids():
    """驱动给 `tables: [[[...]]]`（不带 rows 包装）也要收得下。"""
    built = parse_payload(
        payload(pages=[{"page": 1, "text": "t", "tables": [[["a", "b"]]]}]), "docling"
    )
    assert built.parse_page(1).tables == [[["a", "b"]]]


def test_parse_payload_keeps_warnings_as_notes():
    data = payload()
    data["warnings"] = ["docling 转换状态：partial"]
    assert parse_payload(data, "docling").notes == ["docling 转换状态：partial"]


def test_normalize_grid_coerces_none_and_bad_rows():
    assert normalize_grid([["a", None, 3], "skip", ["b"]]) == [["a", "", "3"], ["b"]]
    assert normalize_grid(None) == []


# --------------------------------------------------------------------------- #
# 后端开关与虚拟环境
# --------------------------------------------------------------------------- #


def test_selfbuilt_backend_needs_no_process():
    assert build_scan_backend("selfbuilt", None) is None


def test_external_backend_without_path_is_error():
    with pytest.raises(ScanBackendError, match="需要 PDF 路径"):
        build_scan_backend("docling", None)


@pytest.mark.parametrize("name", ["", None])
def test_empty_backend_name_defaults_to_selfbuilt(name):
    assert build_scan_backend(name, None) is None


def test_build_scan_backend_rejects_unknown_name():
    with pytest.raises(ScanBackendError, match="不在可选后端里"):
        build_scan_backend("paddleocr-vl", Path("/tmp/x.pdf"))


def test_build_scan_backend_missing_venv_reports_actionable_hint(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ScanBackendError, match="找不到外部解析器的 Python 解释器"):
        build_scan_backend("docling", pdf, python=tmp_path / "nope" / "python",
                           cache_dir=tmp_path / "cache")


def test_build_scan_backend_reads_current_config_switch(monkeypatch):
    """开关在调用时取 config.SCAN_PARSER，测试能就地改（入库前才设环境变量的既有约定）。"""
    monkeypatch.setattr(config, "SCAN_PARSER", "not-a-backend")
    with pytest.raises(ScanBackendError, match="不在可选后端里"):
        _build_scan_backend(Path("/tmp/x.pdf"))


# --------------------------------------------------------------------------- #
# 解析结果缓存
# --------------------------------------------------------------------------- #


def test_cache_key_changes_with_pdf_content_and_backend(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"one")
    b = tmp_path / "b.pdf"
    b.write_bytes(b"two")
    assert cache_key(a, "docling") != cache_key(b, "docling")
    assert cache_key(a, "docling") != cache_key(a, "mineru")


def test_cache_roundtrip_and_reuse(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cache_dir = tmp_path / "cache"
    data = payload()
    write_cache(data, pdf, "docling", cache_dir)
    assert read_cache(pdf, "docling", cache_dir) == data
    assert read_cache(pdf, "mineru", cache_dir) is None

    def boom(*args, **kwargs):
        raise AssertionError("命中缓存就不该再跑驱动")

    with patch("private_rag.ingestion.scan_parse.run_driver", boom):
        built = build_scan_backend("docling", pdf, cache_dir=cache_dir)
    assert built.from_cache is True
    assert built.parse_page(1).tables == [[["产品", "数量"], ["键盘", "12"]]]


def test_refresh_skips_cache_read_but_still_writes(tmp_path):
    """preflight 的 `refresh`：不能被旧缓存糊过去，但这次的结果要写回去供入库复用。"""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cache_dir = tmp_path / "cache"
    stale = payload(pages=[{"page": 1, "text": "旧缓存结果", "tables": [], "notes": []}])
    write_cache(stale, pdf, "docling", cache_dir)

    fresh = payload(pages=[{"page": 1, "text": "驱动新结果", "tables": [], "notes": []}])
    with patch("private_rag.ingestion.scan_parse.run_driver", return_value=fresh):
        built = build_scan_backend("docling", pdf, cache_dir=cache_dir, refresh=True)
    assert built.from_cache is False
    assert built.parse_page(1).text == "驱动新结果"
    assert read_cache(pdf, "docling", cache_dir)["pages"][0]["text"] == "驱动新结果"

    def boom(*args, **kwargs):
        raise AssertionError("写回以后不带 refresh 就该命中新缓存")

    with patch("private_rag.ingestion.scan_parse.run_driver", boom):
        reused = build_scan_backend("docling", pdf, cache_dir=cache_dir)
    assert reused.from_cache is True
    assert reused.parse_page(1).text == "驱动新结果"


def test_cache_key_changes_with_variant(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"one")
    assert cache_key(pdf, "mineru") != cache_key(pdf, "mineru", "basic")
    assert cache_key(pdf, "docling") == cache_key(pdf, "docling", None)


def test_mineru_cache_is_per_tier(tmp_path, monkeypatch):
    """换 `RAG_MINERU_TIER` 必须重解析：否则旧档的缓存会冒充新档的读数。"""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(config, "MINERU_TIER", "standard")

    data = payload("mineru")
    write_cache(data, pdf, "mineru", cache_dir, "standard")
    assert read_cache(pdf, "mineru", cache_dir, "standard") == data
    assert read_cache(pdf, "mineru", cache_dir, "basic") is None

    def boom(*args, **kwargs):
        raise AssertionError("standard 档已有缓存，不该再跑驱动")

    with patch("private_rag.ingestion.scan_parse.run_driver", boom):
        built = build_scan_backend("mineru", pdf, cache_dir=cache_dir)
    assert built.from_cache is True
    assert (cache_dir / cache_key(pdf, "mineru", "standard")).is_file()

    monkeypatch.setattr(config, "MINERU_TIER", "basic")
    with patch("private_rag.ingestion.scan_parse.run_driver", return_value=data):
        rebuilt = build_scan_backend("mineru", pdf, cache_dir=cache_dir)
    assert rebuilt.from_cache is False
    assert (cache_dir / cache_key(pdf, "mineru", "basic")).is_file()


def test_read_cache_ignores_corrupt_file(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / cache_key(pdf, "docling")).write_text("{not json", encoding="utf-8")
    assert read_cache(pdf, "docling", cache_dir) is None


# --------------------------------------------------------------------------- #
# files.py 入库链路
# --------------------------------------------------------------------------- #


def test_external_backend_produces_table_and_body_documents(tmp_path):
    """外部后端整页给正文与表格：doc_id / extra.table / 逗号序列化与自建侧完全一致。"""
    backend = backend_with({
        1: ScanPageParse(
            text="正文一行", tables=[[["产品", "数量"], ["键盘", "12"]]], backend="docling"
        )
    })
    with patch("private_rag.ingestion.ocr.ocr_image") as ocr_image:
        docs, errors = load_with_backend(tmp_path, backend)
    ocr_image.assert_not_called()  # 整页换后端时不再跑自建 OCR
    assert errors == []

    table_doc = docs[0]
    assert table_doc.doc_id == f"file:{RELATIVE}#page=1#table=1"
    assert table_doc.text == "产品,数量,键盘,12"
    assert table_doc.extra["table"] == "scan_table_1"
    assert table_doc.extra["page"] == 1
    assert table_doc.extra["scanned"] is True

    body_doc = docs[1]
    assert body_doc.doc_id == f"file:{RELATIVE}#page=1"
    assert body_doc.text == "正文一行"
    assert body_doc.extra["scanned"] is True


def test_external_backend_empty_body_with_tables_still_indexes_table(tmp_path):
    """后端只认出表格、正文为空时表格仍要入库（自建链路那时会把整页跳过）。"""
    backend = backend_with({
        1: ScanPageParse(text="", tables=[[["a", "b"]]], backend="docling")
    })
    docs, errors = load_with_backend(tmp_path, backend)
    assert [d.doc_id for d in docs] == [f"file:{RELATIVE}#page=1#table=1"]


def test_external_backend_missing_page_falls_back_and_records_error(tmp_path):
    """后端没给某页 → 回退自建链路，并把这件事写进 errors，不静默丢内容。"""
    with patch("private_rag.ingestion.files._build_scan_backend",
               return_value=backend_with({})):
        with patch("private_rag.ingestion.files.pdfplumber", mock_pdfplumber(1)):
            with stub_selfbuilt_ocr("自建 OCR 正文"):
                docs, errors = _load_pdf_with_pdfplumber(
                    tmp_path / "scan.pdf", RELATIVE, "file:scan",
                    "2026-01-01T00:00:00", ["eval"],
                )
    assert [d.text for d in docs] == ["自建 OCR 正文"]
    assert any("未返回" in err and "改用自建链路" in err for err in errors)


def test_backend_assembly_failure_records_error_and_keeps_selfbuilt(tmp_path):
    """装配后端就失败（缺 venv）→ 记错误并整份走自建链路。"""
    def boom(path):
        raise ScanBackendError("找不到外部解析器的 Python 解释器")

    with patch("private_rag.ingestion.files._build_scan_backend", boom):
        with patch("private_rag.ingestion.files.pdfplumber", mock_pdfplumber(1)):
            with stub_selfbuilt_ocr("自建正文"):
                docs, errors = _load_pdf_with_pdfplumber(
                    tmp_path / "scan.pdf", RELATIVE, "file:scan",
                    "2026-01-01T00:00:00", ["eval"],
                )
    assert [d.text for d in docs] == ["自建正文"]
    assert any("解析后端不可用" in err for err in errors)


def test_page_count_mismatch_is_recorded(tmp_path):
    """后端给的页数与 PDF 实有页数不一致要记错误：缺页回退自建，多页忽略。"""
    backend = backend_with({
        1: ScanPageParse(text="后端正文", tables=[], backend="docling"),
        2: ScanPageParse(text="多出来的一页", tables=[], backend="docling"),
    })
    with patch("private_rag.ingestion.ocr.ocr_image") as ocr_image:
        docs, errors = load_with_backend(tmp_path, backend, page_count=1)
    ocr_image.assert_not_called()  # PDF 只有 1 页，那页后端有结果
    assert [d.text for d in docs] == ["后端正文"]
    assert any("实有 1 页" in err for err in errors)


def test_load_file_documents_uses_backend_for_pdf(tmp_path):
    backend = backend_with({
        1: ScanPageParse(text="后端正文", tables=[[["a", "b"]]], backend="docling")
    })
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    source = FileSource(type="file", path=str(pdf), tags=["eval"])
    with patch("private_rag.ingestion.files._build_scan_backend", return_value=backend):
        with patch("private_rag.ingestion.files.pdfplumber", mock_pdfplumber(1)):
            docs, errors = load_file_documents(source, "file:scan")
    assert errors == []
    assert docs[0].text == "a,b"
    assert docs[0].doc_id.endswith("#page=1#table=1")
    assert docs[1].text == "后端正文"
    assert docs[1].doc_id.endswith("#page=1")


# --------------------------------------------------------------------------- #
# 驱动共用工具（scripts/parse_drivers/_grid.py）
# --------------------------------------------------------------------------- #


def test_html_to_grid_expands_colspan_and_rowspan():
    html = (
        "<table>"
        "<tr><td>A</td><td>B</td><td>C</td></tr>"
        "<tr><td rowspan='2'>D</td><td>E</td><td>F</td></tr>"
        "<tr><td>G</td><td>H</td></tr>"
        "<tr><td colspan='2'>I</td><td>J</td></tr>"
        "</table>"
    )
    assert html_to_grid(html) == [
        ["A", "B", "C"],
        ["D", "E", "F"],
        ["D", "G", "H"],
        ["I", "I", "J"],
    ]


def test_html_to_grid_handles_th_and_entities():
    assert html_to_grid("<tr><th>&amp;</th><th>x</th></tr>") == [["&", "x"]]


def test_html_to_grid_empty_input():
    assert html_to_grid("") == []
    assert html_to_grid("no table here") == []


def test_cells_to_grid_from_docling_span_entries():
    grid = cells_to_grid([("A", 0, 0, 1, 1), ("B", 0, 1, 2, 1), ("C", 1, 0, 1, 1)])
    assert grid == [["A", "B"], ["C", "B"]]


def test_cells_to_grid_empty():
    assert cells_to_grid([]) == []


def test_markdown_table_to_grid_drops_separator():
    md = "| 产品 | 数量 |\n| --- | --- |\n| 键盘 | 12 |"
    assert markdown_table_to_grid(md) == [["产品", "数量"], ["键盘", "12"]]
