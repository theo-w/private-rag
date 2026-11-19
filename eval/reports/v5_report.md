# private-rag v5 检索策略评估报告

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

## 运行信息

- 生成时间：2026-11-18T21:33:16+08:00
- 评估集：`eval/v5_paraphrase_qa.jsonl`（18 条：15 可回答 + 3 应拒答）
- 数据集 SHA256：`c9076c8bce5741c51178b53377cf7a1c87a7c4d5d31409470a8c6c891679f2e1`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：['full', 'parent_multi']，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- **Ragas 子集限制：每个配置只评估前 12 条可回答样本，不能当作全量结论。**
- Git commit：`c40eefabc7f3f6d94086bb5bed396e6fbe07ecda`
- 总耗时：11702.6 秒

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
| `evidence_hit@3` | 0.800 | 0.800 | 0.800 | 0.800 |
| `evidence_recall@3` | 0.800 | 0.800 | 0.800 | 0.800 |
| `context_precision@3` | 0.289 | 0.311 | 0.311 | 0.311 |
| `mrr@3` | 0.567 | 0.611 | 0.611 | 0.611 |
| `source_hit` | 0.867 | 0.867 | 0.867 | 0.867 |
| `answer_rate` | 0.933 | 0.933 | 0.933 | 0.933 |
| `refusal_accuracy` | 1.000 | 1.000 | 1.000 | 1.000 |
| `citation_rate` | 1.000 | 1.000 | 1.000 | 1.000 |
| `citation_validity` | 1.000 | 1.000 | 1.000 | 1.000 |
| `avg_latency_ms` | 36854.9 | 48989.8 | 59234.0 | 52769.2 |
| `avg_retrieved` | 3.000 | 2.778 | 2.778 | 2.778 |
| `avg_context_chars` | 1703.2 | 4526.2 | 4592.2 | 4589.4 |
| `avg_query_count` | 1.000 | 1.000 | 2.778 | 1.944 |
| `used_parent_rate` | 0.000 | 1.000 | 1.000 | 1.000 |
| `rewrite_fallbacks` | 0 | 0 | 2 | 1 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `faithfulness` | 0.877 (1.00, 12/12) | 未运行 | 1.000 (0.83, 10/12) | 未运行 |
| `answer_relevancy` | 0.708 (1.00, 12/12) | 未运行 | 0.684 (1.00, 12/12) | 未运行 |
| `context_precision` | 0.903 (1.00, 12/12) | 未运行 | 0.903 (1.00, 12/12) | 未运行 |
| `context_recall` | 0.875 (1.00, 12/12) | 未运行 | 0.875 (1.00, 12/12) | 未运行 |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## Ragas 缺失指标（NaN）

| 样本 | 配置 | 缺失指标 |
|---|---|---|
| v07 | parent_multi | `faithfulness` |
| v12 | parent_multi | `faithfulness` |

这些指标没有产生有效值，平均分只基于有效样本，覆盖率见上方汇总表；具体原因请结合本次运行日志中的 judge 输出解析失败提示。

## 相对 full 基线的差值

| 指标 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| `evidence_hit@3` | 基线 | +0.000 | +0.000 | +0.000 |
| `evidence_recall@3` | 基线 | +0.000 | +0.000 | +0.000 |
| `context_precision@3` | 基线 | +0.022 | +0.022 | +0.022 |
| `mrr@3` | 基线 | +0.044 | +0.044 | +0.044 |
| `source_hit` | 基线 | +0.000 | +0.000 | +0.000 |
| `answer_rate` | 基线 | +0.000 | +0.000 | +0.000 |
| `refusal_accuracy` | 基线 | +0.000 | +0.000 | +0.000 |
| `citation_rate` | 基线 | +0.000 | +0.000 | +0.000 |
| `citation_validity` | 基线 | +0.000 | +0.000 | +0.000 |
| `avg_latency_ms` | 基线 | +12134.865 | +22379.110 | +15914.301 |
| `avg_context_chars` | 基线 | +2823.000 | +2889.000 | +2886.222 |
| `avg_query_count` | 基线 | +0.000 | +1.778 | +0.944 |
| `ragas.faithfulness` | 基线 | NaN | +0.123 | NaN |
| `ragas.answer_relevancy` | 基线 | NaN | -0.023 | NaN |
| `ragas.context_precision` | 基线 | NaN | +0.000 | NaN |
| `ragas.context_recall` | 基线 | NaN | +0.000 | NaN |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | full | parent | parent_multi | parent_hyde |
|---|---:|---:|---:|---:|
| architecture | 1.000 / 0.444 | 1.000 / 0.500 | 1.000 / 0.500 | 1.000 / 0.500 |
| ingestion | 0.667 / 0.222 | 0.667 / 0.222 | 0.667 / 0.222 | 0.667 / 0.222 |
| retrieval | 0.333 / 0.111 | 0.333 / 0.111 | 0.333 / 0.111 | 0.333 / 0.111 |
| api | 1.000 / 0.333 | 1.000 / 0.333 | 1.000 / 0.333 | 1.000 / 0.333 |
| boundary | 1.000 / 0.333 | 1.000 / 0.389 | 1.000 / 0.389 | 1.000 / 0.389 |

## 查询改写回退

下表样本因改写失败退回原问题；检索与生成仍正常执行，`query_count` 为 1。

