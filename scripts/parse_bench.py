"""v8 解析基准：自建扫描件解析栈 vs docling / MinerU 的解析层对照。

两层用法：

    # 1) 先验：venv、库、模型、驱动输出是否就绪（失败早停，不跑半个基准）
    python3 scripts/parse_bench.py --preflight --backends docling,mineru

    # 2) 解析层 golden 对照（不打生成模型、不入库、秒级~分钟级）
    python3 scripts/parse_bench.py --backends selfbuilt,docling,mineru \
        --output /tmp/parse_baseline.md --json-output /tmp/parse_baseline.json

指标都不经过检索与生成，直接量「解析出来的东西对不对」——端到端只看到 evidence 命中与否，
分不清是解析差还是口径不命中，这层负责把两者分开。指标定义见 `metrics_for_page`。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from private_rag import config  # noqa: E402
from private_rag.evaluation import normalize_text  # noqa: E402
from private_rag.ingestion.scan_parse import (  # noqa: E402
    SCAN_PARSERS,
    ScanBackendError,
    build_scan_backend,
)

DEFAULT_FIXTURE = ROOT / "eval" / "fixtures" / "v8_parse_bench.pdf"
DEFAULT_GOLDEN = ROOT / "eval" / "fixtures" / "v8_parse_bench.golden.json"

# 一张表算「认出来了」的门槛：期望非空单元格里至少一半对得上
TABLE_MATCH_CELL_THRESHOLD = 0.5


# --------------------------------------------------------------------------- #
# 各后端的解析结果取法
# --------------------------------------------------------------------------- #


def parse_with_selfbuilt(pdf_path: Path) -> Dict[int, Dict[str, Any]]:
    """自建链路：渲染 → OCR → 版面表格 → 正文过滤。逐页耗时相加。"""
    import numpy as np

    import pdfplumber
    from private_rag.ingestion.ocr import filter_ocr_result, ocr_image, render_page
    from private_rag.ingestion.table_layout import extract_scan_tables

    pages: Dict[int, Dict[str, Any]] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            started = time.perf_counter()
            image = render_page(page)
            ocr = ocr_image(image)
            result = extract_scan_tables(np.array(image), ocr.boxes)
            body = filter_ocr_result(ocr, result.rects).text
            # result.tables 是「表格 → 行 → 单元格」三层，自建链路的空单元格是 None
            grids = [
                [[("" if cell is None else str(cell)) for cell in row] for row in table]
                for table in result.tables
            ]
            pages[page_index] = {
                "text": body,
                "tables": grids,
                "ms": int((time.perf_counter() - started) * 1000),
                "rects": result.rects,
            }
    return pages


def parse_with_external(
    backend: str, pdf_path: Path, *, use_cache: bool = True, refresh: bool = False
) -> Dict[int, Dict[str, Any]]:
    """外部后端：整份 PDF 一次解析，按页取用。耗时是驱动那一次的总耗时，摊到每页记。"""
    built = build_scan_backend(backend, pdf_path, use_cache=use_cache, refresh=refresh)
    assert built is not None
    per_page_ms = int(built.backend_ms / max(1, built.page_count))
    pages: Dict[int, Dict[str, Any]] = {}
    for page_index, parse in sorted(built.pages.items()):
        pages[page_index] = {
            "text": parse.text,
            "tables": parse.tables,
            "ms": per_page_ms,
            "rects": [],
            "notes": parse.notes,
        }
    return pages


# --------------------------------------------------------------------------- #
# golden 指标
# --------------------------------------------------------------------------- #


def grid_shape(grid: List[List[str]]) -> tuple:
    if not grid:
        return (0, 0)
    return (len(grid), max(len(row) for row in grid))


def cell_accuracy(expected: List[List[str]], actual: List[List[str]]) -> float:
    """按行列位置比对：只统计期望非空的地格（合并单元格的延续格两边填法不同，跳过）。"""
    checks = 0
    hits = 0
    for r, row in enumerate(expected):
        for c, cell in enumerate(row):
            needle = normalize_text(cell)
            if not needle:
                continue
            checks += 1
            if r < len(actual) and c < len(actual[r]):
                if normalize_text(actual[r][c]) == needle:
                    hits += 1
    return hits / checks if checks else 0.0


def row_strings(grid: List[List[str]]) -> List[str]:
    """整行的规范化串，用于「正文里有没有混进表格内容」。"""
    out = []
    for row in grid:
        joined = normalize_text("".join(row))
        if joined:
            out.append(joined)
    return out


def metrics_for_page(expected: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Any]:
    """单页指标。

    - `table_recall`：期望表格里有几张被认出（认出的判据是单元格匹配率 ≥ 门槛）
    - `cell_accuracy`：最优配对的单元格命中率
    - `structure_ok`：行列数是否与期望一致
    - `body_purity`：1 − 混入正文的期望行占比
    """
    expected_tables = [table["rows"] for table in expected["tables"]]
    actual_tables = actual["tables"]

    recalls = []
    accuracies = []
    structures = []
    for rows in expected_tables:
        best_acc = 0.0
        best_shape = False
        for grid in actual_tables:
            acc = cell_accuracy(rows, grid)
            if acc > best_acc:
                best_acc = acc
                best_shape = grid_shape(grid) == grid_shape(rows)
        recalls.append(1.0 if best_acc >= TABLE_MATCH_CELL_THRESHOLD else 0.0)
        accuracies.append(best_acc)
        structures.append(1.0 if (best_shape and best_acc >= TABLE_MATCH_CELL_THRESHOLD) else 0.0)

    body = normalize_text(actual["text"])
    expected_rows = [joined for rows in expected_tables for joined in row_strings(rows)]
    contaminated = sum(1 for joined in expected_rows if joined in body)
    purity = 1.0 - (contaminated / len(expected_rows)) if expected_rows else 1.0

    return {
        "page": expected["page"],
        "label": expected.get("label", ""),
        "expected_tables": len(expected_tables),
        "actual_tables": len(actual_tables),
        "table_recall": sum(recalls) / len(recalls) if recalls else 0.0,
        "cell_accuracy": sum(accuracies) / len(accuracies) if accuracies else 0.0,
        "structure_ok": sum(structures) / len(structures) if structures else 0.0,
        "body_purity": purity,
        "contaminated_rows": contaminated,
        "body_chars": len(actual["text"]),
        "parse_ms": actual.get("ms", 0),
        "actual_tables_preview": [
            {"shape": grid_shape(grid), "rows": grid[:3]} for grid in actual_tables[:2]
        ],
    }


def aggregate(page_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐页指标汇总成后端级读数（表格类取均值，耗时取和）。"""
    n = len(page_metrics)
    total_ms = sum(page["parse_ms"] for page in page_metrics)
    return {
        "pages": n,
        "table_recall": sum(page["table_recall"] for page in page_metrics) / n if n else 0.0,
        "cell_accuracy": sum(page["cell_accuracy"] for page in page_metrics) / n if n else 0.0,
        "structure_ok": sum(page["structure_ok"] for page in page_metrics) / n if n else 0.0,
        "body_purity": sum(page["body_purity"] for page in page_metrics) / n if n else 0.0,
        "contaminated_rows": sum(page["contaminated_rows"] for page in page_metrics),
        "detected_tables": sum(page["actual_tables"] for page in page_metrics),
        "expected_tables": sum(page["expected_tables"] for page in page_metrics),
        "body_chars": sum(page["body_chars"] for page in page_metrics),
        "parse_ms_total": total_ms,
        "parse_ms_per_page": int(total_ms / n) if n else 0,
    }


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #


