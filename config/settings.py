"""项目统一配置：所有路径都基于项目根目录，不依赖脚本的运行位置。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 数据与存储位置
DOCS_DIR = ROOT / "data" / "docs"
STORAGE_DIR = ROOT / "storage" / "qdrant_storage"
COLLECTION_NAME = "rag_knowledge_base"

# 文本切分
CHUNK_SIZE = 700
CHUNK_OVERLAP = 100

# 检索参数：召回数大于库内块数时会自动返回全部，不会报错
TOP_K_RECALL = 10
TOP_K_RERANK = 3

# 模型
EMB_MODEL = "bge-m3"
LLM_MODEL = "qwen3.8:27b-mlx"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
