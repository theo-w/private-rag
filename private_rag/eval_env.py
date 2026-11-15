"""评估环境隔离：在导入 private_rag.config 之前设置独立的 Qdrant/BM25 路径。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EVAL_COLLECTION_NAME = "rag_eval_kb"
EVAL_STORAGE_DIR = ROOT / "storage" / "eval" / "qdrant_storage"
EVAL_LEXICAL_INDEX_PATH = ROOT / "storage" / "eval" / "lexical" / "bm25.pkl"
EVAL_CACHE_DIR = ROOT / "storage" / "eval_cache"


def configure_eval_env() -> None:
    """设置评估专用环境变量；已有值优先，便于临时覆盖。"""
    defaults = {
        "RAG_COLLECTION_NAME": EVAL_COLLECTION_NAME,
        "RAG_STORAGE_DIR": str(EVAL_STORAGE_DIR),
        "RAG_LEXICAL_INDEX_PATH": str(EVAL_LEXICAL_INDEX_PATH),
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
