# private-rag v5 检索策略评估报告

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

## 运行信息

- 生成时间：2026-11-19T00:52:56+08:00
- 评估集：`eval/project_rag_qa.jsonl`（40 条：36 可回答 + 4 应拒答）
- 数据集 SHA256：`5d1dff8187f96bef178295eaa7d2861af924cedcf469d073e525fdab48a7077e`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：['full', 'parent_multi']，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- **Ragas 子集限制：每个配置只评估前 12 条可回答样本，不能当作全量结论。**
- Git commit：`c40eefabc7f3f6d94086bb5bed396e6fbe07ecda`
- 总耗时：10739.6 秒

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
| `evidence_hit@3` | 0.861 | 0.861 | 0.833 | 0.833 |
| `evidence_recall@3` | 0.861 | 0.861 | 0.833 | 0.833 |
| `context_precision@3` | 0.315 | 0.356 | 0.347 | 0.347 |
| `mrr@3` | 0.704 | 0.718 | 0.704 | 0.704 |
| `source_hit` | 0.944 | 0.944 | 0.944 | 0.944 |
| `answer_rate` | 0.917 | 0.972 | 0.917 | 0.972 |
| `refusal_accuracy` | 1.000 | 1.000 | 1.000 | 1.000 |
| `citation_rate` | 0.972 | 1.000 | 1.000 | 1.000 |
| `citation_validity` | 0.972 | 1.000 | 1.000 | 1.000 |
| `avg_latency_ms` | 16170.2 | 24005.8 | 29390.7 | 29272.9 |
| `avg_retrieved` | 3.000 | 2.750 | 2.750 | 2.750 |
| `avg_context_chars` | 1756.4 | 4622.8 | 4630.8 | 4641.5 |
| `avg_query_count` | 1.000 | 1.000 | 2.900 | 1.975 |
| `used_parent_rate` | 0.000 | 1.000 | 1.000 | 1.000 |
| `rewrite_fallbacks` | 0 | 0 | 2 | 1 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `faithfulness` | 0.919 (1.00, 12/12) | 未运行 | 0.944 (0.75, 9/12) | 未运行 |
| `answer_relevancy` | 0.733 (1.00, 12/12) | 未运行 | 0.726 (1.00, 12/12) | 未运行 |
| `context_precision` | 0.951 (1.00, 12/12) | 未运行 | 0.931 (1.00, 12/12) | 未运行 |
| `context_recall` | 1.000 (1.00, 12/12) | 未运行 | 0.917 (1.00, 12/12) | 未运行 |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## Ragas 缺失指标（NaN）

| 样本 | 配置 | 缺失指标 |
|---|---|---|
| q01 | parent_multi | `faithfulness` |
| q02 | parent_multi | `faithfulness` |
| q05 | parent_multi | `faithfulness` |

这些指标没有产生有效值，平均分只基于有效样本，覆盖率见上方汇总表；具体原因请结合本次运行日志中的 judge 输出解析失败提示。

## 相对 full 基线的差值

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `evidence_hit@3` | 基线 | +0.000 | -0.028 | -0.028 |
| `evidence_recall@3` | 基线 | +0.000 | -0.028 | -0.028 |
| `context_precision@3` | 基线 | +0.042 | +0.032 | +0.032 |
| `mrr@3` | 基线 | +0.014 | +0.000 | +0.000 |
| `source_hit` | 基线 | +0.000 | +0.000 | +0.000 |
| `answer_rate` | 基线 | +0.056 | +0.000 | +0.056 |
| `refusal_accuracy` | 基线 | +0.000 | +0.000 | +0.000 |
| `citation_rate` | 基线 | +0.028 | +0.028 | +0.028 |
| `citation_validity` | 基线 | +0.028 | +0.028 | +0.028 |
| `avg_latency_ms` | 基线 | +7835.583 | +13220.438 | +13102.677 |
| `avg_context_chars` | 基线 | +2866.350 | +2874.375 | +2885.075 |
| `avg_query_count` | 基线 | +0.000 | +1.900 | +0.975 |
| `ragas.faithfulness` | 基线 | NaN | +0.026 | NaN |
| `ragas.answer_relevancy` | 基线 | NaN | -0.007 | NaN |
| `ragas.context_precision` | 基线 | NaN | -0.021 | NaN |
| `ragas.context_recall` | 基线 | NaN | -0.083 | NaN |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| architecture | 0.875 / 0.292 | 0.875 / 0.354 | 0.875 / 0.354 | 0.875 / 0.333 |
| ingestion | 0.875 / 0.375 | 0.875 / 0.396 | 0.750 / 0.354 | 0.750 / 0.354 |
| retrieval | 0.750 / 0.292 | 0.750 / 0.333 | 0.750 / 0.333 | 0.750 / 0.354 |
| api | 0.875 / 0.292 | 0.875 / 0.333 | 0.875 / 0.333 | 0.875 / 0.333 |
| boundary | 1.000 / 0.333 | 1.000 / 0.375 | 1.000 / 0.375 | 1.000 / 0.375 |

