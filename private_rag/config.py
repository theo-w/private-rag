"""统一配置：路径基于项目根目录，不依赖脚本的运行位置。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
