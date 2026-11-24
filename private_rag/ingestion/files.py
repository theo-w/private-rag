"""本地文件连接器：txt / md / pdf。

PDF 支持：
- 文本型 PDF：使用 pdfplumber 逐页提取文本和表格。
- 扫描件 PDF：使用 pdfplumber 渲染页面 + rapidocr_onnxruntime OCR。
- 若 pdfplumber 不可用，回退到 PyPDFLoader（仅文本提取，无 OCR/表格）。
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader

from private_rag.config import ROOT
from private_rag.models import SourceDocument
from private_rag.sources import FileSource, resolve_path

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}

# 模块级 pdfplumber 导入（供测试打桩使用）
try:
    import pdfplumber  # noqa: F401
except ImportError:
    pdfplumber = None  # type: ignore[assignment]


def _try_import_pdfplumber():
    """返回模块级 pdfplumber，失败返回 None。供测试打桩使用。"""
    return pdfplumber


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _updated_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def iter_files(target: Path, include: list[str] | None = None) -> list[Path]:
    if target.is_file():
        return [target]
    files = sorted(
        path for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if include:
        files = [
            path for path in files
            if any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(str(path), pattern)
                   for pattern in include)
        ]
    return files


def _build_scan_backend(path: Path):
    """按 `RAG_SCAN_PARSER` 装配扫描件解析后端；`selfbuilt` 返回 None（走现有链路）。

    从 `private_rag.config` 取当前值而不是在模块导入时绑定，测试才能就地改开关。
    供测试打桩。
    """
    from private_rag import config
    from private_rag.ingestion.scan_parse import build_scan_backend

    return build_scan_backend(config.SCAN_PARSER, path)


def _is_scanned(text: str) -> bool:
    """判断页面是否为扫描件（文本为空或几乎没有可识别字符）。"""
    stripped = text.strip()
    if not stripped:
        return True
    # 检测是否有"实质字符"：CJK 统一表意文字（汉字）、拉丁字母
    has_cjk = any(0x4E00 <= ord(c) <= 0x9FFF for c in stripped)
    has_latin = any(c.isascii() and c.isalpha() for c in stripped)
    # 既没有汉字也没有拉丁字母，说明是纯标点/噪声 → 扫描件
    if not has_cjk and not has_latin:
        return True
    # 有实质字符，不算扫描件
    return False


def _load_pdf_with_pdfplumber(path: Path, relative: str, source_key: str,
                               updated_at: str, tags: list[str]) -> tuple[list[SourceDocument], list[str]]:
    """使用 pdfplumber 加载 PDF（支持 OCR 和表格提取）。"""
    documents: list[SourceDocument] = []
    errors: list[str] = []
    file_name = path.name
    file_stem = path.stem

    # v8 解析基准：外部解析后端整份 PDF 解析一次（模型冷启动只付一次），逐页取用。
    # 装配失败时把原因记进 errors 再改用自建链路——静默换链路会让第三方侧的读数混进自建侧。
    backend = None
    try:
        backend = _build_scan_backend(path)
    except Exception as exc:
        errors.append(f"扫描件解析后端不可用（{relative}）：{exc}；本文件改用自建链路")

    try:
        pdfplumber = _try_import_pdfplumber()
        with pdfplumber.open(str(path)) as pdf:
            if backend is not None and backend.page_count != len(pdf.pages):
                errors.append(
                    f"{backend.name} 返回 {backend.page_count} 页，PDF 实有 {len(pdf.pages)} 页；"
                    "缺的页改用自建链路，多出来的页忽略"
                )
            for page_index, page in enumerate(pdf.pages, start=1):
                # 提取文本
                text = page.extract_text() or ""
                scanned = _is_scanned(text)

                # 扫描件：外部解析后端整页给正文与表格，否则走自建 OCR + 版面表格提取
                if scanned:
                    parse = backend.parse_page(page_index) if backend is not None else None

                    if parse is not None:
                        # 后端已区分正文与表格，不需要表格矩形；正文为空但检出了表格仍要入库
                        if not parse.text.strip() and not parse.tables:
                            continue
                        text = parse.text
                        tables = parse.tables
                    else:
                        if backend is not None:
                            errors.append(
                                f"{backend.name} 未返回 {relative} 页 {page_index} 的结果，本页改用自建链路"
                            )

                        from private_rag.ingestion.ocr import render_page as _render_page
                        from private_rag.ingestion.ocr import ocr_image as _ocr_image
                        from private_rag.ingestion.ocr import filter_ocr_result as _filter_ocr
                        from private_rag.ingestion.ocr import OCRResult

                        # 渲染一次（避免重复渲染）
                        image = _render_page(page)

                        # OCR 识别
                        ocr_result = _ocr_image(image)
                        if not ocr_result.text.strip():
                            # 纯图片页无内容，跳过
                            continue

                        # 版面表格提取
                        table_rects: list[tuple[int, int, int, int]] = []
                        try:
                            from private_rag.config import SCAN_TABLE_ENABLED

                            if SCAN_TABLE_ENABLED:
                                from private_rag.ingestion.table_layout import (
                                    extract_scan_tables as _extract_scan_tables,
                                )
                                scan_result = _extract_scan_tables(image, ocr_result.boxes)
                                tables = scan_result.tables
                                table_rects = scan_result.rects
                            else:
                                tables = []
                        except Exception as exc:
                            errors.append(f"版面表格提取失败 {relative} 页 {page_index}：{exc}")
                            tables = []

                        # 过滤表格区域文本，避免正文与表格重复
                        text = _filter_ocr(ocr_result, table_rects).text

                    # 写入表格文档
                    for table_index, table_row in enumerate(tables, start=1):
                        table_text = ",".join(
                            ",".join(str(c) if c else "" for c in row)
                            for row in table_row
                        )
                        if not table_text.strip():
                            continue
                        documents.append(
                            SourceDocument(
                                doc_id=f"file:{relative}#page={page_index}#table={table_index}",
                                text=table_text,
                                title=f"{file_name} 第 {page_index} 页 表格 {table_index}",
                                source_type="file",
                                source_uri=relative,
                                source_key=source_key,
                                updated_at=updated_at,
                                tags=list(tags),
                                extra={
                                    "page": page_index,
                                    "table": f"{file_stem}_table_{table_index}",
                                    "file_name": file_name,
                                    "format": "pdf",
                                    "scanned": True,
                                },
                            )
                        )

                    # 用过滤后的 OCR 文本作为正文
                    text = text

                else:
                    # 文本型 PDF：提取表格
                    from private_rag.ingestion.ocr import extract_tables as _extract_tables
                    tables = _extract_tables(page, table_label=file_stem, is_scanned=False)

                    for table_index, table_info in enumerate(tables, start=1):
                        table_text = table_info["text"]
                        documents.append(
                            SourceDocument(
                                doc_id=f"file:{relative}#page={page_index}#table={table_index}",
                                text=table_text,
                                title=f"{file_name} 第 {page_index} 页 表格 {table_index}",
                                source_type="file",
                                source_uri=relative,
                                source_key=source_key,
                                updated_at=updated_at,
                                tags=list(tags),
                                extra={
                                    "page": page_index,
                                    "table": f"{file_stem}_table_{table_index}",
                                    "file_name": file_name,
                                    "format": "pdf",
                                    "scanned": False,
                                },
                            )
                        )

                # 正文
                if text.strip():
                    documents.append(
                        SourceDocument(
                            doc_id=f"file:{relative}#page={page_index}",
                            text=text,
                            title=f"{file_name} 第 {page_index} 页",
                            source_type="file",
                            source_uri=relative,
                            source_key=source_key,
                            updated_at=updated_at,
                            tags=list(tags),
                            extra={
                                "page": page_index,
                                "file_name": file_name,
                                "format": "pdf",
                                "scanned": scanned,
                            },
                        )
                    )

    except Exception as exc:
        errors.append(f"pdfplumber 读取失败 {relative}: {exc}")

    return documents, errors


def _load_pdf_with_pypdf(path: Path, relative: str, source_key: str,
                          updated_at: str, tags: list[str]) -> tuple[list[SourceDocument], list[str]]:
    """回退方案：使用 PyPDFLoader 加载 PDF（仅文本提取，无 OCR/表格）。"""
    documents: list[SourceDocument] = []
    errors: list[str] = []
    file_name = path.name

    try:
        for index, page in enumerate(PyPDFLoader(str(path)).load(), start=1):
            page_number = int(page.metadata.get("page", index - 1)) + 1
            text = page.page_content.strip()
            if not text:
                continue
            documents.append(
                SourceDocument(
                    doc_id=f"file:{relative}#page={page_number}",
                    text=text,
                    title=f"{file_name} 第 {page_number} 页",
                    source_type="file",
                    source_uri=relative,
                    source_key=source_key,
                    updated_at=updated_at,
                    tags=list(tags),
                    extra={"page": page_number, "file_name": file_name, "format": "pdf"},
                )
            )
    except Exception as exc:
        errors.append(f"PyPDFLoader 读取失败 {relative}: {exc}")

    return documents, errors


def load_file_documents(source: FileSource, source_key: str) -> tuple[list[SourceDocument], list[str]]:
    """把一个文件或目录展开成文档单元；PDF 按页展开。"""
    errors: list[str] = []
    documents: list[SourceDocument] = []
    target = resolve_path(source.path)
    files = iter_files(target, source.include)
    if not files:
        errors.append(f"没有找到可入库文件：{target}")
        return documents, errors

    for path in files:
        relative = _relative(path)
        updated_at = _updated_at(path)
        try:
            if path.suffix.lower() == ".pdf":
                # 优先使用 pdfplumber（支持 OCR + 表格）
                if _try_import_pdfplumber() is not None:
                    docs, errs = _load_pdf_with_pdfplumber(path, relative, source_key, updated_at, list(source.tags))
                else:
                    docs, errs = _load_pdf_with_pypdf(path, relative, source_key, updated_at, list(source.tags))
                documents.extend(docs)
                errors.extend(errs)
            else:
                text = path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                documents.append(
                    SourceDocument(
                        doc_id=f"file:{relative}",
                        text=text,
                        title=path.name,
                        source_type="file",
                        source_uri=relative,
                        source_key=source_key,
                        updated_at=updated_at,
                        tags=list(source.tags),
                        extra={"file_name": path.name, "format": path.suffix.lower().lstrip(".")},
                    )
                )
        except Exception as exc:  # 单个文件失败不影响其他文件
            errors.append(f"读取失败 {relative}: {exc}")

    return documents, errors
