# private-rag v5 检索策略评估报告

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

## 运行信息

- 生成时间：2026-11-15T21:49:13+08:00
- 评估集：`eval/project_rag_qa.jsonl`（40 条：36 可回答 + 4 应拒答）
- 数据集 SHA256：`5d1dff8187f96bef178295eaa7d2861af924cedcf469d073e525fdab48a7077e`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：['full', 'parent_multi']，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- **Ragas 子集限制：每个配置只评估前 12 条可回答样本，不能当作全量结论。**
- Git commit：`6dc22faa70e210cbe94efbcc63cd577ae89f025f`
- 总耗时：9859.4 秒

## 策略配置

| 配置 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---:|
| `full` | 混合召回 + 精排（v4 基线） | 开 | 开 | 关 | off | 3 |
| `parent` | 混合召回 + 精排 + 父块上下文 | 开 | 开 | 开 | off | 3 |
| `parent_multi` | 父块上下文 + 多查询改写 | 开 | 开 | 开 | multi | 3 |
| `parent_hyde` | 父块上下文 + HyDE | 开 | 开 | 开 | hyde | 3 |

## 确定性指标汇总

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `evidence_hit@3` | 0.861 | 0.889 | 0.889 | 0.917 |
| `evidence_recall@3` | 0.861 | 0.889 | 0.889 | 0.917 |
| `context_precision@3` | 0.315 | 0.366 | 0.366 | 0.380 |
| `mrr@3` | 0.727 | 0.759 | 0.759 | 0.773 |
| `source_hit` | 0.944 | 0.944 | 0.944 | 0.944 |
| `answer_rate` | 0.944 | 0.944 | 0.972 | 0.944 |
| `refusal_accuracy` | 1.000 | 1.000 | 1.000 | 1.000 |
| `citation_rate` | 1.000 | 1.000 | 1.000 | 1.000 |
| `citation_validity` | 1.000 | 1.000 | 1.000 | 1.000 |
| `avg_latency_ms` | 13530.4 | 21161.1 | 26086.7 | 26472.8 |
| `avg_retrieved` | 3.000 | 2.725 | 2.725 | 2.700 |
| `avg_context_chars` | 1694.4 | 4496.9 | 4458.0 | 4446.4 |
| `avg_query_count` | 1.000 | 1.000 | 2.950 | 1.975 |
| `used_parent_rate` | 0.000 | 1.000 | 1.000 | 1.000 |
| `rewrite_fallbacks` | 0 | 0 | 1 | 1 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `faithfulness` | 0.884 (0.92, 11/12) | 未运行 | 0.945 (0.83, 10/12) | 未运行 |
| `answer_relevancy` | 0.760 (1.00, 12/12) | 未运行 | 0.753 (1.00, 12/12) | 未运行 |
| `context_precision` | 0.986 (1.00, 12/12) | 未运行 | 0.986 (1.00, 12/12) | 未运行 |
| `context_recall` | 1.000 (1.00, 12/12) | 未运行 | 1.000 (1.00, 12/12) | 未运行 |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## Ragas 缺失指标（NaN）

| 样本 | 配置 | 缺失指标 |
|---|---|---|
| q05 | parent_multi | `faithfulness` |
| q08 | parent_multi | `faithfulness` |
| q12 | full | `faithfulness` |

这些指标没有产生有效值，平均分只基于有效样本，覆盖率见上方汇总表；具体原因请结合本次运行日志中的 judge 输出解析失败提示。

## 相对 full 基线的差值

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `evidence_hit@3` | 基线 | +0.028 | +0.028 | +0.056 |
| `evidence_recall@3` | 基线 | +0.028 | +0.028 | +0.056 |
| `context_precision@3` | 基线 | +0.051 | +0.051 | +0.065 |
| `mrr@3` | 基线 | +0.032 | +0.032 | +0.046 |
| `source_hit` | 基线 | +0.000 | +0.000 | +0.000 |
| `answer_rate` | 基线 | +0.000 | +0.028 | +0.000 |
| `refusal_accuracy` | 基线 | +0.000 | +0.000 | +0.000 |
| `citation_rate` | 基线 | +0.000 | +0.000 | +0.000 |
| `citation_validity` | 基线 | +0.000 | +0.000 | +0.000 |
| `avg_latency_ms` | 基线 | +7630.721 | +12556.257 | +12942.350 |
| `avg_context_chars` | 基线 | +2802.425 | +2763.575 | +2751.925 |
| `avg_query_count` | 基线 | +0.000 | +1.950 | +0.975 |
| `ragas.faithfulness` | 基线 | NaN | +0.060 | NaN |
| `ragas.answer_relevancy` | 基线 | NaN | -0.007 | NaN |
| `ragas.context_precision` | 基线 | NaN | -0.000 | NaN |
| `ragas.context_recall` | 基线 | NaN | +0.000 | NaN |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| architecture | 0.875 / 0.292 | 0.875 / 0.354 | 0.875 / 0.354 | 0.875 / 0.354 |
| ingestion | 0.875 / 0.375 | 0.875 / 0.375 | 0.875 / 0.375 | 1.000 / 0.438 |
| retrieval | 0.750 / 0.292 | 0.750 / 0.292 | 0.750 / 0.292 | 0.750 / 0.292 |
| api | 0.875 / 0.292 | 1.000 / 0.417 | 1.000 / 0.417 | 1.000 / 0.417 |
| boundary | 1.000 / 0.333 | 1.000 / 0.417 | 1.000 / 0.417 | 1.000 / 0.417 |

## 查询改写回退

下表样本因改写失败退回原问题；检索与生成仍正常执行，`query_count` 为 1。

