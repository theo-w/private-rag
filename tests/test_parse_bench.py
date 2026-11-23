"""解析层 golden 指标的测试：用假解析输出，不跑 OCR、不装第三方解析器。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import parse_bench  # noqa: E402
import pytest  # noqa: E402


EXPECTED_ROWS = [["产品", "数量", "单价"], ["键盘", "12", "99.5"], ["鼠标", "30", "45.0"]]


def expected_page(page: int = 1, rows=None, label: str = "带边框 3x3") -> dict:
    return {
        "page": page,
        "label": label,
        "tables": [{"name": f"p{page}_table_1", "rows": rows or EXPECTED_ROWS}],
        "body_contains": ["标题", "备注"],
    }


def actual(text: str = "", tables=None, ms: int = 100) -> dict:
    return {"text": text, "tables": tables or [], "ms": ms}


def test_cell_accuracy_full_match():
    assert parse_bench.cell_accuracy(EXPECTED_ROWS, [list(r) for r in EXPECTED_ROWS]) == 1.0


def test_cell_accuracy_counts_position_mismatch():
    got = [["产品", "数量", "单价"], ["键盘", "13", "99.5"], ["鼠标", "30", "45.0"]]
    assert parse_bench.cell_accuracy(EXPECTED_ROWS, got) == 8 / 9


def test_cell_accuracy_skips_expected_empty_cells():
    """合并单元格的延续格两侧填法不同（自建填 None、第三方填副本），不计入分母。"""
    expected = [["合计", "", "3"], ["研发", "张三", "1"]]
    got = [["合计", "合计", "3"], ["研发", "张三", "1"]]
    assert parse_bench.cell_accuracy(expected, got) == 1.0


def test_cell_accuracy_missing_cells_count_as_wrong():
    assert parse_bench.cell_accuracy(EXPECTED_ROWS, [["产品", "数量", "单价"]]) == 3 / 9


def test_cell_accuracy_no_expected_cells():
    assert parse_bench.cell_accuracy([["", ""]], []) == 0.0


def test_grid_shape():
    assert parse_bench.grid_shape([["a", "b"], ["c", "d"]]) == (2, 2)
    assert parse_bench.grid_shape([]) == (0, 0)


def test_metrics_page_no_table_detected():
    metrics = parse_bench.metrics_for_page(expected_page(), actual(text="正文"))
    assert metrics["table_recall"] == 0.0
    assert metrics["cell_accuracy"] == 0.0
    assert metrics["structure_ok"] == 0.0
    assert metrics["actual_tables"] == 0


def test_metrics_page_recognizes_table_and_clean_body():
    metrics = parse_bench.metrics_for_page(
        expected_page(), actual(text="标题\n备注", tables=[[list(r) for r in EXPECTED_ROWS]], ms=250)
    )
    assert metrics["table_recall"] == 1.0
    assert metrics["cell_accuracy"] == 1.0
    assert metrics["structure_ok"] == 1.0
    assert metrics["body_purity"] == 1.0
    assert metrics["parse_ms"] == 250
    assert metrics["body_chars"] == len("标题\n备注")


def test_metrics_page_body_contamination_lowers_purity():
    """后端把表格行当正文输出时，body_purity 要掉下来（无框线页的常见形态）。"""
    polluted = "标题\n产品 数量 单价\n键盘 12 99.5\n备注"
    metrics = parse_bench.metrics_for_page(expected_page(), actual(text=polluted))
    assert metrics["contaminated_rows"] == 2
    assert metrics["body_purity"] == pytest.approx(1 / 3)


def test_metrics_page_wrong_structure_not_counted():
    """单元格都对但行列数不对：recall 给分，structure_ok 不给分。"""
    skewed = [["产品", "数量", "单价", ""], ["键盘", "12", "99.5", ""]]
    metrics = parse_bench.metrics_for_page(expected_page(), actual(tables=[skewed]))
    assert metrics["table_recall"] == 1.0
    assert metrics["structure_ok"] == 0.0


def test_metrics_page_picks_best_matching_table():
    tables = [["垃圾", "表格"], list(EXPECTED_ROWS[0]), list(EXPECTED_ROWS[1]), list(EXPECTED_ROWS[2])]
    metrics = parse_bench.metrics_for_page(expected_page(), actual(tables=tables))
    # 多张碎片表各自比对，最优的一张（第一行）决定 recall
    assert metrics["table_recall"] in (0.0, 1.0)
    assert metrics["actual_tables"] == 4


def test_aggregate_averages_metrics_and_sums_time():
    pages = [
        parse_bench.metrics_for_page(expected_page(1), actual(text="a", tables=[[list(r) for r in EXPECTED_ROWS]], ms=100)),
        parse_bench.metrics_for_page(expected_page(2), actual(text="b", ms=50)),
    ]
    agg = parse_bench.aggregate(pages)
    assert agg["pages"] == 2
    assert agg["table_recall"] == 0.5
    assert agg["parse_ms_total"] == 150
    assert agg["parse_ms_per_page"] == 75
    assert agg["expected_tables"] == 2
    assert agg["detected_tables"] == 1


def test_aggregate_empty():
    assert parse_bench.aggregate([])["parse_ms_per_page"] == 0


def test_render_markdown_has_both_backends_and_metric_legend():
    results = {
        "selfbuilt": {"pages": [parse_bench.metrics_for_page(expected_page(), actual())],
                      "aggregate": parse_bench.aggregate(
                          [parse_bench.metrics_for_page(expected_page(), actual())])},
        "docling": {"pages": [parse_bench.metrics_for_page(
            expected_page(), actual(text="x", tables=[[list(r) for r in EXPECTED_ROWS]]))],
            "aggregate": parse_bench.aggregate([parse_bench.metrics_for_page(
                expected_page(), actual(text="x", tables=[[list(r) for r in EXPECTED_ROWS]]))])},
    }
    md = parse_bench.render_markdown(results, Path("eval/fixtures/v8_parse_bench.pdf"))
    assert "| selfbuilt |" in md
    assert "| docling |" in md
    assert "body_purity" in md
    assert "合并单元格" in md


def test_main_rejects_unknown_backend():
    assert parse_bench.main(["--backends", "selfbuilt,paddleocr"]) == 2
