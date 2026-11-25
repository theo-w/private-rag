#!/usr/bin/env python3
"""评估集自查：跑一遍就报告数据集现在的形状和所有问题。

跑法：
    python scripts/check_eval_dataset.py                              # 默认查三份 agentic 评估集
    python scripts/check_eval_dataset.py eval/v8_agentic_edge.jsonl   # 只查一份
    python scripts/check_eval_dataset.py --json                       # 机器可读输出

报告内容：每条样本的 evidence 能否在其 source 文件里逐字找到、类别分布、
可回答/应拒答配比、声明双侧取证的条数与清单、带历史的清单、跨文件的 id/问题重复、
证据句两两互为子串的清单，以及 SQL 侧样本的证据是否真能在评估专用 SQLite 里查到。

全部离线：不打模型、不联网、不打开真实 Qdrant，也不碰 data/sql/demo_eval.db
（要验 SQL 侧证据时把评估库建到临时文件再查）。结构性问题退出码非 0。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.evaluation import ROOT, DatasetError, load_dataset, normalize_text

# 默认查这几份：扩集以后把新文件加进来就行，不用改检查逻辑
DEFAULT_DATASETS = (
    "eval/v7_agent_qa.jsonl",
    "eval/v8_agentic_expanded.jsonl",
    "eval/v8_agentic_edge.jsonl",
)

# SQL 侧样本的判定口径：证据权威文件是评估库的种子脚本，期望引用是 SQL 源标签
SQL_SOURCE = "scripts/init_eval_db.py"
SQL_LABEL = "demo.products"
# 评估库里能被引用的三列（其余列是数字/日期，不构成可逐字比对的证据）
SQL_COLUMNS = ("description", "storage", "supplier")


def _load_script(name: str) -> ModuleType:
    """按路径加载 scripts 下的模块（与 tests/test_evaluation.py 同一做法）。"""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib_util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DatasetError(f"无法加载脚本：{path}")
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(path: Path) -> list[dict[str, Any]]:
    """按行读 JSONL，跳过空行；不做校验，校验交给后面的检查与 load_dataset。"""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path.name} 第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise DatasetError(f"{path.name} 第 {line_number} 行必须是 JSON 对象")
        rows.append(data)
    return rows


def _normalized_source(source: str, cache: dict[str, str]) -> str | None:
    """把 source 文件读成规范化文本；文件不存在返回 None（由检查项报出来）。"""
    if source in cache:
        return cache[source]
    path = ROOT / source
    if ".." in Path(source).parts or not path.is_file():
        cache[source] = ""
        return None
    text = normalize_text(path.read_text(encoding="utf-8"))
    cache[source] = text
    return text


def _sql_haystack() -> tuple[str, str | None]:
    """把评估专用 SQLite 建到临时文件，返回能被引用的三列的规范化文本。

    返回 (haystack, 无法建库时的说明)。不写 data/sql/demo_eval.db，避免动到
    别的会话正在用的那份库。
    """
    try:
        module = _load_script("init_eval_db")
    except Exception as exc:  # pragma: no cover - 只在缺依赖时走到
        return "", f"无法加载 scripts/init_eval_db.py：{exc}"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "demo_eval.db"
        module.DB_PATH = db_path
        try:
            # 种子脚本会打印进度，--json 模式下不能混进 stdout
            with contextlib.redirect_stdout(io.StringIO()):
                module.main()
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT {} FROM products".format(", ".join(SQL_COLUMNS))
                ).fetchall()
        except Exception as exc:  # pragma: no cover - 种子脚本自身出错
            return "", f"临时建评估库失败：{exc}"
    return normalize_text("|".join(" ".join(str(cell) for cell in row) for row in rows)), None


def inspect_file(rel_path: str, source_cache: dict[str, str]) -> dict[str, Any]:
    """检查一份数据集，返回逐条明细与汇总。"""
    path = ROOT / rel_path
    if not path.is_file():
        return {"path": rel_path, "errors": [f"文件不存在：{rel_path}"]}

    rows = _rows(path)
    per_sample: list[dict[str, Any]] = []
    errors: list[str] = []
    categories: Counter[str] = Counter()
    answerable_count = 0
    refusal_count = 0
    two_sided_ids: list[str] = []
    sql_ids: list[str] = []
    history_ids: list[str] = []

    for index, data in enumerate(rows, start=1):
        sample_id = str(data.get("id") or f"第 {index} 行")
        category = str(data.get("category") or "")
        answerable = bool(data.get("answerable"))
        source = str(data.get("source") or "")
        evidence = [str(item) for item in (data.get("evidence") or [])]
        expected_sources = [str(item) for item in (data.get("expected_sources") or [])]
        history = data.get("history") or []
        categories[category] += 1
        if answerable:
            answerable_count += 1
        else:
            refusal_count += 1
        if len(expected_sources) >= 2:
            two_sided_ids.append(sample_id)
        elif 0 < len(expected_sources) < 2:
            errors.append(f"{sample_id}: expected_sources 只有 1 条，该读数不参与，应留空")
        if source == SQL_SOURCE or SQL_LABEL in expected_sources:
            sql_ids.append(sample_id)
        if history:
            history_ids.append(sample_id)

        hits: dict[str, bool] = {}
        if answerable:
            text = _normalized_source(source, source_cache)
            if text is None:
                errors.append(f"{sample_id}: source 文件不存在或不可读：{source}")
                text = ""
            history_blob = normalize_text(
                "".join(
                    str(turn[1])
                    for turn in history
                    if isinstance(turn, (list, tuple)) and len(turn) == 2
                )
            )
            for phrase in evidence:
                needle = normalize_text(phrase)
                if len(needle) < 4:
                    errors.append(f"{sample_id}: evidence 太短（规范化后不足 4 字符）：{phrase!r}")
                    hits[phrase] = False
                    continue
                found = needle in text
                hits[phrase] = found
                if not found:
                    errors.append(f"{sample_id}: evidence 在 {source} 里找不到：{phrase!r}")
                if needle and needle in history_blob:
                    errors.append(
                        f"{sample_id}: history 含 evidence 原文，等于把答案喂进提示词：{phrase!r}"
                    )
        else:
            if category != "refusal":
                errors.append(f"{sample_id}: 应拒答样本的 category 必须是 refusal，实际 {category!r}")
            for field in ("reference", "evidence", "source", "expected_source", "expected_sources"):
                if data.get(field):
                    errors.append(f"{sample_id}: 应拒答样本的 {field} 必须为空")

        per_sample.append(
            {
                "id": sample_id,
                "question": str(data.get("question") or ""),
                "category": category,
                "answerable": answerable,
                "source": source,
                "evidence": evidence,
                "evidence_hits": hits,
                "expected_sources": expected_sources,
                "two_sided": len(expected_sources) >= 2,
                "sql_side": source == SQL_SOURCE,
                "history_turns": len(history),
            }
        )

    # 证据句互为子串 = 一次检索命中能同时糊掉两条样本的判定。
    # v7 那 29 条里已有既有重叠（历史读数钉住了它们），所以这类只记警告，
    # 新加样本时要先看这份清单再挑证据句。
    owners: dict[str, str] = {}
    overlaps: list[str] = []
    for item in per_sample:
        for phrase in item["evidence"]:
            needle = normalize_text(phrase)
            if not needle:
                continue
            if needle in owners and owners[needle] != item["id"]:
                overlaps.append(f"证据完全相同：{item['id']} 与 {owners[needle]} 都用了 {needle!r}")
            owners.setdefault(needle, item["id"])
    for needle, owner in owners.items():
        for other, other_owner in owners.items():
            if needle != other and needle in other:
                overlaps.append(f"同文件内证据互为子串：{owner} 的 {needle!r} 是 {other_owner} 的一部分")

    # load_dataset 是运行链路的真口径，自查结论必须和它一致
    try:
        loaded = load_dataset(path)
        if len(loaded) != len(rows):
            errors.append(f"{rel_path}: load_dataset 读到 {len(loaded)} 条，文件里有 {len(rows)} 行")
    except DatasetError as exc:
        errors.append(f"load_dataset 拒绝该文件：{exc}")

    return {
        "path": rel_path,
        "count": len(rows),
        "categories": dict(sorted(categories.items())),
        "answerable": answerable_count,
        "refusal": refusal_count,
        "two_sided": sorted(two_sided_ids),
        "sql_side": sorted(sql_ids),
        "with_history": sorted(history_ids),
        "samples": per_sample,
        "errors": errors,
        "overlaps": sorted(set(overlaps)),
    }


def cross_file_findings(reports: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """跨文件的 id/问题重复与证据互为子串。

    证据互为子串跨文件只算警告：v7 与 v8 已有既有重叠，历史读数钉住了它们，
    不能因为这个卡住 CI；但清单必须打印出来，加新题时先看清会不会撞上。
    """
    errors: list[str] = []
    warnings: list[str] = []
    ids: dict[str, str] = {}
    questions: dict[str, str] = {}
    needles: list[tuple[str, str, str]] = []
    for report in reports:
        for item in report.get("samples", []):
            if item["id"] in ids:
                errors.append(f"id 重复：{item['id']} 同时出现在 {ids[item['id']]} 和 {report['path']}")
            ids[item["id"]] = report["path"]
            if item.get("question") and item["question"] in questions:
                errors.append(
                    f"问题重复：{item['question'][:30]}… 同时出现在 "
                    f"{questions[item['question']]} 和 {report['path']}"
                )
            questions[item["question"]] = report["path"]
            for phrase in item["evidence"]:
                needles.append((item["id"], report["path"], normalize_text(phrase)))
    for a_id, a_path, needle_a in needles:
        for b_id, b_path, needle_b in needles:
            # 同文件的重叠已经由 inspect_file 报过，这里只看跨文件；
            # 完全相同的证据句跨文件也要报——一次命中会同时满足两条样本
            if not needle_a or a_path == b_path:
                continue
            if (a_path, a_id) > (b_path, b_id):
                continue
            if needle_a in needle_b:
                warnings.append(
                    f"跨文件证据互为子串：{a_path}:{a_id} 的 {needle_a!r} 出现在 "
                    f"{b_path}:{b_id} 的证据里"
                )
    return errors, sorted(warnings)


def check_sql_side(reports: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    """SQL 侧样本的证据必须能在评估库能被引用的三列里查到。"""
    haystack, note = _sql_haystack()
    if note:
        return [], note
    errors: list[str] = []
    for report in reports:
        for item in report.get("samples", []):
            if not item["sql_side"] or not item["answerable"]:
                continue
            for phrase in item["evidence"]:
                if normalize_text(phrase) not in haystack:
                    errors.append(
                        f"{report['path']}:{item['id']} 的证据在评估库里查不到："
                        f"{phrase!r}（SQL 侧证据只能取 {'/'.join(SQL_COLUMNS)} 的连续句子）"
                    )
    return errors, None


def render_text(reports: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> str:
    lines: list[str] = []
    total = sum(report.get("count", 0) for report in reports)
    lines.append(f"=== 评估集自查：{len(reports)} 份文件，合计 {total} 条 ===")
    for report in reports:
        if "errors" in report and "count" not in report:
            lines.append(f"\n!! {report['path']}：{'; '.join(report['errors'])}")
            continue
        lines.append(f"\n--- {report['path']}（{report['count']} 条）---")
        lines.append(
            f"类别分布：{', '.join(f'{k} {v}' for k, v in report['categories'].items())}"
            f"｜可回答 {report['answerable']}／应拒答 {report['refusal']}"
            f"｜声明双侧 {len(report['two_sided'])} 条｜SQL 侧 {len(report['sql_side'])} 条"
            f"｜带历史 {len(report['with_history'])} 条"
        )
        lines.append(f"双侧清单：{', '.join(report['two_sided']) or '（无）'}")
        lines.append(f"SQL 侧清单：{', '.join(report['sql_side']) or '（无）'}")
        lines.append(f"带历史清单：{', '.join(report['with_history']) or '（无）'}")
        for item in report["samples"]:
            mark = "可答" if item["answerable"] else "拒答"
            hit = "证据全命中" if all(item["evidence_hits"].values()) else "证据有问题"
            if not item["answerable"]:
                hit = "不该有证据"
            extras = []
            if item["two_sided"]:
                extras.append("双侧:" + "+".join(item["expected_sources"]))
            if item["sql_side"]:
                extras.append("SQL侧")
            if item["history_turns"]:
                extras.append(f"历史{item['history_turns'] / 2:.0f}轮")
            lines.append(
                f"  {item['id']:<5} {item['category']:<11} {mark:<4} {hit:<6} "
                f"{item['source'] or '—'}{'  [' + ', '.join(extras) + ']' if extras else ''}"
            )
        if report.get("overlaps"):
            lines.append(f"  本文件内证据互为子串 {len(report['overlaps'])} 组（既有重叠，只当警告）")
    if warnings:
        lines.append(f"\n警告 {len(warnings)} 条（不影响退出码）：")
        lines.extend(f"  - {msg}" for msg in warnings)
    if errors:
        lines.append(f"\n结构性问题 {len(errors)} 条：")
        lines.extend(f"  - {msg}" for msg in errors)
    else:
        lines.append("\n结构性问题：0 条")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评估集自查（离线，不打模型不联网不开 Qdrant）")
    parser.add_argument("datasets", nargs="*", default=None, help="数据集路径，默认查全部 agentic 集")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    parser.add_argument("--strict", action="store_true", help="把警告也算失败")
    args = parser.parse_args(argv)

    paths = list(args.datasets or DEFAULT_DATASETS)
    source_cache: dict[str, str] = {}
    reports = [inspect_file(path, source_cache) for path in paths]
    cross_errors, warnings = cross_file_findings(reports)
    sql_errors, sql_note = check_sql_side(reports)

    errors = [msg for report in reports for msg in report.get("errors", [])]
    errors = errors + cross_errors + sql_errors
    warnings = warnings + [
        f"{report['path']}: {msg}" for report in reports for msg in report.get("overlaps", [])
    ]
    if sql_note:
        warnings.append(f"SQL 侧证据未能验证：{sql_note}")
    if args.strict:
        errors = errors + warnings

    payload = {
        "datasets": reports,
        "total": sum(report.get("count", 0) for report in reports),
        "errors": sorted(errors),
        "warnings": sorted(set(warnings)),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(reports, errors, warnings))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
