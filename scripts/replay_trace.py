"""回放 agentic 决策轨迹：逐条展示每一步「为什么这么选」。

轨迹由 /api/v1/chat/agent 与 scripts/chat_agent.py 追加写入 JSONL，
每行一次问答的完整决策链。本脚本按 trace_id 取回并渲染成可读文本。

运行：
    python3 scripts/replay_trace.py --list
    python3 scripts/replay_trace.py --trace-id 5e4b5f9a9f23
    python3 scripts/replay_trace.py --last 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from private_rag.agent.trace import format_trace, load_traces
from private_rag.config import AGENT_TRACE_PATH

# --list 是索引，默认要多列几条；不带 --list 时只回放最近一条
LIST_LIMIT = 20


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回放 agentic 决策轨迹")
    parser.add_argument("--trace-id", help="要回放的 trace_id")
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help=f"没有指定 trace-id 时取最近 N 条（回放默认 1 条，--list 默认 {LIST_LIMIT} 条）",
    )
    parser.add_argument("--list", action="store_true", help="只列出轨迹索引，不展开细节")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON，便于二次处理")
    parser.add_argument("--path", default=str(AGENT_TRACE_PATH), help="轨迹文件路径")
    return parser.parse_args(argv)


def print_index(records: list[dict]) -> None:
    if not records:
        print("（还没有任何轨迹；先跑一次 python3 scripts/chat_agent.py 或调用 /api/v1/chat/agent）")
        return
    print(f"{'trace_id':<14}{'意图':<10}{'工具':<5}{'停止原因':<20}{'耗时ms':<9}问题")
    for record in reversed(records):
        print(
            f"{str(record.get('trace_id', '')):<14}"
            f"{str(record.get('intent', '')):<10}"
            f"{str(record.get('tool_calls', 0)):<5}"
            f"{str(record.get('stop_reason', '')):<20}"
            f"{str(record.get('elapsed_ms', 0)):<9}"
            f"{str(record.get('question', ''))[:40]}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    path = Path(args.path)

    if args.trace_id:
        records = load_traces(path, trace_id=args.trace_id.strip())
        if not records:
            print(f"❌ 找不到 trace_id={args.trace_id}（轨迹文件：{path}）")
            print("   可用 --list 查看现有轨迹")
            return 1
    else:
        limit = args.last if args.last is not None else (LIST_LIMIT if args.list else 1)
        records = load_traces(path, limit=max(1, limit))

    if args.list:
        print_index(records)
        return 0

    if not records:
        print(f"（轨迹文件为空或不存在：{path}）")
        return 1

    for index, record in enumerate(records):
        if index:
            print("\n" + "=" * 70 + "\n")
        if args.json:
            print(json.dumps(record, ensure_ascii=False, indent=2))
        else:
            print(format_trace(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
