# private-rag 检索策略评估报告：v8_agentic_expanded

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

> **agentic 组口径不同**：`agent` 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 `sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），但延迟与查询次数不能与固定链路当同义指标比较。

## 运行信息

- 生成时间：2026-11-25T14:15:32+08:00
- 评估集：`eval/v8_agentic_expanded.jsonl`（40 条：35 可回答 + 5 应拒答）
- 数据集 SHA256：`a99e2ca3fa7784eb9bb6c6bc853497751c60125932ff32a50a152e208d58f634`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`7eeb0c5f43cb08e54d8490466fada101074e939f`
- 跑批备注：v8.7 新集基线（40 条）；HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1；huggingface-hub 0.36.2 + reranker 已加载
- 总耗时：1612.9 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `agent` | agentic | Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL） | 开 | 开 | 开 | off | 3 |
| `full` | 固定 | 混合召回 + 精排（v4 基线） | 开 | 开 | 关 | off | 3 |

## 确定性指标汇总

| 指标 | agent | full |
|---|---:|---:|
| `evidence_hit@3` | 0.571 | 0.229 |
| `evidence_recall@3` | 0.571 | 0.229 |
| `context_precision@3` | 0.305 | 0.076 |
| `mrr@3` | 0.505 | 0.157 |
| `source_hit` | 0.714 | 0.286 |
| `both_sides_hit` | 0.875 | 0.000 |
| `answer_rate` | 0.743 | 0.286 |
| `refusal_accuracy` | 1.000 | 1.000 |
| `citation_rate` | 0.771 | 0.600 |
| `citation_validity` | 0.771 | 0.600 |
| `avg_latency_ms` | 26165.5 | 14154.0 |
| `avg_retrieved` | 3.250 | 3.000 |
| `avg_context_chars` | 1961.1 | 1173.3 |
| `avg_query_count` | 1.500 | 1.000 |
| `avg_tool_calls` | 1.775 | NaN |
| `avg_tool_steps` | 4.175 | NaN |
| `avg_tool_calls_knowledge` | 1.250 | NaN |
| `avg_tool_calls_database` | 1.000 | NaN |
| `avg_tool_calls_mixed` | 3.500 | NaN |
| `dual_tool_rate` | 0.275 | NaN |
| `dual_tool_rate_knowledge` | 0.042 | NaN |
| `dual_tool_rate_database` | 0.000 | NaN |
| `dual_tool_rate_mixed` | 1.000 | NaN |
| `gate_intercept_rate` | 0.225 | NaN |
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
| `evidence_hit@3` | +0.343 | 基线 |
| `evidence_recall@3` | +0.343 | 基线 |
| `context_precision@3` | +0.229 | 基线 |
| `mrr@3` | +0.348 | 基线 |
| `source_hit` | +0.429 | 基线 |
| `answer_rate` | +0.457 | 基线 |
| `refusal_accuracy` | +0.000 | 基线 |
| `citation_rate` | +0.171 | 基线 |
| `citation_validity` | +0.171 | 基线 |
| `avg_latency_ms` | +12011.473 | 基线 |
| `avg_context_chars` | +787.725 | 基线 |
| `avg_query_count` | +0.500 | 基线 |
| `ragas.faithfulness` | NaN | 基线 |
| `ragas.answer_relevancy` | NaN | 基线 |
| `ragas.context_precision` | NaN | 基线 |
| `ragas.context_recall` | NaN | 基线 |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | agent | full |
|---|---:|---:|
| knowledge | 0.556 / 0.185 | 0.778 / 0.259 |
| database | 0.000 / 0.000 | 0.000 / 0.000 |
| mixed | 0.857 / 0.286 | 0.000 / 0.000 |
| multi_step | 0.833 / 0.722 | 0.000 / 0.000 |
| coreference | 0.800 / 0.533 | 0.200 / 0.067 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| xd01 | agent | 蓝莓收到以后该怎么处理？ | 拒答 | 收到后应尽快食用，未洗的可以冷藏存放三四天 | 【资料中没有相关信息】 缺少的信息：证据中未包含蓝莓的保存或处理建议，products 表也无蓝莓数据 |
| xd02 | agent | 牛油果买回来还是硬的，该拿它怎么办？ | 拒答 | 牛油果硬的时候常温放两三天催熟 | 【资料中没有相关信息】 缺少的信息：知识库与数据库均未包含牛油果的保存或催熟建议 |
| xd04 | agent | 核桃剥了壳以后还能常温放着吗？ | 拒答 | 剥壳后必须密封冷藏，受潮会变苦 | 【资料中没有相关信息】 缺少的信息：知识库与业务库中均无关于核桃的存储方式信息 |
| xd05 | agent | 腰果拆开以后一直敞着放，会出什么问题？ | 拒答 | 敞放两周就会返软失去脆度 | 【资料中没有相关信息】 缺少的信息：知识库与数据库均未包含腰果的存储或变质相关信息 |
| xd06 | agent | 桂圆干开袋以后要注意什么？ | 拒答 | 开袋后尽快吃完，返潮容易发霉 | 【资料中没有相关信息】 缺少的信息：知识库中未包含关于「桂圆干」开袋后注意事项的任何资料 |
| xd07 | agent | 鳕鱼放到冷藏化冻以后，还有什么要求？ | 拒答 | 冷藏化冻后当天必须烹煮，否则腥味明显 | 【资料中没有相关信息】 缺少的信息：知识库与数据库中均无关于鳕鱼化冻后处理要求的资料 |
| xd08 | agent | 酸奶胀包了还能喝吗？ | 拒答 | 胀包就直接扔掉 | 【资料中没有相关信息】 缺少的信息：已收集证据仅涉及项目文档与水果产品数据，未包含酸奶胀包能否食用的食品安全信息 |
| xs01 | agent | 库里单价最高的是哪一种？它该怎么保存？ | 拒答 | 鳕鱼冷冻保存不要反复解冻 | 【资料中没有相关信息】 缺少的信息：证据中返回的“鳕鱼”不在已知 products.name 取值（橙子/苹果/香蕉/猕猴桃）范围内，无法确认其是否为库内真实数据，且缺少该名称对应的保存方式描述（description  |
| xs03 | agent | 西北山货供的那两种坚果里，单价更高的是哪种？另一种带壳的能放多久？ | 拒答 | 核桃带壳干燥常温能放半年 | 【资料中没有相关信息】证据中未提及"西北山货"这一供应商或品牌，无法确认其所供坚果的具体范围；但证据中确实只列出了两种坚果，以下基于这两条记录作答。 - **单价更高的坚果**：腰果，单价 45.0 元；核桃单价 32. |
| xc03 | agent | 它的供应商是谁？证据编号的口径又写在文档的哪一节？ | 未命中证据 | 切开吃不完需冷藏并淋柠檬汁 | 牛油果的供应商是南美果品 [1]。关于证据编号口径的说明，写在文档 README.md 的“可用工具与参数”一节中 [3]。 |
| xc01 | full | 那一步用的是哪个问题？ | 拒答 | 所有查询最终仍用原问题做 reranker 精排 | 【资料中没有相关信息】 |
| xc02 | full | 它开封以后该怎么放？ | 拒答 | 腰果开封后务必密封并冷藏 | 【资料中没有相关信息】 |
| xc03 | full | 它的供应商是谁？证据编号的口径又写在文档的哪一节？ | 拒答 | 切开吃不完需冷藏并淋柠檬汁 | 【资料中没有相关信息】 上下文未给出任何具体产品/实体及其供应商名称（"它"无明确指代，`supplier` 仅作为数据库列名和拒答样本示例出现 [1]）；同时，上下文虽在 `README.md` 中描述了引用编号与 ` |
| xd01 | full | 蓝莓收到以后该怎么处理？ | 拒答 | 收到后应尽快食用，未洗的可以冷藏存放三四天 | 【资料中没有相关信息】 |
| xd02 | full | 牛油果买回来还是硬的，该拿它怎么办？ | 拒答 | 牛油果硬的时候常温放两三天催熟 | 【资料中没有相关信息】 |
| xd03 | full | 芒果放冰箱能多存几天吗？会有什么代价？ | 拒答 | 成熟后冷藏可多放两天，但香气会变淡 | **关于"芒果放冰箱能多存几天"：** 【资料中没有相关信息】上下文未提供芒果（或任何具体水果）在冰箱中的保存天数数据。上下文仅提到，水果的保存方法（如"香蕉能不能放冰箱"）存储在业务库 `products.descri |
| xd04 | full | 核桃剥了壳以后还能常温放着吗？ | 拒答 | 剥壳后必须密封冷藏，受潮会变苦 | 【资料中没有相关信息】 |
| xd05 | full | 腰果拆开以后一直敞着放，会出什么问题？ | 拒答 | 敞放两周就会返软失去脆度 | 【资料中没有相关信息】 |
| xd06 | full | 桂圆干开袋以后要注意什么？ | 拒答 | 开袋后尽快吃完，返潮容易发霉 | 【资料中没有相关信息】 |
| xd07 | full | 鳕鱼放到冷藏化冻以后，还有什么要求？ | 拒答 | 冷藏化冻后当天必须烹煮，否则腥味明显 | 【资料中没有相关信息】 |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v8_agentic_expanded.jsonl \
  --configs agent,full \
  --agent-sources config/sources.eval.agent.v8.yaml \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --output eval/reports/v8_agentic_expanded_baseline.md \
  --json-output eval/reports/v8_agentic_expanded_baseline.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 40；分数不代表其他领域或生产流量。
- 生成与 judge 都是 `qwen3.8:27b-mlx`，存在自评偏差；分数用于同模型下的相对比较，不能当作绝对质量结论。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。
- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；这是为了命中后无需二次查询 docstore，换来更完整的上下文。
- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。
- `both_sides_hit` 只对声明了双侧取证来源（`expected_sources`）的样本有意义，其余条目恒为 `None` 不进均值；固定链路没挂运行时 SQL 源时它必然整列 NaN——双侧样本要跑得通，得用 agentic 组加 `--agent-sources`。
- 评估链路会把两条链路的生成温度钉成 0（`RAG_AGENT_ANSWER_TEMPERATURE` / `RAG_GENERATION_TEMPERATURE`）并传固定 seed，同配置重跑的指标应当一致；运行时（demo/serve）仍然不传这些参数。带思考的作答即使温度为 0 仍会让措辞抖，判定口径见 docs/ROADMAP.md 的 v8.3；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
- 评估库只入库项目文档，SQL 工具只在运行时挂上（`config/sources.eval.agent.yaml`，不参与入库）；因此 `database` 类问题在固定链路上必然拒答——这是本次要量的差值本身，不是数据泄漏或配置疏漏。
- agentic 组的 `evidence_hit` 取决于模型 SELECT 出了哪些列：只查 `name`/`price` 而没取 `description` 时，答案可以完全正确但证据判 0。该指标在 agentic 组偏保守，应结合引用与回答文本一起看。
- `evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只看前 3 条上下文，而 agentic 一次可产出 4–9 条证据块：SQL 证据常排在 `kb_search` 结果之后（本机实跑有样本的证据落在第 5 与第 7 块，回答引用正确但指标判 0），因此 `database` / `mixed` 类别的低分不能读成“证据没进上下文”。
- 拒答判定认的是生成侧的 `【资料中没有相关信息】` 哨兵文本：agentic 组在证据不足时倾向给“带引用的否定回答”（例如查库后说明 `products` 表没有该字段），本口径下算未拒答，`refusal_accuracy` 低于固定链路属于行为差异，需结合答案文本判断。
- agentic 组的延迟是整条循环的墙钟耗时（含意图路由与多次工具调用）：`avg_retrieval_ms` 记工具执行耗时之和，`avg_generation_ms` 记其余开销，两者相加才是总耗时，不能拿固定链路的同名列来比快慢。
- `avg_tool_calls` / `avg_tool_steps` 只是旁证：`multi_step` 类样本没有单独判定模型是否真的分了两次查询（单条子查询 SQL 也能一次做对）。
- 评估集支持多轮：样本可带 `history`（`[role, content]` 成对轮次），评估时原样传给 `agent.run` 的 history 参数，报告用 `history_turns` 标出哪几条走了多轮；不带该字段的样本传空列表，与单轮等价。加载时校验 role 必须 user/assistant 交替、content 非空，且 history 里不得出现 evidence 原文——那等于把答案喂进提示词。
- `both_sides_hit` 是 v7.4 新增的双侧取证读数：样本可用 `expected_sources` 声明「文档侧 + SQL 侧」，声明的每一侧都要在全量引用列表里出现才判 1；它扫全量引用、不套 `@3` 窗口，与 `source_hit` 不同口径，未声明双侧的样本不参与其均值。
- 评估运行设置 `RAG_DISABLE_AGENT_TRACE=1`，不写 `storage/traces/agent.jsonl`，避免把评估样本混进演示轨迹；因此报告里的 `trace_id` 不能逐条回放。
