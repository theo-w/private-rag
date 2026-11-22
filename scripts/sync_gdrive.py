"""Google Drive 语料接入 CLI：下载到本地目录 -> 复用现有入库链路写进知识库。

下载走本机 CLI（rclone / gdrive），不引入 Google SDK；联网只发生在这一步，
入库与检索链路照旧完全本地离线。凭据留在本机（rclone remote 配置），不进仓库。

用法：
    python3 scripts/sync_gdrive.py --remote gdrive:Notes                    # 只下载
    python3 scripts/sync_gdrive.py --remote gdrive:Notes --ingest           # 下载后入库
    python3 scripts/sync_gdrive.py --remote gdrive:Notes --mode sync --dry-run
    python3 scripts/sync_gdrive.py --skip-sync --ingest --prune             # 只入库已下载内容
    python3 scripts/sync_gdrive.py --tool gdrive --target <folderId> --ingest
    python3 scripts/sync_gdrive.py --list-remotes
    额外参数透传给下载工具：-- --drive-skip-gdocs

入库默认写进演示库那个 collection，用 tags=gdrive 区分；想把真实语料单独放一个库，
用 RAG_COLLECTION_NAME / RAG_STORAGE_DIR / RAG_LEXICAL_INDEX_PATH 指向独立路径
（与评估库同一套隔离开关），或改用 --config 自备配置。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.config import EMB_MODEL, GDRIVE_REMOTE, ROOT
from private_rag.ingestion.gdrive import (
    DEFAULT_EXPORT_FORMATS,
    DEFAULT_TAGS,
    Inventory,
    SyncError,
    assert_dest_is_private,
    build_ingest_config,
    build_sync_plan,
    inventory,
    require_binary,
    resolve_dest,
    run_sync,
)
from private_rag.sources import ConfigError, load_sources_config


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Google Drive 内容下载到本地目录，并可选走现有入库链路",
        epilog="-- 之后的参数原样传给下载工具（例如 -- --drive-skip-gdocs）",
    )
    parser.add_argument(
        "--remote",
        "--target",
        dest="remote",
        default=None,
        help="下载目标：rclone 写 remote:path（如 gdrive:Notes），gdrive 写文件/文件夹 ID；"
        "省略时取环境变量 RAG_GDRIVE_REMOTE",
    )
    parser.add_argument("--tool", choices=["rclone", "gdrive"], default="rclone", help="下载工具")
    parser.add_argument(
        "--binary", help="下载工具的可执行文件路径（默认按 PATH 找 rclone/gdrive）"
    )
    parser.add_argument(
        "--dest", help=f"本地下载目录，相对路径按项目根解析（默认 {resolve_dest()}）"
    )
    parser.add_argument(
        "--mode",
        choices=["copy", "sync"],
        default="copy",
        help="copy 只增量添加（默认）；sync 会让本地目录与 Drive 一致，删除本地多余文件",
    )
    parser.add_argument(
        "--export-formats",
        default=DEFAULT_EXPORT_FORMATS,
        help="Google 原生文档（Docs/Sheets/Slides）导出格式，逗号分隔（默认 pdf，"
        "因为入库链路只认 txt/md/pdf）",
    )
    parser.add_argument("--include", action="append", default=[], help="只下载匹配的文件名，可重复")
    parser.add_argument("--dry-run", action="store_true", help="下载工具预演，不实际写文件")
    parser.add_argument("--single-file", action="store_true", help="gdrive：目标是单个文件，不递归")
    parser.add_argument("--skip-sync", action="store_true", help="不下载，只用目录里已有内容")
    parser.add_argument("--list-remotes", action="store_true", help="列出本机 rclone remote 后退出")
    parser.add_argument("--ingest", action="store_true", help="下载完成后走入库链路写入知识库")
    parser.add_argument("--config", help="入库用的配置文件（不传则自动生成一个指向下载目录的 file 源）")
    parser.add_argument("--recreate", action="store_true", help="清空集合并全量重建")
    parser.add_argument("--prune", action="store_true", help="删除本次没有再次出现的内容")
    parser.add_argument("--no-lexical", action="store_true", help="跳过 BM25 索引重建")
    parser.add_argument(
        "--tags",
        default=",".join(DEFAULT_TAGS),
        help="自动生成 file 源时打的标签，逗号分隔（默认 gdrive）",
    )
    parser.add_argument(
        "--allow-tracked-dest",
        action="store_true",
        help="允许下载目录不被 gitignore（不推荐：真实语料正文会进公开仓库）",
    )
    args, extra = parser.parse_known_args(argv)
    args.extra_args = extra
    if args.dry_run and args.ingest:
        parser.error("--dry-run 不写文件，不能与 --ingest 同时用")
    if args.skip_sync and args.remote and not args.list_remotes:
        # --skip-sync 时目标参数没有意义，说清楚而不是默默忽略
        parser.error("--skip-sync 不需要 --remote")
    return args


def print_inventory(dest: Path, report: Inventory, logger=print) -> None:
    logger(f"📁 {dest}：{report.summary()}")


def list_remotes(extra_args: List[str], binary: Optional[str] = None) -> int:
    from private_rag.ingestion.gdrive import _run_download  # 下载层同一处，便于统一打桩

    tool_binary = require_binary("rclone", binary)
    result = _run_download([tool_binary, "listremotes"] + list(extra_args), ROOT)
    if result.returncode != 0:
        print(f"❌ rclone listremotes 失败：{(result.stderr or result.stdout).strip()[:300]}")
        return 1
    names = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if not names:
        print("本机还没有 remote：先跑 rclone config 新建一个类型为 drive 的 remote")
        return 1
    print("🛰️ 本机 rclone remote：")
    for name in names:
        print(f"   {name}")
    return 0


def ingest(dest: Path, args: argparse.Namespace) -> int:
    """入库：--config 优先，否则用下载目录自动生成一个 file 源。"""
    from langchain_ollama import OllamaEmbeddings

    from private_rag.ingestion.pipeline import run_ingest
    from private_rag.store import close_client, open_client

    if args.config:
        config = load_sources_config(args.config)
    else:
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        config = build_ingest_config(dest, tags=tags, include=args.include)

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


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    dest = resolve_dest(args.dest)

    if args.list_remotes:
        return list_remotes(args.extra_args, args.binary)

    try:
        assert_dest_is_private(dest, allow=args.allow_tracked_dest)
    except SyncError as exc:
        print(f"❌ {exc}")
        return 1

    if not args.skip_sync:
        remote = args.remote or GDRIVE_REMOTE
        if not remote:
            print("❌ 没有下载目标：加 --remote gdrive:某个文件夹，或导出 RAG_GDRIVE_REMOTE")
            return 1
        try:
            plan = build_sync_plan(
                args.tool,
                remote,
                dest,
                mode=args.mode,
                export_formats=args.export_formats,
                include=args.include,
                extra_args=args.extra_args,
                dry_run=args.dry_run,
                single_file=args.single_file,
                binary=args.binary,
            )
            run_sync(plan)
        except SyncError as exc:
            print(f"❌ {exc}")
            return 1

    report = inventory(dest)
    print_inventory(dest, report)

    if args.ingest:
        if report.ingestable == 0:
            print(
                "❌ 下载目录里没有入库链路支持的文件（txt/md/pdf），没有可入库的内容。"
                "Google 原生文档请用 --export-formats pdf 重新下载"
            )
            return 1
        try:
            return ingest(dest, args)
        except ConfigError as exc:
            print(f"❌ 配置错误：{exc}")
            return 1
    else:
        print("ℹ️ 未加 --ingest，本次只下载。入库：python3 scripts/sync_gdrive.py --skip-sync --ingest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
