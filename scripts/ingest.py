"""多源入库 CLI：文件 / 网页 / 数据库 -> 切分 -> 向量库 + BM25 索引。

用法：
    python scripts/ingest.py                          # 默认扫描 data/docs
    python scripts/ingest.py --config config/sources.yaml
    python scripts/ingest.py --path data/docs/a.pdf   # 单文件/单目录，兼容旧用法
    python scripts/ingest.py --config config/sources.yaml --recreate
    python scripts/ingest.py --config config/sources.yaml --prune
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_ollama import OllamaEmbeddings

from private_rag.config import EMB_MODEL
from private_rag.ingestion.pipeline import run_ingest
from private_rag.sources import (
    ConfigError,
    FileSource,
    SourcesConfig,
    default_sources_config,
    load_sources_config,
)
from private_rag.store import close_client, open_client


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把文件/网页/数据库内容写入本地 RAG 知识库")
    parser.add_argument("--config", help="多源配置文件，例如 config/sources.yaml")
    parser.add_argument("--path", help="单个文件或目录（不写 config 时的快捷方式）")
    parser.add_argument("--recreate", action="store_true", help="清空集合并全量重建")
    parser.add_argument("--prune", action="store_true", help="删除本次没有再次出现的来源内容")
    parser.add_argument("--no-lexical", action="store_true", help="跳过 BM25 索引重建")
    args = parser.parse_args(argv)
    if args.config and args.path:
        parser.error("--config 与 --path 不能同时使用")
    return args


def build_config(args: argparse.Namespace) -> SourcesConfig:
    if args.config:
        return load_sources_config(args.config)
    if args.path:
        return SourcesConfig(sources=[FileSource(path=args.path)])
    return default_sources_config()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = build_config(args)
    except ConfigError as exc:
        print(f"❌ 配置错误：{exc}")
        return 1

    client = open_client()
    try:
        stats = run_ingest(
            config,
            client=client,
            embedding=OllamaEmbeddings(model=EMB_MODEL),
            recreate=args.recreate,
            prune=args.prune,
            rebuild_lexical=not args.no_lexical,
        )
    finally:
        close_client(client)

    if stats.errors:
        print("\n⚠️ 部分内容未入库：")
        for error in stats.errors:
            print(f"  - {error}")
        return 1

    print(
        f"\n📊 完成：新增 {stats.added}，更新 {stats.updated}，跳过 {stats.skipped}，"
        f"写入 {stats.chunks} 个 chunk"
        + (f"，清理 {stats.pruned} 个旧向量" if stats.pruned else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