def preflight(backends: List[str], fixture: Path) -> int:
    """逐后端验：venv 解释器 → 驱动脚本 → 库能 import → 能产出一页合法 JSON。

    任何一步失败就打印可操作提示并返回非 0，绝不在跑了半个基准后才暴露。
    """
    if not fixture.is_file():
        print(f"❌ fixture 不存在：{fixture}\n   先跑 python3 scripts/make_parse_bench_fixture.py")
        return 1
    python = Path(config.PARSE_VENV_PYTHON)
    if not python.is_file():
        print(f"❌ 找不到外部解析器的解释器：{python}\n"
              "   uv venv --python /opt/homebrew/bin/python3.11 .venv-parse && \\\n"
              "   uv pip install --python .venv-parse/bin/python -r requirements-parse-bench.txt")
        return 1
    print(f"✅ 虚拟环境解释器：{python}")

    failed = False
    for backend in backends:
        print(f"\n--- {backend} ---")
        driver = config.ROOT / "scripts" / "parse_drivers" / f"{backend}_driver.py"
        if not driver.is_file():
            print(f"❌ 驱动脚本缺失：{driver}")
            failed = True
            continue
        print(f"✅ 驱动脚本：{driver}")

        import_code = f"import {backend}; print(getattr({backend}, '__version__', 'unknown'))"
        probe = _run([str(python), "-I", "-c", import_code], timeout=120)
        if probe.returncode != 0:
            print(f"❌ {backend} 不能在虚拟环境里 import：\n{_tail(probe.stderr)}")
            failed = True
            continue
        print(f"✅ {backend} 版本：{probe.stdout.strip()}")

        try:
            started = time.perf_counter()
            # 绕过缓存重跑（要验的是驱动真跑一遍能不能出结果），但把结果写回缓存供入库复用
            pages = parse_with_external(backend, fixture, refresh=True)
        except ScanBackendError as exc:
            print(f"❌ {backend} 驱动跑不通：{exc}")
            failed = True
            continue
        print(f"✅ 解析成功（{int((time.perf_counter() - started) * 1000)} ms），逐页摘要：")
        for page_index, page in sorted(pages.items()):
            shapes = [grid_shape(grid) for grid in page["tables"]]
            print(f"   页 {page_index}：正文 {len(page['text'])} 字，"
                  f"表格 {len(page['tables'])} 张 形状 {shapes}")
            print(f"     正文前 80 字：{page['text'][:80]!r}")
            for grid in page["tables"][:1]:
                for row in grid[:3]:
                    print(f"     {row}")
            for note in page.get("notes") or []:
                print(f"     注：{note}")
    print("\n✅ preflight 通过" if not failed else "\n❌ preflight 失败（上面有可操作提示）")
    return 1 if failed else 0


