"""在 Python 3.11 虚拟环境里用 MinerU 解析整份 PDF，输出统一 JSON。

调用方是 `private_rag/ingestion/scan_parse.py`（主进程钉在 3.9，MinerU 要求 ≥3.11）。
走命令行而不是 Python API：4.x 的内部模块路径换过，CLI 是官方承诺稳定的入口。4.2.3 起
`mineru` 是「文档库 + 本地服务」的形态（`mineru parse` 要走 server 与档位模型），批量解析
的入口换到了 `mineru-kit parse`——它直接跑本地模型并产出中间 JSON，不依赖那个服务，所以本
驱动用它。档位由 `RAG_MINERU_TIER` 选：`basic` = 流水线小模型（OCR + 版面 + 公式 + 表格结构，
与自建链路同形态），`standard` = 再加 llama.cpp 的 VLM。模型要先用
`mineru-kit models download --tier <档位>` 备好，缺模型时命令会报错，不静默降到低档。

输出契约与 docling 驱动一致：

    {"backend": "mineru", "elapsed_ms": ...,
     "pages": [{"page": 1, "text": "...", "tables": [{"rows": [["产品","数量","单价"]]}]}],
     "warnings": []}

正文与表格都从中间 JSON 里取，不读它的 markdown 产物——markdown 把表格与正文混在一条流里，
「正文里有没有混进表格内容」这项读数就没法算了。4.2.3 的中间 JSON 是 `docvortex.middle 2.0`：
每页 `blocks`，块类型 `table` 下挂 `table_body`（HTML）与 `table_footnote`，其余块的正文是
`{"type": "text", "content": ...}`。更早版本的 `pdf_info`（`spans` 里 `{"type": "text", "text": ...}`）
一并认，取法只差在字段名。表格脚注算正文不算表格——自建链路里框外的注记也落在正文。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from _grid import html_to_grid


def _iter_dicts(node):
    seen = set()

    def walk(value):
        if isinstance(value, dict):
            if id(value) in seen:
                return
            seen.add(id(value))
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    yield from walk(node)


def _text_of(node: dict):
    """文本叶子的取值：2.0 的 `content`，旧版 span 的 `text`。"""
    for key in ("content", "text"):
        value = node.get(key)
        if isinstance(value, str):
            return value
    return None


def _page_text(page: dict) -> str:
    """整页正文：取所有文本叶子，表格本体（`table_body` 是 HTML，不是 `text` 类型）天然不在里面。"""
    parts = []
    for node in _iter_dicts(page):
        if node.get("type") not in ("text", "text_all"):
            continue
        text = _text_of(node)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _page_tables(page: dict) -> list:
    tables = []
    for node in _iter_dicts(page):
        if node.get("type") != "table":
            continue
        html = None
        for sub in _iter_dicts(node):
            candidate = (
                sub.get("content") if sub.get("type") == "table_body" else sub.get("html")
            )
            if isinstance(candidate, str) and "<table" in candidate:
                html = candidate
                break
        if html is None:
            tables.append({"rows": [], "source": "no-html"})
        else:
            tables.append({"rows": html_to_grid(html), "source": "html"})
    return tables


def _find_result_json(outdir: Path, stem: str) -> Path:
    # mineru-kit 的中间 JSON 落在 <输出>/<stem>/<stem>_middle.json，文件名随版本换过，按前缀找
    candidates = sorted(p for p in outdir.rglob("*.json") if p.stem.startswith(stem))
    if not candidates:
        found = [str(p.name) for p in outdir.rglob("*.json")][:10]
        raise RuntimeError(f"MinerU 没有产出 {stem}*.json（目录里的 JSON：{found or '无'}）")
    # 同名 JSON 可能有多个（中间产物与结果），取带逐页内容的那个
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(data, dict) and (data.get("pdf_info") or data.get("pages")):
            return path
    raise RuntimeError(
        f"MinerU 产出的 JSON 里没有逐页内容（pdf_info / pages 都没有）：{candidates[0]}"
    )


def _mineru_kit() -> str:
    """`mineru-kit` 可执行文件的路径。

    主进程用 subprocess 直接跑本脚本，不会先 activate 虚拟环境，所以 PATH 里通常没有
    `mineru-kit`——按解释器自己的 bin 目录找，找不到才退回 PATH 上的同名命令。
    """
    candidate = Path(sys.executable).parent / "mineru-kit"
    return str(candidate) if candidate.is_file() else "mineru-kit"


def parse(pdf_path: Path, *, tier: str, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
        outdir = Path(tmp)
        started = time.perf_counter()
        proc = subprocess.run(
            [
                _mineru_kit(),
                "parse",
                str(pdf_path),
                "--output", str(outdir),
                "--format", "middle_json",
                "--pages", "all",
                "--tier", tier,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            raise RuntimeError(
                f"mineru-kit 退出码 {proc.returncode}：{' / '.join(tail) or '无输出'}"
            )

        result_path = _find_result_json(outdir, pdf_path.stem)
        data = json.loads(result_path.read_text(encoding="utf-8"))

    pages = []
    for entry in data.get("pages") or data.get("pdf_info") or []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("page_idx")
        page_no = (index + 1) if isinstance(index, int) else len(pages) + 1
        pages.append(
            {
                "page": page_no,
                "text": _page_text(entry),
                "tables": _page_tables(entry),
                "notes": [],
            }
        )
    return {
        "backend": "mineru",
        "elapsed_ms": elapsed_ms,
        "pages": pages,
        "warnings": [],
        "notes": [
            f"tier={tier}",
            f"schema={data.get('schema') or 'pdf_info'}",
            f"result_json={result_path.name}",
        ],
    }


# 默认档位：本机硬件推荐的标准档（流水线小模型 + llama.cpp 的 VLM）。缺模型时命令会报错，
# 由调用方提示 `mineru-kit models download --tier standard`，不静默降到 basic。
DEFAULT_TIER = os.environ.get("RAG_MINERU_TIER", "standard").strip() or "standard"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="要解析的 PDF")
    parser.add_argument(
        "--mineru-tier",
        default=DEFAULT_TIER,
        help="MinerU 档位；basic = 流水线小模型，standard = 再加 llama.cpp VLM。"
             "也可用 RAG_MINERU_TIER 设",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--dump", action="store_true", help="额外打印解析摘要，供 preflight 核对")
    args = parser.parse_args(argv)

    try:
        payload = parse(Path(args.input), tier=args.mineru_tier, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"mineru 解析超时（{args.timeout} 秒）", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 驱动把异常转成退出码与 stderr
        print(f"mineru 解析失败：{exc}", file=sys.stderr)
        traceback.print_exc()
        return 2
    if args.dump:
        print(json.dumps(payload["pages"], ensure_ascii=False)[:2000], file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
