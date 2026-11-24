"""评估环境隔离：在导入 private_rag.config 之前设置独立的 Qdrant/BM25 路径。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EVAL_COLLECTION_NAME = "rag_eval_kb"
EVAL_STORAGE_DIR = ROOT / "storage" / "eval" / "qdrant_storage"
EVAL_LEXICAL_INDEX_PATH = ROOT / "storage" / "eval" / "lexical" / "bm25.pkl"
EVAL_CACHE_DIR = ROOT / "storage" / "eval_cache"
# agentic 评估组的运行时数据源：只挂只读 SQL 工具，不入库，与入库配置分开维护
EVAL_AGENT_SOURCES_CONFIG = ROOT / "config" / "sources.eval.agent.yaml"


def configure_eval_env() -> None:
    """设置评估专用环境变量；已有值优先，便于临时覆盖。"""
    defaults = {
        "RAG_COLLECTION_NAME": EVAL_COLLECTION_NAME,
        "RAG_STORAGE_DIR": str(EVAL_STORAGE_DIR),
        "RAG_LEXICAL_INDEX_PATH": str(EVAL_LEXICAL_INDEX_PATH),
        # 评估跑的是样本，不是真实问答；不写进演示轨迹文件，避免污染 storage/traces
        "RAG_DISABLE_AGENT_TRACE": "1",
        # 评估要的是可复现的前后对比，不是采样多样性：钉死两条链路的生成温度与全局 seed。
        # 此前同一条默认配置的两轮评估都对不上（40.6s vs 56.6s、source_hit 1.000 vs 0.960、
        # d02 意图 mixed vs database），钉死以后前后对比才能归因到改动本身。
        # 固定链路（full / none 等配置）走的是 generation.py 的生成，同样不传温度，所以一起钉。
        # 只影响评估链路（serve/demo 不读这里），且已有值优先，想跑采样版把它 export 掉。
        "RAG_AGENT_ANSWER_TEMPERATURE": "0",
        "RAG_GENERATION_TEMPERATURE": "0",
        "RAG_LLM_SEED": "7",
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
