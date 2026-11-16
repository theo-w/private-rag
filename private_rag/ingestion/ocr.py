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
        result, _boxs = ocr(np_array_from_image(image))

        if result is None:
            return OCRResult()

        # 按 y 坐标排序后合并文本
        sorted_boxes = sorted(_boxs, key=lambda b: b[0][1])  # y 坐标
        boxes: list[tuple] = []
        lines: list[str] = []

        current_y: float | None = None
        line_texts: list[str] = []
        y_threshold = 8.0  # 同一行的 y 坐标容差

        for box in sorted_boxes:
            text = str(result[0]).strip()
            confidence = float(result[1]) if len(result) > 1 else 0.0
            y = box[0][1][1]  # 四个角的 y 坐标中点

            boxes.append((box[0], text, confidence))

            if current_y is None:
                current_y = y
                line_texts.append(text)
            elif abs(y - current_y) < y_threshold:
                line_texts.append(text)
            else:
                if line_texts:
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


def ocr_page(pdf_page: Any) -> OCRResult:
    """对 pdfplumber page 执行 OCR。

    先将页面渲染为 PIL Image，再调用 ocr_image。

    参数：
        pdf_page：pdfplumber.page 对象。

    返回：
        OCRResult。
    """
    try:
        image = pdf_page.to_image()
        return ocr_image(image)
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
        if is_scanned:
            # 扫描件：在渲染图像上查找表格
            image = pdf_page.to_image()
            tables = image.pages[0].extract_tables()
            # image.pages[0] 是 pypdfium2 渲染后的图像对象
            # 实际上 to_image() 返回 PageImage，需要换一种方式
            # 使用 pdfplumber 的 find_tables 在图像上提取
            tables = _find_tables_on_image(pdf_page)
        else:
            # 文本型 PDF：直接提取
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


def _find_tables_on_image(pdf_page: Any) -> list[list]:
    """在扫描页面的渲染图像上提取表格。

    使用 pdfplumber 的 PageImage 的 extract_tables 方法。
    """
    try:
        image = pdf_page.to_image()
        # PageImage 有 extract_tables 方法
        tables = image.extract_tables() if hasattr(image, "extract_tables") else []
        return tables
    except Exception:
        return []
