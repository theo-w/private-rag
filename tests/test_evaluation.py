"""v4 评估测试：数据集、确定性指标、Ragas 映射与报告渲染（不依赖 Ollama/Ragas）。"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from private_rag import config
from private_rag import eval_env
from private_rag.evaluation import (
    CONFIGS,
    DETERMINISTIC_METRICS,
    DatasetError,
    EvalSample,
    SampleResult,
    _source_matches,
    aggregate_results,
    build_report,
    build_ragas_inputs,
    citation_numbers,
    collect_agent_results,
    collect_results,
    dataset_fingerprint,
    extract_ragas_scores,
    is_refusal,
    load_dataset,
    normalize_text,
    render_markdown,
    score_deterministic,
    summarize_ragas,
    validate_distribution,
)
from private_rag.generation import REFUSAL
from private_rag.models import Citation
from private_rag.qa import RetrievalTrace
from private_rag.retrieval.hybrid import RetrievedChunk


def test_default_dataset_is_valid_and_balanced():
    samples = load_dataset()
    assert len(samples) == 40
    assert sum(sample.answerable for sample in samples) == 36
    assert sum(not sample.answerable for sample in samples) == 4
    assert validate_distribution(samples) == []
    assert len({sample.id for sample in samples}) == 40
    assert len({sample.question for sample in samples}) == 40
    assert dataset_fingerprint() == dataset_fingerprint()
    assert all(len(normalize_text(item)) >= 4 for sample in samples for item in sample.evidence)


def test_v5_paraphrase_dataset_is_valid():
    path = Path(config.ROOT) / "eval" / "v5_paraphrase_qa.jsonl"
    samples = load_dataset(path)
    assert len(samples) == 18
    assert sum(sample.answerable for sample in samples) == 15
    assert sum(not sample.answerable for sample in samples) == 3
    assert len({sample.id for sample in samples}) == 18
    assert {sample.category for sample in samples} == {
        "architecture",
        "ingestion",
        "retrieval",
        "api",
        "boundary",
        "refusal",
    }
    # v5 数据集不适用 v4 的固定 40 条分布校验
    assert validate_distribution(samples)


def test_dataset_rejects_stale_evidence(tmp_path: Path):
    path = tmp_path / "qa.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "x01",
                "question": "测试问题",
                "reference": "参考答案",
                "evidence": ["这段文字不在 README 中"],
                "source": "README.md",
                "category": "architecture",
                "answerable": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="evidence 未出现在"):
        load_dataset(path)


def test_dataset_rejects_duplicate_ids_and_bad_refusal(tmp_path: Path):
    base = {
        "id": "x01",
        "question": "问题一",
        "reference": "",
        "evidence": [],
        "source": "",
        "category": "refusal",
        "answerable": False,
    }
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        json.dumps(base, ensure_ascii=False)
        + "\n"
        + json.dumps({**base, "question": "问题二"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="id 重复"):
        load_dataset(duplicate)

    bad_refusal = tmp_path / "bad_refusal.jsonl"
    bad_refusal.write_text(
        json.dumps({**base, "reference": "不应有答案"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="必须为空"):
        load_dataset(bad_refusal)


def test_score_deterministic_reward_and_citation():
    sample = EvalSample(
        id="t01",
        question="混合召回是什么？",
        reference="向量与 BM25 融合",
        evidence=("目标片段",),
        source="README.md",
        category="retrieval",
        answerable=True,
    )
    metrics = score_deterministic(
        sample,
        contexts=["无关内容", "这里是目标片段"],
        source_uris=["docs/ROADMAP.md", "README.md"],
        citations=[{"index": 1}, {"index": 2}],
        answer="答案 [2]",
    )
    assert metrics["evidence_hit@3"] == 1.0
    assert metrics["evidence_recall@3"] == 1.0
    assert metrics["context_precision@3"] == 0.5
    assert metrics["mrr@3"] == 0.5
    assert metrics["source_hit"] == 1.0
    assert metrics["citation_rate"] == 1.0
    assert metrics["citation_validity"] == 1.0

    invalid = score_deterministic(
        sample,
        contexts=[],
        source_uris=[],
        citations=[{"index": 1}],
        answer="答案 [3]",
    )
    assert invalid["evidence_hit@3"] == 0.0
    assert invalid["citation_validity"] == 0.0


def test_citation_numbers_ignore_code_and_format_notations():
    # 反引号/代码块中的 [n] 是在讲引用格式，不应算作引用。
    assert citation_numbers("先看 `[1][2][3]` 的示例。") == []
    assert citation_numbers("```text\n[4]\n```\n依据 [1]。") == [1]
    # 文档里的“带 [1][2][3] 编号”属于被引用的格式说明，不算越界引用。
    assert citation_numbers("系统拼成带 [1][2][3] 编号的提示词，依据 [1]。") == [1]
    # 没有“编号”字样的格式句同样按连续 1..N 规则剔除。
    assert citation_numbers(
        "模型只负责在答案里标 [1][2][3]。依据 [1]。", citation_count=1
    ) == [1]
    # citations 足够时，连续编号可以是真的多引用，应保留。
    assert citation_numbers("依据 [1][2][3]。", citation_count=3) == [1, 2, 3]
    # 真正的越界引用仍然保留，交给指标判为无效。
    assert citation_numbers("答案依据 [1]，另见 [3]。") == [1, 3]


def test_score_deterministic_citation_format_mention_is_valid():
    sample = EvalSample(
        id="t04",
        question="提示词如何编号？",
        reference="带编号的上下文",
        evidence=("上下文",),
        source="README.md",
        category="retrieval",
        answerable=True,
    )
    metrics = score_deterministic(
        sample,
        contexts=["上下文片段"],
        source_uris=["README.md"],
        citations=[{"index": 1}],
        answer="系统拼成带 [1][2][3] 编号的提示词，真实依据 [1]。",
    )
    assert metrics["citation_rate"] == 1.0
    assert metrics["citation_validity"] == 1.0

    unmarked_notation = score_deterministic(
        sample,
        contexts=["上下文片段"],
        source_uris=["README.md"],
        citations=[{"index": 1}],
        answer="模型只负责在答案里标 [1][2][3]。真实依据 [1]。",
    )
    assert unmarked_notation["citation_rate"] == 1.0
    assert unmarked_notation["citation_validity"] == 1.0

    notation_only = score_deterministic(
        sample,
        contexts=["上下文片段"],
        source_uris=["README.md"],
        citations=[{"index": 1}],
        answer="只介绍格式：`[1][2][3]`。",
    )
    assert notation_only["citation_rate"] == 0.0
    assert notation_only["citation_validity"] == 0.0


def test_score_deterministic_partial_evidence_and_refusal():
    sample = EvalSample(
        id="t02",
        question="两个证据",
        reference="两段",
        evidence=("证据甲", "证据乙"),
        source="README.md",
        category="retrieval",
        answerable=True,
    )
    metrics = score_deterministic(
        sample,
        contexts=["这里只有证据甲"],
        source_uris=["README.md"],
        citations=[{"index": 1}],
        answer="答案 [1]",
    )
    assert metrics["evidence_recall@3"] == 0.5
    assert metrics["evidence_hit@3"] == 1.0

    refusal_sample = EvalSample(
        id="t03",
        question="不存在的问题",
        reference="",
        evidence=(),
        source="",
        category="refusal",
        answerable=False,
    )
    refusal_metrics = score_deterministic(
        refusal_sample, [], [], [], REFUSAL
    )
    assert refusal_metrics["evidence_hit@3"] is None
    assert is_refusal(REFUSAL)
    assert not is_refusal("正常回答")


def test_configs_are_ablations():
    assert CONFIGS["dense"].use_lexical is False
    assert CONFIGS["dense"].use_rerank is False
    assert CONFIGS["hybrid"].use_lexical is True
    assert CONFIGS["hybrid"].use_rerank is False
    assert CONFIGS["full"].use_lexical is True
    assert CONFIGS["full"].use_rerank is True
    assert CONFIGS["full"].use_parent is False
    assert CONFIGS["full"].query_rewrite == "off"
    assert CONFIGS["parent"].use_parent is True
    assert CONFIGS["parent"].query_rewrite == "off"
    assert CONFIGS["parent_multi"].use_parent is True
    assert CONFIGS["parent_multi"].query_rewrite == "multi"
    assert CONFIGS["parent_hyde"].use_parent is True
    assert CONFIGS["parent_hyde"].query_rewrite == "hyde"
    assert all(spec.top_k == 3 for spec in CONFIGS.values())


def test_collect_results_uses_retrieve_and_caches_generation(monkeypatch):
    chunk = RetrievedChunk(
        point_id="p1",
        text="README 中说明：向量与 BM25 通过 RRF 融合。",
        metadata={"source_uri": "README.md", "title": "README"},
    )

    class FakeEngine:
        def __init__(self):
            self.calls = []

        def retrieve(self, question, options):
            self.calls.append((question, options))
            return [chunk], options.use_lexical, options.use_rerank

    calls = []

    def fake_generate(question, chunks, model=None):
        calls.append((question, [item.point_id for item in chunks], model))
        return "向量与 BM25 融合 [1]", [
            Citation(
                index=1,
                title="README",
                source_type="file",
                source_uri="README.md",
                snippet="融合",
            )
        ]

    monkeypatch.setattr("private_rag.evaluation.generate_answer", fake_generate)
    sample = EvalSample(
        id="t04",
        question="向量和 BM25 如何融合？",
        reference="通过 RRF 融合。",
        evidence=("向量与 BM25",),
        source="README.md",
        category="retrieval",
        answerable=True,
    )
    engine = FakeEngine()
    cache = {}
    results = collect_results(
        engine,
        [sample],
        [CONFIGS["dense"], CONFIGS["full"]],
        answer_cache=cache,
    )

    assert len(engine.calls) == 2
    # v5 缓存键包含配置名：dense 与 full 即使命中同一子块也不复用答案
    assert len(calls) == 2
    assert all(result.generation_cached is False for result in results)
    assert results[1].used_rerank is True
    assert results[1].retrieved == 1
    assert results[1].metrics["evidence_hit@3"] == 1.0

    # 同一配置再次运行且上下文完全一致时命中缓存
    repeated = collect_results(
        engine,
        [sample],
        [CONFIGS["dense"], CONFIGS["full"]],
        answer_cache=cache,
    )
    assert all(result.generation_cached is True for result in repeated)
    assert len(calls) == 2
    aggregate = aggregate_results(results)
    assert aggregate["answer_rate"] == 1.0
    assert aggregate["citation_validity"] == 1.0
    assert aggregate["generation_cache_hits"] == 0


def test_collect_results_uses_trace_and_parent_metadata(monkeypatch):
    chunk = RetrievedChunk(
        point_id="p1",
        text="父块完整文本：向量与 BM25 通过 RRF 融合。",
        metadata={"source_uri": "README.md", "title": "README"},
        parent_id="parent-1",
        parent_text="父块完整文本：向量与 BM25 通过 RRF 融合。",
        parent_index=0,
        snippet_text="向量与 BM25",
    )

    class TraceEngine:
        def __init__(self):
            self.calls = []

        def retrieve_with_trace(self, question, options):
            self.calls.append((question, options))
            return RetrievalTrace(
                chunks=[chunk],
                used_lexical=options.use_lexical,
                used_rerank=False,
                used_parent=options.use_parent,
                queries=[question, "改写查询"],
                query_rewrite=options.query_rewrite,
                query_count=2 if options.query_rewrite != "off" else 1,
                context_chars=len(chunk.text),
                rewrite_fallback=False,
            )

    monkeypatch.setattr(
        "private_rag.evaluation.generate_answer",
        lambda question, chunks, model=None: ("融合 [1]", []),
    )
    sample = EvalSample(
        id="t05",
        question="向量和 BM25 如何融合？",
        reference="通过 RRF 融合。",
        evidence=("向量与 BM25",),
        source="README.md",
        category="retrieval",
        answerable=True,
    )
    engine = TraceEngine()
    results = collect_results(engine, [sample], [CONFIGS["parent_multi"]])

    assert len(engine.calls) == 1
    options = engine.calls[0][1]
    assert options.use_parent is True
    assert options.query_rewrite == "multi"
    result = results[0]
    assert result.used_parent is True
    assert result.query_rewrite == "multi"
    assert result.query_count == 2
    assert result.context_chars == len(chunk.text)
    assert result.rewrite_fallback is False
    assert aggregate_results(results)["used_parent_rate"] == 1.0
    assert aggregate_results(results)["avg_query_count"] == 2.0


def test_build_ragas_inputs_only_answerable_without_error():
    rows = [
        SampleResult(
            sample_id="a",
            config="full",
            category="retrieval",
            answerable=True,
            question="问题 A",
            reference="答案 A",
            evidence=["e"],
            expected_source="README.md",
            answer="回答 A",
            contexts=["上下文 A"],
        ),
        SampleResult(
            sample_id="b",
            config="full",
            category="refusal",
            answerable=False,
            question="问题 B",
            reference="",
            evidence=[],
            expected_source="",
            answer=REFUSAL,
        ),
        SampleResult(
            sample_id="c",
            config="full",
            category="retrieval",
            answerable=True,
            question="问题 C",
            reference="答案 C",
            evidence=["e"],
            expected_source="README.md",
            error="RuntimeError: boom",
        ),
    ]
    inputs = build_ragas_inputs(rows)
    assert len(inputs) == 1
    assert inputs[0] == {
        "user_input": "问题 A",
        "response": "回答 A",
        "retrieved_contexts": ["上下文 A"],
        "reference": "答案 A",
    }


def test_extract_ragas_scores_maps_legacy_keys_and_nan():
    mapped = extract_ragas_scores(
        [
            {
                "faithfulness": 0.8,
                "answer_relevancy": math.nan,
                "llm_context_precision_with_reference": 0.5,
                "context_recall": 0.7,
            },
            {
                "faithfulness": 0.9,
                "response_relevancy": 0.6,
                "context_precision": 0.4,
                "llm_context_recall": 0.3,
            },
        ]
    )
    assert mapped[0]["faithfulness"] == 0.8
    assert mapped[0]["answer_relevancy"] is None
    assert mapped[0]["context_precision"] == 0.5
    assert mapped[0]["context_recall"] == 0.7
    assert mapped[1]["answer_relevancy"] == 0.6
    assert mapped[1]["context_precision"] == 0.4


def test_summarize_ragas_coverage():
    rows = [
        SampleResult(
            sample_id="a",
            config="full",
            category="retrieval",
            answerable=True,
            question="q",
            reference="r",
            evidence=["e"],
            expected_source="README.md",
            ragas={
                "faithfulness": 0.8,
                "answer_relevancy": None,
                "context_precision": 0.5,
                "context_recall": 0.4,
            },
        ),
        SampleResult(
            sample_id="b",
            config="full",
            category="retrieval",
            answerable=True,
            question="q2",
            reference="r2",
            evidence=["e"],
            expected_source="README.md",
            ragas={
                "faithfulness": None,
                "answer_relevancy": 0.7,
                "context_precision": None,
                "context_recall": 0.6,
            },
        ),
    ]
    summary = summarize_ragas(rows)
    assert summary["n"] == 2
    assert summary["metrics"]["faithfulness"] == {
        "value": 0.8,
        "valid": 1,
        "coverage": 0.5,
    }
    assert summary["metrics"]["answer_relevancy"]["value"] == 0.7


def test_report_markdown_and_json_are_complete():
    samples = [
        EvalSample(
            id="r01",
            question="问题",
            reference="答案",
            evidence=("证据",),
            source="README.md",
            category="retrieval",
            answerable=True,
        ),
        EvalSample(
            id="r02",
            question="拒答",
            reference="",
            evidence=(),
            source="",
            category="refusal",
            answerable=False,
        ),
    ]
    results = [
        SampleResult(
            sample_id="r01",
            config="dense",
            category="retrieval",
            answerable=True,
            question="问题",
            reference="答案",
            evidence=["证据"],
            expected_source="README.md",
            answer="回答 [1]",
            contexts=["证据"],
            source_uris=["README.md"],
            citations=[{"index": 1}],
            metrics={
                "evidence_hit@3": 1.0,
                "evidence_recall@3": 1.0,
                "context_precision@3": 1.0,
                "mrr@3": 1.0,
                "source_hit": 1.0,
                "citation_rate": 1.0,
                "citation_validity": 1.0,
            },
            ragas={
                "faithfulness": 0.8,
                "answer_relevancy": None,
                "context_precision": 0.6,
                "context_recall": 0.7,
            },
        ),
        SampleResult(
            sample_id="r01",
            config="full",
            category="retrieval",
            answerable=True,
            question="问题",
            reference="答案",
            evidence=["证据"],
            expected_source="README.md",
            answer=REFUSAL,
            contexts=[],
            source_uris=[],
            refusal=True,
            rewrite_fallback=True,
            rewrite_reason="模拟模型超时",
            metrics={
                "evidence_hit@3": 0.0,
                "evidence_recall@3": 0.0,
                "context_precision@3": 0.0,
                "mrr@3": 0.0,
                "source_hit": 0.0,
                "citation_rate": 0.0,
                "citation_validity": 0.0,
            },
        ),
        SampleResult(
            sample_id="r02",
            config="full",
            category="refusal",
            answerable=False,
            question="拒答",
            reference="",
            evidence=[],
            expected_source="",
            answer=REFUSAL,
            refusal=True,
        ),
    ]
    meta = {
        "generated_at": "2026-11-15T10:00:00+08:00",
        "dataset": "eval/project_rag_qa.jsonl",
        "dataset_sha256": "abc",
        "samples_total": 2,
        "samples_answerable": 1,
        "samples_refusal": 1,
        "configs": ["dense", "full"],
        "llm_model": "test-model",
        "judge_model": "test-judge",
        "embed_model": "test-embed",
        "ragas": {
            "enabled": True,
            "configs": ["dense"],
            "limit": None,
            "metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
            "strictness": 3,
            "max_workers": 2,
        },
        "ragas_errors": {},
        "duration_seconds": 1.5,
        "git_commit": "deadbeef",
    }
    report = build_report(meta, samples, results)
    payload = json.dumps(report, ensure_ascii=False)
    assert '"configs"' in payload

    markdown = render_markdown(report)
    assert "# private-rag v5 检索策略评估报告" in markdown
    assert "## 策略配置" in markdown
    assert "`full`" in markdown
    assert "父块上下文" in markdown
    assert "faithfulness" in markdown
    assert "NaN" in markdown
    assert "relative" not in markdown
    assert "相对 full 基线的差值" in markdown
    assert "avg_context_chars" in markdown
    assert "rewrite_fallbacks" in markdown
    # 回退次数是计数，不能显示成 1.000 这种比例格式。
    assert "| `rewrite_fallbacks` | 0 | 1 |" in markdown
    assert "查询改写回退" in markdown
    assert "模拟模型超时" in markdown
    assert "r01" in markdown


def test_eval_env_defaults_and_override():
    names = ("RAG_COLLECTION_NAME", "RAG_STORAGE_DIR", "RAG_LEXICAL_INDEX_PATH")
    original = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        eval_env.configure_eval_env()
        assert os.environ["RAG_COLLECTION_NAME"] == eval_env.EVAL_COLLECTION_NAME
        assert os.environ["RAG_STORAGE_DIR"] == str(eval_env.EVAL_STORAGE_DIR)
        assert os.environ["RAG_LEXICAL_INDEX_PATH"] == str(
            eval_env.EVAL_LEXICAL_INDEX_PATH
        )

        os.environ["RAG_COLLECTION_NAME"] = "custom_collection"
        eval_env.configure_eval_env()
        assert os.environ["RAG_COLLECTION_NAME"] == "custom_collection"
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_env_path_resolves_relative_and_absolute(monkeypatch):
    default = config.ROOT / "storage" / "default"
    monkeypatch.delenv("TEST_RAG_PATH", raising=False)
    assert config._env_path("TEST_RAG_PATH", default) == default

    monkeypatch.setenv("TEST_RAG_PATH", "storage/custom")
    assert config._env_path("TEST_RAG_PATH", default) == config.ROOT / "storage" / "custom"

    monkeypatch.setenv("TEST_RAG_PATH", "/tmp/rag-eval")
    assert config._env_path("TEST_RAG_PATH", default) == Path("/tmp/rag-eval")


# --------------------------------------------------------------------------- #
# v7 agentic 评估组
# --------------------------------------------------------------------------- #


def test_v7_agent_dataset_is_valid():
    path = Path(config.ROOT) / "eval" / "v7_agent_qa.jsonl"
    samples = load_dataset(path)
    assert len(samples) == 29
    assert sum(sample.answerable for sample in samples) == 25
    assert sum(not sample.answerable for sample in samples) == 4
    assert len({sample.id for sample in samples}) == 29
    assert {sample.category for sample in samples} == {
        "knowledge",
        "database",
        "mixed",
        "multi_step",
        "coreference",
        "refusal",
    }
    # v7 集不适用 v4 的固定 40 条分布校验
    assert validate_distribution(samples)
    # 只有需要运行时 SQL 的样本才声明 expected_source
    db_samples = [s for s in samples if s.category in ("database", "mixed", "multi_step")]
    assert len(db_samples) == 15
    assert all(s.expected_source == "demo.products" for s in db_samples)
    assert all(s.source == "scripts/init_demo_db.py" for s in db_samples)
    knowledge = [s for s in samples if s.category == "knowledge"]
    assert all(s.expected_source == "" for s in knowledge)
    # 指代消解样本必须真的带历史，否则「覆盖了多轮」只是文档上的说法
    coreference = [s for s in samples if s.category == "coreference"]
    assert len(coreference) == 3
    assert all(len(s.history) >= 2 for s in coreference)
    assert all(
        s.history[0][0] == "user" and s.history[1][0] == "assistant" for s in coreference
    )
    # 三条里只有一条的得分侧依赖运行时 SQL（c02），其余按文档来源判定
    assert sum(1 for s in coreference if s.expected_source == "demo.products") == 1
    # v7.4 双侧取证：恰好 m01–m05 + c03 六条声明「文档侧 + SQL 侧」
    two_sided = [s for s in samples if len(s.expected_sources) >= 2]
    assert [s.id for s in two_sided] == ["m01", "m02", "m03", "m04", "m05", "c03"]
    for s in two_sided:
        assert "demo.products" in s.expected_sources
        docs = [x for x in s.expected_sources if x != "demo.products"]
        # 文档侧必须落在真实存在、且真的会被评估库入库的文件上
        assert len(docs) == 1, (s.id, s.expected_sources)
        doc_path = Path(config.ROOT) / docs[0]
        assert doc_path.exists(), (s.id, docs[0])
        assert docs[0] == "README.md" or docs[0].startswith("docs/")
    # 其余样本不得声明双侧，否则 both_sides_hit 的分母会悄悄漂移
    assert sum(1 for s in samples if 0 < len(s.expected_sources) < 2) == 0
    assert all(not s.expected_sources for s in samples if s.category == "refusal")


def test_both_sides_hit_requires_every_declared_side():
    sample = EvalSample(
        id="m99",
        question="香蕉能放冰箱吗？引用列表从哪来？",
        reference="不适合；引用来自入库元数据。",
        evidence=("香蕉不适合放冰箱",),
        source="scripts/init_demo_db.py",
        category="mixed",
        answerable=True,
        expected_source="demo.products",
        expected_sources=("demo.products", "docs/beginner-guide.md"),
    )
    contexts = ["香蕉不适合放冰箱，低温会让表皮变黑、口感变差", "引用不是模型编的"]
    citations = [{"index": 1, "source_uri": "demo.products#sql_query"}]
    both = score_deterministic(
        sample,
        contexts,
        ["demo.products#sql_query", "docs/beginner-guide.md"],
        citations,
        "不适合[1]，引用来自元数据[1]",
    )
    assert both["both_sides_hit"] == 1.0

    # 复刻 v7.3 的 c03：只引到文档侧、SQL 那一半抄的历史 —— 必须判 0
    one_sided = score_deterministic(
        sample,
        contexts,
        ["docs/beginner-guide.md", "README.md"],
        citations,
        "引用来自元数据[1]",
    )
    assert one_sided["both_sides_hit"] == 0.0
    # 现有口径不动：source_hit 仍只看 expected_source 单字符串与前 3 条引用
    assert one_sided["source_hit"] == 0.0
    both["source_hit"]  # 双侧样本的 source_hit 口径保持 v7 原样

    # 声明的文档侧没被引用到，只引了 README.md / docs/ROADMAP.md —— 仍判 0，
    # 证明 README.md 与 docs/beginner-guide.md 之间不会互相误命中
    wrong_doc = score_deterministic(
        sample,
        contexts,
        ["README.md", "docs/ROADMAP.md", "demo.products#sql_query"],
        citations,
        "四种意图[1]",
    )
    assert wrong_doc["both_sides_hit"] == 0.0

    # 复刻 v7.3 的 m04：README.md 侧 + SQL 侧，SQL 引到第 4 条（@3 窗口之外）。
    # 新读数扫全量引用，因此仍判 1——这正是它与 source_hit 的口径差别
    m04_like = EvalSample(
        id="m98",
        question="苹果怎么保存？支持哪几种意图？",
        reference="冷藏；四种意图。",
        evidence=("苹果适合冷藏保存",),
        source="scripts/init_demo_db.py",
        category="mixed",
        answerable=True,
        expected_source="demo.products",
        expected_sources=("demo.products", "README.md"),
    )
    late_sql = score_deterministic(
        m04_like,
        ["苹果适合冷藏保存，可以延长保鲜时间"],
        ["README.md", "README.md", "docs/ROADMAP.md", "demo.products#sql_query"],
        citations,
        "冷藏[1]，四种意图[1]",
    )
    assert late_sql["both_sides_hit"] == 1.0
    assert late_sql["source_hit"] == 0.0  # source_hit 只看前 3 条引用，SQL 排在窗口外

    # 只声明一侧 / 未声明：不参与该读数
    single = EvalSample(
        id="k99",
        question="embedding 由谁负责？",
        reference="Ollama。",
        evidence=("Ollama 负责 embedding 与生成",),
        source="README.md",
        category="knowledge",
        answerable=True,
        expected_sources=("README.md",),
    )
    only_doc = score_deterministic(
        single, ["Ollama 负责 embedding 与生成"], ["README.md"], [{"index": 1}], "Ollama[1]"
    )
    assert only_doc["both_sides_hit"] is None
    assert single.expected_sources == ("README.md",)

    # 应拒答样本同样不参与（保持全 None，不进任何均值）
    refusal_sample = EvalSample(
        id="r99",
        question="有 iOS 客户端吗",
        reference="",
        evidence=(),
        source="",
        category="refusal",
        answerable=False,
    )
    refusal_metrics = score_deterministic(refusal_sample, [], [], [], "没有相关信息")
    assert refusal_metrics["both_sides_hit"] is None


def test_expected_sources_optional_and_validated(tmp_path: Path):
    needle = "Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储"
    base = {
        "id": "x11",
        "question": "embedding 由谁负责？",
        "reference": "Ollama 负责 embedding 与生成。",
        "evidence": [needle],
        "source": "README.md",
        "category": "knowledge",
        "answerable": True,
    }
    path = tmp_path / "qa.jsonl"
    path.write_text(json.dumps(base, ensure_ascii=False) + "\n", encoding="utf-8")
    assert load_dataset(path)[0].expected_sources == ()

    path.write_text(
        json.dumps(
            {**base, "expected_sources": ["README.md", "demo.products", "README.md"]},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    # 去重保序：同一侧写两遍不该算两侧
    assert load_dataset(path)[0].expected_sources == ("README.md", "demo.products")

    for bad in ("README.md", ["README.md", ""], ["README.md", 3]):
        path.write_text(
            json.dumps({**base, "expected_sources": bad}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(DatasetError):
            load_dataset(path)

    path.write_text(
        json.dumps(
            {
                **base,
                "id": "x12",
                "reference": "",
                "evidence": [],
                "source": "",
                "category": "refusal",
                "answerable": False,
                "expected_sources": ["README.md"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError):
        load_dataset(path)


def test_both_sides_hit_aggregates_and_renders():
    assert "both_sides_hit" in DETERMINISTIC_METRICS
    # 只有声明双侧的样本进均值：1.0 与 0.0 各一条 → 0.5，单侧样本不得稀释
    mixed_a = SampleResult(
        sample_id="m01",
        config="agent",
        category="mixed",
        answerable=True,
        question="q",
        reference="r",
        evidence=[],
        expected_source="demo.products",
        expected_sources=["demo.products", "docs/beginner-guide.md"],
        metrics={"evidence_hit@3": 1.0, "source_hit": 1.0, "both_sides_hit": 1.0},
    )
    mixed_b = SampleResult(
        sample_id="c03",
        config="agent",
        category="coreference",
        answerable=True,
        question="q",
        reference="r",
        evidence=[],
        expected_source="docs/beginner-guide.md",
        expected_sources=["docs/beginner-guide.md", "demo.products"],
        metrics={"evidence_hit@3": 1.0, "source_hit": 1.0, "both_sides_hit": 0.0},
    )
    single = SampleResult(
        sample_id="k01",
        config="agent",
        category="knowledge",
        answerable=True,
        question="q",
        reference="r",
        evidence=[],
        expected_source="README.md",
        metrics={"evidence_hit@3": 1.0, "source_hit": 1.0, "both_sides_hit": None},
    )
    aggregate = aggregate_results([mixed_a, mixed_b, single])
    assert aggregate["both_sides_hit"] == 0.5

    samples = [
        EvalSample(
            id=item.sample_id,
            question="q",
            reference="r",
            evidence=(),
            source="README.md",
            category=item.category,
            answerable=True,
            expected_sources=tuple(item.expected_sources),
        )
        for item in (mixed_a, mixed_b, single)
    ]
    report = build_report({"configs": ["agent"]}, samples, [mixed_a, mixed_b, single])
    markdown = render_markdown(report)
    assert "`both_sides_hit`" in markdown
    # c03 只取到一侧：evidence_hit@3 满分也必须进失败清单，且现象单独成类
    assert "只取到一侧证据" in markdown


def test_source_matches_ignores_fragment():
    # SQL 入库文档是 label#主键，运行时查询引用是 label#sql_query，来源身份都是 label
    assert _source_matches("demo.products#sql_query", "demo.products")
    assert _source_matches("demo.products#3", "demo.products")
    assert _source_matches("docs/beginner-guide.md", "beginner-guide.md")
    assert not _source_matches("README.md", "docs/beginner-guide.md")
    assert not _source_matches("README.md", "")


def test_expected_source_optional_and_validated(tmp_path: Path):
    needle = "Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储"
    base = {
        "id": "x01",
        "question": "embedding 由谁负责？",
        "reference": "Ollama 负责 embedding 与生成。",
        "evidence": [needle],
        "source": "README.md",
        "category": "knowledge",
        "answerable": True,
    }
    path = tmp_path / "qa.jsonl"
    path.write_text(json.dumps(base, ensure_ascii=False) + "\n", encoding="utf-8")
    plain = load_dataset(path)[0]
    # 省略该字段时保持空串：source_hit 才会退回用 source 判定
    assert plain.expected_source == ""

    path.write_text(
        json.dumps({**base, "expected_source": "demo.products"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert load_dataset(path)[0].expected_source == "demo.products"

    # 拒答样本所有事实字段都必须为空
    path.write_text(
        json.dumps(
            {
                "id": "x02",
                "question": "iOS 客户端有吗",
                "reference": "",
                "evidence": [],
                "source": "",
                "category": "refusal",
                "answerable": False,
                "expected_source": "demo.products",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError):
        load_dataset(path)


def test_source_hit_uses_expected_source():
    sample = EvalSample(
        id="d99",
        question="香蕉能放冰箱吗",
        reference="不适合放冰箱。",
        evidence=("香蕉不适合放冰箱",),
        source="scripts/init_demo_db.py",
        category="database",
        answerable=True,
        expected_source="demo.products",
    )
    contexts = ["香蕉不适合放冰箱，低温会让表皮变黑、口感变差；建议常温阴凉处保存。"]
    citations = [{"index": 1, "source_uri": "demo.products#sql_query"}]
    metrics = score_deterministic(
        sample, contexts, ["demo.products#sql_query"], citations, "香蕉不适合放冰箱[1]"
    )
    assert metrics["evidence_hit@3"] == 1.0
    # 若仍按 source（种子脚本）判定，SQL 引用会被误判为来源不对
    assert metrics["source_hit"] == 1.0


def test_agent_config_is_flagged():
    spec = CONFIGS["agent"]
    assert spec.is_agent is True
    assert spec.top_k == 3
    assert all(not other.is_agent for name, other in CONFIGS.items() if name != "agent")


def _agent_sample() -> EvalSample:
    return EvalSample(
        id="d01",
        question="香蕉能放冰箱吗",
        reference="不适合放冰箱。",
        evidence=("香蕉不适合放冰箱",),
        source="scripts/init_demo_db.py",
        category="database",
        answerable=True,
        expected_source="demo.products",
    )


def _fake_agent_result(**overrides):
    from types import SimpleNamespace

    data = {
        "answer": "香蕉不适合放冰箱[1]",
        "contexts": ["香蕉不适合放冰箱，低温会让表皮变黑"],
        "citations": [{"index": 1, "source_uri": "demo.products#sql_query", "title": "t"}],
        "intent": "database",
        "stop_reason": "final",
        "tool_calls": 1,
        "steps": [
            SimpleNamespace(kind="route", tool="sql_query", elapsed_ms=0),
            SimpleNamespace(kind="tool", tool="sql_query", elapsed_ms=12),
            SimpleNamespace(kind="final", tool="", elapsed_ms=0),
        ],
        "trace_id": "abc123",
        "evidence_blocks": 1,
        "context_chars": 21,
        "elapsed_ms": 50,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeAgent:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def run(self, question, *, options=None, history=None):
        self.calls.append({"question": question, "options": options, "history": history})
        if self.error:
            raise self.error
        return self.result


def test_collect_agent_results_records_metrics_and_options():
    agent = FakeAgent(_fake_agent_result())
    results = collect_agent_results(agent, [_agent_sample()], CONFIGS["agent"], max_steps=2)
    assert len(results) == 1
    result = results[0]

    assert result.error is None
    assert result.config == "agent"
    assert result.intent == "database"
    assert result.stop_reason == "final"
    assert result.tool_calls == 1
    assert result.tool_steps == 3
    assert result.trace_id == "abc123"
    assert result.retrieved == 1
    assert result.context_chars == 21
    assert result.source_uris == ["demo.products#sql_query"]
    assert result.refusal is False
    assert result.metrics["evidence_hit@3"] == 1.0
    assert result.metrics["source_hit"] == 1.0

    # 耗时口径：retrieval_ms 只记工具步耗时，其余归 generation_ms
    assert result.retrieval_ms == 12.0
    assert result.generation_ms == 38.0
    assert result.query_count == 1

    options = agent.calls[0]["options"]
    assert options.max_steps == 2
    assert options.top_k == 3
    assert options.allow_sql is True


def test_collect_agent_results_can_disable_sql():
    agent = FakeAgent(_fake_agent_result())
    collect_agent_results(
        agent, [_agent_sample()], CONFIGS["agent"], allow_sql=False
    )
    assert agent.calls[0]["options"].allow_sql is False


def test_collect_agent_results_keeps_going_after_failure():
    agent = FakeAgent(error=RuntimeError("决策模型不可用"))
    results = collect_agent_results(agent, [_agent_sample()], CONFIGS["agent"])
    result = results[0]
    assert result.error.startswith("RuntimeError:")
    assert result.answer == ""
    assert result.tool_calls is None
    assert result.metrics["evidence_hit@3"] == 0.0


def test_aggregate_and_report_render_agent_metrics():
    sample = _agent_sample()
    agent = FakeAgent(_fake_agent_result())
    results = collect_agent_results(agent, [sample], CONFIGS["agent"])
    summary = aggregate_results(results)
    assert summary["avg_tool_calls"] == 1.0
    assert summary["avg_tool_steps"] == 3.0

    # 固定链路没有工具概念，均值保持 None 而不是伪装成 0
    fixed = SampleResult(
        sample_id="k01",
        config="full",
        category="knowledge",
        answerable=True,
        question="q",
        reference="",
        evidence=[],
        expected_source="README.md",
    )
    assert aggregate_results([fixed])["avg_tool_calls"] is None

    report = build_report(
        {
            "configs": ["agent"],
            "agent": {"enabled": True, "sources": "config/sources.eval.agent.yaml"},
        },
        [sample],
        results,
    )
    markdown = render_markdown(report)
    assert "agentic 组口径不同" in markdown
    assert "| 配置 | 链路 |" in markdown
    assert "| `agent` | agentic |" in markdown
    assert "--agent-sources config/sources.eval.agent.yaml" in markdown
    assert "avg_tool_calls" in markdown


def test_history_field_defaults_empty_and_validated(tmp_path: Path):
    needle = "Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储"
    base = {
        "id": "c10",
        "question": "那一步为什么用原问题？",
        "reference": "避免检索跑偏。",
        "evidence": [needle],
        "source": "README.md",
        "category": "coreference",
        "answerable": True,
    }
    path = tmp_path / "qa.jsonl"

    # 省略 history 时保持空元组：报告里 history_turns 为 0，链路上等价于单轮
    path.write_text(json.dumps(base, ensure_ascii=False) + "\n", encoding="utf-8")
    assert load_dataset(path)[0].history == ()

    path.write_text(
        json.dumps(
            {**base, "history": [["user", "精排是干什么的？"], ["assistant", "逐张比较候选。"]]},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_dataset(path)[0].history == (
        ("user", "精排是干什么的？"),
        ("assistant", "逐张比较候选。"),
    )

    def expect_error(history, match):
        path.write_text(
            json.dumps({**base, "history": history}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(DatasetError, match=match):
            load_dataset(path)

    expect_error([["user", "只有一轮"]], "成对")
    expect_error([["assistant", "a"], ["user", "b"]], "role 必须是 user")
    expect_error([["user", "a"], ["assistant", "   "]], "content 不能为空")
    expect_error([["user", "a", "多余一项"], ["assistant", "b"]], r"\[role, content\] 二元组")
    expect_error("history 写成字符串", "必须是数组")
    # 历史里抄一份 evidence 原文，等于把答案喂进提示词
    expect_error([["user", "为什么？"], ["assistant", needle]], "等于把答案喂进提示词")


def test_collect_agent_results_forwards_history_to_agent():
    agent = FakeAgent(_fake_agent_result())
    multi = EvalSample(
        id="c02",
        question="那它的保存方式是啥？",
        reference="橙子表皮较厚。",
        evidence=("表皮较厚",),
        source="scripts/init_demo_db.py",
        category="coreference",
        answerable=True,
        expected_source="demo.products",
        history=(("user", "橙子现在多少钱？"), ("assistant", "橙子当前单价 12.5 元。")),
    )

    results = collect_agent_results(agent, [_agent_sample(), multi], CONFIGS["agent"])

    # 单轮样本也显式传空列表：链路上「没历史」与「传了空历史」是同一件事，但要保证参数真的被用到
    assert agent.calls[0]["history"] == []
    assert agent.calls[1]["history"] == [
        ("user", "橙子现在多少钱？"),
        ("assistant", "橙子当前单价 12.5 元。"),
    ]
    assert results[0].history_turns == 0
    assert results[1].history_turns == 2


def test_report_exposes_history_turns():
    sample = EvalSample(
        id="c01",
        question="那一步为什么用原问题？",
        reference="避免检索跑偏。",
        evidence=("Ollama 负责 embedding 与生成",),
        source="README.md",
        category="coreference",
        answerable=True,
        history=(("user", "精排挑几张？"), ("assistant", "3 张。")),
    )
    agent = FakeAgent(_fake_agent_result())
    results = collect_agent_results(agent, [sample], CONFIGS["agent"])
    report = build_report({"dataset": "x.jsonl", "configs": ["agent"]}, [sample], results)

    assert report["samples_meta"][0]["history_turns"] == 2
    assert report["samples"][0]["history_turns"] == 2


def test_collect_agent_results_records_gate_intercepts():
    agent = FakeAgent(_fake_agent_result(intent="mixed", gate_intercepts=2))
    results = collect_agent_results(agent, [_agent_sample()], CONFIGS["agent"])
    assert results[0].gate_intercepts == 2
    assert aggregate_results(results)["gate_intercept_rate"] == 1.0


def test_gate_intercept_rate_only_over_agentic_samples():
    # 固定链路样本 intent 为空，不进门控指标的分母
    fixed = SampleResult(
        sample_id="q01",
        config="dense",
        category="factual",
        answerable=True,
        question="q",
        reference="r",
        evidence=["e"],
        expected_source="README.md",
    )
    gated = SampleResult(
        sample_id="q02",
        config="agent",
        category="mixed",
        answerable=True,
        question="q",
        reference="r",
        evidence=["e"],
        expected_source="README.md",
        intent="mixed",
        tool_calls=2,
        gate_intercepts=1,
    )
    clean = SampleResult(
        sample_id="q03",
        config="agent",
        category="knowledge",
        answerable=True,
        question="q",
        reference="r",
        evidence=["e"],
        expected_source="README.md",
        intent="knowledge",
        tool_calls=1,
    )
    assert aggregate_results([fixed, gated, clean])["gate_intercept_rate"] == 0.5


def test_report_marks_dirty_commit_and_run_note():
    sample = EvalSample(
        id="q01",
        question="q",
        reference="r",
        evidence=("e",),
        source="README.md",
        category="factual",
        answerable=True,
    )
    result = SampleResult(
        sample_id="q01",
        config="dense",
        category="factual",
        answerable=True,
        question="q",
        reference="r",
        evidence=["e"],
        expected_source="README.md",
        answer="r[1]",
    )
    dirty = render_markdown(
        build_report(
            {
                "configs": ["dense"],
                "git_commit": "deadbeef",
                "git_dirty": True,
                "run_note": "修法 C 复跑 gate=ON",
            },
            [sample],
            [result],
        )
    )
    assert "deadbeef" in dirty
    assert "工作区有未提交改动" in dirty
    assert "跑批备注：修法 C 复跑 gate=ON" in dirty
    clean = render_markdown(
        build_report({"configs": ["dense"], "git_commit": "cafe0000"}, [sample], [result])
    )
    assert "工作区有未提交改动" not in clean
    assert "跑批备注" not in clean


def test_prompt_fingerprint_stable_hex():
    from private_rag.evaluation import _prompt_fingerprint

    first = _prompt_fingerprint()
    assert first == _prompt_fingerprint()
    assert len(first) == 16
    int(first, 16)
