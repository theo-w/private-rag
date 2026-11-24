"""统一配置：路径基于项目根目录，不依赖脚本的运行位置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def _env_optional_float(name: str) -> Optional[float]:
    """可选浮点参数：未设置或非法都返回 ``None``，意思是「不传给模型」。

    这类参数的默认值必须由调用方保留（不传），否则一改环境变量就改变了运行时行为。
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_optional_int(name: str) -> Optional[int]:
    """可选整数参数：未设置或非法都返回 ``None``，同 ``_env_optional_float``。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


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


# 「哪些环节启用」型取值：真值等价于全部环节，假值等价于都不启用
_TRUTHY_WORDS = {"1", "true", "yes", "on", "all"}
_FALSY_WORDS = {"0", "false", "no", "off", "none"}


def _env_stages(name: str, default: str) -> frozenset:
    """读取环节集合：``all``/真值取全部环节，``off``/假值取空集，其余按逗号分隔。

    只认已声明的环节名，非法取值回退到 ``default``，不会因为拼错开关而静默失效。
    """
    raw = os.getenv(name, "").strip().lower()
    if raw in _TRUTHY_WORDS:
        return frozenset({"route", "step"})
    if raw in _FALSY_WORDS:
        return frozenset()
    stages = frozenset(part for part in raw.split(",") if part in {"route", "step"})
    return stages or frozenset({default})


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
# Google Drive 语料：下载目录（真实语料，已 gitignore，正文不进公开仓库）与默认 remote
GDRIVE_SYNC_DIR = _env_path("RAG_GDRIVE_SYNC_DIR", ROOT / "data" / "gdrive")
GDRIVE_REMOTE = os.getenv("RAG_GDRIVE_REMOTE", "").strip()

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
# 决策调用里哪些环节让模型先思考：route / step / all / off，默认只让路由思考。
# 思考 token 既计入上面的输出预算（撞满就把决策 JSON 截断，见 planner_unavailable），
# 又占掉每次决策调用的绝大部分时间（本机实测同一条提示词：路由开思考 40.4 秒 /
# 245 输出 token，关思考 6.3 秒 / 61 输出 token；步进决策 62.8 秒 -> 5.1 秒）。
# 之所以默认保留路由：路由判错意图的代价最大——误判成 mixed 会触发双侧门控，
# 把步数烧光并稀释证据上下文，实测三条 multi_step 样本因此从作答变成拒答。
# 步进决策次数多、输出同样只是一小段 JSON，关掉最划算。
# 置 all（或 1/true）两个环节都保留思考，置 off 全部关掉。最终作答不受这个开关影响。
AGENT_PLANNER_THINKING_STAGES = _env_stages("RAG_AGENT_PLANNER_THINKING", "route")
# 要不要在路由提示词末尾要求模型把思考写短。决策 JSON 只要几十个字，思考实测能写到上千个
# 输出 token（同一条路由提示词 60~1215 个），路由那一次调用因此占掉整轮 48% 的时间，
# 而且长度很不稳定。这台 Ollama(0.40.16) 的 reasoning 档位（low/minimal）实测管不住思考长度，
# 提示词里加一句长度约束实测能把路由调用砍掉约一半。置 1 退回不加这句的老提示词。
AGENT_THINKING_BRIEF = not _env_flag("RAG_DISABLE_AGENT_THINKING_BRIEF")


def _answer_thinking_mode(raw: str) -> str:
    """最终作答思考开关的取值归一：``keep`` / ``on`` / ``off``，其余一律回退 ``keep``。"""
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return "on"
    if value in {"0", "false", "no", "off"}:
        return "off"
    return value if value in {"keep", "on", "off"} else "keep"


# 最终作答要不要让模型先思考：keep / on / off，默认 keep（连 reasoning 都不传，保持模型默认行为）。
# 同一条证据文本本机实测 keep 与 on 都是 10.3 秒、off 是 2.95 秒，也就是默认确实在思考，
# 关掉一条能省约 7 秒。但 29 条对照里关思考带来三条误拒（c03 / m01 / m04），
# answer_rate 1.000 → 0.880、source_hit 1.000 → 0.960，所以默认保留思考，只留开关。
# 读数与逐条变化见 docs/ROADMAP.md 的 v8.2。
AGENT_ANSWER_THINKING = _answer_thinking_mode(os.getenv("RAG_AGENT_ANSWER_THINKING", "keep"))
# 采样参数与随机种子：默认都不传，运行时保持服务端的默认抽样行为。
# 评估链路会在 configure_eval_env() 里把两者钉死（温度 0 + 固定 seed）——此前同一条默认配置的
# 两轮评估对不上（单条均耗时 40.6s vs 56.6s、source_hit 1.000 vs 0.960、d02 意图 mixed vs database），
# 因为最终作答从来没传 temperature，用的是服务端默认值，29 条上 ±3 条拒答可能只是抽样噪声。
# 钉死以后前后对比才谈得上归因，见 docs/ROADMAP.md 的 v8.3。
AGENT_ANSWER_TEMPERATURE = _env_optional_float("RAG_AGENT_ANSWER_TEMPERATURE")
# 经典 RAG 链路（generate_answer / stream_answer）的生成温度：改动前同样不传温度，
# 所以跑 full / none 这些配置的评估照样重跑不一致。评估链路把它与 agentic 一起钉成 0，
# 运行时（demo、serve）仍然不传，行为不变。
GENERATION_TEMPERATURE = _env_optional_float("RAG_GENERATION_TEMPERATURE")
# 随机种子：agentic 的决策与最终作答、经典链路的生成与查询改写共用这一个值
# （原名 RAG_AGENT_LLM_SEED，现在不只 agentic 在读了）。实测这台 MLX 后端不接受 seed，
# 复现靠温度为 0；它留着是为了让调用意图明确。
LLM_SEED = _env_optional_int("RAG_LLM_SEED")
# 决策输出通道（v7 function calling 对比的实验开关）：
# json —— format="json" 结构化输出（默认）；function —— 原生 function calling。
# 本机 MLX 模型的工具调用格式不稳定，默认不启用；对比见 scripts/compare_fc_decisions.py。
AGENT_PLANNER_MODE = os.getenv("RAG_AGENT_PLANNER_MODE", "json").strip().lower()
if AGENT_PLANNER_MODE not in {"json", "function"}:
    AGENT_PLANNER_MODE = "json"
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

# v6 扫描件版面表格提取
# 置 1 / true / yes / on 时关闭扫描件表格提取（默认开启）
SCAN_TABLE_ENABLED = not _env_flag("RAG_DISABLE_SCAN_TABLE")
# 预置 SLANet 模型文件路径；环境变量 RAG_SCAN_TABLE_MODEL 覆盖
SCAN_TABLE_MODEL_PATH = os.getenv(
    "RAG_SCAN_TABLE_MODEL",
    str(ROOT / "data" / "models" / "slanet_plus.onnx"),
)