## 查询改写回退

下表样本因改写失败退回原问题；检索与生成仍正常执行，`query_count` 为 1。

| 样本 | 配置 | 原因 |
|---|---|---|
| q13 | `parent_multi` | ValueError: 模型输出中没有 JSON 对象 |
| q27 | `parent_multi` | ValueError: 模型输出中没有 JSON 对象 |
| q27 | `parent_hyde` | ValueError: 模型输出中没有 JSON 对象 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| q07 | full | 问答阶段有哪些关键角色？ | 拒答 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 【资料中没有相关信息】 上下文 [1] 在描述阶段二（问答）的流程后提到"这里有五个关键角色："，但随后内容即截止，并未列出这五个关键角色具体是哪些、各自职责是什么。因此，仅凭所提供的上下文无法回答该问题。 |
| q21 | full | BM25 索引缺失时问答还能工作吗？ | 拒答 | 问答时缺失会自动降级为纯向量检索并提示 | 【资料中没有相关信息】 上下文提到了 BM25 索引的构建方式（入库结束后从 Qdrant 重建）[1]，也提到向量索引与 BM25 索引是两路并行召回、由 RRF 融合 [3]，但并未说明当 BM25 索引缺失（如未重 |
| q24 | full | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 |
| q14 | full | SQL 接入时如何避免数据库凭据进入日志或元数据？ | 未命中证据 | 连接串只保留主机和库名，用户名密码不会写进日志或元数据 | ## SQL 接入时避免数据库凭据进入日志或元数据的机制 根据上下文，系统从多个层面确保凭据不泄露： 1. **元数据层面——DSN 脱敏**：SQL 的 `source_uri` 只使用 `label#pk` 形式，日 |
| q27 | full | 知识库没有就绪时 /ready 返回什么状态？ | 未命中证据 | 未就绪返回 503 | 根据上下文，知识库没有就绪时，`/ready` 接口返回 **503** 状态码。[1] 具体来说，`/ready` 用于检查知识库、BM25、reranker 的就绪状态，其中任一组件不就绪即返回 503；对应的错误码 |
| q24 | parent | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文中虽多次提到 reranker 的功能（用原问题逐条精读候选子块、将 10 条压到 3 条）[1][2]，也列出了大量默认参数（如 `TOP_K_RERANK = 3`）[3]，但并未说明默 |
| q07 | parent | 问答阶段有哪些关键角色？ | 未命中证据 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 根据提供的上下文，问答阶段（阶段二）的流程中确实提到了"这里有五个关键角色"[1]，但上下文在引出这句话后即截断，**并未列出这五个关键角色分别是什么**，因此无法从现有资料中给出完整名单。 从该流程图的描述中，可以辨认 |
| q14 | parent | SQL 接入时如何避免数据库凭据进入日志或元数据？ | 未命中证据 | 连接串只保留主机和库名，用户名密码不会写进日志或元数据 | ## SQL 接入时避免数据库凭据进入日志或元数据的措施 根据上下文，项目从多个层面确保凭据不泄露： 1. **`source_uri` 不含凭据**：SQL 的 `source_uri` 只使用 `label#pk`  |
| q21 | parent | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | 可以，BM25 索引缺失时问答仍然能工作，系统会自动退化为纯向量检索。 在检索代码中，路线 B（关键词检索）的写法是： ```python # 路线 B：按关键词找；BM25 不存在时自动退化成纯向量 lexical = |
| q27 | parent | 知识库没有就绪时 /ready 返回什么状态？ | 未命中证据 | 未就绪返回 503 | 根据上下文，知识库没有就绪时，`/ready` 接口返回 **503** 状态码。[1] 具体而言，错误码为 `503 knowledge_base_not_ready`，表示知识库未就绪。[2] 该接口的说明为："知识 |
| q07 | parent_multi | 问答阶段有哪些关键角色？ | 拒答 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 根据提供的上下文，问答阶段（阶段二）的流程描述结束后确实提到"这里有五个关键角色："[1]，但上下文在此处截断，**并未列出这五个关键角色的具体内容**。 因此，关于"问答阶段有哪些关键角色"这一具体问题，【资料中没有相 |
| q21 | parent_multi | BM25 索引缺失时问答还能工作吗？ | 拒答 | 问答时缺失会自动降级为纯向量检索并提示 | 根据上下文，BM25 索引缺失时问答**仍能工作**，系统具备自动降级机制。 在 v2 的详细验收标准中明确列出了一条： > `--source-type` / `--tag` 过滤；**BM25 索引缺失自动降级**  |
| q24 | parent_multi | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文 [1] 和 [2] 中描述了 reranker 的功能（"用原问题逐条精读，从 10 条候选中选出最相关的 3 条子块"）[1][2]，但未提及默认使用的具体模型名称。 |
| q11 | parent_multi | chunk 的稳定 ID 是怎么生成的？ | 未命中证据 | uuid5(doc_id + content_hash + chunk_index) | 根据上下文，chunk 的稳定 ID 生成涉及两个层面： **父块 ID：** `split_parent_child`（位于 `chunking.py`）在将文档正文按 2000 字符切父块、再在父块内按 700/10 |
| q14 | parent_multi | SQL 接入时如何避免数据库凭据进入日志或元数据？ | 未命中证据 | 连接串只保留主机和库名，用户名密码不会写进日志或元数据 | ## SQL 接入时避免数据库凭据进入日志或元数据的措施 根据上下文，主要有以下几层机制： 1. **`source_uri` 不含密码**：SQL 源的 `source_uri` 只使用 `label#pk` 形式，不 |
| q24 | parent_hyde | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 上下文中描述了 reranker 的功能（将"原问题 + 每张子块"逐条比较，选出最相关的 3 张）[1]，也列出了相关参数如 `TOP_K_RERANK` 默认值为 3 [2]，以及 `Rera |
| q07 | parent_hyde | 问答阶段有哪些关键角色？ | 未命中证据 | 查询改写（可选）；reranker（重排模型）；父块扩展 | 根据上下文 [1]，在"阶段二：问答"的流程描述末尾，文档明确写道： > "这里有五个关键角色：" [1] 但所提供的上下文在此处截断，**并未列出这五个关键角色的具体名称和职责**。因此，资料中只告知了"有五个"这一事 |
| q11 | parent_hyde | chunk 的稳定 ID 是怎么生成的？ | 未命中证据 | uuid5(doc_id + content_hash + chunk_index) | 根据现有上下文，chunk 稳定 ID 的生成涉及以下几个层面，但**具体算法细节（如 `deterministic_ids` 的内部实现）在资料中未展开**，只能给出以下要点： 1. **父块 ID 的生成** `ch |
| q14 | parent_hyde | SQL 接入时如何避免数据库凭据进入日志或元数据？ | 未命中证据 | 连接串只保留主机和库名，用户名密码不会写进日志或元数据 | ## SQL 接入时避免数据库凭据进入日志或元数据的措施 根据上下文，项目从多个层面防止凭据泄露： 1. **`source_uri` 不含凭据**：SQL 的 `source_uri` 只使用 `label#pk` 的 |
| q21 | parent_hyde | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | **可以，BM25 索引缺失时系统会自动降级，问答仍然能工作。** 在 v2 的详细验收清单中明确列出了一条： > `--source-type` / `--tag` 过滤；**BM25 索引缺失自动降级** [3] 也 |

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
