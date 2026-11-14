"""启动 private-rag HTTP 服务。

Qdrant 使用本地磁盘模式时同一路径只允许一个进程访问，因此这里固定单 worker；
需要多 worker/横向扩展时，请改用 Qdrant server 模式并把会话存储换成 Redis 等外部实现。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG_LEVELS = ["critical", "error", "warning", "info", "debug", "trace"]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-rerank" in argv:
        # 必须在导入 config 之前设置；config 在导入时读取该开关
        os.environ["RAG_DISABLE_RERANK"] = "1"

    # 延迟导入：确保上面的环境变量先生效
    from private_rag.config import DEFAULT_API_HOST, DEFAULT_API_PORT

    parser = argparse.ArgumentParser(description="启动 private-rag HTTP 服务（单进程）")
    parser.add_argument("--host", default=DEFAULT_API_HOST, help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_API_PORT, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式，代码变更自动重启")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=LOG_LEVELS,
        help="日志级别",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="跳过 reranker 加载，直接使用融合结果（启动更快、占用更低）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    import uvicorn

    print(f"🚀 private-rag API: http://{args.host}:{args.port}/docs")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("⚠️ 服务默认无鉴权，请勿直接暴露到公网。")
    uvicorn.run(
        "private_rag.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
