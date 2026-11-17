"""决策器：把「路由」和「步进」两件事收敛成一个可注入的接口。

运行时只依赖 Planner 协议，测试用脚本化的实现替身即可完整覆盖循环，
不需要本机 Ollama；生产用 LlmPlanner，失败时由运行时回退到启发式路由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from private_rag.agent.prompts import (
    ask_json,
    build_route_prompt,
    build_step_prompt,
    parse_decision,
    parse_route,
)
from private_rag.config import AGENT_LLM_MODEL
from private_rag.generation import History


@dataclass
class RouteDecision:
    """意图路由的结果，同时带上首个要执行的动作。"""

    intent: str
    action: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    fallback: bool = False

    @property
    def tool(self) -> str:
        return str(self.action.get("tool") or "")

    @property
    def args(self) -> dict[str, Any]:
        args = self.action.get("args")
        return args if isinstance(args, dict) else {}


@dataclass
class StepDecision:
    """循环中某一步的选择：继续调用工具，还是就此作答。"""

    action: str
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    answer: str = ""


class Planner:
    def route(
        self,
        question: str,
        tool_specs: str,
        history: Optional[History] = None,
    ) -> RouteDecision:
        raise NotImplementedError

    def decide(
        self,
        question: str,
        tool_specs: str,
        evidence: str,
        observations: Sequence[str],
        history: Optional[History] = None,
        *,
        steps_used: int = 1,
        max_steps: int = 4,
    ) -> StepDecision:
        raise NotImplementedError


class LlmPlanner(Planner):
    """用本机 LLM 的 JSON 输出做决策；不可用时抛 PlannerError 交给运行时回退。"""

    def __init__(self, model: str = AGENT_LLM_MODEL):
        self.model = model

    def route(
        self,
        question: str,
        tool_specs: str,
        history: Optional[History] = None,
    ) -> RouteDecision:
        prompt = build_route_prompt(question, tool_specs, history)
        data = ask_json(prompt, model=self.model, num_predict=400)
        intent, action, reason = parse_route(data)
        return RouteDecision(intent=intent, action=action, reason=reason)

    def decide(
        self,
        question: str,
        tool_specs: str,
        evidence: str,
        observations: Sequence[str],
        history: Optional[History] = None,
        *,
        steps_used: int = 1,
        max_steps: int = 4,
    ) -> StepDecision:
        prompt = build_step_prompt(
            question,
            tool_specs,
            evidence,
            observations,
            history,
            steps_used=steps_used,
            max_steps=max_steps,
        )
        data = ask_json(prompt, model=self.model, num_predict=600)
        action, payload, reason, answer = parse_decision(data)
        if action == "final":
            return StepDecision(action="final", reason=reason, answer=answer)
        return StepDecision(
            action="tool",
            tool=str(payload.get("tool") or ""),
            args=payload.get("args") if isinstance(payload.get("args"), dict) else {},
            reason=reason,
        )