| 样本 | 配置 | 原因 |
|---|---|---|
| v03 | `parent_multi` | ValueError: 模型输出中没有 JSON 对象 |
| v03 | `parent_hyde` | ValueError: 模型输出中没有 JSON 对象 |
| v07 | `parent_multi` | ValueError: 模型输出中没有 JSON 对象 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| v09 | full | 我只想找带某个标签的资料，传好几个标签时是全部都要满足，还是满足一个就行？ | 拒答 | 命中任意一个即可 | 【资料中没有相关信息】 上下文中提到了检索时支持"来源/标签过滤"[2]，以及数据流中涉及"标签"作为元数据的一部分[1]，但并未说明当同时传入多个标签时，过滤逻辑是"全部满足（AND）"还是"满足其一（OR）"。因此无 |
| v04 | full | 同一篇文章我反复入库好几次，库里会不会堆出一大堆重复卡片？ | 未命中证据 | doc_id + content_hash 未变化时跳过；重复写入是覆盖而不是新增 | 不会。系统在入库时做了两层保护，避免重复卡片堆积： 1. **哈希去重，直接跳过**：每次入库前，系统会检查该 `doc_id` 是否已存在，并且对比内容哈希（`content_hash`）。如果"同一个 doc_id  |
| v08 | full | 要是 BM25 索引文件没生成，问答页面会直接报错吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示；问答仍按融合结果降级运行 | 不会直接报错。根据上下文，系统设计了对 BM25 索引缺失的容错机制：**BM25 索引缺失时会自动降级**[1]，即仅使用 dense 向量召回，跳过 BM25 这一路，问答仍可正常工作。 具体来说，检索采用 dens |
| v09 | parent | 我只想找带某个标签的资料，传好几个标签时是全部都要满足，还是满足一个就行？ | 拒答 | 命中任意一个即可 | 【资料中没有相关信息】 上下文只提到检索时支持"来源/标签过滤"作为参数 [2]，命令行示例中也出现了 `--tag business` 的用法 [3]，但并未说明同时传入多个标签时是要求全部满足（AND）还是满足其一即 |
| v04 | parent | 同一篇文章我反复入库好几次，库里会不会堆出一大堆重复卡片？ | 未命中证据 | doc_id + content_hash 未变化时跳过；重复写入是覆盖而不是新增 | 不会。系统在设计上就防止了重复堆积，主要有两层机制： 1. **内容哈希去重（跳过）**：每次入库时，会先查该 `doc_id` 是否已存在且内容哈希未变，如果一样就直接返回 `"skipped"`，根本不会重新切块、不 |
| v08 | parent | 要是 BM25 索引文件没生成，问答页面会直接报错吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示；问答仍按融合结果降级运行 | 不会直接报错。根据文档描述，BM25 索引缺失时系统会**自动降级为纯向量检索**，而不是抛出错误： - 在 v2 的验收标准中明确写到："BM25 索引缺失自动降级" [1]。 - 在检索流程的伪代码里也体现了这一逻辑 |
| v09 | parent_multi | 我只想找带某个标签的资料，传好几个标签时是全部都要满足，还是满足一个就行？ | 拒答 | 命中任意一个即可 | 【资料中没有相关信息】 上下文提到了 `HybridRetriever.retrieve` 接收"来源/标签过滤"作为入参 [2]，命令行也展示了 `--tag business` 的用法 [3]，但均未说明当传入多个标 |
| v04 | parent_multi | 同一篇文章我反复入库好几次，库里会不会堆出一大堆重复卡片？ | 未命中证据 | doc_id + content_hash 未变化时跳过；重复写入是覆盖而不是新增 | 不会。系统在设计上专门防止了同一篇文章反复入库产生重复卡片，机制如下： 1. **哈希比对，内容没变直接跳过**：入库时会先检查该 `doc_id` 是否已存在，如果已入库且内容哈希（`content_hash`）与上次 |
| v08 | parent_multi | 要是 BM25 索引文件没生成，问答页面会直接报错吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示；问答仍按融合结果降级运行 | 不会直接报错。根据上下文，BM25 索引缺失时系统会**自动降级**为纯向量检索，而不是抛出错误： - v2 验收标准明确写道："BM25 索引缺失自动降级" [1]。 - 检索代码中的注释也说明："BM25 不存在时自 |
| v09 | parent_hyde | 我只想找带某个标签的资料，传好几个标签时是全部都要满足，还是满足一个就行？ | 拒答 | 命中任意一个即可 | 【资料中没有相关信息】 上下文中仅提到 `HybridRetriever.retrieve` 接收"来源/标签过滤"作为入参 [2]，以及命令行示例 `--tag business` 的用法 [3]，但并未说明传入多个标 |
| v04 | parent_hyde | 同一篇文章我反复入库好几次，库里会不会堆出一大堆重复卡片？ | 未命中证据 | doc_id + content_hash 未变化时跳过；重复写入是覆盖而不是新增 | 不会。系统设计了多层机制防止重复卡片堆积： 1. **内容哈希跳过**：入库时会先检查——"同一个 `doc_id` 已入库，且内容哈希没变 → 直接跳过"，返回 `"skipped"`，根本不会再切块、不会再写入。[1 |
| v08 | parent_hyde | 要是 BM25 索引文件没生成，问答页面会直接报错吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示；问答仍按融合结果降级运行 | 不会直接报错。根据文档，BM25 索引缺失时系统会**自动降级为纯向量检索**，而不是抛出错误： - 在 v2 验收标准中明确写道："BM25 索引缺失自动降级" [1]。 - 在检索流程的说明中，路线 B（关键词召回） |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v5_paraphrase_qa.jsonl \
  --configs full,parent,parent_multi,parent_hyde \
  --ragas-configs full,parent_multi \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --ragas-limit 12 \
  --output eval/reports/v5_report.md \
  --json-output eval/reports/v5_report.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 18；分数不代表其他领域或生产流量。
- 生成与 judge 都是 `qwen3.8:27b-mlx`，存在自评偏差；分数用于同模型下的相对比较，不能当作绝对质量结论。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。
- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；这是为了命中后无需二次查询 docstore，换来更完整的上下文。
- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。
- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
