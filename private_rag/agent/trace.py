"""决策轨迹：记录 agentic 循环每一步「为什么这么选」，并支持回放。

一次问答写一行 JSON 到 AGENT_TRACE_PATH，回放脚本按 trace_id 取回并逐条展示。
观察与理由按字符上限截断，避免大段上下文把轨迹文件撑爆。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from private_rag.config import AGENT_TRACE_FIELD_CHARS


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _clip(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


@dataclass
class AgentStep:
    """循环中的一步：一次工具调用，或一次直接结论。"""

    index: int
    kind: str  # route | tool | final | error | stop
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    ok: bool = True
    observation: str = ""
    rows: Optional[int] = None
    evidence_blocks: int = 0
    elapsed_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentTrace:
    """一次 agentic 问答的完整决策链。"""

    trace_id: str
    question: str
    model: str
    created_at: float
    intent: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    elapsed_ms: int = 0
    tool_calls: int = 0
    request_id: str = ""
    session_id: str = ""
    field_chars: int = AGENT_TRACE_FIELD_CHARS

    def add(self, step: AgentStep) -> None:
        self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("field_chars", None)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def save(self, path: Path) -> Optional[Path]:
        """以 JSONL 追加一行；写盘失败不影响回答，只打日志由调用方决定。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(self.to_json() + "\n")
        return path

    def record_observation(self, text: str) -> str:
        return _clip(text, self.field_chars)


def load_traces(
    path: Path,
    *,
    trace_id: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """读取轨迹；trace_id 指定时只返回匹配的那条。文件不存在返回空列表。"""
    path = Path(path)
    if not path.exists():
        return []

    traces: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if trace_id and data.get("trace_id") != trace_id:
                continue
            traces.append(data)

    if trace_id:
        return traces
    return traces[-limit:]


def format_trace(data: dict[str, Any]) -> str:
    """把一条轨迹渲染成可回放的文本，供 CLI 与接口复用。"""
    lines: list[str] = []
    lines.append(f"trace_id: {data.get('trace_id', '')}")
    lines.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get('created_at', 0)))}")
    lines.append(f"模型: {data.get('model', '')}")
    lines.append(f"问题: {data.get('question', '')}")
    lines.append(f"意图: {data.get('intent', '')}｜停止原因: {data.get('stop_reason', '')}")
    lines.append(f"总耗时: {data.get('elapsed_ms', 0)} ms｜工具调用: {data.get('tool_calls', 0)} 次")
    lines.append("")
    lines.append("决策过程：")
    for step in data.get("steps", []):
        head = f"[{step.get('index')}] {step.get('kind')}"
        if step.get("tool"):
            head += f" -> {step['tool']}"
        lines.append(head)
        if step.get("reason"):
            lines.append(f"     理由: {step['reason']}")
        if step.get("args"):
            lines.append(f"     参数: {json.dumps(step['args'], ensure_ascii=False)}")
        if step.get("error"):
            lines.append(f"     错误: {step['error']}")
        elif step.get("observation"):
            label = "结论" if step.get("kind") == "final" else "观察"
            lines.append(f"     {label}: {step['observation']}")
        if step.get("rows") is not None:
            lines.append(f"     返回行数: {step['rows']}")

    citations = data.get("citations", [])
    if citations:
        lines.append("")
        lines.append("引用：")
        for citation in citations:
            location = citation.get("source_uri", "")
            if citation.get("page") is not None:
                location += f"｜第 {citation['page']} 页"
            if citation.get("table"):
                location += f"｜表 {citation['table']}"
            lines.append(
                f"[{citation.get('index')}] {citation.get('title')}（{citation.get('source_type')}） {location}"
            )

    lines.append("")
    lines.append(f"回答: {data.get('answer', '')}")
    return "\n".join(lines)
