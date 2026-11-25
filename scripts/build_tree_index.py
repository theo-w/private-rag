"""离线构建树索引：从已入库的块 payload 还原标题层级，落盘成独立索引文件。

树索引与 BM25 索引同一套做法，不需要重新入库，也不改动块的写入格式。

用法：
    python scripts/build_tree_index.py                 # 演示库 -> storage/tree/tree.pkl
    python scripts/build_tree_index.py --eval          # 评估库（与演示库隔离）
    python scripts/build_tree_index.py --path /tmp/t.pkl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `private_rag.config` 里的路径常量在导入时就确定，所以评估环境变量必须先设好。
# 这里只粗查 --eval（而不是先跑完整 argparse），就是为了把设置放在导入之前。
if "--eval" in sys.argv:
    from private_rag.eval_env import (  # noqa: E402
        EVAL_COLLECTION_NAME,
        EVAL_TREE_INDEX_PATH,
        EVAL_STORAGE_DIR,
        configure_eval_env,
    )

    configure_eval_env()

from private_rag.config import COLLECTION_NAME, TREE_INDEX_PATH, STORAGE_DIR  # noqa: E402
from private_rag.retrieval.tree import TreeIndex, build_from_store  # noqa: E402
from private_rag.store import close_client, count_points, open_client  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从已入库的块重建章节树索引")
    parser.add_argument("--eval", action="store_true", help="用评估知识库与评估目录，不碰演示库")
    parser.add_argument("--path", help="索引输出路径，默认取 RAG_TREE_INDEX_PATH")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.path) if args.path else TREE_INDEX_PATH

    print("🌳 章节树索引")
    print(f"   collection : {COLLECTION_NAME}")
    print(f"   Qdrant     : {STORAGE_DIR}")
    print(f"   输出        : {path}")

    points = 0
    client = open_client()
    try:
        points = count_points(client)
        if points <= 0:
            print("❌ 知识库为空或集合不存在，请先运行 ingest")
            return 1
        nodes = build_from_store(client, path, logger=print)
    finally:
        close_client(client)

    index = TreeIndex.load(path)
    if index is None:
        print("❌ 索引写完了但读不回来，请检查文件权限或版本")
        return 1
    stats = index.stats()
    print(
        f"✅ 完成：{points} 个块 / {stats['docs']} 个文档 / "
        f"{stats['sections']} 个章节节点（含文档根共 {stats['nodes']} 个）"
    )
    if stats["chunks"] < points:
        print(f"⚠️ 有 {points - stats['chunks']} 个块没有挂到章节上（缺 doc_id）")
    if args.eval:
        print(f"   评估索引 : {EVAL_TREE_INDEX_PATH}")
    print(
        "   启用     : 运行服务/评估时设 RAG_ENABLE_TREE_INDEX=1（默认关闭，不影响现有行为）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
