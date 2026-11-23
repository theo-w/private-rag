"""构建 v4 评估专用知识库：独立 Qdrant 路径与集合，不影响演示库。

用法：
    python scripts/prepare_eval_kb.py --recreate
    python scripts/prepare_eval_kb.py --no-lexical
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须先设置环境变量，再导入 private_rag.config 等模块（常量在导入时确定）
from private_rag.eval_env import configure_eval_env  # noqa: E402

configure_eval_env()

from langchain_ollama import OllamaEmbeddings  # noqa: E402

from private_rag import config  # noqa: E402
from private_rag.config import EMB_MODEL  # noqa: E402
from private_rag.ingestion.pipeline import run_ingest  # noqa: E402
from private_rag.sources import ConfigError, load_sources_config  # noqa: E402
from private_rag.store import close_client, count_points, open_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "sources.eval.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 v4 Ragas 评估专用知识库")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="评估数据源配置")
    parser.add_argument("--recreate", action="store_true", help="清空评估集合并全量重建")
    parser.add_argument("--no-lexical", action="store_true", help="跳过 BM25 索引重建")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sources_cfg = load_sources_config(args.config)
    except ConfigError as exc:
        print(f"❌ 配置错误：{exc}")
        return 1

    print("📦 评估知识库（与演示库隔离）")
    # 打印生效值而不是 eval_env 的默认常量：消融靠环境变量分库，打印默认值会让人以为隔离没生效
    print(f"   collection : {config.COLLECTION_NAME}")
    print(f"   Qdrant     : {config.STORAGE_DIR}")
    print(f"   BM25       : {config.LEXICAL_INDEX_PATH}")
    print(f"   解析后端   : {config.SCAN_PARSER}")

    client = open_client()
    try:
        stats = run_ingest(
            sources_cfg,
            client=client,
            embedding=OllamaEmbeddings(model=EMB_MODEL),
            recreate=args.recreate,
            rebuild_lexical=not args.no_lexical,
        )
        vectors = count_points(client)
    finally:
        close_client(client)

    if stats.errors:
        print("\n⚠️ 部分内容未入库：")
        for error in stats.errors:
            print(f"  - {error}")
        return 1

    print(
        f"\n📊 完成：新增 {stats.added}，更新 {stats.updated}，跳过 {stats.skipped}，"
        f"写入 {stats.chunks} 个 chunk；当前集合共 {vectors} 个向量"
    )
    print("✅ 评估库已就绪，可运行 python scripts/evaluate.py --validate-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