def _run(argv: List[str], timeout: int):
    import subprocess

    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _tail(text: str, lines: int = 4) -> str:
    tail = (text or "").strip().splitlines()[-lines:]
    return " / ".join(tail) or "无输出"


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


def render_markdown(results: Dict[str, Dict[str, Any]], fixture: Path) -> str:
    lines = [
        "# v8 解析基准：解析层 golden 对照",
        "",
        f"- fixture：`{fixture}`",
        f"- golden：`{DEFAULT_GOLDEN.relative_to(ROOT)}`",
        "- 指标不打生成模型、不入库，只量解析结果；端到端读数见 `eval/reports/`。",
        "",
        "## 后端级读数",
        "",
        "| 后端 | 表格检出 | table_recall | cell_accuracy | structure_ok | body_purity | 正文混入行 | 正文字数 | 解析耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, result in results.items():
        agg = result["aggregate"]
        lines.append(
            f"| {name} | {agg['detected_tables']}/{agg['expected_tables']} "
            f"| {agg['table_recall']:.2f} | {agg['cell_accuracy']:.2f} | {agg['structure_ok']:.2f} "
            f"| {agg['body_purity']:.2f} | {agg['contaminated_rows']} | {agg['body_chars']} "
            f"| {agg['parse_ms_total']} ms |"
        )
    lines.append("")
    lines.append("指标口径：`table_recall` = 期望表格中单元格匹配率 ≥ "
                 f"{TABLE_MATCH_CELL_THRESHOLD} 的比例；`cell_accuracy` 只比对期望非空的地格"
                 "（合并单元格的延续格两侧填法不同，不计）；`body_purity` = 1 − 混入正文的期望行占比。")
    lines.append("")

    for name, result in results.items():
        lines.append(f"## {name} 逐页")
        lines.append("")
        lines.append("| 页 | 期望表 | 检出的表 | recall | cell_acc | structure | purity | 耗时 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for page in result["pages"]:
            lines.append(
                f"| {page['page']} {page['label']} | {page['expected_tables']} "
                f"| {page['actual_tables']} | {page['table_recall']:.2f} | {page['cell_accuracy']:.2f} "
                f"| {page['structure_ok']:.2f} | {page['body_purity']:.2f} | {page['parse_ms']} ms |"
            )
        lines.append("")
        for page in result["pages"]:
            if page["actual_tables_preview"]:
                lines.append(f"- 页 {page['page']} 检出的表：")
                for preview in page["actual_tables_preview"]:
                    lines.append(f"  - 形状 {preview['shape']}：" +
                                 " / ".join(json.dumps(row, ensure_ascii=False) for row in preview["rows"]))
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--backends", default="selfbuilt,docling,mineru")
    parser.add_argument("--preflight", action="store_true", help="只验环境与驱动输出，不算指标")
    parser.add_argument("--no-cache", action="store_true", help="外部后端绕过解析结果缓存")
    parser.add_argument("--output", default="")
    parser.add_argument("--json-output", default="")
    args = parser.parse_args(argv)

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    unknown = [b for b in backends if b not in SCAN_PARSERS]
    if unknown:
        print(f"❌ 未知后端：{unknown}（可选 {', '.join(SCAN_PARSERS)}）")
        return 2

    fixture = Path(args.fixture)
    if args.preflight:
        external = [b for b in backends if b != "selfbuilt"]
        if not external:
            print("preflight 只验外部后端（selfbuilt 在本机依赖里，无需验）")
            return 0
        return preflight(external, fixture)

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    results: Dict[str, Dict[str, Any]] = {}
    for backend in backends:
        try:
            if backend == "selfbuilt":
                pages = parse_with_selfbuilt(fixture)
            else:
                pages = parse_with_external(backend, fixture, use_cache=not args.no_cache)
        except Exception as exc:  # noqa: BLE001 - 一侧失败不该带走另一侧
            print(f"❌ {backend} 解析失败：{exc}")
            continue
        page_metrics = []
        for expected in golden["pages"]:
            actual = pages.get(expected["page"], {"text": "", "tables": [], "ms": 0})
            page_metrics.append(metrics_for_page(expected, actual))
        results[backend] = {"pages": page_metrics, "aggregate": aggregate(page_metrics)}
        agg = results[backend]["aggregate"]
        print(f"{backend}: table_recall={agg['table_recall']:.2f} "
              f"cell_accuracy={agg['cell_accuracy']:.2f} structure_ok={agg['structure_ok']:.2f} "
              f"body_purity={agg['body_purity']:.2f} 检出 {agg['detected_tables']}/{agg['expected_tables']} 张 "
              f"耗时 {agg['parse_ms_total']} ms")

    if not results:
        print("❌ 没有任何后端出结果")
        return 1

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(results, fixture), encoding="utf-8")
        print(f"✅ 报告：{out}")
    if args.json_output:
        out = Path(args.json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fixture": str(fixture), "results": results},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅ JSON：{out}")
    if not args.output and not args.json_output:
        print(render_markdown(results, fixture))
    return 0


if __name__ == "__main__":
    sys.exit(main())
