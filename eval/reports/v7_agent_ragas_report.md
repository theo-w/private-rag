# private-rag 检索策略评估报告：v7_agent_qa

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

> **agentic 组口径不同**：`agent` 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 `sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），但延迟与查询次数不能与固定链路当同义指标比较。

## 运行信息

- 生成时间：2026-11-19T16:31:34+08:00
- 评估集：`eval/v7_agent_qa.jsonl`（固定 26 条；本次运行子集 6 条：6 可回答 + 0 应拒答）
- **本次只运行子集：6 / 26 条，分数不能当作全量数据集结论。**
- 数据集 SHA256：`53a8d001131d5c81f0304be907f81b211ef0ec44ed2d6fa6248c942b5fade787`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：['agent', 'full']，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- **Ragas 子集限制：每个配置只评估前 6 条可回答样本，不能当作全量结论。**
- Git commit：`9147bd3a30b418a5c29ba75d4df9f4102e0e361c`
- 总耗时：3965.1 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `agent` | agentic | Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL） | 开 | 开 | 开 | off | 3 |
| `full` | 固定 | 混合召回 + 精排（v4 基线） | 开 | 开 | 关 | off | 3 |

## 确定性指标汇总

| 指标 | agent | full |
|---|---:|---:|
| `evidence_hit@3` | 0.333 | 0.167 |
| `evidence_recall@3` | 0.333 | 0.167 |
| `context_precision@3` | 0.139 | 0.056 |
| `mrr@3` | 0.139 | 0.167 |
| `source_hit` | 0.667 | 0.167 |
| `answer_rate` | 1.000 | 0.167 |
| `refusal_accuracy` | NaN | NaN |
| `citation_rate` | 1.000 | 0.333 |
| `citation_validity` | 1.000 | 0.333 |
| `avg_latency_ms` | 71922.8 | 17210.3 |
| `avg_retrieved` | 3.000 | 3.000 |
| `avg_context_chars` | 1318.7 | 1362.7 |
| `avg_query_count` | 1.000 | 1.000 |
| `avg_tool_calls` | 1.667 | NaN |
| `avg_tool_steps` | 3.667 | NaN |
| `used_parent_rate` | 1.000 | 0.000 |
| `rewrite_fallbacks` | 0 | 0 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | agent | full |
|---|---:|---:|
| `faithfulness` | 0.659 (1.00, 6/6) | 0.600 (0.83, 5/6) |
| `answer_relevancy` | 0.853 (0.83, 5/6) | 0.138 (0.83, 5/6) |
| `context_precision` | 0.492 (1.00, 6/6) | 0.306 (1.00, 6/6) |
| `context_recall` | 0.750 (1.00, 6/6) | 0.250 (1.00, 6/6) |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## Ragas 缺失指标（NaN）

| 样本 | 配置 | 缺失指标 |
|---|---|---|
| m03 | full | `faithfulness`, `answer_relevancy` |
| d01 | agent | `answer_relevancy` |

这些指标没有产生有效值，平均分只基于有效样本，覆盖率见上方汇总表；具体原因请结合本次运行日志中的 judge 输出解析失败提示。

## 相对 full 基线的差值

| 指标 | agent | full |
|---|---:|---:|
| `evidence_hit@3` | +0.167 | 基线 |
| `evidence_recall@3` | +0.167 | 基线 |
| `context_precision@3` | +0.083 | 基线 |
| `mrr@3` | -0.028 | 基线 |
| `source_hit` | +0.500 | 基线 |
| `answer_rate` | +0.833 | 基线 |
| `refusal_accuracy` | NaN | 基线 |
| `citation_rate` | +0.667 | 基线 |
| `citation_validity` | +0.667 | 基线 |
| `avg_latency_ms` | +54712.491 | 基线 |
| `avg_context_chars` | -44.000 | 基线 |
| `avg_query_count` | +0.000 | 基线 |
| `ragas.faithfulness` | +0.059 | 基线 |
| `ragas.answer_relevancy` | +0.715 | 基线 |
| `ragas.context_precision` | +0.186 | 基线 |
| `ragas.context_recall` | +0.500 | 基线 |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | agent | full |
|---|---:|---:|
| knowledge | 1.000 / 0.333 | 1.000 / 0.333 |
| database | 0.000 / 0.000 | 0.000 / 0.000 |
| mixed | 0.000 / 0.000 | 0.000 / 0.000 |
| multi_step | 0.500 / 0.250 | 0.000 / 0.000 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| d01 | agent | 香蕉到底能不能放冰箱保存？ | 未命中证据 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | 在已检索的知识库资料中，未找到关于香蕉保存方式的专门文档（现有证据均为 RAG 系统技术文档 [1][2][3]），因此无法从知识库给出引用依据。以下仅为通用常识性说明，非知识库内容： - 香蕉对低温敏感，短期（1–2  |
| d03 | agent | 把这四种水果按价格从高到低列出来，并说明各自的保存方式。 | 未命中证据 | 苹果适合冷藏保存，可以延长保鲜时间 | 四种水果按价格从高到低排列如下： 1. **猕猴桃** — 15.0 元：维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化。[5] 2. **橙子** — 12.5 元：富含维生素 C，适合日常补充维 C；表皮较 |
| m03 | agent | 最便宜的水果是什么、单价多少？精排后最终给大模型几条资料？ | 未命中证据 | 香蕉不适合放冰箱，低温会让表皮变黑 | 最便宜的水果是**香蕉**，单价为 **6.5 元**[1]。本次检索（SQL 查询按水果类别、价格升序取第 1 条）最终精排后给大模型的资料为 **1 条**，即证据 [1] 所示的香蕉记录。 |
| s04 | agent | 最近更新的是哪条水果记录？它的保存建议是什么？ | 未命中证据 | 成熟后应尽快食用，也可以冷藏延缓软化 | 最近更新的水果记录是「猕猴桃」（价格 15.0 元，更新日期 2026-01-18）[4]。它的保存建议是：成熟后应尽快食用，也可以冷藏延缓软化 [4]。 |
| d01 | full | 香蕉到底能不能放冰箱保存？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | 【资料中没有相关信息】 |
| d03 | full | 把这四种水果按价格从高到低列出来，并说明各自的保存方式。 | 拒答 | 苹果适合冷藏保存，可以延长保鲜时间 | 【资料中没有相关信息】 |
| m03 | full | 最便宜的水果是什么、单价多少？精排后最终给大模型几条资料？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑 | **关于"最便宜的水果是什么、单价多少"：** 【资料中没有相关信息】。所提供的上下文仅涉及 RAG 系统的参数配置与流程说明，不包含任何水果或价格数据。 **关于"精排后最终给大模型几条资料"：** 精排（rerank |
| s02 | full | 按价格看最贵的是哪种水果？它的维生素 C 情况怎么样？ | 拒答 | 猕猴桃维生素 C 含量高 | 【资料中没有相关信息】 |
| s04 | full | 最近更新的是哪条水果记录？它的保存建议是什么？ | 拒答 | 成熟后应尽快食用，也可以冷藏延缓软化 | 【资料中没有相关信息】 |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl \
  --configs agent,full \
  --agent-sources config/sources.eval.agent.yaml \
  --ragas-configs agent,full \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --ragas-limit 6 \
  --ids k01,d01,d03,m03,s02,s04 \
  --output eval/reports/v7_agent_ragas_report.md \
  --json-output eval/reports/v7_agent_ragas_report.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 6；分数不代表其他领域或生产流量。
- 生成与 judge 都是 `qwen3.8:27b-mlx`，存在自评偏差；分数用于同模型下的相对比较，不能当作绝对质量结论。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。
- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；这是为了命中后无需二次查询 docstore，换来更完整的上下文。
- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。
- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
- 评估库只入库项目文档，SQL 工具只在运行时挂上（`config/sources.eval.agent.yaml`，不参与入库）；因此 `database` 类问题在固定链路上必然拒答——这是本次要量的差值本身，不是数据泄漏或配置疏漏。
- agentic 组的 `evidence_hit` 取决于模型 SELECT 出了哪些列：只查 `name`/`price` 而没取 `description` 时，答案可以完全正确但证据判 0。该指标在 agentic 组偏保守，应结合引用与回答文本一起看。
- `evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只看前 3 条上下文，而 agentic 一次可产出 4–9 条证据块：SQL 证据常排在 `kb_search` 结果之后（本机实跑有样本的证据落在第 5 与第 7 块，回答引用正确但指标判 0），因此 `database` / `mixed` 类别的低分不能读成“证据没进上下文”。
- 拒答判定认的是生成侧的 `【资料中没有相关信息】` 哨兵文本：agentic 组在证据不足时倾向给“带引用的否定回答”（例如查库后说明 `products` 表没有该字段），本口径下算未拒答，`refusal_accuracy` 低于固定链路属于行为差异，需结合答案文本判断。
- agentic 组的延迟是整条循环的墙钟耗时（含意图路由与多次工具调用）：`avg_retrieval_ms` 记工具执行耗时之和，`avg_generation_ms` 记其余开销，两者相加才是总耗时，不能拿固定链路的同名列来比快慢。
- `avg_tool_calls` / `avg_tool_steps` 只是旁证：`multi_step` 类样本没有单独判定模型是否真的分了两次查询（单条子查询 SQL 也能一次做对）。
- 评估按单轮问答执行，不传会话历史，指代消解类能力未覆盖。
- 评估运行设置 `RAG_DISABLE_AGENT_TRACE=1`，不写 `storage/traces/agent.jsonl`，避免把评估样本混进演示轨迹；因此报告里的 `trace_id` 不能逐条回放。
