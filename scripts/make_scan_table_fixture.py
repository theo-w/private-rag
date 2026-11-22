"""生成扫描件表格 fixture：用 PIL 绘制带中文表格的页面，存成图片型 PDF。

图片型 PDF 没有文本对象，pdfplumber 提取不到文字 → 被 _is_scanned 判为扫描件，
从而走完整链路：渲染 → OCR → OpenCV 表格检测 → SLANet 结构识别 → 正文过滤。

用法：
    python3 scripts/make_scan_table_fixture.py [--output eval/fixtures/v6_scan_table.pdf] [--smoke]

--smoke 时直接跑一遍真实链路并打印四个核对点。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent

# 中文候选字体（macOS 系统字体）
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]

# 表格规格：3 行 x 3 列（1 表头 + 2 数据行）
TABLE_X0, TABLE_Y0 = 80, 150
CELL_W, CELL_H = 113, 46
HEADER = ["产品", "数量", "单价"]
ROWS = [["键盘", "12", "99.5"], ["鼠标", "30", "45.0"]]
BODY_ABOVE = "库存盘点表（草稿）"
BODY_BELOW = "备注：以上为草稿数据。"


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise RuntimeError("找不到可用中文字体")


def make_page_image() -> Image.Image:
    """绘制：上方正文一行 + 3x3 表格 + 下方备注一行。"""
    img = Image.new("RGB", (500, 360), "white")
    draw = ImageDraw.Draw(img)
    font_body = load_font(22)
    font_cell = load_font(20)

    # 正文（表格区域外）
    draw.text((60, 60), BODY_ABOVE, fill="black", font=font_body)

    # 表格
    rows = [HEADER] + ROWS
    table_w = CELL_W * 3
    table_h = CELL_H * len(rows)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            x0 = TABLE_X0 + c * CELL_W
            y0 = TABLE_Y0 + r * CELL_H
            draw.rectangle([x0, y0, x0 + CELL_W, y0 + CELL_H], outline="black", width=2)
            # 文本居中（近似：按估算宽高偏移）
            draw.text((x0 + CELL_W // 2 - 14, y0 + CELL_H // 2 - 12), text, fill="black", font=font_cell)

    # 备注（表格区域外）
    draw.text((60, TABLE_Y0 + table_h + 40), BODY_BELOW, fill="black", font=font_body)
    return img


def save_pdf(img: Image.Image, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output), "PDF", resolution=72.0)
    print(f"✅ fixture 已生成：{output}")


def run_smoke(pdf_path: Path) -> int:
    """跑一遍真实链路，打印四个待确认点。"""
    import numpy as np

    from private_rag.ingestion.ocr import filter_ocr_result, ocr_image, render_page
    from private_rag.ingestion.table_layout import extract_scan_tables, find_table_rects

    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        image = render_page(page)

    arr = np.array(image)
    print(f"[1] 渲染完成：shape={arr.shape}")

    ocr = ocr_image(image)
    print(f"[2] OCR 文本：{ocr.text!r}")
    print(f"    OCR 文本框数：{len(ocr.boxes)}")

    rects = find_table_rects(arr)
    print(f"[3] OpenCV 表格框：{rects}")

    result = extract_scan_tables(arr, ocr.boxes, rects)
    print(f"[4] SLANet 表格结构（{len(result.tables)} 张）：")
    for table in result.tables:
        for row in table:
            print("    ", row)

    filtered = filter_ocr_result(ocr, result.rects)
    print(f"[5] 过滤后正文：{filtered.text!r}")

    # 核对点断言（宽松：OCR/SLANet 输出允许噪声，只断言方向正确）
    ok = True
    if not rects:
        print("❌ 未检测到表格框"); ok = False
    if not result.tables:
        print("❌ SLANet 未产出表格结构"); ok = False
    if "备" not in filtered.text and "草稿" not in filtered.text:
        print("❌ 过滤后正文丢失了表格外的内容"); ok = False
    print("✅ 冒烟通过" if ok else "❌ 冒烟失败")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "eval" / "fixtures" / "v6_scan_table.pdf"))
    parser.add_argument("--smoke", action="store_true", help="生成后直接跑真实链路核对")
    args = parser.parse_args(argv)

    output = Path(args.output)
    img = make_page_image()
    save_pdf(img, output)

    if args.smoke:
        return run_smoke(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
