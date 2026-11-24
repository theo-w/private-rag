"""各处调用本地模型时要不要传采样参数与随机种子。

默认一个都不传，运行时保持服务端的默认抽样行为；只有评估链路会在
``configure_eval_env()`` 里把温度钉成 0、并给一个固定 seed——否则同一条证据两次生成
可以给出不同答案，前后对比的差别就混进了抽样噪声（见 docs/ROADMAP.md 的 v8.3）。

参数都从 ``config`` 现读而不是在导入时抓一份快照，这样测试与运行期改配置都能立刻生效。
"""

from __future__ import annotations

from typing import Any

from private_rag import config


def seed_kwargs() -> dict[str, Any]:
    """整条链路共用的随机种子：没配置就不传，保持服务端默认。

    实测这台 MLX 后端不接受 seed（温度大于 0 时固定 seed 每次输出都不同），传了只是
    表达意图；真正保证复现的是温度为 0。
    """
    return {"seed": config.LLM_SEED} if config.LLM_SEED is not None else {}


def agent_answer_kwargs() -> dict[str, Any]:
    """agentic 最终作答的温度：没配置就不传（改动前这里根本不传温度）。"""
    value = config.AGENT_ANSWER_TEMPERATURE
    return {"temperature": value} if value is not None else {}


def classic_generation_kwargs() -> dict[str, Any]:
    """经典 RAG 生成（``generate_answer`` / ``stream_answer``）的温度：没配置就不传。

    固定链路跑 `full` / `none` 这些配置时同样会因为服务端默认抽样而重跑不一致，
    所以与 agentic 一起纳入可复现的范围。
    """
    value = config.GENERATION_TEMPERATURE
    return {"temperature": value} if value is not None else {}
