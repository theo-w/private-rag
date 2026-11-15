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
    DatasetError,
    EvalSample,
    SampleResult,
    aggregate_results,
    build_report,
    build_ragas_inputs,
    citation_numbers,
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
