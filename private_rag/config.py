"""统一配置：路径基于项目根目录，不依赖脚本的运行位置。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；非法值回退默认值，避免服务因一个变量启动失败。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

# 数据与存储
DOCS_DIR = ROOT / "data" / "docs"
STORAGE_DIR = ROOT / "storage" / "qdrant_storage"
LEXICAL_INDEX_PATH = ROOT / "storage" / "lexical" / "bm25.pkl"
COLLECTION_NAME = "rag_knowledge_base"
DEFAULT_SOURCES_CONFIG = ROOT / "config" / "sources.yaml"

# 文本切分
CHUNK_SIZE = 700
CHUNK_OVERLAP = 100

# 检索参数
DENSE_TOP_K = 20
LEXICAL_TOP_K = 20
FUSION_TOP_K = 10
RRF_K = 60
TOP_K_RERANK = 3

# 模型
EMB_MODEL = "bge-m3"
LLM_MODEL = "qwen3.8:27b-mlx"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"

# 网络抓取
WEB_USER_AGENT = "private-rag/0.2 (+https://github.com/theo-w/private-rag)"

# v3 HTTP 服务
APP_VERSION = "0.3.0"
API_PREFIX = "/api/v1"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
MAX_QUESTION_CHARS = 2000

# 多轮会话（内存存储，重启即清空）
SESSION_TTL_SECONDS = _env_int("RAG_SESSION_TTL_SECONDS", 1800)
MAX_SESSIONS = _env_int("RAG_MAX_SESSIONS", 500)
MAX_SESSION_MESSAGES = _env_int("RAG_MAX_SESSION_MESSAGES", 60)
MAX_STORED_MESSAGE_CHARS = _env_int("RAG_MAX_STORED_MESSAGE_CHARS", 4000)
MAX_HISTORY_TURNS = _env_int("RAG_MAX_HISTORY_TURNS", 6)
MAX_HISTORY_CHARS = _env_int("RAG_MAX_HISTORY_CHARS", 3000)
HISTORY_MESSAGE_CHARS = _env_int("RAG_MAX_HISTORY_MESSAGE_CHARS", 800)
RERANK_ENABLED = not _env_flag("RAG_DISABLE_RERANK")
