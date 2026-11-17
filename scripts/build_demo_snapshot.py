"""生成只读演示快照：把 v6 的入库产物导出成一个 JSON 文件。

只读部署（例如 Render）不加载任何模型、不连 Qdrant，检索只在快照内做 BM25。
快照由真实加载器产出（含扫描件 OCR 文本与表格文档），因此页码与表格名与线上一致。

运行：
    python scripts/init_demo_db.py
    python scripts/build_demo_snapshot.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.config import APP_VERSION, ROOT
from private_rag.ingestion.files import load_file_documents
from private_rag.ingestion.sql import load_sql_documents
from private_rag.models import SourceDocument
from private_rag.sources import (
    FileSource,
    SourcesConfig,
    SqlSource,
    load_sources_config,
    source_key_for,
)

# 只读快照允许出现的语料位置。放白名单是为了防止把个人文档打进公网部署。
ALLOWED_FILE_ROOTS = ("data/docs",)
ALLOWED_SQL_DB = "data/sql/demo.db"

DEFAULT_CONFIG = ROOT / "config" / "sources.demo.yaml"
DEFAULT_OUTPUT = ROOT / "data" / "demo_snapshot.json"


class SnapshotError(RuntimeError):
    """快照构建失败（多为止血用的白名单校验没通过）。"""


def _relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def assert_demo_only(config: SourcesConfig, config_path: Path) -> list[str]:
    """校验数据源只覆盖演示语料，越界直接拒绝构建。"""

    problems: list[str] = []
    for source in config.sources:
        if isinstance(source, FileSource):
            resolved = _relative_to_root((ROOT / source.path).expanduser())
            if not any(
                resolved == allowed or resolved.startswith(allowed.rstrip("/") + "/")
                for allowed in ALLOWED_FILE_ROOTS
            ):
                problems.append(f"file 数据源越出白名单：{resolved}")
        elif isinstance(source, SqlSource):
            if ALLOWED_SQL_DB not in source.dsn.replace("\\", "/"):
                problems.append(
                    f"sql 数据源不是演示库，只允许 {ALLOWED_SQL_DB}：{source.dsn}"
                )
        else:
            # 网页抓取依赖外网且内容不可控，只读快照不接受
            problems.append("只读快照不接受 web 数据源")
    if problems:
        raise SnapshotError(
            "拒绝生成快照（配置 %s）：\n- %s" % (config_path, "\n- ".join(problems))
        )
    return [_relative_to_root((ROOT / s.path).expanduser()) if isinstance(s, FileSource)
            else getattr(s, "label", None) or "sql" for s in config.sources]


def collect_documents(config: SourcesConfig) -> tuple[list[dict], list[str]]:
    """按数据源收集文档，返回（文档列表，逐源日志）。"""

    records: list[dict] = []
    logs: list[str] = []
    for source in config.sources:
        if isinstance(source, FileSource):
            source_key = f"file:{_relative_to_root((ROOT / source.path).expanduser())}"
            documents, warnings = load_file_documents(source, source_key)
        elif isinstance(source, SqlSource):
            source_key = source_key_for(source)
            documents, warnings = load_sql_documents(source, source_key)
        else:  # pragma: no cover - assert_demo_only 已拦下
            continue
        logs.append(f"{source_key}: {len(documents)} 个文档，{len(warnings)} 条告警")
        records.extend(document.to_snapshot() if hasattr(document, "to_snapshot")
                       else _as_record(document) for document in documents)
    return records, logs


def _as_record(document: SourceDocument) -> dict[str, Any]:
    """把 SourceDocument 转成可 JSON 序列化的快照条目。"""

    return {
        "doc_id": document.doc_id,
        "text": document.text,
        "title": document.title,
        "source_type": document.source_type,
        "source_uri": document.source_uri,
        "source_key": document.source_key,
        "updated_at": document.updated_at,
        "tags": list(document.tags),
        "content_hash": document.content_hash,
        "page": document.extra.get("page"),
        "table": document.extra.get("table"),
        "file_name": document.extra.get("file_name"),
        "scanned": bool(document.extra.get("scanned")),
        "char_count": len(document.text),
    }


def build_snapshot(
    config_path: Path = DEFAULT_CONFIG, output_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    """构建并写出快照，返回快照内容。"""

    config = load_sources_config(config_path)
    sources = assert_demo_only(config, Path(config_path))
    records, logs = collect_documents(config)
    if not records:
        raise SnapshotError("快照为空：演示语料没有产出任何文档")

    scanned = sum(1 for record in records if record["scanned"])
    tables = sum(1 for record in records if record["table"])
    snapshot = {
        "schema_version": 1,
        "app_version": APP_VERSION,
        "source_config": _relative_to_root(Path(config_path)),
        "sources": sources,
        # 只读部署不含模型，检索为快照内 BM25，索引粒度为页/表/行
        "mode": "readonly",
        "retrieval": "bm25",
        "chunk_granularity": "document",
        "stats": {
            "documents": len(records),
            "scanned_documents": scanned,
            "table_documents": tables,
            "total_chars": sum(record["char_count"] for record in records),
        },
        "build_log": logs,
        "documents": records,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return snapshot


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成只读演示快照 JSON")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="数据源配置")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="快照输出路径")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    snapshot = build_snapshot(args.config, args.output)
    stats = snapshot["stats"]
    print(
        f"✅ 快照已生成：{stats['documents']} 个文档"
        f"（扫描件 {stats['scanned_documents']}，表格 {stats['table_documents']}）"
        f" → {_relative_to_root(args.output)}"
    )
    for line in snapshot["build_log"]:
        print(f"   {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
