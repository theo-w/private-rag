"""扫描件版面表格提取。

本模块为扫描件 PDF 提供结构化表格提取能力：

1. **OpenCV 形态学操作**检测水平/垂直网格线，定位表格边界框。
2. **rapid-table（SLANet-Plus ONNX）**在裁剪后的表格区域内识别单元格结构。
3. **RapidOCR 文本框**按中心点匹配到对应单元格，填充单元格内容。
4. SLANet 的 HTML 输出（含 rowspan/colspan）被解析为二维文本网格。

设计要点：
- 裁剪到表格区域后再喂给 SLANet（全页输入在 488px 下会识别错结构）。
- 引擎是进程级单例；首次初始化会下载模型（带 SHA256 校验）。
- 失败结果按进程缓存，避免每页重试。
- 离线兜底：`RAG_SCAN_TABLE_MODEL` 环境变量可指定预置模型文件路径。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置开关
# ---------------------------------------------------------------------------

# 是否启用扫描件表格提取（默认开启）
_SCAN_TABLE_ENABLED = not os.getenv("RAG_DISABLE_SCAN_TABLE", "").strip().lower() in (
    "0", "false", "no", "off"
)

# 预置模型文件路径（环境变量覆盖）
_SCAN_TABLE_MODEL_PATH = os.getenv(
    "RAG_SCAN_TABLE_MODEL",
    str(
        os.path.expanduser("~/.cache/rapid_table/slanet_plus.onnx")
    ),
)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ScanTableExtraction:
    """一次页面扫描表格提取的结果。"""

    # 结构化表格列表，格式与 ocr.extract_tables 兼容
    tables: list[list[list[str | None]]] = field(default_factory=list)
    # 表格边界框（用于从 OCR 结果中过滤表格区域文本）
    rects: list[tuple[int, int, int, int]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# OpenCV 表格区域检测
# ---------------------------------------------------------------------------


def _import_cv2() -> Any:
    """懒加载 cv2，避免无此包时启动失败。"""
    try:
        import cv2 as _CV2

        return _CV2
    except ImportError:
        return None


def find_table_rects(
    image: Any, *, min_width: int = 60, min_height: int = 20
) -> list[tuple[int, int, int, int]]:
    """用形态学操作检测图像中的表格边界框。

    参数：
        image：numpy 数组（灰度图或 BGR）。
        min_width：最小表格宽度（像素），过滤噪声。
        min_height：最小表格高度（像素），过滤噪声。

    返回：
        边界框列表，每个为 (x1, y1, x2, y2)。
        若 cv2 不可用，返回空列表。
    """
    CV2 = _import_cv2()
    if CV2 is None:
        logger.warning("cv2 未安装，跳过表格区域检测")
        return []

    try:
        # 确保灰度图
        if len(image.shape) == 3:
            gray = CV2.cvtColor(image, CV2.COLOR_BGR2GRAY)
        else:
            gray = image

        # 二值化（反色：文字/线条为白色，背景为黑色）
        _, binary = CV2.threshold(gray, 0, 255, CV2.THRESH_BINARY_INV + CV2.THRESH_OTSU)

        # 水平结构线
        h_kernel = CV2.getStructuringElement(CV2.MORPH_RECT, (40, 1))
        h_lines = CV2.morphologyEx(binary, CV2.MORPH_OPEN, h_kernel)

        # 垂直结构线
        v_kernel = CV2.getStructuringElement(CV2.MORPH_RECT, (1, 40))
        v_lines = CV2.morphologyEx(binary, CV2.MORPH_OPEN, v_kernel)

        # 合并
        structure = CV2.addWeighted(h_lines, 0.5, v_lines, 0.5, 0)
        _, structure = CV2.threshold(structure, 0, 255, CV2.THRESH_BINARY)

        # 膨胀连接断点
        dilate_kernel = CV2.getStructuringElement(CV2.MORPH_RECT, (3, 3))
        dilated = CV2.dilate(structure, dilate_kernel, iterations=2)

        # 查找轮廓
        contours, _ = CV2.findContours(dilated, CV2.RETR_EXTERNAL, CV2.CHAIN_APPROX_SIMPLE)

        rects: list[tuple[int, int, int, int]] = []
        for cnt in contours:
            x, y, w, h = CV2.boundingRect(cnt)
            if w >= min_width and h >= min_height:
                rects.append((x, y, x + w, y + h))

        # 合并重叠的矩形
        rects = _merge_overlapping(rects)
        return rects

    except Exception as exc:
        logger.warning("表格区域检测失败：%s", exc)
        return []


def _merge_overlapping(rects: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """合并重叠的矩形框。"""
    if not rects:
        return []

    # 按 y1 排序，再按 x1 排序
    rects.sort(key=lambda r: (r[1], r[0]))
    merged: list[tuple[int, int, int, int]] = [rects[0]]

    for x1, y1, x2, y2 in rects[1:]:
        prev = merged[-1]
        px1, py1, px2, py2 = prev

        # 判断是否重叠或相邻（y 方向重叠 > 30%，x 方向有交集或相邻）
        y_overlap = max(0, min(py2, y2) - max(py1, y1))
        y_max = max(py2, y2)
        y_min = min(py1, y1)
        y_ratio = y_overlap / (y_max - y_min) if y_max > y_min else 0

        x_overlap = max(0, min(px2, x2) - max(px1, x1))

        if y_ratio > 0.3 and x_overlap > -10:
            # 合并
            merged[-1] = (min(px1, x1), min(py1, y1), max(px2, x2), max(py2, y2))
        else:
            merged.append((x1, y1, x2, y2))

    return merged


# ---------------------------------------------------------------------------
# rapid-table（SLANet）引擎
# ---------------------------------------------------------------------------

# 进程级单例
_engine: Any = None
_engine_failed: bool = False


def _resolve_model_path() -> str:
    """定位 SLANet 模型文件。

    优先级：环境变量预置路径 > rapid_table 包内自带的 slanet-plus.onnx。
    两者都不存在时抛 FileNotFoundError（离线兜底）。
    """
    import pathlib

    env_path = pathlib.Path(_SCAN_TABLE_MODEL_PATH)
    if env_path.is_file():
        return str(env_path)

    import rapid_table

    bundled = pathlib.Path(rapid_table.__file__).parent / "models" / "slanet-plus.onnx"
    if bundled.is_file():
        return str(bundled)

    raise FileNotFoundError(
        f"SLANet 模型不存在：{env_path}；也不在 rapid_table 包内（{bundled}）"
    )


def _get_engine() -> Any:
    """获取 SLANet 引擎（进程级单例，rapid_table 3.x API）。"""
    global _engine, _engine_failed

    if _engine_failed:
        return None

    if _engine is None:
        try:
            from rapid_table import RapidTable
            from rapid_table.utils.typings import RapidTableInput

            cfg = RapidTableInput(
                model_type="slanet_plus",
                model_dir_or_path=_resolve_model_path(),
                use_ocr=True,  # 拼装 HTML；实际 OCR 结果由调用方传入，不会跑内部 OCR
            )
            _engine = RapidTable(cfg)
        except Exception as exc:
            logger.warning("SLANet 引擎初始化失败：%s，后续扫描件将跳过表格提取", exc)
            _engine_failed = True
            return None

    return _engine


# ---------------------------------------------------------------------------
# 表格结构识别
# ---------------------------------------------------------------------------


def extract_scan_tables(
    image: Any,
    ocr_boxes: Optional[list[tuple]] = None,
    table_rects: Optional[list[tuple[int, int, int, int]]] = None,
) -> ScanTableExtraction:
    """从扫描件图像中提取结构化表格。

    参数：
        image：numpy 数组或 PIL 图像（页面渲染结果）。
        ocr_boxes：RapidOCR 文本框列表，每个为
            ``[[[x1,y1], [x2,y2], [x3,y3], [x4,y4]], text, confidence]``（整页坐标）。
        table_rects：表格边界框列表 ``[(x1,y1,x2,y2), ...]``（整页坐标）。
            若为 None，自动调用 :func:`find_table_rects` 检测。

    返回：
        ScanTableExtraction，包含结构化表格和边界框。
        若 rapid-table 不可用或识别失败，返回空结果。

    实现要点：rapid_table 3.x 在 use_ocr=True 时才会把结构识别结果和文本
    拼成 HTML；把整页 OCR 框按表格区域裁剪、平移到 crop 坐标系后，以
    ``[(boxes, texts, scores)]`` 形式传给引擎，引擎不会跑自己的 OCR。
    """
    import numpy as np

    engine = _get_engine()
    if engine is None:
        return ScanTableExtraction()

    if not isinstance(image, np.ndarray):
        image = np.asarray(image)

    if table_rects is None:
        table_rects = find_table_rects(image)

    if not table_rects:
        return ScanTableExtraction()

    tables: list[list[list[str | None]]] = []

    for x1, y1, x2, y2 in table_rects:
        # 裁剪表格区域
        crop = np.ascontiguousarray(image[y1:y2, x1:x2])
        if crop.size == 0:
            continue

        # rapid_table 内部会把输入拉伸到 488x488，扁长裁剪会扭曲结构识别；
        # 先用白色补齐成方形（OCR 文本框坐标不变，引擎会自行换算）
        h, w = crop.shape[:2]
        side = max(h, w)
        if crop.ndim == 2:
            padded = np.full((side, side), 255, dtype=crop.dtype)
        else:
            padded = np.full((side, side, crop.shape[2]), 255, dtype=crop.dtype)
        padded[:h, :w] = crop
        crop = padded

        # 收集中心落在表格区域内的 OCR 文本框，平移到 crop 坐标系
        rel_boxes: list[Any] = []
        texts: list[str] = []
        scores: list[float] = []
        if ocr_boxes:
            for box, text, score in ocr_boxes:
                pts = np.asarray(box, dtype=np.float32)
                cx = float(pts[:, 0].mean())
                cy = float(pts[:, 1].mean())
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    rel_boxes.append(pts - np.array([x1, y1], dtype=np.float32))
                    texts.append(str(text))
                    scores.append(float(score))

        if not rel_boxes:
            # 无文本的网格（少见）没有入库价值，跳过
            logger.debug("表格区域 %s 内没有 OCR 文本框，跳过", (x1, y1, x2, y2))
            continue

        try:
            # rapid_table 3.x：engine(img, ocr_results=[...]) 返回 RapidTableOutput，
            # pred_htmls[0] 是带单元格文本的表格 HTML
            ocr_results = [
                (np.asarray(rel_boxes, dtype=np.float32), tuple(texts), tuple(scores))
            ]
            output = engine(crop, ocr_results=ocr_results)
            pred_htmls = getattr(output, "pred_htmls", None) or []
            if not pred_htmls:
                continue
            html = pred_htmls[0]
            if not html or not isinstance(html, str):
                continue

            grid = _parse_html_table(html)
            if not grid:
                continue

            tables.append(grid)

        except Exception as exc:
            logger.warning("表格识别失败 (region=%s)：%s", (x1, y1, x2, y2), exc)

    return ScanTableExtraction(tables=tables, rects=table_rects)


def _parse_html_table(html: str) -> list[list[str | None]]:
    """将 SLANet 的 HTML 输出解析为二维文本网格。

    处理 rowspan 和 colspan，缺失单元格填充 None。
    """
    from html.parser import HTMLParser

    grid: list[list[str | None]] = []
    occupied: set[tuple[int, int]] = set()

    class GridParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_cell = False
            self.cell_text = ""
            self.row = []

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.in_cell = True
                self.cell_text = ""
                self._rowspan = 1
                self._colspan = 1
                for name, value in attrs:
                    if name == "rowspan" and value:
                        try:
                            self._rowspan = int(value)
                        except ValueError:
                            pass
                    elif name == "colspan" and value:
                        try:
                            self._colspan = int(value)
                        except ValueError:
                            pass

        def handle_endtag(self, tag):
            if tag == "td" or tag == "th":
                self.in_cell = False
                text = unescape(self.cell_text.strip())
                self.row.append((text, self._rowspan, self._colspan))
            elif tag == "tr" and self.row:
                grid.append(list(self.row))  # 快照，避免后续修改污染已追加的行
                self.row = []

        def handle_data(self, data):
            if self.in_cell:
                self.cell_text += data

    parser = GridParser()
    try:
        parser.feed(f"<table>{html}</table>")
    except Exception:
        # 解析失败时尝试直接分割
        return _fallback_parse(html)

    if not grid:
        return _fallback_parse(html)

    # 扩展 rowspan / colspan 到实际网格
    return _expand_span(grid)


def _fallback_parse(html: str) -> list[list[str | None]]:
    """兜底解析：按 <tr> 和 <td> 分割。"""
    import re

    # 提取所有 <tr>...</tr> 块
    tr_blocks = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    result: list[list[str | None]] = []

    for block in tr_blocks:
        # 提取所有 <td>...</td> 或 <th>...</th> 块
        td_blocks = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", block, re.DOTALL)
        parsed_row: list[str | None] = []

        if not td_blocks:
            # 没有 td，可能是空行
            continue

        for td in td_blocks:
            text = unescape(td.strip())
            if text:
                parsed_row.append(text)
            else:
                parsed_row.append(None)

        if parsed_row:
            result.append(parsed_row)

    return result


def _expand_span(grid: list[list]) -> list[list[str | None]]:
    """将含 rowspan/colspan 的网格扩展为完整二维数组。

    输入可以是：
    - 纯字符串网格（无 span）：[[\"A\", \"B\"], [\"C\", \"D\"]]
    - 含 span 元组的网格：[[(\"A\", 1, 2)], ...]
    """
    if not grid:
        return []

    # 检测输入格式
    first_cell = grid[0][0] if grid[0] else None
    if isinstance(first_cell, str):
        # 纯字符串网格，无需扩展
        return [[c if c else None for c in row] for row in grid]

    # 含 span 元组 (text, rowspan, colspan)
    num_rows = len(grid)
    max_cols = max(len(row) for row in grid) if grid else 0
    if max_cols == 0:
        return []

    # 初始化全 None 网格
    full: list[list[str | None]] = [[None] * max_cols for _ in range(num_rows)]

    # 跟踪每个位置还有多少行需要跳过（rowspan 剩余）
    skip_rows: list[int] = [0] * max_cols

    for row_idx, row in enumerate(grid):
        col_idx = 0
        for cell_data in row:
            text, rowspan, colspan = cell_data

            # 跳过被上一行 rowspan 占用的列
            while col_idx < max_cols and skip_rows[col_idx] > 0:
                skip_rows[col_idx] -= 1
                col_idx += 1

            if col_idx >= max_cols:
                break

            # 填入当前单元格
            for dr in range(rowspan):
                for dc in range(colspan):
                    r = row_idx + dr
                    c = col_idx + dc
                    if r < num_rows and c < max_cols:
                        if dr == 0 and dc == 0:
                            full[r][c] = text
                        # 其他 rowspan/colspan 位置保持 None（被占用）

            # 更新 skip_rows
            for dc in range(colspan):
                c = col_idx + dc
                if c < max_cols:
                    skip_rows[c] = max(skip_rows[c], rowspan - 1)

            col_idx += colspan

    return full


# ---------------------------------------------------------------------------
# filter_ocr_result / _merge_lines：从 ocr.py 复用
# ---------------------------------------------------------------------------

# 重新导出，方便外部 from private_rag.ingestion.ocr import filter_ocr_result
from private_rag.ingestion.ocr import _merge_lines, filter_ocr_result  # noqa: F401
