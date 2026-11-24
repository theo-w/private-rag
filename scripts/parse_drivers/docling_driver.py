"""在 Python 3.11 虚拟环境里用 docling 解析整份 PDF，输出统一 JSON。

调用方是 `private_rag/ingestion/scan_parse.py`（主进程钉在 3.9，docling 自 2.70.0 起不要 3.9）。
本脚本只用 docling 与标准库，不 import `private_rag`——那边是 langchain / qdrant 等主进程依赖，
这个虚拟环境里没有装，也不需要。

输出契约（与 scan_parse.parse_payload 对齐）：

    {"backend": "docling", "elapsed_ms": 4120,
     "pages": [{"page": 1, "text": "...", "tables": [{"rows": [["产品","数量","单价"]]}], "notes": []}],
     "warnings": []}

页号取 docling 的 `page_no`（1 基）。正文只取文本类条目，表格单独取，两者不混——
否则「正文里混进了表格内容」这一项读数就测不出来了。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from _grid import cells_to_grid, markdown_table_to_grid


def _page_no(item) -> int:
    """页号取 docling 的 `prov[0].page_no`（1 基）。

    2.151.1 的 `TableItem.page` 是 None，页归属只在 `prov` 里；取错来源会让四页的表
    全落到第 1 页，页数对齐检查也就白做了。旧版有 `page.page_no` 的写法保留兜底。
    """
    prov = getattr(item, "prov", None) or []
    for entry in prov:
        no = getattr(entry, "page_no", None)
        if isinstance(no, int):
            return no
    page = getattr(item, "page", None)
    if page is not None:
        no = getattr(page, "page_no", None)
        if isinstance(no, int):
            return no
    no = getattr(item, "page_no", None)
    return no if isinstance(no, int) else 1


def _iter_items(document, name: str) -> list:
    """`document.texts` / `document.tables` 在 2.151.1 是列表，更早的版本是方法。"""
    attr = getattr(document, name, None)
    if callable(attr):
        return list(attr())
    return list(attr or [])


def _table_cells(table):
    """从 docling 的 TableItem 里取单元格与行列跨度。版本字段名换过，逐个试探。"""
    data = getattr(table, "data", None)
    raw = getattr(data, "cells", None)
    cells = list(raw.values()) if isinstance(raw, dict) else (raw or [])
    entries = []
    for cell in cells:
        text = str(getattr(cell, "text", "") or "")
        span = getattr(cell, "span", None)
        if span is not None and hasattr(span, "row_idx"):
            entries.append(
                (text, int(span.row_idx), int(span.col_idx),
                 int(getattr(span, "row_span", 1) or 1),
                 int(getattr(span, "col_span", 1) or 1))
            )
            continue
        if hasattr(cell, "start_row") and hasattr(cell, "start_col"):
            entries.append(
                (text, int(cell.start_row), int(cell.start_col),
                 int(cell.end_row) - int(cell.start_row) + 1,
                 int(cell.end_col) - int(cell.start_col) + 1)
            )
            continue
        return None  # 认不出单元格结构，交给调用方退回 markdown
    return entries


def table_grid(table, document=None) -> tuple:
    """表格 → (网格, 取法说明)。

    优先 `data.grid`（2.151.1 已把合并单元格展开成占位副本，与自建链路的展开可比），
    退回 `data.cells` 的 span 列表自己铺网格，最后退回 markdown 管道表。
    """
    data = getattr(table, "data", None)
    grid = getattr(data, "grid", None) if data is not None else None
    if grid:
        rows = []
        for row in grid:
            cells = getattr(row, "cells", None)
            if cells is None:
                cells = row if isinstance(row, list) else [row]
            rows.append([str(getattr(cell, "text", "") or "") for cell in cells])
        return rows, "grid"
    entries = _table_cells(table)
    if entries:
        return cells_to_grid(entries), "cells"
    markdown = ""
    export = getattr(table, "export_to_markdown", None)
    if export is not None:
        try:
            markdown = export(doc=document) if document is not None else export()
        except TypeError:
            markdown = export()  # 更早版本的签名没有 doc 参数
        except Exception as exc:  # noqa: BLE001 - 退路本身不能把整次解析带崩
            return [], f"markdown 导出失败：{exc}"
    return markdown_table_to_grid(markdown), "markdown"


def parse(pdf_path: Path) -> dict:
    from docling.document_converter import DocumentConverter

    started = time.perf_counter()
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    document = result.document

    texts: dict = {}
    tables: dict = {}
    notes: list = []
    for item in _iter_items(document, "texts"):
        texts.setdefault(_page_no(item), []).append(str(getattr(item, "text", "") or ""))
    for table in _iter_items(document, "tables"):
        grid, how = table_grid(table, document)
        page = _page_no(table)
        tables.setdefault(page, []).append({"rows": grid, "source": how})
        if how != "grid":
            notes.append(f"页 {page} 表格结构用 {how} 取法")

    pages = []
    for page_no in sorted(set(texts) | set(tables)):
        pages.append(
            {
                "page": page_no,
                "text": "\n".join(part for part in texts.get(page_no, []) if part),
                "tables": tables.get(page_no, []),
                "notes": [],
            }
        )
    warnings = []
    status = getattr(result, "status", None)
    if status is not None and str(status) not in ("success", "ExecutionStatus.SUCCESS"):
        warnings.append(f"docling 转换状态：{status}")
    return {
        "backend": "docling",
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "pages": pages,
        "warnings": warnings,
        "notes": notes,
        "doc_version": getattr(__import__("docling"), "__version__", "unknown"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="要解析的 PDF")
    parser.add_argument("--dump", action="store_true", help="额外打印条目结构，供 preflight 核对")
    args = parser.parse_args(argv)

    try:
        payload = parse(Path(args.input))
    except Exception as exc:  # noqa: BLE001 - 驱动把异常转成退出码与 stderr
        print(f"docling 解析失败：{exc}", file=sys.stderr)
        traceback.print_exc()
        return 2
    if args.dump:
        print(json.dumps({"notes": payload.get("notes"), "pages": payload["pages"]},
                         ensure_ascii=False)[:2000], file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
