"""扫描件解析后端：把「整页正文 + 表格网格」的解析责任从自建链路换到外部解析器。

默认后端 `selfbuilt` 就是现有的 rapidocr + OpenCV 版面表格 + rapid-table SLANet 链路，
也是 v6.2 消融的 ON 侧。`docling` / `mineru` 是外部解析器：它们要求 Python ≥3.10 / ≥3.11，
而本仓库钉死 3.9，只能装进另一个解释器的虚拟环境，主进程用 subprocess 调驱动脚本
（选型与代价见 docs/adr/0007）。

一次调用解析整份 PDF——外部解析器的模型冷启动是十几秒量级，逐页调用会把基准耗时变成
冷启动次数乘以页数。结果按页取，页号以 pdfplumber 的页序为准；某一页后端没给结果时
调用方回退自建链路，并把这件事记进入库错误，不静默丢内容。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from private_rag.config import (
    PARSE_BACKEND_TIMEOUT_SECONDS,
    PARSE_CACHE_DIR,
    PARSE_VENV_PYTHON,
    ROOT,
    SCAN_PARSER,
)

# 可选后端；selfbuilt 不需要外部进程
SCAN_PARSERS = ("selfbuilt", "docling", "mineru")
EXTERNAL_PARSERS = ("docling", "mineru")

# 输出契约版本：驱动脚本换了输出形态时，旧缓存必须作废
CACHE_SCHEMA_VERSION = "1"

DRIVERS: Dict[str, Path] = {
    "docling": ROOT / "scripts" / "parse_drivers" / "docling_driver.py",
    "mineru": ROOT / "scripts" / "parse_drivers" / "mineru_driver.py",
}


class ScanBackendError(RuntimeError):
    """外部解析后端不可用：后端名非法、虚拟环境缺失、驱动失败或输出不合法。

    这个异常只在入口（入库装配、`scripts/parse_bench.py --preflight`）被当成致命错误；
    逐页解析失败不回退到「什么都不说」，调用方必须把它写进可见的错误列表。
    """


@dataclass
class ScanPageParse:
    """一页的解析结果，形态与 `table_layout.ScanTableExtraction` 对齐。

    `tables` 是已展开行列的二维文本网格（`list[list[str]]`），交给 files.py 现有的
    表格文档装配与逗号拼接序列化；后端已经区分了正文与表格，所以不需要表格矩形。
    """

    text: str
    tables: List[List[List[str]]]
    backend: str
    notes: List[str] = field(default_factory=list)


@dataclass
class ScanBackend:
    """整份 PDF 的一次解析结果，按 pdfplumber 页号取用。"""

    name: str
    pages: Dict[int, ScanPageParse]
    backend_ms: int
    notes: List[str] = field(default_factory=list)
    from_cache: bool = False

    def parse_page(self, page_index: int) -> Optional[ScanPageParse]:
        return self.pages.get(page_index)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def cache_key(pdf_path: Path, backend: str, variant: Optional[str] = None) -> str:
    """以 PDF 字节内容为键：换文件即换键，不会因为同路径同名而复用旧结果。

    `variant` 是影响解析结果的开关（MinerU 的档位）。换档必须换键，否则改了
    `RAG_MINERU_TIER` 还读到旧档的缓存，两档的读数就串在一起了。
    """
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    suffix = f"-{variant}" if variant else ""
    return f"{backend}-{CACHE_SCHEMA_VERSION}{suffix}-{digest}.json"


def read_cache(
    pdf_path: Path, backend: str, cache_dir: Path, variant: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    path = cache_dir / cache_key(pdf_path, backend, variant)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_cache(
    payload: Dict[str, Any],
    pdf_path: Path,
    backend: str,
    cache_dir: Path,
    variant: Optional[str] = None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / cache_key(pdf_path, backend, variant)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def normalize_grid(rows: Any) -> List[List[str]]:
    """把驱动输出的表格网格收敛成 `list[list[str]]`；None / 非字符串都转成空串。"""
    grid: List[List[str]] = []
    if not isinstance(rows, list):
        return grid
    for row in rows:
        if not isinstance(row, list):
            continue
        grid.append([("" if cell is None else str(cell)) for cell in row])
    return grid


def parse_payload(payload: Any, backend: str) -> ScanBackend:
    """校验驱动输出并转成 `ScanBackend`。不合法的字段直接报错，不静默填默认值。"""
    if not isinstance(payload, dict):
        raise ScanBackendError(f"{backend} 驱动输出不是 JSON 对象")
    reported = payload.get("backend")
    if reported != backend:
        raise ScanBackendError(f"{backend} 驱动自报后端为 {reported!r}")
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ScanBackendError(f"{backend} 驱动没有给出任何页（pages 缺失或为空）")

    parsed: Dict[int, ScanPageParse] = {}
    notes: List[str] = list(payload.get("warnings") or [])
    for entry in pages:
        if not isinstance(entry, dict):
            raise ScanBackendError(f"{backend} 驱动的页记录不是对象：{entry!r}")
        page_index = entry.get("page")
        if not isinstance(page_index, int) or page_index < 1:
            raise ScanBackendError(f"{backend} 驱动的页号非法：{page_index!r}")
        text = entry.get("text")
        if not isinstance(text, str):
            raise ScanBackendError(f"{backend} 驱动页 {page_index} 的 text 不是字符串")
        tables_raw = entry.get("tables") or []
        if not isinstance(tables_raw, list):
            raise ScanBackendError(f"{backend} 驱动页 {page_index} 的 tables 不是列表")
        tables = [normalize_grid(t.get("rows") if isinstance(t, dict) else t) for t in tables_raw]
        parsed[page_index] = ScanPageParse(
            text=text,
            tables=tables,
            backend=backend,
            notes=[str(n) for n in (entry.get("notes") or [])],
        )

    elapsed = payload.get("elapsed_ms")
    return ScanBackend(
        name=backend,
        pages=parsed,
        backend_ms=int(elapsed) if isinstance(elapsed, (int, float)) else 0,
        notes=notes,
        from_cache=bool(payload.get("from_cache")),
    )


def run_driver(
    backend: str,
    pdf_path: Path,
    *,
    python: Optional[Path] = None,
    driver: Optional[Path] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """在外部虚拟环境里跑驱动脚本，返回解析 JSON。"""
    interpreter = Path(python) if python is not None else Path(PARSE_VENV_PYTHON)
    driver_path = Path(driver) if driver is not None else DRIVERS[backend]
    limit = timeout if timeout is not None else PARSE_BACKEND_TIMEOUT_SECONDS

    if not interpreter.is_file():
        raise ScanBackendError(
            f"找不到外部解析器的 Python 解释器：{interpreter}。"
            "先建虚拟环境（见 requirements-parse-bench.txt 与 eval/README.md 的解析基准小节），"
            "或用 RAG_PARSE_VENV_PYTHON 指向已有解释器。"
        )
    if not driver_path.is_file():
        raise ScanBackendError(f"缺少 {backend} 驱动脚本：{driver_path}")

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(interpreter), str(driver_path), "--input", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=limit,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScanBackendError(
            f"{backend} 解析 {pdf_path.name} 超时（{limit} 秒）；"
            "模型首次下载会额外花时间，可先跑 scripts/parse_bench.py --preflight。"
        ) from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise ScanBackendError(
            f"{backend} 驱动退出码 {proc.returncode}：{' / '.join(tail) or '无 stderr 输出'}"
        )
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise ScanBackendError(
            f"{backend} 驱动的输出不是合法 JSON（stdout 前 200 字："
            f"{proc.stdout[:200]!r}）"
        ) from exc
    payload.setdefault("elapsed_ms", elapsed_ms)
    return payload


def build_scan_backend(
    name: str = SCAN_PARSER,
    pdf_path: Path = None,
    *,
    python: Optional[Path] = None,
    driver: Optional[Path] = None,
    timeout: Optional[int] = None,
    use_cache: bool = True,
    refresh: bool = False,
    cache_dir: Optional[Path] = None,
    variant: Optional[str] = None,
) -> Optional[ScanBackend]:
    """装配扫描件解析后端；`selfbuilt` 返回 None，表示调用方走现有链路。

    后端名非法或外部进程失败时抛 `ScanBackendError`——基准读数最怕静默降级，
    宁可让入库报错也不要把「其实跑的是自建链路」混进第三方侧的读数里。

    `refresh=True` 是「绕过缓存重跑，但把这次的结果写回缓存」：preflight 要验的是驱动
    真跑一遍能不能出结果（不能被旧缓存糊过去），而它跑出来的结果正是入库要用的那份，
    不写回去就得让入库再付一次冷启动。
    """
    backend = (name or "selfbuilt").strip().lower()
    if backend not in SCAN_PARSERS:
        raise ScanBackendError(
            f"RAG_SCAN_PARSER={name!r} 不在可选后端里（{', '.join(SCAN_PARSERS)}）"
        )
    if backend == "selfbuilt":
        return None
    if pdf_path is None:
        raise ScanBackendError(f"后端 {backend} 需要 PDF 路径")
    if variant is None and backend == "mineru":
        from private_rag import config  # 调用时取，测试能就地改档位（与 SCAN_PARSER 同约定）

        variant = config.MINERU_TIER

    cached = (
        read_cache(pdf_path, backend, Path(cache_dir or PARSE_CACHE_DIR), variant)
        if use_cache and not refresh
        else None
    )
    if cached is not None:
        parsed = parse_payload(cached, backend)
        parsed.from_cache = True
        return parsed

    payload = run_driver(backend, pdf_path, python=python, driver=driver, timeout=timeout)
    payload["backend"] = backend
    if use_cache:
        try:
            write_cache(
                payload, pdf_path, backend, Path(cache_dir or PARSE_CACHE_DIR), variant
            )
        except OSError as exc:
            payload.setdefault("warnings", []).append(f"解析结果缓存写入失败：{exc}")
    return parse_payload(payload, backend)
