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
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
