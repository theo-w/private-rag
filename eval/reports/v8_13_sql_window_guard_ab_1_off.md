# private-rag 检索策略评估报告：v8_agentic_expanded

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

> **agentic 组口径不同**：`agent` 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 `sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），但延迟与查询次数不能与固定链路当同义指标比较。

## 运行信息

- 生成时间：2026-11-26T13:09:50+08:00
- 评估集：`eval/v8_agentic_expanded.jsonl`（固定 40 条；本次运行子集 14 条：9 可回答 + 5 应拒答）
- **本次只运行子集：14 / 40 条，分数不能当作全量数据集结论。**
- 数据集 SHA256：`a99e2ca3fa7784eb9bb6c6bc853497751c60125932ff32a50a152e208d58f634`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`745540b6e6d88ed9c8b7ccd6c6772e4770a14e38`（工作区有未提交改动，读数对应的是这份混合代码）
- 跑批备注：同一进程交替对照 第1轮 sql_evidence_window=off
- 总耗时：312.8 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `agent` | agentic | Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL） | 开 | 开 | 开 | off | 3 |

## 确定性指标汇总

| 指标 | agent |
|---|---:|
| `evidence_hit@3` | 0.556 |
| `evidence_recall@3` | 0.556 |
| `context_precision@3` | 0.185 |
| `mrr@3` | 0.426 |
| `source_hit` | 1.000 |
| `both_sides_hit` | NaN |
| `answer_rate` | 1.000 |
| `refusal_accuracy` | 1.000 |
| `citation_rate` | 1.000 |
| `citation_validity` | 1.000 |
| `avg_latency_ms` | 22342.9 |
| `avg_retrieved` | 3.500 |
| `avg_context_chars` | 2828.6 |
| `avg_query_count` | 1.214 |
| `avg_tool_calls` | 1.214 |
| `avg_tool_steps` | 3.286 |
| `avg_tool_calls_knowledge` | 1.214 |
| `avg_tool_calls_database` | NaN |
| `avg_tool_calls_mixed` | NaN |
| `dual_tool_rate` | 0.000 |
| `dual_tool_rate_knowledge` | 0.000 |
| `dual_tool_rate_database` | NaN |
| `dual_tool_rate_mixed` | NaN |
| `gate_intercept_rate` | 0.000 |
| `used_parent_rate` | 1.000 |
| `used_tree_rate` | 0.071 |
| `rewrite_fallbacks` | 0 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；`avg_context_chars` 是送入 LLM 的上下文字符数，`rewrite_fallbacks` 是查询改写失败回退原问题的次数；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | agent |
|---|---:|
| `faithfulness` | 未运行 |
| `answer_relevancy` | 未运行 |
| `context_precision` | 未运行 |
| `context_recall` | 未运行 |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | agent |
|---|---:|
| knowledge | 0.556 / 0.185 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| xk02 | agent | 父块扩展和查询改写这两个开关，默认是开还是关？ | 未命中证据 | 父块默认开启、查询改写默认关闭 | 父块扩展默认是开启的，查询改写默认是关闭的 [1]。 |
| xk03 | agent | agentic 那一轮拿到工具结果以后，模型要做的是什么？ | 未命中证据 | 读完观察决定继续查还是作答 | 在 agentic 流程中，模型拿到工具返回的结果（即“观察”）后，会基于这些结果进行决策，判断是否需要继续调用其他工具或结束检索并生成最终回答；这一过程构成“检索-行动循环”，直到满足终止条件（如达到最大步数、证据字符 |
| xk06 | agent | 两路召回融合以后，什么样的卡片总分最高？ | 未命中证据 | 两边都靠前的卡片总分最高 | 两路召回融合采用 RRF 合并，名次越靠前加分越多，且若卡片在语义召回和关键词召回两路中都出现，则会加两次分，因此“语义相似度高”且“关键词匹配度高”的卡片总分最高 [1][2]。 |
| xk07 | agent | 资料里没有提到的内容，模型会自己补上去吗？ | 未命中证据 | 资料里没有的内容不会被编出来 | 不会。系统明确禁止模型使用通用常识补全证据中未提及的内容，并禁止将“资料里没提到”等同于“不存在”；当证据不足时，模型应直接返回拒答哨兵并说明缺失项，而不是自行补全答案 [1]。 |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v8_agentic_expanded.jsonl \
  --configs agent \
  --agent-sources config/sources.eval.agent.v8.yaml \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --ids xk01,xk02,xk03,xk04,xk05,xk06,xk07,xk08,xk09,xr01,xr02,xr03,xr04,xr05 \
  --output eval/reports/v8_13_sql_window_guard_ab_1_off.md \
  --json-output eval/reports/v8_13_sql_window_guard_ab_1_off.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 14；分数不代表其他领域或生产流量。
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