| 样本 | 配置 | 原因 |
|---|---|---|
| q27 | `parent_multi` | ValueError: 模型输出中没有 JSON 对象 |
| q27 | `parent_hyde` | ValueError: 模型输出中没有 JSON 对象 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| q07 | full | 问答阶段有哪些关键角色？ | 拒答 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 【资料中没有相关信息】 上下文 [1] 中虽然提到问答阶段（阶段二）"有五个关键角色"，但该句之后内容被截断，并未列出这五个角色的具体名称和职责，因此无法根据现有资料给出完整回答。 |
| q24 | full | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文仅描述了 reranker 的功能（将候选从 10 条压到 3 条、用原问题逐条精读子块）[1][2][3]，但未提及默认使用的具体模型名称。 |
| q16 | full | 网页来源用什么组件抽取正文和标题？ | 未命中证据 | trafilatura 抽取正文、标题与发布日期 | 网页来源使用 **trafilatura** 组件来抽取正文和标题（以及日期）。[1] 具体来说，系统会先抓取 HTML，然后用 trafilatura 提取正文内容和标题/日期信息。[1] |
| q21 | full | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | **能工作，但会降级为纯向量检索。** 具体机制如下： 1. **代码层面的降级逻辑**：在混合检索的"路线 B（关键词召回）"中，代码明确写了 `lexical = bm25.search(question, top= |
| q27 | full | 知识库没有就绪时 /ready 返回什么状态？ | 未命中证据 | 未就绪返回 503 | 根据文档，`/ready` 端点用于检查知识库/BM25/reranker 的就绪状态，**不就绪时返回 503**。[1] 具体而言，统一错误格式中对应的错误码为 `503 knowledge_base_not_rea |
| q07 | parent | 问答阶段有哪些关键角色？ | 拒答 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 【资料中没有相关信息】 上下文 [1] 在问答阶段末尾提到"这里有五个关键角色："，但该句之后内容即被截断，并未列出具体是哪五个角色。上下文 [2]（ROADMAP）也未涉及此问题。因此，根据所提供的资料，无法给出问答阶 |
| q24 | parent | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文详细描述了 reranker 的作用（"像面试官逐份精读简历，把'原问题 + 每张子块'放在一起逐条比较，选出真正最相关的 3 张"）[1][2]，也给出了相关参数（如 `TOP_K_RER |
| q16 | parent | 网页来源用什么组件抽取正文和标题？ | 未命中证据 | trafilatura 抽取正文、标题与发布日期 | 网页来源使用 **trafilatura** 组件来抽取正文和标题（以及日期）。[1] 具体流程是：先抓取 HTML，再用 trafilatura 从中抽取正文和标题/日期。[1] 在代码层面，该逻辑对应 `ingest |
| q21 | parent | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | **可以，BM25 索引缺失时问答仍能正常工作，系统会自动降级为纯向量检索。** 具体机制如下： 1. **代码层面的降级逻辑**：在检索函数中，关键词路线（路线 B）的写法是 `lexical = bm25.searc |
| q24 | parent_multi | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文多次提到了 reranker 的作用（将候选从 10 条精排到 3 条、用原问题逐条精读子块等）[1][2]，也列出了 `TOP_K_RERANK` 的默认值为 3 [3]，但并未说明默认使 |
| q07 | parent_multi | 问答阶段有哪些关键角色？ | 未命中证据 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 根据上下文[1]，问答阶段（阶段二）的流程中提到了"这里有五个关键角色"，但上下文在该句之后即结束，**并未列出这五个关键角色的具体名称和内容** [1]。 上下文[1]中问答阶段所涉及的流程环节包括：查询改写（可选）、 |
| q16 | parent_multi | 网页来源用什么组件抽取正文和标题？ | 未命中证据 | trafilatura 抽取正文、标题与发布日期 | 网页来源使用 **trafilatura** 组件来抽取正文和标题。具体流程是：先抓取 HTML，再用 trafilatura 抽取正文以及标题/日期信息。[1] 在代码结构中，该功能由 `ingestion/web.p |
| q21 | parent_multi | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | **能，BM25 索引缺失时问答仍然可以工作，系统会自动降级为纯向量检索。** 具体来说： 1. **自动退化机制**：在混合检索的路线 B（关键词检索）中，代码逻辑是 `lexical = bm25.search(qu |
| q07 | parent_hyde | 问答阶段有哪些关键角色？ | 拒答 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 根据上下文 [1]，在"阶段二：问答"的流程描述末尾提到： > 这里有五个关键角色： [1] 但所提供的上下文在此处截断，**并未列出这五个关键角色的具体名称与职责**。上下文 [2]（ROADMAP）中也没有相关内容。 |
| q24 | parent_hyde | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文仅说明了 reranker 的功能（用原问题逐条精读候选子块，最终只留 3 条）[1][2]，以及相关参数 `TOP_K_RERANK` 默认值为 3 [3]，但并未提及 reranker  |
| q21 | parent_hyde | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | **能工作，会自动降级为纯向量检索。** 具体来说： 1. **代码层面**：在混合检索的路线 B（关键词召回）中，BM25 不存在时 `lexical` 直接返回空列表，而路线 A（语义/向量召回）不受影响，仍然正常执 |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/project_rag_qa.jsonl \
  --configs full,parent,parent_multi,parent_hyde \
  --ragas-configs full,parent_multi \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --ragas-limit 12 \
  --output eval/reports/v5_v4_regression.md \
  --json-output eval/reports/v5_v4_regression.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 40；分数不代表其他领域或生产流量。
- 生成与 judge 都是 `qwen3.8:27b-mlx`，存在自评偏差；分数用于同模型下的相对比较，不能当作绝对质量结论。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。
- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；这是为了命中后无需二次查询 docstore，换来更完整的上下文。
- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。
- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
