"""v4 评估基础设施：数据集校验、策略对比、确定性指标、Ragas 与报告。

设计原则：
- 数据集与指标计算是纯函数，不依赖 Ollama / Qdrant / Ragas，便于单元测试。
- Ragas 只在真正运行时延迟导入；解析失败或 judge 异常记为 NaN，不伪造分数。
- 报告同时给出确定性检索诊断和 Ragas 语义指标，所有差值相对 full 基线。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from private_rag.config import (
    AGENT_MAX_STEPS,
    CONTEXT_MAX_CHARS,
    EMB_MODEL,
    LLM_MODEL,
    TOP_K_RERANK,
    ROOT,
)
from private_rag.generation import REFUSAL, generate_answer
from private_rag.qa import RetrievalOptions

# v7 agentic 评估集新增三类：database 需要运行时 SQL，mixed 需要知识库与 SQL 各出一段证据，
# multi_step 需要先读观察再追问。既有数据集不含这三类，扩展白名单向后兼容。
CATEGORIES = (
    "architecture",
    "ingestion",
    "retrieval",
    "api",
    "boundary",
    # v7 集改用「需要哪个工具」这一维度分类，文档类问题单独成组
    "knowledge",
    "database",
    "mixed",
    "multi_step",
    # v7.3 起单独成组：标记「要靠对话历史还原指代」的样本，与「需要哪个工具」那一维度并列
    "coreference",
    "refusal",
)
EXPECTED_TOTAL = 40
EXPECTED_ANSWERABLE = 36
EXPECTED_REFUSAL = 4
EXPECTED_CATEGORIES = {
    "architecture": 8,
    "ingestion": 8,
    "retrieval": 8,
    "api": 8,
    "boundary": 4,
    "refusal": 4,
}
DEFAULT_DATASET = ROOT / "eval" / "project_rag_qa.jsonl"

RAGAS_DISPLAY_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)
RAGAS_SCORE_KEYS = {
    "faithfulness": ("faithfulness",),
    "answer_relevancy": ("answer_relevancy", "response_relevancy"),
    "context_precision": (
        "llm_context_precision_with_reference",
        "context_precision",
    ),
    "context_recall": ("context_recall", "llm_context_recall"),
}

CITATION_RE = re.compile(r"\[(\d+)\]")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
# 文档里会用“带 [1][2][3] 编号”这类说法讲引用格式；它是被引用的原文，
# 不是模型给出的引用，统计前应剔除，避免把格式说明误判成越界引用。
NOTATION_RE = re.compile(
    r"(?:\[[0-9]+\]\s*)+(?:编号|标号|引用格式|编号格式|标注格式)"
)
CITATION_RUN_RE = re.compile(r"(?:\[[0-9]+\])+")


def _strip_format_runs(text: str, citation_count: int) -> str:
    """剔除“1..N 连续编号”的格式示例，保留真正的引用。

    文档里常出现“标 [1][2][3]”这类讲引用格式的句子；当答案被引用的
    citations 不足 N 条时，这种从 1 开始连续枚举的编号不可能是真实引用，
    视为格式说明。citations 足够时（N ≤ citation_count）仍按真实引用统计。
    """

    def replace(match: re.Match[str]) -> str:
        numbers = [int(value) for value in CITATION_RE.findall(match.group(0))]
        if (
            len(numbers) >= 3
            and numbers == list(range(1, len(numbers) + 1))
            and len(numbers) > citation_count
        ):
            return ""
        return match.group(0)

    return CITATION_RUN_RE.sub(replace, text)


def citation_numbers(answer: str, *, citation_count: int | None = None) -> list[int]:
    """提取答案里真正表示引用的 [n]，忽略代码片段与引用格式说明。"""
    text = CODE_FENCE_RE.sub("", answer or "")
    text = INLINE_CODE_RE.sub("", text)
    text = NOTATION_RE.sub("", text)
    if citation_count is not None:
        text = _strip_format_runs(text, citation_count)
    return [int(value) for value in CITATION_RE.findall(text)]


class DatasetError(ValueError):
    """评估数据集格式或证据校验失败。"""


@dataclass(frozen=True)
class EvalSample:
    id: str
    question: str
    reference: str
    evidence: tuple[str, ...]
    source: str
    category: str
    answerable: bool
    # 期望引用指向的来源；留空表示与 source 相同。SQL 样本的 source 是证据权威文件
    # （种子脚本里逐字含业务数据），而引用指向 SQL 源标签，两者必须能分开表达，
    # 否则 source_hit 会恒为 0 —— 那是口径缺陷，不是检索缺陷。
    expected_source: str = ""
    # 前置对话轮次，形如 (("user", "..."), ("assistant", "..."))；只用于还原指代，
    # 不作为事实依据。空元组表示单轮样本。加载时校验成对出现、role 合法、content 非空，
    # 运行时原样传给 agent.run 的 history 参数。
    history: tuple[tuple[str, str], ...] = ()
    # 双侧样本声明的全部应取证来源（如「文档侧 + SQL 侧」）；只有 both_sides_hit 读数使用。
    # 留空或只有一条表示本条不参与该读数；expected_source 的单字符串口径保持不变，
    # 旧字段与既有指标逐条可比。
    expected_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfigSpec:
    name: str
    label: str
    use_lexical: bool
    use_rerank: bool
    top_k: int = TOP_K_RERANK
    use_parent: bool = True
    query_rewrite: str = "off"
    context_max_chars: int = CONTEXT_MAX_CHARS
    # agentic 组不走“一次检索 + 一次生成”，由 collect_agent_results 采集
    is_agent: bool = False


CONFIGS = {
    "dense": ConfigSpec("dense", "仅向量召回", False, False, use_parent=False),
    "hybrid": ConfigSpec("hybrid", "向量 + BM25 + RRF", True, False, use_parent=False),
    "full": ConfigSpec(
        "full",
        "混合召回 + 精排（v4 基线）",
        True,
        True,
        use_parent=False,
    ),
    "parent": ConfigSpec(
        "parent",
        "混合召回 + 精排 + 父块上下文",
        True,
        True,
        use_parent=True,
    ),
    "parent_multi": ConfigSpec(
        "parent_multi",
        "父块上下文 + 多查询改写",
        True,
        True,
        use_parent=True,
        query_rewrite="multi",
    ),
    "parent_hyde": ConfigSpec(
        "parent_hyde",
        "父块上下文 + HyDE",
        True,
        True,
        use_parent=True,
        query_rewrite="hyde",
    ),
    "agent": ConfigSpec(
        "agent",
        "Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL）",
        True,
        True,
        use_parent=True,
        is_agent=True,
    ),
}


@dataclass
class SampleResult:
    sample_id: str
    config: str
    category: str
    answerable: bool
    question: str
    reference: str
    evidence: list[str]
    expected_source: str
    answer: str = ""
    contexts: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    source_uris: list[str] = field(default_factory=list)
    used_lexical: bool = False
    used_rerank: bool = False
    used_parent: bool = False
    query_rewrite: str = "off"
    query_count: int = 1
    context_chars: int = 0
    rewrite_fallback: bool = False
    rewrite_reason: str = ""
    requested_rerank: bool = False
    retrieved: int = 0
    refusal: bool = False
    retrieval_ms: float = 0.0
    generation_ms: float = 0.0
    generation_cached: bool = False
    error: Optional[str] = None
    metrics: dict[str, Optional[float]] = field(default_factory=dict)
    ragas: Optional[dict[str, Optional[float]]] = None
    # 以下字段只对 agentic 配置有意义；固定链路保持 None/空，报告显示为“—”
    intent: str = ""
    stop_reason: str = ""
    tool_calls: Optional[int] = None
    tool_steps: Optional[int] = None
    trace_id: str = ""
    # 该样本带了几轮历史；固定链路保持 None，报告显示为「—」
    history_turns: Optional[int] = None
    # 样本声明的双侧取证来源（来自 EvalSample.expected_sources）；逐条读数看 metrics 里的
    # both_sides_hit，这里只为让 JSON/报告能直接看出这条样本该取哪几侧
    expected_sources: list[str] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """规范化文本：NFKC + 小写，只保留字母/数字/汉字，便于证据片段比对。"""
    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    return "".join(char for char in normalized if char.isalnum())


# --------------------------------------------------------------------------- #
# 数据集
# --------------------------------------------------------------------------- #


def _resolve_source(source: str) -> Path:
    path = Path(source)
    if path.is_absolute() or ".." in path.parts:
        raise DatasetError(f"source 必须是项目内相对路径：{source}")
    full = ROOT / path
    if not full.is_file():
        raise DatasetError(f"source 文件不存在：{source}")
    return full


def load_dataset(path: Path | str = DEFAULT_DATASET) -> list[EvalSample]:
    """读取并校验 JSONL 评估集；任何结构或证据问题都抛 DatasetError。"""
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise DatasetError(f"评估集不存在：{dataset_path}")

    samples: list[EvalSample] = []
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    source_cache: dict[str, str] = {}
    required = {
        "id",
        "question",
        "reference",
        "evidence",
        "source",
        "category",
        "answerable",
    }
    # 三个可选字段：expected_source 只在证据权威文件与期望引用不是同一个东西时使用；
    # history 给需要还原指代的样本提供前置对话轮次；
    # expected_sources 给「文档侧 + SQL 侧」的双侧样本声明全部应取证来源（both_sides_hit 用）
    optional = {"expected_source", "history", "expected_sources"}
    allowed = required | optional

    lines = dataset_path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise DatasetError(f"第 {line_number} 行必须是 JSON 对象")

        missing = required - set(data)
        extra = set(data) - allowed
        if missing:
            raise DatasetError(f"第 {line_number} 行缺少字段：{sorted(missing)}")
        if extra:
            raise DatasetError(f"第 {line_number} 行存在未知字段：{sorted(extra)}")

        sample_id = data["id"]
        question = data["question"]
        reference = data["reference"]
        evidence = data["evidence"]
        source = data["source"]
        category = data["category"]
        answerable = data["answerable"]
        expected_source = data.get("expected_source", "")
        expected_sources = data.get("expected_sources", [])
        history = data.get("history", [])

        if not isinstance(sample_id, str) or not sample_id.strip():
            raise DatasetError(f"第 {line_number} 行 id 非法")
        if sample_id in seen_ids:
            raise DatasetError(f"第 {line_number} 行 id 重复：{sample_id}")
        if not isinstance(question, str) or not question.strip():
            raise DatasetError(f"第 {line_number} 行 question 非法")
        if question in seen_questions:
            raise DatasetError(f"第 {line_number} 行 question 重复：{question}")
        if category not in CATEGORIES:
            raise DatasetError(f"第 {line_number} 行 category 非法：{category}")
        if not isinstance(answerable, bool):
            raise DatasetError(f"第 {line_number} 行 answerable 必须是布尔值")
        if not isinstance(reference, str):
            raise DatasetError(f"第 {line_number} 行 reference 必须是字符串")
        if not isinstance(source, str):
            raise DatasetError(f"第 {line_number} 行 source 必须是字符串")
        if not isinstance(expected_source, str):
            raise DatasetError(f"第 {line_number} 行 expected_source 必须是字符串")
        if not isinstance(expected_sources, list):
            raise DatasetError(f"第 {line_number} 行 expected_sources 必须是字符串数组")
        for item in expected_sources:
            if not isinstance(item, str) or not item.strip():
                raise DatasetError(
                    f"第 {line_number} 行 expected_sources 的每一项都必须是非空字符串"
                )
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise DatasetError(f"第 {line_number} 行 evidence 必须是非空字符串数组")

        if not isinstance(history, list):
            raise DatasetError(f"第 {line_number} 行 history 必须是数组")
        if len(history) % 2 != 0:
            raise DatasetError(
                f"第 {line_number} 行 history 必须是 user/assistant 成对的轮次，当前 {len(history)} 项"
            )
        turns: list[tuple[str, str]] = []
        for position, turn in enumerate(history):
            if (
                not isinstance(turn, list)
                or len(turn) != 2
                or not all(isinstance(item, str) for item in turn)
            ):
                raise DatasetError(f"第 {line_number} 行 history[{position}] 必须是 [role, content] 二元组")
            role, content = turn
            expected_role = "user" if position % 2 == 0 else "assistant"
            if role != expected_role:
                raise DatasetError(
                    f"第 {line_number} 行 history[{position}] 的 role 必须是 {expected_role}，实际 {role!r}"
                )
            if not content.strip():
                raise DatasetError(f"第 {line_number} 行 history[{position}] 的 content 不能为空")
            turns.append((role, content.strip()))

        if answerable:
            if category == "refusal":
                raise DatasetError(f"第 {line_number} 行可回答样本不能属于 refusal 类别")
            if not reference.strip():
                raise DatasetError(f"第 {line_number} 行可回答样本缺少 reference")
            if not evidence:
                raise DatasetError(f"第 {line_number} 行可回答样本缺少 evidence")
            if not source.strip():
                raise DatasetError(f"第 {line_number} 行可回答样本缺少 source")
            source_text = source_cache.get(source)
            if source_text is None:
                source_text = normalize_text(_resolve_source(source).read_text(encoding="utf-8"))
                source_cache[source] = source_text
            for phrase in evidence:
                needle = normalize_text(phrase)
                if len(needle) < 4:
                    raise DatasetError(
                        f"第 {line_number} 行 evidence 太短，无法可靠定位：{phrase!r}"
                    )
                if needle not in source_text:
                    raise DatasetError(
                        f"第 {line_number} 行 evidence 未出现在 {source}：{phrase!r}"
                    )
            # 历史里出现 evidence 原文，等于把答案直接喂进提示词，指标就不干净了
            history_blob = normalize_text("".join(content for _, content in turns))
            for phrase in evidence:
                leaked = normalize_text(phrase)
                if leaked and leaked in history_blob:
                    raise DatasetError(
                        f"第 {line_number} 行 history 含 evidence 原文，等于把答案喂进提示词：{phrase!r}"
                    )
        else:
            if category != "refusal":
                raise DatasetError(f"第 {line_number} 行拒答样本的 category 必须是 refusal")
            if reference or evidence or source or expected_source or expected_sources:
                raise DatasetError(
                    f"第 {line_number} 行拒答样本的 reference/evidence/source/"
                    "expected_source/expected_sources 必须为空"
                )

        seen_ids.add(sample_id)
        seen_questions.add(question)
        samples.append(
            EvalSample(
                id=sample_id,
                question=question,
                reference=reference,
                evidence=tuple(evidence),
                source=source,
                category=category,
                answerable=answerable,
                expected_source=expected_source.strip(),
                history=tuple(turns),
                # 去重保序：同一侧写两遍不该算两侧
                expected_sources=tuple(
                    dict.fromkeys(item.strip() for item in expected_sources)
                ),
            )
        )

    if not samples:
        raise DatasetError("评估集为空")
    return samples


def validate_distribution(samples: Sequence[EvalSample]) -> list[str]:
    """检查固定评估集的规模与类别分布；返回问题列表，空列表表示通过。"""
    errors: list[str] = []
    if len(samples) != EXPECTED_TOTAL:
        errors.append(f"样本总数应为 {EXPECTED_TOTAL}，实际 {len(samples)}")
    answerable = sum(1 for sample in samples if sample.answerable)
    refusal = len(samples) - answerable
    if answerable != EXPECTED_ANSWERABLE:
        errors.append(f"可回答样本应为 {EXPECTED_ANSWERABLE}，实际 {answerable}")
    if refusal != EXPECTED_REFUSAL:
        errors.append(f"应拒答样本应为 {EXPECTED_REFUSAL}，实际 {refusal}")
    for category, expected in EXPECTED_CATEGORIES.items():
        actual = sum(1 for sample in samples if sample.category == category)
        if actual != expected:
            errors.append(f"类别 {category} 应为 {expected}，实际 {actual}")
    return errors


def dataset_fingerprint(path: Path | str = DEFAULT_DATASET) -> str:
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 确定性指标
# --------------------------------------------------------------------------- #


def is_refusal(answer: str) -> bool:
    return REFUSAL in (answer or "")


def _source_matches(source_uri: str, expected_source: str) -> bool:
    """引用是否指向期望来源。

    `#` 之后是行/查询片段：SQL 入库文档是 `label#主键`，运行时查询引用是
    `label#sql_query`，两者的来源身份都是 `label`，比较前先去掉片段。
    """
    uri = str(source_uri).replace("\\", "/").split("#", 1)[0]
    expected = expected_source.replace("\\", "/").split("#", 1)[0]
    return bool(expected) and (uri == expected or uri.endswith("/" + expected))


def score_deterministic(
    sample: EvalSample,
    contexts: Sequence[str],
    source_uris: Sequence[str],
    citations: Sequence[dict],
    answer: str,
) -> dict[str, Optional[float]]:
    """计算单条样本的确定性指标；拒答样本只保留不适用于检索的 None。"""
    if not sample.answerable:
        return {
            "evidence_hit@3": None,
            "evidence_recall@3": None,
            "context_precision@3": None,
            "mrr@3": None,
            "source_hit": None,
            "citation_rate": None,
            "citation_validity": None,
            "both_sides_hit": None,
        }

    top_contexts = list(contexts[:3])
    normalized_contexts = [normalize_text(item) for item in top_contexts]
    normalized_evidence = [normalize_text(item) for item in sample.evidence]

    matched_contexts = [
        any(needle in context for needle in normalized_evidence)
        for context in normalized_contexts
    ]
    evidence_hit = 1.0 if any(matched_contexts) else 0.0
    evidence_recall = (
        sum(
            1.0
            for needle in normalized_evidence
            if any(needle in context for context in normalized_contexts)
        )
        / len(normalized_evidence)
    )
    context_precision = (
        sum(1.0 for matched in matched_contexts if matched) / len(top_contexts)
        if top_contexts
        else 0.0
    )
    mrr = 0.0
    for rank, matched in enumerate(matched_contexts, start=1):
        if matched:
            mrr = 1.0 / rank
            break
    expected_source = sample.expected_source or sample.source
    source_hit = (
        1.0
        if any(_source_matches(uri, expected_source) for uri in source_uris[:3])
        else 0.0
    )
    # 双侧取证读数（v7.4）：声明的每一侧都要在全量引用里出现。
    # 刻意不套 @3 窗口——source_hit 只看前 3 条引用，混合问题常把 SQL 侧排到第 4 条
    # （v7.3 报告 m04 即如此），那属于已知窗口口径限制，不该让新读数一上来就带上。
    sides = sample.expected_sources
    if len(sides) >= 2:
        both_sides_hit = (
            1.0
            if all(
                any(_source_matches(uri, side) for uri in source_uris)
                for side in sides
            )
            else 0.0
        )
    else:
        # 未声明双侧（或只有一条，视为单侧期望）：记 None，不进 both_sides_hit 的均值
        both_sides_hit = None

    citation_numbers_list = citation_numbers(
        answer, citation_count=len(citations)
    )
    citation_rate = 1.0 if citation_numbers_list else 0.0
    citation_validity = (
        1.0
        if citation_numbers_list
        and all(1 <= number <= len(citations) for number in citation_numbers_list)
        else 0.0
    )

    return {
        "evidence_hit@3": evidence_hit,
        "evidence_recall@3": evidence_recall,
        "context_precision@3": context_precision,
        "mrr@3": mrr,
        "source_hit": source_hit,
        "citation_rate": citation_rate,
        "citation_validity": citation_validity,
        "both_sides_hit": both_sides_hit,
    }


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    real = [float(value) for value in values if value is not None]
    return sum(real) / len(real) if real else None


def aggregate_results(results: Sequence[SampleResult]) -> dict[str, Any]:
    answerable = [item for item in results if item.answerable]
    refusal = [item for item in results if not item.answerable]
    return {
        "evidence_hit@3": _mean([item.metrics.get("evidence_hit@3") for item in answerable]),
        "evidence_recall@3": _mean(
            [item.metrics.get("evidence_recall@3") for item in answerable]
        ),
        "context_precision@3": _mean(
            [item.metrics.get("context_precision@3") for item in answerable]
        ),
        "mrr@3": _mean([item.metrics.get("mrr@3") for item in answerable]),
        "source_hit": _mean([item.metrics.get("source_hit") for item in answerable]),
        # 只有声明双侧的样本参与均值（其余条目该键为 None，_mean 自动跳过）
        "both_sides_hit": _mean(
            [item.metrics.get("both_sides_hit") for item in answerable]
        ),
        "answer_rate": _mean([0.0 if item.refusal else 1.0 for item in answerable]),
        "refusal_accuracy": _mean([1.0 if item.refusal else 0.0 for item in refusal]),
        "citation_rate": _mean([item.metrics.get("citation_rate") for item in answerable]),
        "citation_validity": _mean(
            [item.metrics.get("citation_validity") for item in answerable]
        ),
        "avg_latency_ms": _mean(
            [item.retrieval_ms + item.generation_ms for item in results]
        ),
        "avg_retrieval_ms": _mean([item.retrieval_ms for item in results]),
        "avg_generation_ms": _mean([item.generation_ms for item in results]),
        "generation_cache_hits": sum(1 for item in results if item.generation_cached),
        "avg_retrieved": _mean([float(item.retrieved) for item in results]),
        "avg_context_chars": _mean([float(item.context_chars) for item in results]),
        "avg_query_count": _mean([float(item.query_count) for item in results]),
        "avg_tool_calls": _mean(
            [float(item.tool_calls) for item in results if item.tool_calls is not None]
        ),
        "avg_tool_steps": _mean(
            [float(item.tool_steps) for item in results if item.tool_steps is not None]
        ),
        "used_parent_rate": _mean(
            [1.0 if item.used_parent else 0.0 for item in results]
        ),
        "rewrite_fallbacks": sum(1 for item in results if item.rewrite_fallback),
        "n_answerable": len(answerable),
        "n_refusal": len(refusal),
        "n_errors": sum(1 for item in results if item.error),
    }


# --------------------------------------------------------------------------- #
# 结果收集
# --------------------------------------------------------------------------- #


Progress = Callable[[str], None]


def _citation_to_dict(citation: Any) -> dict:
    if isinstance(citation, dict):
        return dict(citation)
    return asdict(citation)


def collect_results(
    engine: Any,
    samples: Sequence[EvalSample],
    configs: Sequence[ConfigSpec],
    *,
    model: str = LLM_MODEL,
    answer_cache: Optional[dict] = None,
    progress: Optional[Progress] = None,
) -> list[SampleResult]:
    """对每个样本/配置执行检索与生成，并计算确定性指标。

    直接调用 engine.retrieve + generate_answer，避免 RagEngine.answer 重复检索/精排。
    相同问题命中相同 chunk 集合时复用生成答案，减少本地 LLM 调用。
    """
    cache: dict = {} if answer_cache is None else answer_cache
    results: list[SampleResult] = []
    total = len(samples) * len(configs)
    index = 0

    for sample in samples:
        for spec in configs:
            index += 1
            result = SampleResult(
                sample_id=sample.id,
                config=spec.name,
                category=sample.category,
                answerable=sample.answerable,
                question=sample.question,
                reference=sample.reference,
                evidence=list(sample.evidence),
                expected_source=sample.source,
                expected_sources=list(sample.expected_sources),
                requested_rerank=spec.use_rerank,
            )

            retrieval_started = time.perf_counter()
            try:
                options = RetrievalOptions(
                    top_k=spec.top_k,
                    use_lexical=spec.use_lexical,
                    use_rerank=spec.use_rerank,
                    use_parent=spec.use_parent,
                    query_rewrite=spec.query_rewrite,
                )
                if hasattr(engine, "retrieve_with_trace"):
                    trace = engine.retrieve_with_trace(sample.question, options)
                    chunks = trace.chunks
                    used_lexical = trace.used_lexical
                    used_rerank = trace.used_rerank
                    result.used_parent = bool(trace.used_parent)
                    result.query_rewrite = trace.query_rewrite
                    result.query_count = int(trace.query_count)
                    result.context_chars = int(trace.context_chars)
                    result.rewrite_fallback = bool(trace.rewrite_fallback)
                    result.rewrite_reason = str(
                        getattr(trace, "rewrite_reason", "") or ""
                    )
                else:
                    # 兼容只实现 retrieve() 的测试替身
                    chunks, used_lexical, used_rerank = engine.retrieve(
                        sample.question, options
                    )
                    result.used_parent = spec.use_parent and any(
                        chunk.parent_id for chunk in chunks
                    )
                    result.query_rewrite = spec.query_rewrite
                    result.query_count = 1
                    result.context_chars = sum(len(chunk.text) for chunk in chunks)
                result.retrieval_ms = (time.perf_counter() - retrieval_started) * 1000.0
                result.used_lexical = bool(used_lexical)
                result.used_rerank = bool(used_rerank)
                result.retrieved = len(chunks)
                result.contexts = [chunk.text for chunk in chunks]
                result.source_uris = [
                    str((chunk.metadata or {}).get("source_uri", "")) for chunk in chunks
                ]

                context_digest = hashlib.sha256(
                    "\n\n".join(chunk.text for chunk in chunks).encode("utf-8")
                ).hexdigest()[:16]
                cache_key = (
                    spec.name,
                    sample.question,
                    tuple(chunk.point_id for chunk in chunks),
                    context_digest,
                    model,
                )
                if cache_key in cache:
                    answer, citations = cache[cache_key]
                    result.generation_cached = True
                else:
                    generation_started = time.perf_counter()
                    answer, generated_citations = generate_answer(
                        sample.question, chunks, model=model
                    )
                    result.generation_ms = (time.perf_counter() - generation_started) * 1000.0
                    citations = [_citation_to_dict(item) for item in generated_citations]
                    cache[cache_key] = (answer, citations)

                result.answer = str(answer or "")
                result.citations = citations
                result.refusal = is_refusal(result.answer)
                result.metrics = score_deterministic(
                    sample,
                    result.contexts,
                    result.source_uris,
                    result.citations,
                    result.answer,
                )
            except Exception as exc:  # 单条失败不阻断整体评估
                result.error = f"{type(exc).__name__}: {exc}"
                result.retrieval_ms = (time.perf_counter() - retrieval_started) * 1000.0
                result.metrics = score_deterministic(
                    sample, [], [], [], ""
                )

            results.append(result)
            if progress:
                status = "ERROR" if result.error else f"{result.retrieval_ms:.0f}ms"
                progress(
                    f"[{spec.name}] {index}/{total} {sample.id} {status}"
                    + (" (缓存)" if result.generation_cached else "")
                )
    return results


def collect_agent_results(
    agent: Any,
    samples: Sequence[EvalSample],
    spec: ConfigSpec,
    *,
    max_steps: int = AGENT_MAX_STEPS,
    allow_sql: bool = True,
    progress: Optional[Progress] = None,
) -> list[SampleResult]:
    """对每个样本跑一次 agentic 链路（意图路由 + 工具循环），复用同一套确定性指标。

    与 `collect_results` 分开，是因为链路口径不同：这里的“检索”是模型自主决定的
    工具调用，`contexts` 是统一编号后的证据块文本，引用来自各工具而不是一次检索。
    耗时口径：`retrieval_ms` 记工具执行耗时之和（没有工具步时记整条链路），
    `generation_ms` 记同一请求里其余开销（意图路由与最终作答）之和；
    两者相加等于整条链路的墙钟耗时。
    """
    # 延迟导入：评估模块本身不应依赖 agent（单测只测纯函数）
    from private_rag.agent.runtime import AgentOptions

    results: list[SampleResult] = []
    total = len(samples)
    for index, sample in enumerate(samples, start=1):
        result = SampleResult(
            sample_id=sample.id,
            config=spec.name,
            category=sample.category,
            answerable=sample.answerable,
            question=sample.question,
            reference=sample.reference,
            evidence=list(sample.evidence),
            expected_source=sample.expected_source or sample.source,
            expected_sources=list(sample.expected_sources),
            requested_rerank=spec.use_rerank,
            used_lexical=spec.use_lexical,
            used_rerank=spec.use_rerank,
            used_parent=spec.use_parent,
            query_rewrite=spec.query_rewrite,
            history_turns=len(sample.history),
        )
        started = time.perf_counter()
        try:
            options = AgentOptions(
                max_steps=max_steps,
                allow_sql=allow_sql,
                top_k=spec.top_k,
                use_lexical=spec.use_lexical,
                use_rerank=spec.use_rerank,
                use_parent=spec.use_parent,
                query_rewrite=spec.query_rewrite,
            )
            # 历史原样透传：评估要测的就是「带历史时指代能不能还原」，单轮样本传空列表
            agent_result = agent.run(
                sample.question, options=options, history=list(sample.history)
            )
            steps = list(getattr(agent_result, "steps", []) or [])
            elapsed_ms = float(getattr(agent_result, "elapsed_ms", 0) or 0)
            tool_ms = sum(
                float(getattr(step, "elapsed_ms", 0) or 0)
                for step in steps
                if getattr(step, "kind", "") == "tool"
            )
            kb_calls = sum(
                1
                for step in steps
                if getattr(step, "kind", "") == "tool"
                and getattr(step, "tool", "") == "kb_search"
            )
            citations = [
                _citation_to_dict(item)
                for item in (getattr(agent_result, "citations", None) or [])
            ]
            result.answer = str(getattr(agent_result, "answer", "") or "")
            result.contexts = [
                str(text) for text in (getattr(agent_result, "contexts", None) or [])
            ]
            result.citations = citations
            result.source_uris = [
                str(item.get("source_uri") or "") for item in citations
            ]
            result.retrieved = int(getattr(agent_result, "evidence_blocks", 0) or 0)
            result.context_chars = int(getattr(agent_result, "context_chars", 0) or 0)
            result.query_count = max(1, kb_calls)
            result.retrieval_ms = tool_ms if tool_ms else elapsed_ms
            result.generation_ms = max(0.0, elapsed_ms - result.retrieval_ms)
            result.intent = str(getattr(agent_result, "intent", "") or "")
            result.stop_reason = str(getattr(agent_result, "stop_reason", "") or "")
            result.tool_calls = int(getattr(agent_result, "tool_calls", 0) or 0)
            result.tool_steps = len(steps)
            result.trace_id = str(getattr(agent_result, "trace_id", "") or "")
            result.refusal = is_refusal(result.answer)
            result.metrics = score_deterministic(
                sample,
                result.contexts,
                result.source_uris,
                result.citations,
                result.answer,
            )
        except Exception as exc:  # 单条失败不阻断整体评估
            result.error = f"{type(exc).__name__}: {exc}"
            result.retrieval_ms = (time.perf_counter() - started) * 1000.0
            result.metrics = score_deterministic(sample, [], [], [], "")

        results.append(result)
        if progress:
            status = (
                f"ERROR {result.error}"
                if result.error
                else f"intent={result.intent or '—'} tools={result.tool_calls}"
                + (" (拒答)" if result.refusal else "")
            )
            progress(f"[{spec.name}] {index}/{total} {sample.id} {status}")
    return results


# --------------------------------------------------------------------------- #
# Ragas
# --------------------------------------------------------------------------- #


def build_ragas_inputs(results: Sequence[SampleResult]) -> list[dict]:
    """按 Ragas SingleTurn 字段构造输入；只包含可回答且未报错的样本。"""
    rows: list[dict] = []
    for result in results:
        if not result.answerable or result.error:
            continue
        rows.append(
            {
                "user_input": result.question,
                "response": result.answer,
                "retrieved_contexts": list(result.contexts),
                "reference": result.reference,
            }
        )
    return rows


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def extract_ragas_scores(raw_scores: Sequence[dict]) -> list[dict[str, Optional[float]]]:
    """把 Ragas 原始指标键映射成报告使用的四个指标，缺失/NaN 统一为 None。"""
    mapped: list[dict[str, Optional[float]]] = []
    for row in raw_scores:
        item: dict[str, Optional[float]] = {}
        for display_name, candidates in RAGAS_SCORE_KEYS.items():
            value: Any = None
            for key in candidates:
                if key in row:
                    value = row[key]
                    break
            item[display_name] = _to_optional_float(value)
        mapped.append(item)
    return mapped


def summarize_ragas(results: Sequence[SampleResult]) -> Optional[dict[str, Any]]:
    evaluated = [item for item in results if item.ragas is not None]
    if not evaluated:
        return None
    metrics: dict[str, dict[str, Any]] = {}
    for name in RAGAS_DISPLAY_NAMES:
        values = [item.ragas.get(name) for item in evaluated if item.ragas is not None]
        valid = [value for value in values if value is not None]
        metrics[name] = {
            "value": sum(valid) / len(valid) if valid else None,
            "valid": len(valid),
            "coverage": len(valid) / len(evaluated) if evaluated else 0.0,
        }
    return {"n": len(evaluated), "metrics": metrics}


def run_ragas_for_config(
    results: Sequence[SampleResult],
    config_name: str,
    *,
    judge_model: str = LLM_MODEL,
    embed_model: str = EMB_MODEL,
    strictness: int = 3,
    max_workers: int = 1,
    cache_dir: Optional[Path | str] = None,
    limit: Optional[int] = None,
    progress: Optional[Progress] = None,
) -> int:
    """对一个配置的可回答样本运行 Ragas，并把分数写回 SampleResult.ragas。"""
    targets = [
        item
        for item in results
        if item.config == config_name and item.answerable and not item.error
    ]
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        return 0

    rows = build_ragas_inputs(targets)
    if not rows:
        return 0

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from langchain_ollama import ChatOllama, OllamaEmbeddings
        from ragas import DiskCacheBackend, EvaluationDataset, RunConfig, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            AnswerRelevancy,
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
        )

        run_config = RunConfig(
            # 本地单卡一次只处理一个请求；默认 1 个 worker，避免并发排队。
            timeout=600,
            max_retries=2,
            max_wait=30,
            max_workers=max_workers,
            seed=42,
        )
        cache = None
        if cache_dir is not None:
            cache_path = Path(cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)
            cache = DiskCacheBackend(str(cache_path))

        judge = ChatOllama(
            model=judge_model,
            temperature=0,
            format="json",
            num_ctx=8192,
            # 本地 MLX runner 若不限输出长度，会一直生成到上下文耗尽，
            # 表现为连接断开或 JSON 被截断；限制输出并关闭流式，保证判分稳定。
            num_predict=4096,
            disable_streaming=True,
            keep_alive="30m",
        )
        llm = LangchainLLMWrapper(
            judge,
            run_config=run_config,
            cache=cache,
            bypass_n=True,
        )
        embeddings = LangchainEmbeddingsWrapper(
            OllamaEmbeddings(model=embed_model),
            run_config=run_config,
            cache=cache,
        )
        metrics = [
            Faithfulness(),
            AnswerRelevancy(strictness=strictness),
            LLMContextPrecisionWithReference(),
            LLMContextRecall(),
        ]
        dataset = EvaluationDataset.from_list(rows)

        if progress:
            progress(
                f"🔄 Ragas {config_name}: {len(rows)} 条样本，"
                f"judge={judge_model}，workers={max_workers}"
            )
        evaluation = evaluate(
            dataset,
            metrics=metrics,
            llm=llm,
            embeddings=embeddings,
            run_config=run_config,
            raise_exceptions=False,
            show_progress=True,
        )
        raw_scores = list(getattr(evaluation, "scores", []) or [])
        if len(raw_scores) != len(targets):
            raise RuntimeError(
                f"Ragas 返回 {len(raw_scores)} 条分数，但输入为 {len(targets)} 条，"
                "无法按顺序对齐"
            )
        for target, scores in zip(targets, extract_ragas_scores(raw_scores)):
            target.ragas = scores
    return len(targets)


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


DETERMINISTIC_METRICS = (
    "evidence_hit@3",
    "evidence_recall@3",
    "context_precision@3",
    "mrr@3",
    "source_hit",
    "both_sides_hit",
    "answer_rate",
    "refusal_accuracy",
    "citation_rate",
    "citation_validity",
    "avg_latency_ms",
    "avg_retrieved",
    "avg_context_chars",
    "avg_query_count",
    "avg_tool_calls",
    "avg_tool_steps",
    "used_parent_rate",
    "rewrite_fallbacks",
)
DELTA_METRICS = (
    "evidence_hit@3",
    "evidence_recall@3",
    "context_precision@3",
    "mrr@3",
    "source_hit",
    "answer_rate",
    "refusal_accuracy",
    "citation_rate",
    "citation_validity",
    "avg_latency_ms",
    "avg_context_chars",
    "avg_query_count",
)


def _result_to_dict(result: SampleResult) -> dict:
    return {
        "id": result.sample_id,
        "config": result.config,
        "category": result.category,
        "answerable": result.answerable,
        "question": result.question,
        "reference": result.reference,
        "evidence": list(result.evidence),
        "expected_source": result.expected_source,
        "expected_sources": list(result.expected_sources),
        "answer": result.answer,
        "contexts": list(result.contexts),
        "citations": list(result.citations),
        "source_uris": list(result.source_uris),
        "used_lexical": result.used_lexical,
        "used_rerank": result.used_rerank,
        "used_parent": result.used_parent,
        "query_rewrite": result.query_rewrite,
        "query_count": result.query_count,
        "context_chars": result.context_chars,
        "rewrite_fallback": result.rewrite_fallback,
        "rewrite_reason": result.rewrite_reason,
        "requested_rerank": result.requested_rerank,
        "retrieved": result.retrieved,
        "refusal": result.refusal,
        "intent": result.intent,
        "stop_reason": result.stop_reason,
        "tool_calls": result.tool_calls,
        "tool_steps": result.tool_steps,
        "trace_id": result.trace_id,
        "history_turns": result.history_turns,
        "retrieval_ms": round(result.retrieval_ms, 2),
        "generation_ms": round(result.generation_ms, 2),
        "generation_cached": result.generation_cached,
        "error": result.error,
        "metrics": dict(result.metrics),
        "ragas": dict(result.ragas) if result.ragas is not None else None,
    }


def build_report(
    meta: dict,
    samples: Sequence[EvalSample],
    results: Sequence[SampleResult],
) -> dict:
    """构造可序列化报告；configs 顺序沿用 meta['configs']。"""
    config_names = list(meta.get("configs") or CONFIGS.keys())
    configs: dict[str, dict] = {}
    for name in config_names:
        config_results = [item for item in results if item.config == name]
        configs[name] = {
            "spec": asdict(CONFIGS[name]) if name in CONFIGS else {},
            "metrics": aggregate_results(config_results),
            "ragas": summarize_ragas(config_results),
            "n_results": len(config_results),
        }
    return {
        "meta": dict(meta),
        "samples_meta": [
            {
                "id": sample.id,
                "question": sample.question,
                "category": sample.category,
                "answerable": sample.answerable,
                "expected_source": sample.source,
                "expected_sources": list(sample.expected_sources),
                "history_turns": len(sample.history),
            }
            for sample in samples
        ],
        "configs": configs,
        "samples": [_result_to_dict(result) for result in results],
    }


def _fmt(value: Any, *, digits: int = 3) -> str:
    if value is None:
        return "NaN"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "NaN"
    if abs(number) >= 100:
        return f"{number:.1f}"
    return f"{number:.{digits}f}"


def _fmt_count(value: Any) -> str:
    """计数类指标按整数展示，避免“2 次回退”被写成 2.000。"""
    if value is None:
        return "NaN"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _cell(text: Any) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _category_table(report: dict) -> list[str]:
    config_names = list(report["configs"])
    # 只列本次样本里实际出现的类别，避免 v7 集在 v4 类别上刷出一排空行
    present = {item["category"] for item in report["samples"]}
    categories = [
        name for name in CATEGORIES if name != "refusal" and name in present
    ]
    lines = [
        "| 类别 | " + " | ".join(config_names) + " |",
        "|---|" + "|".join("---:" for _ in config_names) + "|",
    ]
    for category in categories:
        cells = []
        for name in config_names:
            records = [
                item
                for item in report["samples"]
                if item["category"] == category
                and item["config"] == name
                and item["answerable"]
            ]
            hit = _mean([item["metrics"].get("evidence_hit@3") for item in records])
            precision = _mean(
                [item["metrics"].get("context_precision@3") for item in records]
            )
            cells.append(f"{_fmt(hit)} / {_fmt(precision)}")
        lines.append(f"| {category} | " + " | ".join(cells) + " |")
    return lines


def _rewrite_fallback_lines(report: dict, limit: int = 20) -> list[str]:
    """列出改写失败回退的样本与原因，便于区分模型波动与实现问题。"""
    fallbacks = [
        item for item in report["samples"] if item.get("rewrite_fallback")
    ]
    if not fallbacks:
        return []
    lines = [
        "## 查询改写回退",
        "",
        "下表样本因改写失败退回原问题；检索与生成仍正常执行，`query_count` 为 1。",
        "",
        "| 样本 | 配置 | 原因 |",
        "|---|---|---|",
    ]
    for item in fallbacks[:limit]:
        reason = item.get("rewrite_reason") or "未记录原因"
        lines.append(
            f"| {item['id']} | `{item['config']}` | {_cell(reason)} |"
        )
    if len(fallbacks) > limit:
        lines.append(f"| … | 其余 {len(fallbacks) - limit} 条 | 省略 |")
    return lines


def _failure_rank(item: dict) -> tuple[int, str]:
    """失败案例排序：执行失败/应拒答却作答 > 误拒答 > 证据未命中。"""
    if item["error"]:
        rank = 0
    elif not item["answerable"] and not item["refusal"]:
        rank = 0
    elif item["refusal"]:
        rank = 1
    else:
        rank = 2
    return rank, item["id"]


def _failure_lines(report: dict, limit: int = 20) -> list[str]:
    failures = []
    config_order = {name: index for index, name in enumerate(report["configs"])}
    for item in report["samples"]:
        if item["answerable"]:
            failed = (
                bool(item["error"])
                or item["refusal"]
                or item["metrics"].get("evidence_hit@3") == 0.0
                # 双侧样本只引到一侧：evidence_hit@3 可能仍是满分，不单独判失败就看不见
                or item["metrics"].get("both_sides_hit") == 0.0
            )
        else:
            # 应拒答样本：未输出拒答文案也算失败，避免只展示可回答样本的问题。
            failed = bool(item["error"]) or not item["refusal"]
        if failed:
            failures.append(item)

    # 每个配置先保留最多 6 条，避免失败较多的 dense 把 full 基线案例挤掉；
    # 剩余名额再优先分给失败总数较少的配置。
    groups: dict[str, list[dict]] = {}
    for name in config_order:
        groups[name] = sorted(
            (item for item in failures if item["config"] == name),
            key=_failure_rank,
        )
    cap = max(1, limit // max(1, len(config_order)))
    selected: list[dict] = []
    counts: dict[str, int] = {}
    for name in config_order:
        chosen = groups[name][:cap]
        selected.extend(chosen)
        counts[name] = len(chosen)

    remaining = limit - len(selected)
    leftovers = []
    for name in config_order:
        leftovers.extend(groups[name][cap:])
    leftovers.sort(
        key=lambda item: (
            len(groups[item["config"]]) - counts[item["config"]],
            config_order.get(item["config"], 99),
            _failure_rank(item),
        )
    )
    selected.extend(leftovers[: max(0, remaining)])
    selected.sort(key=lambda item: (config_order.get(item["config"], 99), _failure_rank(item)))

    lines = [
        "| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |",
        "|---|---|---|---|---|---|",
    ]
    for item in selected:
        if item["error"]:
            phenomenon = "执行失败"
        elif not item["answerable"]:
            phenomenon = "应拒答但未拒答"
        elif item["refusal"]:
            phenomenon = "拒答"
        elif item["metrics"].get("both_sides_hit") == 0.0:
            phenomenon = "只取到一侧证据"
        else:
            phenomenon = "未命中证据"
        answer = _cell(item["answer"])[:110] if item["answer"] else "—"
        evidence = _cell("；".join(item["evidence"])) or "—"
        lines.append(
            f"| {item['id']} | {item['config']} | {_cell(item['question'])} | "
            f"{phenomenon} | {evidence} | {answer} |"
        )
    if not selected:
        lines.append("| — | — | 本次没有失败案例 | — | — | — |")
    return lines


def _ragas_missing_lines(report: dict) -> list[str]:
    """列出没有有效值的 Ragas 指标，保证 NaN 可定位。"""
    missing = []
    for item in report["samples"]:
        ragas = item.get("ragas")
        if not ragas:
            continue
        absent = [metric for metric in RAGAS_DISPLAY_NAMES if ragas.get(metric) is None]
        if absent:
            missing.append((item, absent))
    if not missing:
        return []

    lines = [
        "## Ragas 缺失指标（NaN）",
        "",
        "| 样本 | 配置 | 缺失指标 |",
        "|---|---|---|",
    ]
    for item, absent in missing:
        lines.append(
            f"| {item['id']} | {item['config']} | "
            + ", ".join(f"`{metric}`" for metric in absent)
            + " |"
        )
    lines.append("")
    lines.append(
        "这些指标没有产生有效值，平均分只基于有效样本，覆盖率见上方汇总表；"
        "具体原因请结合本次运行日志中的 judge 输出解析失败提示。"
    )
    lines.append("")
    return lines



def render_markdown(report: dict) -> str:
    """把 build_report 的结构渲染成可读 Markdown。"""
    meta = report["meta"]
    config_names = list(report["configs"])
    lines: list[str] = []
    # 标题可由调用方覆盖：v7 agentic 对比报告不该顶着 v5 的标题
    lines.append(meta.get("report_title") or "# private-rag v5 检索策略评估报告")
    lines.append("")
    lines.append(
        "本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线"
        "（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` "
        "依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，"
        "NaN 表示 judge 失败或输出无法解析，未用 0 替代。"
    )
    lines.append("")

    agent_names = [name for name in config_names if name in CONFIGS and CONFIGS[name].is_agent]
    if agent_names:
        lines.append(
            "> **agentic 组口径不同**：" + "、".join(f"`{n}`" for n in agent_names) +
            " 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 "
            "`sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），"
            "但延迟与查询次数不能与固定链路当同义指标比较。"
        )
        lines.append("")
    lines.append("## 运行信息")
    lines.append("")
    lines.append(f"- 生成时间：{meta.get('generated_at', '—')}")
    dataset_total = meta.get("dataset_total", meta.get("samples_total", 0))
    selected_total = meta.get("samples_total", 0)
    if dataset_total and selected_total != dataset_total:
        lines.append(
            f"- 评估集：`{meta.get('dataset', '—')}`（固定 {dataset_total} 条；"
            f"本次运行子集 {selected_total} 条："
            f"{meta.get('samples_answerable', 0)} 可回答 + "
            f"{meta.get('samples_refusal', 0)} 应拒答）"
        )
        lines.append(
            f"- **本次只运行子集：{selected_total} / {dataset_total} 条，"
            "分数不能当作全量数据集结论。**"
        )
    else:
        lines.append(
            f"- 评估集：`{meta.get('dataset', '—')}`（{selected_total} 条："
            f"{meta.get('samples_answerable', 0)} 可回答 + "
            f"{meta.get('samples_refusal', 0)} 应拒答）"
        )
    lines.append(f"- 数据集 SHA256：`{meta.get('dataset_sha256', '—')}`")
    lines.append(f"- 生成模型：`{meta.get('llm_model', '—')}`")
    lines.append(f"- Ragas judge：`{meta.get('judge_model', '—')}`")
    lines.append(f"- Embedding：`{meta.get('embed_model', '—')}`")
    lines.append(
        f"- Ragas 配置：{meta.get('ragas', {}).get('configs', [])}，"
        f"指标 {meta.get('ragas', {}).get('metrics', [])}，"
        f"strictness={meta.get('ragas', {}).get('strictness', '—')}，"
        f"max_workers={meta.get('ragas', {}).get('max_workers', '—')}"
    )
    if meta.get("ragas", {}).get("limit") is not None:
        lines.append(
            f"- **Ragas 子集限制：每个配置只评估前 "
            f"{meta['ragas']['limit']} 条可回答样本，不能当作全量结论。**"
        )
    if meta.get("git_commit"):
        lines.append(f"- Git commit：`{meta['git_commit']}`")
    lines.append(f"- 总耗时：{_fmt(meta.get('duration_seconds'), digits=1)} 秒")
    lines.append("")

    lines.append("## 策略配置")
    lines.append("")
    lines.append(
        "| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |"
    )
    lines.append("|---|---|---|---|---|---|---|---:|")
    for name in config_names:
        spec = asdict(CONFIGS[name]) if name in CONFIGS else {}
        lines.append(
            f"| `{name}` | {'agentic' if spec.get('is_agent') else '固定'} | "
            f"{_cell(spec.get('label', '—'))} | "
            f"{'开' if spec.get('use_lexical') else '关'} | "
            f"{'开' if spec.get('use_rerank') else '关'} | "
            f"{'开' if spec.get('use_parent') else '关'} | "
            f"{spec.get('query_rewrite', '—')} | {spec.get('top_k', '—')} |"
        )
    lines.append("")

    lines.append("## 确定性指标汇总")
    lines.append("")
    lines.append("| 指标 | " + " | ".join(config_names) + " |")
    lines.append("|---|" + "|".join("---:" for _ in config_names) + "|")
    for metric in DETERMINISTIC_METRICS:
        cells = [
            _fmt_count(report["configs"][name]["metrics"].get(metric))
            if metric == "rewrite_fallbacks"
            else _fmt(report["configs"][name]["metrics"].get(metric))
            for name in config_names
        ]
        lines.append(f"| `{metric}` | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` "
        "只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；"
        "`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` "
        "是查询改写失败回退原问题的次数；"
        "生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。"
    )
    lines.append("")

    lines.append("## Ragas 指标汇总")
    lines.append("")
    header = ["指标"] + config_names
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|---|" + "|".join("---:" for _ in config_names) + "|")
    for metric in RAGAS_DISPLAY_NAMES:
        cells = []
        for name in config_names:
            ragas = report["configs"][name].get("ragas")
            if ragas is None:
                cells.append("未运行")
            else:
                info = ragas["metrics"][metric]
                cell = _fmt(info["value"])
                if info["value"] is not None:
                    cell += f" ({_fmt(info['coverage'], digits=2)}, {info['valid']}/{ragas['n']})"
                cells.append(cell)
        lines.append(f"| `{metric}` | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "括号内为有效样本覆盖率与有效数/该配置实际尝试数；"
        "配置显示“未运行”表示本次没有对它执行 Ragas。"
    )
    lines.append("")

    missing_lines = _ragas_missing_lines(report)
    if missing_lines:
        lines.extend(missing_lines)

    if "full" in report["configs"]:
        lines.append("## 相对 full 基线的差值")
        lines.append("")
        lines.append("| 指标 | " + " | ".join(config_names) + " |")
        lines.append("|---|" + "|".join("---:" for _ in config_names) + "|")
        baseline_metrics = report["configs"]["full"]["metrics"]
        baseline_ragas = report["configs"]["full"].get("ragas")
        for metric in DELTA_METRICS:
            cells = []
            baseline = baseline_metrics.get(metric)
            for name in config_names:
                value = report["configs"][name]["metrics"].get(metric)
                if name == "full":
                    cells.append("基线")
                elif value is None or baseline is None:
                    cells.append("NaN")
                else:
                    cells.append(f"{value - baseline:+.3f}")
            lines.append(f"| `{metric}` | " + " | ".join(cells) + " |")
        for metric in RAGAS_DISPLAY_NAMES:
            cells = []
            baseline = (
                baseline_ragas["metrics"][metric]["value"] if baseline_ragas else None
            )
            for name in config_names:
                ragas = report["configs"][name].get("ragas")
                value = ragas["metrics"][metric]["value"] if ragas else None
                if name == "full":
                    cells.append("基线")
                elif value is None or baseline is None:
                    cells.append("NaN")
                else:
                    cells.append(f"{value - baseline:+.3f}")
            lines.append(f"| `ragas.{metric}` | " + " | ".join(cells) + " |")
        lines.append("")
        lines.append(
            "差值按 `配置 - full` 计算；确定性指标为正表示优于基线，"
            "延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。"
        )
        lines.append("")

    lines.append("## 按类别：evidence_hit@3 / context_precision@3")
    lines.append("")
    lines.extend(_category_table(report))
    lines.append("")

    fallback_lines = _rewrite_fallback_lines(report)
    if fallback_lines:
        lines.extend(fallback_lines)
        lines.append("")

    lines.append("## 失败与关注案例（最多 20 条）")
    lines.append("")
    lines.extend(_failure_lines(report))
    lines.append("")

    if meta.get("ragas_errors"):
        lines.append("## Ragas 运行错误")
        lines.append("")
        for name, error in meta["ragas_errors"].items():
            lines.append(f"- `{name}`：{_cell(error)}")
        lines.append("")

    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 -m pip install -r requirements-eval.txt")
    lines.append("python3 scripts/prepare_eval_kb.py --recreate")
    configs = ",".join(meta.get("configs", []))
    ragas_configs = ",".join(meta.get("ragas", {}).get("configs", []))
    dataset = meta.get("dataset")
    if dataset:
        lines.append(f"python3 scripts/evaluate.py --dataset {dataset} \\")
        lines.append(f"  --configs {configs} \\")
    else:
        lines.append(f"python3 scripts/evaluate.py --configs {configs} \\")
    if agent_names:
        agent_meta = meta.get("agent") or {}
        lines.append(
            f"  --agent-sources {agent_meta.get('sources', 'config/sources.eval.agent.yaml')} \\"
        )
    if ragas_configs:
        lines.append(f"  --ragas-configs {ragas_configs} \\")
    else:
        lines.append("  --skip-ragas \\")
    lines.append(f"  --judge-model {meta.get('judge_model', '—')} \\")
    if meta.get("ragas", {}).get("max_workers") is not None:
        lines.append(f"  --max-workers {meta['ragas']['max_workers']} \\")
    if meta.get("ragas", {}).get("cache_dir"):
        lines.append(f"  --cache-dir {meta['ragas']['cache_dir']} \\")
    elif meta.get("ragas", {}).get("cache") is False:
        lines.append("  --no-cache \\")
    if meta.get("ragas", {}).get("limit") is not None:
        lines.append(f"  --ragas-limit {meta['ragas']['limit']} \\")
    dataset_total = meta.get("dataset_total")
    sample_ids = meta.get("sample_ids") or []
    if dataset_total and sample_ids and len(sample_ids) != dataset_total:
        lines.append(f"  --ids {','.join(sample_ids)} \\")
    output = meta.get("output_markdown", "eval/reports/v5_report.md")
    json_output = meta.get("output_json", "eval/reports/v5_report.json")
    lines.append(f"  --output {output} \\")
    lines.append(f"  --json-output {json_output}")
    lines.append("```")
    lines.append("")

    lines.append("## 限制")
    lines.append("")
    sample_total = meta.get("samples_total", 0)
    lines.append(
        f"- 评估语料仅为项目文档，本次样本数 {sample_total}；"
        "分数不代表其他领域或生产流量。"
    )
    llm_model = meta.get("llm_model", "—")
    judge_model = meta.get("judge_model", "—")
    if judge_model == llm_model:
        lines.append(
            f"- 生成与 judge 都是 `{llm_model}`，存在自评偏差；"
            "分数用于同模型下的相对比较，不能当作绝对质量结论。"
        )
    else:
        lines.append(
            f"- 生成模型为 `{llm_model}`，Ragas judge 为 `{judge_model}`；"
            "这不是同模型自评，但不同 judge 的分数不能直接混比，"
            "分数只能在该 judge 与当前语料下作相对比较。"
        )
    lines.append(
        "- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，"
        "已固定版本；升级 Ragas 需要迁移到 collections API。"
    )
    lines.append(
        "- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。"
    )
    lines.append(
        "- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，"
        "延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。"
    )
    lines.append(
        "- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；"
        "这是为了命中后无需二次查询 docstore，换来更完整的上下文。"
    )
    lines.append(
        "- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；"
        "v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。"
    )
    lines.append(
        "- `both_sides_hit` 只对声明了双侧取证来源（`expected_sources`）的样本有意义，"
        "其余条目恒为 `None` 不进均值；固定链路没挂运行时 SQL 源时它必然整列 NaN——"
        "双侧样本要跑得通，得用 agentic 组加 `--agent-sources`。"
    )
    lines.append(
        "- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），"
        "同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，"
        "因此重跑耗时不会归零。"
    )
    if agent_names:
        lines.extend(
            [
                "- 评估库只入库项目文档，SQL 工具只在运行时挂上"
                "（`config/sources.eval.agent.yaml`，不参与入库）；"
                "因此 `database` 类问题在固定链路上必然拒答——这是本次要量的差值本身，"
                "不是数据泄漏或配置疏漏。",
                "- agentic 组的 `evidence_hit` 取决于模型 SELECT 出了哪些列："
                "只查 `name`/`price` 而没取 `description` 时，答案可以完全正确但证据判 0。"
                "该指标在 agentic 组偏保守，应结合引用与回答文本一起看。",
                "- `evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只看前 3 条上下文，"
                "而 agentic 一次可产出 4–9 条证据块：SQL 证据常排在 `kb_search` 结果之后"
                "（本机实跑有样本的证据落在第 5 与第 7 块，回答引用正确但指标判 0），"
                "因此 `database` / `mixed` 类别的低分不能读成“证据没进上下文”。",
                "- 拒答判定认的是生成侧的 `【资料中没有相关信息】` 哨兵文本：agentic 组在证据不足时"
                "倾向给“带引用的否定回答”（例如查库后说明 `products` 表没有该字段），"
                "本口径下算未拒答，`refusal_accuracy` 低于固定链路属于行为差异，需结合答案文本判断。",
                "- agentic 组的延迟是整条循环的墙钟耗时（含意图路由与多次工具调用）："
                "`avg_retrieval_ms` 记工具执行耗时之和，`avg_generation_ms` 记其余开销，"
                "两者相加才是总耗时，不能拿固定链路的同名列来比快慢。",
                "- `avg_tool_calls` / `avg_tool_steps` 只是旁证："
                "`multi_step` 类样本没有单独判定模型是否真的分了两次查询"
                "（单条子查询 SQL 也能一次做对）。",
                "- 评估集支持多轮：样本可带 `history`（`[role, content]` 成对轮次），评估时原样传给 "
                "`agent.run` 的 history 参数，报告用 `history_turns` 标出哪几条走了多轮；"
                "不带该字段的样本传空列表，与单轮等价。加载时校验 role 必须 user/assistant 交替、"
                "content 非空，且 history 里不得出现 evidence 原文——那等于把答案喂进提示词。",
                "- `both_sides_hit` 是 v7.4 新增的双侧取证读数：样本可用 `expected_sources` "
                "声明「文档侧 + SQL 侧」，声明的每一侧都要在全量引用列表里出现才判 1；"
                "它扫全量引用、不套 `@3` 窗口，与 `source_hit` 不同口径，"
                "未声明双侧的样本不参与其均值。",
                "- 评估运行设置 `RAG_DISABLE_AGENT_TRACE=1`，不写 "
                "`storage/traces/agent.jsonl`，避免把评估样本混进演示轨迹；"
                "因此报告里的 `trace_id` 不能逐条回放。",
            ]
        )
    lines.append("")
    return "\n".join(lines)
