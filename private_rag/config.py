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


def _env_path(name: str, default: Path) -> Path:
    """读取路径环境变量；相对路径按项目根目录解析，空值回退默认。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


# 数据与存储
# 可用环境变量隔离评估库，避免与演示服务共用同一个 Qdrant 本地目录
DOCS_DIR = ROOT / "data" / "docs"
STORAGE_DIR = _env_path("RAG_STORAGE_DIR", ROOT / "storage" / "qdrant_storage")
LEXICAL_INDEX_PATH = _env_path(
    "RAG_LEXICAL_INDEX_PATH", ROOT / "storage" / "lexical" / "bm25.pkl"
)
COLLECTION_NAME = os.getenv("RAG_COLLECTION_NAME", "").strip() or "rag_knowledge_base"
DEFAULT_SOURCES_CONFIG = ROOT / "config" / "sources.yaml"

# 文本切分
# 子块用于向量/BM25 召回，父块保留完整上下文；父块默认不重叠，避免重复内容。
CHUNK_SIZE = 700
CHUNK_OVERLAP = 100
PARENT_CHUNK_SIZE = _env_int("RAG_PARENT_CHUNK_SIZE", 2000)
PARENT_CHUNK_OVERLAP = _env_int("RAG_PARENT_CHUNK_OVERLAP", 0)

# 检索参数
DENSE_TOP_K = 20
LEXICAL_TOP_K = 20
FUSION_TOP_K = 10
RRF_K = 60
TOP_K_RERANK = 3
# 送入 LLM 的上下文总字符预算；父块超预算时回退到命中的子块。
CONTEXT_MAX_CHARS = _env_int("RAG_CONTEXT_MAX_CHARS", 6000)

# 模型
EMB_MODEL = "bge-m3"
LLM_MODEL = "qwen3.8:27b-mlx"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"

# 网络抓取
WEB_USER_AGENT = "private-rag/0.2 (+https://github.com/theo-w/private-rag)"

# v3 HTTP 服务
APP_VERSION = "0.6.0"
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

# v7 Agentic RAG
# 行动循环的最大步数；每一步是一次工具调用或最终结论。
AGENT_MAX_STEPS = _env_int("RAG_AGENT_MAX_STEPS", 4)
# 单个工具结果回灌给模型的字符预算，避免多轮观察把上下文撑爆。
AGENT_TOOL_CHARS = _env_int("RAG_AGENT_TOOL_CHARS", 3000)
# 单次工具调用的总字符预算（所有观察加起来），超限后停止调用新工具。
AGENT_CONTEXT_CHARS = _env_int("RAG_AGENT_CONTEXT_CHARS", 7000)
# 运行时 SQL 最多返回多少行
AGENT_SQL_ROWS = _env_int("RAG_AGENT_SQL_ROWS", 20)
# 写进工具描述的「库内数据目录」字符上限：列名加少量样例值，路由器靠它判断事实是否在表里
AGENT_CATALOG_CHARS = _env_int("RAG_AGENT_CATALOG_CHARS", 420)
# 每个文本列最多取几个样例值；0 表示只列列名不取样例（换成大表时用它关掉取样）
AGENT_CATALOG_SAMPLES = _env_int("RAG_AGENT_CATALOG_SAMPLES", 4)

# 路由器判 none 时不再直接拒答：先做一次知识库检索验证，拒答必须建立在证据上。
# 置 1 退回旧行为（判成 none 就跳过检索直接拒答）。
AGENT_NONE_PROBE = not _env_flag("RAG_DISABLE_AGENT_NONE_PROBE")
# 决策 JSON 的输出预算。推理型模型会先输出一段不可见的思考内容，
# 预算给小了正文就是空的，路由会整段回退到关键词判断。
AGENT_PLANNER_TOKENS = _env_int("RAG_AGENT_PLANNER_TOKENS", 2000)
# 决策模型；默认沿用生成模型
AGENT_LLM_MODEL = os.getenv("RAG_AGENT_LLM_MODEL", "").strip() or LLM_MODEL
# 决策轨迹落盘，用于「trace 可回放每条决策」
AGENT_TRACE_ENABLED = not _env_flag("RAG_DISABLE_AGENT_TRACE")
# 双侧查询门控：intent=mixed 时，代码级禁止在任一侧零证据的情况下选 final
# 置 1 关掉门控（回退到仅靠提示词约束的修法 A）
AGENT_MIXED_GATE = not _env_flag("RAG_DISABLE_AGENT_MIXED_GATE")
AGENT_TRACE_PATH = _env_path("RAG_AGENT_TRACE_PATH", ROOT / "storage" / "traces" / "agent.jsonl")
# 单条观察/理由写入轨迹的字符上限
AGENT_TRACE_FIELD_CHARS = _env_int("RAG_AGENT_TRACE_FIELD_CHARS", 1200)
# 运行时 SQL 的授权来源：只有这里声明了 allow_runtime_query 的 sql 源才可被查询
AGENT_SOURCES_CONFIG = _env_path("RAG_AGENT_SOURCES_CONFIG", DEFAULT_SOURCES_CONFIG)
