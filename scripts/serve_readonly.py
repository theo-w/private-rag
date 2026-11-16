"""启动只读演示服务（用于公网演示，例如 Render）。

与 scripts/serve.py 的区别：不加载模型与向量库，监听 0.0.0.0，端口读 Render 注入的
PORT 环境变量。数据来自 data/demo_snapshot.json。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 private-rag 只读演示服务")
    # Render 通过 PORT 注入端口，默认 10000 与 Render 容器内约定一致
    parser.add_argument("--host", default=os.getenv("RAG_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "10000")))
    parser.add_argument("--snapshot", default="", help="快照 JSON 路径")
    parser.add_argument("--token", default="", help="访问令牌；留空表示公开只读")
    parser.add_argument("--log-level", default="info")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from private_rag.api.readonly import DEFAULT_SNAPSHOT_PATH, create_readonly_app

    snapshot_path = Path(args.snapshot) if args.snapshot else DEFAULT_SNAPSHOT_PATH
    if args.token:
        os.environ["RAG_DEMO_TOKEN"] = args.token
    app = create_readonly_app(snapshot_path=snapshot_path)

    import uvicorn

    print(f"🔒 private-rag 只读演示: http://{args.host}:{args.port}/demo")
    print(f"   快照: {snapshot_path}")
    if not (os.getenv("RAG_DEMO_TOKEN") or "").strip():
        print("⚠️ 未设置 RAG_DEMO_TOKEN，语料浏览与检索对公网开放（快照仅含演示语料）。")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
