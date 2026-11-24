"""生成 v8 解析基准 fixture：四页纯图片 PDF + 文字底稿 + golden JSON。

四页分别压四种解析能力：
- p1 带边框 3×3：最简单形态，两侧都应该全对。
- p2 合并单元格（rowspan + colspan）：考验结构还原。
- p3 **无框线表格**：只有文字对齐，没有结构线——自建链路的 `find_table_rects`
  靠形态学找线，这是它的已知短板，也是这次要量的区分点。
- p4 六列表格：考验多列的列切分。

页面是纯图片（没有文本对象），pdfplumber 提不到文字 → 判为扫描件，自建侧走完整链路，
第三方侧走整页解析。golden 与绘制共用同一份表格规格（`PAGES`），两者不会写岔。

用法：
    python3 scripts/make_parse_bench_fixture.py [--smoke]

--smoke 时对四页跑一遍自建真实链路并打印核对点（OCR / 表格框 / 结构 / 正文过滤）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PDF_OUTPUT = ROOT / "eval" / "fixtures" / "v8_parse_bench.pdf"
DRAFT_OUTPUT = ROOT / "eval" / "fixtures" / "v8_parse_bench.md"
GOLDEN_OUTPUT = ROOT / "eval" / "fixtures" / "v8_parse_bench.golden.json"

FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]

PAGE_W, PAGE_H = 640, 420
TABLE_X0, TABLE_Y0 = 60, 150
CELL_W, CELL_H = 92, 46

# 单元格规格：(row, col, rowspan, colspan, text)。golden 从这个规格铺出网格，
# 绘图也从同一个规格画框，两边共用一份真值。
PAGES = [
    {
        "page": 1,
        "label": "带边框 3x3",
        "bordered": True,
        "title": "配件库存表（第一页）",
        "footer": "备注：第一页为草稿数据。",
        "cells": [
            (0, 0, 1, 1, "产品"), (0, 1, 1, 1, "数量"), (0, 2, 1, 1, "单价"),
            (1, 0, 1, 1, "显示器"), (1, 1, 1, 1, "8"), (1, 2, 1, 1, "1299.0"),
            (2, 0, 1, 1, "硬盘"), (2, 1, 1, 1, "25"), (2, 2, 1, 1, "380.0"),
        ],
    },
    {
        "page": 2,
        "label": "合并单元格",
        "bordered": True,
        "title": "部门人员表（第二页，含合并单元格）",
        "footer": "备注：人数为在编人数。",
        "cells": [
            (0, 0, 1, 1, "部门"), (0, 1, 1, 1, "人员"), (0, 2, 1, 1, "人数"),
            # 研发纵向合并两行；合计横向占两列
            (1, 0, 2, 1, "研发"), (1, 1, 1, 1, "张三"), (1, 2, 1, 1, "1"),
            (2, 1, 1, 1, "李四"), (2, 2, 1, 1, "2"),
            (3, 0, 1, 2, "合计"), (3, 2, 1, 1, "3"),
        ],
    },
    {
        "page": 3,
        "label": "无框线表格",
        "bordered": False,
        "title": "供应商报价单（第三页，无框线）",
        "footer": "以上报价有效期 30 天。",
        "cells": [
            (0, 0, 1, 1, "供应商"), (0, 1, 1, 1, "品类"), (0, 2, 1, 1, "起订量"), (0, 3, 1, 1, "交期"),
            (1, 0, 1, 1, "恒达"), (1, 1, 1, 1, "键盘"), (1, 2, 1, 1, "50"), (1, 3, 1, 1, "15天"),
            (2, 0, 1, 1, "华南"), (2, 1, 1, 1, "鼠标"), (2, 2, 1, 1, "100"), (2, 3, 1, 1, "20天"),
        ],
    },
    {
        "page": 4,
        "label": "六列表格",
        "bordered": True,
        "title": "季度销售明细（第四页，六列）",
        "footer": "数据来源：内部报表系统。",
        "cells": [
            (0, 0, 1, 1, "季度"), (0, 1, 1, 1, "华东"), (0, 2, 1, 1, "华南"),
            (0, 3, 1, 1, "华北"), (0, 4, 1, 1, "西南"), (0, 5, 1, 1, "合计"),
            (1, 0, 1, 1, "Q1"), (1, 1, 1, 1, "120"), (1, 2, 1, 1, "90"),
            (1, 3, 1, 1, "70"), (1, 4, 1, 1, "40"), (1, 5, 1, 1, "320"),
            (2, 0, 1, 1, "Q2"), (2, 1, 1, 1, "130"), (2, 2, 1, 1, "95"),
            (2, 3, 1, 1, "75"), (2, 4, 1, 1, "45"), (2, 5, 1, 1, "345"),
        ],
    },
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise RuntimeError("找不到可用中文字体（本机 macOS 系统字体）")


def expanded_rows(cells: list) -> list:
    """单元格规格 → 二维网格；合并单元格的延续格填占位副本。

    与自建链路 `_expand_span` 的差别只在延续格：那边填 None，这边填副本。
    golden 指标只比对「期望非空」的地格，所以两种填法都不影响读数。
    """
    height = max(r + rs for r, _, rs, _, _ in cells)
    width = max(c + cs for _, c, _, cs, _ in cells)
    grid: list = [[""] * width for _ in range(height)]
    for row, col, row_span, col_span, text in cells:
        for r in range(row, row + row_span):
            for c in range(col, col + col_span):
                grid[r][c] = text
    return grid


def text_width(draw: ImageDraw.Draw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def make_page_image(page: dict) -> Image.Image:
    """绘制：上方正文一行 + 表格（有框或无框）+ 下方正文一行。"""
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(img)
    font_body = load_font(22)
    font_cell = load_font(20)

    draw.text((40, 60), page["title"], fill="black", font=font_body)

    for row, col, row_span, col_span, text in page["cells"]:
        x0 = TABLE_X0 + col * CELL_W
        y0 = TABLE_Y0 + row * CELL_H
        x1 = x0 + col_span * CELL_W
        y1 = y0 + row_span * CELL_H
        if page["bordered"]:
            draw.rectangle([x0, y0, x1, y1], outline="black", width=2)
        # 居中：按实测文本宽高偏移
        dx = (x1 - x0 - text_width(draw, text, font_cell)) // 2
        dy = (y1 - y0 - 22) // 2
        draw.text((x0 + dx, y0 + dy), text, fill="black", font=font_cell)

    rows = expanded_rows(page["cells"])
    draw.text(
        (40, TABLE_Y0 + len(rows) * CELL_H + 40),
        page["footer"],
        fill="black",
        font=font_body,
    )
    return img


def build_golden(pdf_relative: str) -> dict:
    """golden JSON：每页的期望表格网格与正文约束。"""
    pages = []
    for page in PAGES:
        rows = expanded_rows(page["cells"])
        pages.append(
            {
                "page": page["page"],
                "label": page["label"],
                "bordered": page["bordered"],
                "tables": [
                    {
                        "name": f"p{page['page']}_table_1",
                        "rows": rows,
                    }
                ],
                # 正文必须含这些短语（正文 OCR 来源换了也能核对有没有丢内容）
                "body_contains": [page["title"], page["footer"]],
            }
        )
    return {
        "fixture": pdf_relative,
        "note": (
            "golden 的表格网格按合并单元格的占位副本铺出；指标只比对期望非空的地格，"
            "所以自建侧的 None 延续格与第三方的文本副本延续格都算通过。"
        ),
        "pages": pages,
    }


def build_draft(pdf_relative: str, draft_relative: str) -> str:
    """文字底稿：只做评估集 evidence 逐字校验，不入库。行文本用空格分隔。"""
    lines = [
        "# 解析基准 fixture 文字底稿（v8）",
        "",
        f"本文件是 {Path(pdf_relative).name} 的人工文字底稿，仅用于评估集证据校验，不入库。",
        "",
    ]
    for page in PAGES:
        lines.append(f"## 第 {page['page']} 页：{page['label']}")
        lines.append("")
        lines.append(page["title"])
        lines.append("")
        lines.append("表格列：" + " ".join(expanded_rows(page["cells"])[0]))
        lines.append("")
        for row in expanded_rows(page["cells"])[1:]:
            lines.append("- " + " ".join(row))
        lines.append("")
        lines.append(page["footer"])
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(PDF_OUTPUT))
    parser.add_argument("--draft", default=str(DRAFT_OUTPUT))
    parser.add_argument("--golden", default=str(GOLDEN_OUTPUT))
    parser.add_argument("--smoke", action="store_true", help="生成后对四页跑一遍自建真实链路")
    args = parser.parse_args(argv)

    pdf_path = Path(args.output)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    images = [make_page_image(page) for page in PAGES]
    # Pillow 的 PDF 插件只在 save_all=True 时才写 append_images 里的后续页
    images[0].save(str(pdf_path), "PDF", resolution=72.0, save_all=True, append_images=images[1:])
    print(f"✅ PDF fixture：{pdf_path}（{len(images)} 页）")

    draft_relative = str(Path(args.draft).resolve().relative_to(ROOT))
    Path(args.draft).write_text(build_draft(str(pdf_path), draft_relative), encoding="utf-8")
    print(f"✅ 文字底稿：{args.draft}")

    Path(args.golden).write_text(
        json.dumps(build_golden(str(pdf_path)), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"✅ golden：{args.golden}")

    if args.smoke:
        return run_smoke(pdf_path)
    return 0


def run_smoke(pdf_path: Path) -> int:
    """四页各跑一遍自建链路，打印四张核对点；无框线页允许检不到表格（那是预期短板）。"""
    import numpy as np

    import pdfplumber
    from private_rag.ingestion.ocr import filter_ocr_result, ocr_image, render_page
    from private_rag.ingestion.table_layout import extract_scan_tables, find_table_rects
    from private_rag.evaluation import normalize_text

    ok = True
    with pdfplumber.open(str(pdf_path)) as pdf:
        for spec, page in zip(PAGES, pdf.pages):
            image = render_page(page)
            arr = np.array(image)
            ocr = ocr_image(image)
            rects = find_table_rects(arr)
            result = extract_scan_tables(arr, ocr.boxes, rects)
            filtered = filter_ocr_result(ocr, result.rects)

            expected = expanded_rows(spec["cells"])
            print(f"\n=== 第 {spec['page']} 页 {spec['label']} ===")
            print(f"  OCR 文本：{ocr.text[:120]!r}")
            print(f"  表格框：{rects}")
            print(f"  表格结构（{len(result.tables)} 张）：")
            for table in result.tables:
                for row in table:
                    print("    ", row)
            print(f"  过滤后正文：{filtered.text[:120]!r}")

            got = normalize_text("".join(filtered.text))
            missing = [
                phrase for phrase in (spec["title"], spec["footer"])
                if normalize_text(phrase) not in got
            ]
            if missing:
                print(f"  ❌ 正文缺短语：{missing}")
                ok = False
            if spec["bordered"] and not result.tables:
                print("  ❌ 带边框页没检出表格")
                ok = False
            if not spec["bordered"] and not result.tables:
                print("  ⚠️ 无框线页没检出表格（自建链路的已知短板，正是这次要量的区分点）")

    print("\n✅ 冒烟通过" if ok else "\n❌ 冒烟失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
