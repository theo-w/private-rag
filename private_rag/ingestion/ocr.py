"""扫描件 OCR 与 PDF 表格提取。

本模块提供两个能力：
1. 对 PIL Image 执行 OCR（中英双语），返回合并文本与边界框。
2. 对 pdfplumber page 提取结构化表格。

所有外部依赖（rapidocr_onnxruntime、pdfplumber）均懒加载，
未安装时函数返回降级结果（空文本 / 空表格列表），不阻塞服务启动。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """OCR 识别结果。"""

    text: str = ""
    boxes: list[tuple] = field(default_factory=list)


def _import_ocr() -> Any:
    """懒加载 rapidocr_onnxruntime，避免无此包时启动失败。"""
    try:
        from rapidocr_onnxruntime import RapidOCR as _OCR

        return _OCR
    except ImportError:
        return None


def _import_pdfplumber() -> Any:
    """懒加载 pdfplumber，避免无此包时启动失败。"""
    try:
        import pdfplumber as _PP

        return _PP
    except ImportError:
        return None


def ocr_image(image: Any) -> OCRResult:
    """对一张 PIL Image 执行 OCR，返回合并文本与边界框。

    参数：
        image：PIL.Image.Image 对象。

    返回：
        OCRResult，包含合并文本（按 y 坐标排序）和边界框列表。
        若 OCR 后端不可用，返回空结果。
    """
    OCRClass = _import_ocr()
    if OCRClass is None:
        logger.warning("rapidocr_onnxruntime 未安装，跳过 OCR")
        return OCRResult()

    try:
        ocr = OCRClass(
            show_log=False,
            det_use_cuda=False,
            cls_use_cuda=False,
            rec_use_cuda=False,
        )
        # rapidocr 返回 (result, elapse)：
        #   result = [[box, text, confidence], ...]，box 是四个角点
        #   elapse = [det, cls, rec] 三段耗时，不是边界框
        result, _elapse = ocr(np_array_from_image(image))

        if not result:
            return OCRResult()

        entries: list[tuple] = []
        for item in result:
            box, text = item[0], str(item[1]).strip()
            confidence = float(item[2]) if len(item) > 2 else 0.0
            if not text:
                continue
            # 左上角 y 作为行坐标
            entries.append((float(box[0][1]), box, text, confidence))

        if not entries:
            return OCRResult()

        # 按 y 坐标排序后按行合并
        entries.sort(key=lambda entry: entry[0])
        boxes: list[tuple] = []
        lines: list[str] = []

        current_y: float | None = None
        line_texts: list[str] = []
        y_threshold = 12.0  # 同一行的 y 坐标容差（200 dpi 下约半行高）

        for y, box, text, confidence in entries:
            boxes.append((box, text, confidence))

            if current_y is None:
                current_y = y
                line_texts.append(text)
            elif abs(y - current_y) < y_threshold:
                line_texts.append(text)
            else:
                lines.append(" ".join(line_texts))
                current_y = y
                line_texts = [text]

        if line_texts:
            lines.append(" ".join(line_texts))

        return OCRResult(text="\n".join(lines), boxes=boxes)
    except Exception as exc:
        logger.warning("OCR 识别失败：%s", exc)
        return OCRResult()


def np_array_from_image(image: Any) -> Any:
    """将 PIL Image 转换为 numpy array（避免在模块顶层导入 numpy）。"""
    import numpy as np

    return np.array(image)


# OCR 渲染分辨率。pdfplumber 默认 72 dpi，实测中文会识别错
# （"产品库存" -> "产品本存"，置信度 0.79）；200 dpi 整句正确、置信度约 0.98。
OCR_RENDER_RESOLUTION = 200


def render_page(pdf_page: Any, resolution: int = OCR_RENDER_RESOLUTION) -> Any:
    """把 pdfplumber page 渲染成 PIL Image。

    page.to_image() 返回的是 PageImage 包装对象，对它做 np.array() 只会得到
    一个 0 维对象（形状 ()），OCR 会直接拒收。这里取出它内部的 PIL Image。
    """
    image = pdf_page.to_image(resolution=resolution)
    for attr in ("to_pil", "original"):
        inner = getattr(image, attr, None)
        if inner is not None:
            return inner() if callable(inner) else inner
    return image


def ocr_page(pdf_page: Any) -> OCRResult:
    """对 pdfplumber page 执行 OCR。

    先将页面渲染为 PIL Image，再调用 ocr_image。

    参数：
        pdf_page：pdfplumber.page 对象。

    返回：
        OCRResult。
    """
    try:
        return ocr_image(render_page(pdf_page))
    except Exception as exc:
        logger.warning("页面 OCR 渲染失败：%s", exc)
        return OCRResult()


def _format_table(table_info: dict) -> str:
    """将表格信息格式化为纯文本（CSV 风格）。"""
    lines: list[str] = []
    header = table_info.get("header")
    rows = table_info.get("rows", [])

    if header:
        lines.append(",".join(str(c) for c in header if c))
    for row in rows:
        lines.append(",".join(str(c) for c in row if c))

    return "\n".join(lines)


def extract_tables(
    pdf_page: Any,
    *,
    table_label: str = "",
    is_scanned: bool = False,
) -> list[dict]:
    """从 pdfplumber page 提取结构化表格。

    文本型 PDF 使用 extract_tables()；扫描件使用 find_tables()
    在渲染图像上提取。

    参数：
        pdf_page：pdfplumber.page 对象。
        table_label：表格标签（通常为文件名），用于引用展示。
        is_scanned：页面是否为扫描件。

    返回：
        表格列表，每个元素为 {"header", "rows", "label", "text"}。
        若无表格或 pdfplumber 不可用，返回空列表。
    """
    PP = _import_pdfplumber()
    if PP is None:
        logger.warning("pdfplumber 未安装，跳过表格提取")
        return []

    try:
        # 说明：表格提取依赖页面里的文字对象与绘制线条。扫描页是一整张位图，
        # 这两者都不存在，因此扫描件只能拿到 OCR 文本，提不出结构化表格
        # （版面表格识别属于后续版本范围）。
        tables = pdf_page.extract_tables() or []

        results: list[dict] = []
        for table in tables:
            # 过滤空表格（全 None 或全空字符串）
            flat = [c for row in table for c in row if c is not None]
            if not flat:
                continue

            # table 是 pdfplumber extract_tables 返回的一个表格：list of rows
            # 每行是 list/tuple of cells
            if table:
                header = [c if c else "" for c in table[0]]
                rows = [[c if c else "" for c in row] for row in table[1:]]
            else:
                header = []
                rows = []

            text = _format_table({"header": header, "rows": rows})
            if not text.strip():
                continue

            results.append(
                {
                    "header": header,
                    "rows": rows,
                    "label": table_label,
                    "text": text,
                }
            )

        return results
    except Exception as exc:
        logger.warning("表格提取失败：%s", exc)
        return []
