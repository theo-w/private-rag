"""驱动脚本共用的表格网格工具（只用标准库）。

外部解析器给表格的形态各不相同（HTML、带 span 的单元格列表、markdown），统一收敛成
`list[list[str]]`——与 `private_rag/ingestion/table_layout.py` 的
`ScanTableExtraction.tables` 同形态，这样主进程那边的文档装配与序列化不用分叉。

合并单元格在这里展开成占位副本：rowspan/colspan 覆盖到的每个格子都填同一个文本，
与自建链路里 `_expand_span` 的处理一致，A/B 两侧的地格坐标才可比。
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import List, Optional, Tuple


def _positive_int(raw: Optional[str], default: int = 1) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class _HtmlTableParser(HTMLParser):
    """收集 HTML 表格的单元格及其 rowspan / colspan。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[Tuple[str, int, int]]] = []
        self._row: Optional[List[Tuple[str, int, int]]] = None
        self._in_cell = False
        self._parts: List[str] = []
        self._rowspan = 1
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._parts = []
            data = dict(attrs)
            self._rowspan = _positive_int(data.get("rowspan"))
            self._colspan = _positive_int(data.get("colspan"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._in_cell:
                if self._row is None:
                    self._row = []
                self._row.append(
                    ("".join(self._parts).strip(), self._rowspan, self._colspan)
                )
            self._in_cell = False
        elif tag == "tr":
            if self._row is not None:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._parts.append(data)


def cells_to_grid(cells) -> List[List[str]]:
    """把 `(text, row, col, row_span, col_span)` 的单元格列表铺成二维网格。

    行列坐标是 0 基（docling 的 `row_idx`、HTML 的行序都是 0 基），只做负数兜底，
    不能抬到 1——抬了会把整张表右下移一格。
    """
    entries = []
    for text, row, col, row_span, col_span in cells:
        entries.append(
            (str(text or ""), max(0, int(row)), max(0, int(col)),
             _positive_int(row_span), _positive_int(col_span))
        )
    if not entries:
        return []
    height = max(row + row_span for _, row, _, row_span, _ in entries)
    width = max(col + col_span for _, _, col, _, col_span in entries)
    grid: List[List[Optional[str]]] = [[None] * width for _ in range(height)]
    for text, row, col, row_span, col_span in entries:
        for r in range(row, row + row_span):
            for c in range(col, col + col_span):
                if grid[r][c] is None:
                    grid[r][c] = text
    return [[("" if cell is None else cell) for cell in row] for row in grid]


def html_to_grid(html: str) -> List[List[str]]:
    """HTML 表格 → 二维网格，展开 rowspan / colspan。

    先按「已占用的地格」推出每个单元格的行列坐标，再交给 `cells_to_grid` 铺网格——
    合并单元格的展开只写一处，两条来源（HTML、docling 的 span 列表）共用同一套铺法。
    """
    if not html or "<" not in html:
        return []
    parser = _HtmlTableParser()
    parser.feed(html)
    if not parser.rows:
        return []
    occupied = set()
    entries: List[Tuple[str, int, int, int, int]] = []
    for r, row in enumerate(parser.rows):
        col = 0
        for text, row_span, col_span in row:
            while (r, col) in occupied:
                col += 1
            entries.append((text, r, col, row_span, col_span))
            for rr in range(r, r + row_span):
                for cc in range(col, col + col_span):
                    occupied.add((rr, cc))
            col += col_span
    return cells_to_grid(entries)


def markdown_table_to_grid(markdown: str) -> List[List[str]]:
    """markdown 管道表格 → 二维网格；分隔行（`|---|---|`）丢掉。"""
    rows: List[List[str]] = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows
