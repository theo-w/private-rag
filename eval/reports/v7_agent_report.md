# private-rag 检索策略评估报告：v7_agent_qa

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

> **agentic 组口径不同**：`agent` 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 `sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），但延迟与查询次数不能与固定链路当同义指标比较。

## 运行信息

- 生成时间：2026-11-19T15:10:07+08:00
- 评估集：`eval/v7_agent_qa.jsonl`（26 条：22 可回答 + 4 应拒答）
- 数据集 SHA256：`53a8d001131d5c81f0304be907f81b211ef0ec44ed2d6fa6248c942b5fade787`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`a6e2bd8ee81d0289b207d3978680a727c0c0a3e3`
- 总耗时：2291.6 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `agent` | agentic | Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL） | 开 | 开 | 开 | off | 3 |
| `full` | 固定 | 混合召回 + 精排（v4 基线） | 开 | 开 | 关 | off | 3 |

## 确定性指标汇总

| 指标 | agent | full |
|---|---:|---:|
| `evidence_hit@3` | 0.409 | 0.318 |
| `evidence_recall@3` | 0.409 | 0.318 |
| `context_precision@3` | 0.197 | 0.106 |
| `mrr@3` | 0.348 | 0.295 |
| `source_hit` | 0.591 | 0.318 |
| `answer_rate` | 0.909 | 0.318 |
| `refusal_accuracy` | 0.250 | 1.000 |
| `citation_rate` | 0.864 | 0.591 |
| `citation_validity` | 0.864 | 0.591 |
| `avg_latency_ms` | 71181.0 | 16955.1 |
| `avg_retrieved` | 3.654 | 3.000 |
| `avg_context_chars` | 1676.0 | 1278.1 |
| `avg_query_count` | 1.231 | 1.000 |
| `avg_tool_calls` | 1.500 | NaN |
| `avg_tool_steps` | 3.731 | NaN |
| `used_parent_rate` | 1.000 | 0.000 |
| `rewrite_fallbacks` | 0 | 0 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | agent | full |
|---|---:|---:|
| `faithfulness` | 未运行 | 未运行 |
| `answer_relevancy` | 未运行 | 未运行 |
| `context_precision` | 未运行 | 未运行 |
| `context_recall` | 未运行 | 未运行 |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## 相对 full 基线的差值

| 指标 | agent | full |
|---|---:|---:|
| `evidence_hit@3` | +0.091 | 基线 |
| `evidence_recall@3` | +0.091 | 基线 |
| `context_precision@3` | +0.091 | 基线 |
| `mrr@3` | +0.053 | 基线 |
| `source_hit` | +0.273 | 基线 |
| `answer_rate` | +0.591 | 基线 |
| `refusal_accuracy` | -0.750 | 基线 |
| `citation_rate` | +0.273 | 基线 |
| `citation_validity` | +0.273 | 基线 |
| `avg_latency_ms` | +54225.822 | 基线 |
| `avg_context_chars` | +397.885 | 基线 |
| `avg_query_count` | +0.231 | 基线 |
| `ragas.faithfulness` | NaN | 基线 |
| `ragas.answer_relevancy` | NaN | 基线 |
| `ragas.context_precision` | NaN | 基线 |
| `ragas.context_recall` | NaN | 基线 |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | agent | full |
|---|---:|---:|
| knowledge | 1.000 / 0.333 | 1.000 / 0.333 |
| database | 0.000 / 0.000 | 0.000 / 0.000 |
| mixed | 0.000 / 0.000 | 0.000 / 0.000 |
| multi_step | 0.500 / 0.500 | 0.000 / 0.000 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| r01 | agent | 橙子的产地和供应商联系方式是什么？ | 应拒答但未拒答 | — | 根据现有数据，无法提供橙子的产地和供应商联系方式。数据库中 products 表仅包含 id、name、category、description、price、updated_at 六个字段 [4]，其中橙子的记录为：名称 |
| r02 | agent | 这个项目有 iOS 客户端吗？ | 应拒答但未拒答 | — | 根据现有资料，这个项目没有 iOS 客户端。项目是一个基于 FastAPI 的 RAG（检索增强生成）服务，前端仅提供一个由 FastAPI 同源托管的单 HTML 演示页（/demo），无构建步骤、无 CDN、无外部资 |
| r04 | agent | private-rag 商业版的定价是多少？ | 应拒答但未拒答 | — | 根据已检索到的知识库资料，未找到关于 private-rag「商业版」及其定价的任何信息。现有资料涵盖的是项目架构、版本演进路线（v1–v6.1）、评估策略、只读公网演示等内容 [3][5]，以及检索/护栏/意图分类等机 |
| d01 | agent | 香蕉到底能不能放冰箱保存？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | 【资料中没有相关信息】 所提供的证据均为某 RAG 系统（父子块分块、双索引检索、会话与并发模型）的技术文档，其中虽出现"冷藏""放冰箱"等字样，但仅是作为向量检索同义匹配的技术示例 [1]，并非关于香蕉保存的实质性知识 |
| s01 | agent | 单价高于这四种水果平均价的水果有哪些？分别多少钱、该怎么保存？ | 拒答 | 表皮较厚，常温可存放一周左右 | 【资料中没有相关信息】 |
| d02 | agent | 猕猴桃现在单价是多少，买回来应该怎么保存？ | 未命中证据 | 猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化 | 1. 猕猴桃单价：根据数据库查询结果，猕猴桃当前单价为 **15.0 元** [4]。 2. 关于猕猴桃买回来怎么保存：在已检索到的知识库资料中未找到相关说明，现有资料主要涉及 RAG 系统的父子块切分、入库流程等技术文 |
| d03 | agent | 把这四种水果按价格从高到低列出来，并说明各自的保存方式。 | 未命中证据 | 苹果适合冷藏保存，可以延长保鲜时间 | 以下是四种水果按价格从高到低的排列及各自保存方式： 1. **猕猴桃** — 15.0 元：维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化。[5] 2. **橙子** — 12.5 元：表皮较厚，常温可存放一周 |
| d04 | agent | 橙子常温下大概能放多久？ | 未命中证据 | 表皮较厚，常温可存放一周左右 | 抱歉，当前本地知识库（RAG 系统技术文档）与 products 商品表中均未检索到关于「橙子常温保存时间」的资料 [1][2][3]。该问题属于通用生活常识，不在本知识库的覆盖范围内。 作为一般常识参考（非知识库依据） |
| d05 | agent | 哪种水果的维生素 C 含量比较高，它的单价是多少？ | 未命中证据 | 猕猴桃维生素 C 含量高 | 根据证据 [7]，有两种水果被描述为维生素 C 含量高： - **橙子**：描述为"富含维生素 C，适合日常补充维 C"，单价 **12.5** 元 [7]。 - **猕猴桃**：描述为"维生素 C 含量高，成熟后应尽快 |
| d06 | agent | 苹果切开后为什么建议尽快吃完？ | 未命中证据 | 切开后容易氧化，建议尽快食用 | 苹果切开后建议尽快吃完，主要原因有两点： 1. **酶促褐变（氧化）**：苹果果肉中含有多酚氧化酶（PPO）和酚类物质。切开后果肉暴露于空气中，PPO 在氧气参与下将酚类氧化为醌，醌进一步聚合生成黑色素，使切面迅速由白变 |
| d01 | full | 香蕉到底能不能放冰箱保存？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | 【资料中没有相关信息】 |
| d02 | full | 猕猴桃现在单价是多少，买回来应该怎么保存？ | 拒答 | 猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化 | 【资料中没有相关信息】 |
| d03 | full | 把这四种水果按价格从高到低列出来，并说明各自的保存方式。 | 拒答 | 苹果适合冷藏保存，可以延长保鲜时间 | 【资料中没有相关信息】 |
| d04 | full | 橙子常温下大概能放多久？ | 拒答 | 表皮较厚，常温可存放一周左右 | 【资料中没有相关信息】 上下文中虽然出现了"橙子常温下大概能放多久？"这句话，但它仅作为 API 调用的示例问题出现 [3]，并未给出橙子实际能存放多久的答案。其余上下文（[1][2]）均为系统架构与技术文档，不包含橙子 |
| d05 | full | 哪种水果的维生素 C 含量比较高，它的单价是多少？ | 拒答 | 猕猴桃维生素 C 含量高 | 【资料中没有相关信息】 |
| d06 | full | 苹果切开后为什么建议尽快吃完？ | 拒答 | 切开后容易氧化，建议尽快食用 | 【资料中没有相关信息】 |
| m01 | full | 香蕉能不能放冰箱？另外这个项目的引用列表是从哪儿来的？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | **关于"香蕉能不能放冰箱"：** 【资料中没有相关信息】提供的上下文仅以"冷藏"和"放冰箱"举例说明向量检索能匹配同义表达 [3]，但并未涉及香蕉等具体食物能否冷藏的内容。 --- **关于"引用列表是从哪儿来的"：* |
| m02 | full | 橙子多少钱？这个项目的 embedding 和向量存储分别由什么负责？ | 拒答 | 橙子富含维生素 C，适合日常补充维 C | **关于"橙子多少钱"：** 【资料中没有相关信息】上下文仅提及"橙子"作为会话指代消解的示例（帮助模型知道"它"指橙子）[2]，并未包含任何关于橙子价格的内容。 **关于 embedding 和向量存储分别由什么负责： |
| m03 | full | 最便宜的水果是什么、单价多少？精排后最终给大模型几条资料？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑 | **关于"最便宜的水果是什么、单价多少"：** 【资料中没有相关信息】 **关于"精排后最终给大模型几条资料"：** 精排（reranker）后最终只给大模型 **3 条** 资料。[1] 中参数 `TOP_K_RERA |
| m04 | full | 苹果适合怎么保存？v7 的 agentic 问答支持哪几种意图？ | 拒答 | 苹果适合冷藏保存，可以延长保鲜时间 | **关于"苹果适合怎么保存"：** 【资料中没有相关信息】 **关于"v7 的 agentic 问答支持哪几种意图"：** 上下文未给出意图的完整枚举清单，但提到模型会"按问题意图在**知识库检索**与**只读 SQL* |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl \
  --configs agent,full \
  --agent-sources config/sources.eval.agent.yaml \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --output eval/reports/v7_agent_report.md \
  --json-output eval/reports/v7_agent_report.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 26；分数不代表其他领域或生产流量。
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
