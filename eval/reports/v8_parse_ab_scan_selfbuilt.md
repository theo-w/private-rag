# private-rag 检索策略评估报告：v6_scan_table

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

## 运行信息

- 生成时间：2026-11-23T13:24:24+08:00
- 评估集：`eval/v6_scan_table.jsonl`（6 条：5 可回答 + 1 应拒答）
- 数据集 SHA256：`f82101d5701ea5d782f4b5c77f08e12ce8719b616108ff2a393fbf9d76d2b0e3`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`530418c302c8a09f171795828343d649e2e73dea`（工作区有未提交改动，读数对应的是这份混合代码）
- 跑批备注：解析后端 selfbuilt（v8 解析基准 A/B 自建侧，v6 六条复现）
- 总耗时：48.2 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `full` | 固定 | 混合召回 + 精排（v4 基线） | 开 | 开 | 关 | off | 3 |

## 确定性指标汇总

| 指标 | full |
|---|---:|
| `evidence_hit@3` | 1.000 |
| `evidence_recall@3` | 1.000 |
| `context_precision@3` | 0.500 |
| `mrr@3` | 0.900 |
| `source_hit` | 0.000 |
| `both_sides_hit` | NaN |
| `answer_rate` | 1.000 |
| `refusal_accuracy` | 1.000 |
| `citation_rate` | 1.000 |
| `citation_validity` | 1.000 |
| `avg_latency_ms` | 8039.5 |
| `avg_retrieved` | 2.000 |
| `avg_context_chars` | 50.000 |
| `avg_query_count` | 1.000 |
| `avg_tool_calls` | NaN |
| `avg_tool_steps` | NaN |
| `avg_tool_calls_knowledge` | NaN |
| `avg_tool_calls_database` | NaN |
| `avg_tool_calls_mixed` | NaN |
| `dual_tool_rate` | NaN |
| `dual_tool_rate_knowledge` | NaN |
| `dual_tool_rate_database` | NaN |
| `dual_tool_rate_mixed` | NaN |
| `gate_intercept_rate` | NaN |
| `used_parent_rate` | 0.000 |
| `rewrite_fallbacks` | 0 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | full |
|---|---:|
| `faithfulness` | 未运行 |
| `answer_relevancy` | 未运行 |
| `context_precision` | 未运行 |
| `context_recall` | 未运行 |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## 相对 full 基线的差值

| 指标 | full |
|---|---:|
| `evidence_hit@3` | 基线 |
| `evidence_recall@3` | 基线 |
| `context_precision@3` | 基线 |
| `mrr@3` | 基线 |
| `source_hit` | 基线 |
| `answer_rate` | 基线 |
| `refusal_accuracy` | 基线 |
| `citation_rate` | 基线 |
| `citation_validity` | 基线 |
| `avg_latency_ms` | 基线 |
| `avg_context_chars` | 基线 |
| `avg_query_count` | 基线 |
| `ragas.faithfulness` | 基线 |
| `ragas.answer_relevancy` | 基线 |
| `ragas.context_precision` | 基线 |
| `ragas.context_recall` | 基线 |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | full |
|---|---:|
| ingestion | 1.000 / 0.500 |
| multi_step | 1.000 / 0.500 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| — | — | 本次没有失败案例 | — | — | — |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v6_scan_table.jsonl \
  --configs full \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --output eval/reports/v8_parse_ab_scan_selfbuilt.md \
  --json-output eval/reports/v8_parse_ab_scan_selfbuilt.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 6；分数不代表其他领域或生产流量。
- 生成与 judge 都是 `qwen3.8:27b-mlx`，存在自评偏差；分数用于同模型下的相对比较，不能当作绝对质量结论。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。
- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；这是为了命中后无需二次查询 docstore，换来更完整的上下文。
- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。
- `both_sides_hit` 只对声明了双侧取证来源（`expected_sources`）的样本有意义，其余条目恒为 `None` 不进均值；固定链路没挂运行时 SQL 源时它必然整列 NaN——双侧样本要跑得通，得用 agentic 组加 `--agent-sources`。
- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
