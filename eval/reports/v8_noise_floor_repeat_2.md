# private-rag 检索策略评估报告：v7_agent_qa

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

> **agentic 组口径不同**：`agent` 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 `sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），但延迟与查询次数不能与固定链路当同义指标比较。

## 运行信息

- 生成时间：2026-11-23T21:41:38+08:00
- 评估集：`eval/v7_agent_qa.jsonl`（29 条：25 可回答 + 4 应拒答）
- 数据集 SHA256：`a9a7415a5c748a60b08f77d2629ab8e90b2d8b4b20e73a62640d01436803eaf5`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`241c2b350e80e46f5ddefe550bcbf0df8b20a5bf`（工作区有未提交改动，读数对应的是这份混合代码）
- 跑批备注：同配置重复轮，量噪声底：默认档（路由思考、步进关思考、最终作答 keep），评估链路已钉死 RAG_AGENT_ANSWER_TEMPERATURE=0 与 RAG_AGENT_LLM_SEED=7；同一评估库 rag_eval_v8_latency、同一模型 qwen3.8:27b-mlx、串行单并发、离线加载精排
- 总耗时：913.8 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `agent` | agentic | Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL） | 开 | 开 | 开 | off | 3 |

## 确定性指标汇总

| 指标 | agent |
|---|---:|
| `evidence_hit@3` | 0.800 |
| `evidence_recall@3` | 0.800 |
| `context_precision@3` | 0.533 |
| `mrr@3` | 0.733 |
| `source_hit` | 0.960 |
| `both_sides_hit` | 1.000 |
| `answer_rate` | 1.000 |
| `refusal_accuracy` | 1.000 |
| `citation_rate` | 1.000 |
| `citation_validity` | 1.000 |
| `avg_latency_ms` | 31507.6 |
| `avg_retrieved` | 2.759 |
| `avg_context_chars` | 1455.8 |
| `avg_query_count` | 1.552 |
| `avg_tool_calls` | 1.897 |
| `avg_tool_steps` | 4.414 |
| `avg_tool_calls_knowledge` | 1.000 |
| `avg_tool_calls_database` | 1.100 |
| `avg_tool_calls_mixed` | 3.778 |
| `dual_tool_rate` | 0.310 |
| `dual_tool_rate_knowledge` | 0.000 |
| `dual_tool_rate_database` | 0.000 |
| `dual_tool_rate_mixed` | 1.000 |
| `gate_intercept_rate` | 0.310 |
| `used_parent_rate` | 1.000 |
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
| knowledge | 0.857 / 0.286 |
| database | 1.000 / 0.889 |
| mixed | 0.200 / 0.067 |
| multi_step | 1.000 / 1.000 |
| coreference | 1.000 / 0.556 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| k06 | agent | v7 的意图路由分成哪几种意图？ | 未命中证据 | 意图路由：`knowledge` / `database` / `mixed` / `none` | v7 的意图路由分成 knowledge、database、mixed、none 四种意图 [1]。 |
| m02 | agent | 橙子多少钱？这个项目的 embedding 和向量存储分别由什么负责？ | 未命中证据 | 橙子富含维生素 C，适合日常补充维 C | **橙子多少钱？** 橙子价格为 12.5 [1]。 **这个项目的 embedding 和向量存储分别由什么负责？** Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储 [2]。 |
| m03 | agent | 最便宜的水果是什么、单价多少？精排后最终给大模型几条资料？ | 未命中证据 | 香蕉不适合放冰箱，低温会让表皮变黑 | 最便宜的水果是香蕉，单价为 6.5 元 [1]；精排后最终给大模型 3 条资料 [2]。 |
| m04 | agent | 苹果适合怎么保存？v7 的 agentic 问答支持哪几种意图？ | 未命中证据 | 苹果适合冷藏保存，可以延长保鲜时间 | **苹果适合怎么保存？** 苹果适合冷藏保存，可以延长保鲜时间；切开后容易氧化，建议尽快食用。[4] **v7 的 agentic 问答支持哪几种意图？** 意图分成 `knowledge` / `database` / |
| m05 | agent | 橙子的单价是多少？扫描件 PDF 入库时每页 OCR 大概多花时间？ | 未命中证据 | 橙子富含维生素 C，适合日常补充维 C | **橙子的单价：** 橙子单价为 12.5（来源：demo.products 表）[1]。 **扫描件 PDF 入库时每页 OCR 耗时：** 安装 OCR 依赖后，扫描件 PDF 每页会多花 **1–5 秒**做 OC |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl \
  --configs agent \
  --agent-sources config/sources.eval.agent.yaml \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --output eval/reports/v8_noise_floor_repeat_2.md \
  --json-output eval/reports/v8_noise_floor_repeat_2.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 29；分数不代表其他领域或生产流量。
- 生成与 judge 都是 `qwen3.8:27b-mlx`，存在自评偏差；分数用于同模型下的相对比较，不能当作绝对质量结论。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 多查询/HyDE 配置每次提问会额外调用一次本地 LLM，延迟与显存占用高于 `full`，默认 API 配置保持 `query_rewrite=off`。
- 父块文本随每个子块存入 Qdrant/BM25，索引体积会明显增大；这是为了命中后无需二次查询 docstore，换来更完整的上下文。
- v4 的 40 条固定评估集仍用 `validate_distribution` 校验；v5 口语化数据集规模较小，只做结构/证据校验，不能与 v4 分数直接混比。
- `both_sides_hit` 只对声明了双侧取证来源（`expected_sources`）的样本有意义，其余条目恒为 `None` 不进均值；固定链路没挂运行时 SQL 源时它必然整列 NaN——双侧样本要跑得通，得用 agentic 组加 `--agent-sources`。
- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
- 评估库只入库项目文档，SQL 工具只在运行时挂上（`config/sources.eval.agent.yaml`，不参与入库）；因此 `database` 类问题在固定链路上必然拒答——这是本次要量的差值本身，不是数据泄漏或配置疏漏。
- agentic 组的 `evidence_hit` 取决于模型 SELECT 出了哪些列：只查 `name`/`price` 而没取 `description` 时，答案可以完全正确但证据判 0。该指标在 agentic 组偏保守，应结合引用与回答文本一起看。
- `evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只看前 3 条上下文，而 agentic 一次可产出 4–9 条证据块：SQL 证据常排在 `kb_search` 结果之后（本机实跑有样本的证据落在第 5 与第 7 块，回答引用正确但指标判 0），因此 `database` / `mixed` 类别的低分不能读成“证据没进上下文”。
- 拒答判定认的是生成侧的 `【资料中没有相关信息】` 哨兵文本：agentic 组在证据不足时倾向给“带引用的否定回答”（例如查库后说明 `products` 表没有该字段），本口径下算未拒答，`refusal_accuracy` 低于固定链路属于行为差异，需结合答案文本判断。
- agentic 组的延迟是整条循环的墙钟耗时（含意图路由与多次工具调用）：`avg_retrieval_ms` 记工具执行耗时之和，`avg_generation_ms` 记其余开销，两者相加才是总耗时，不能拿固定链路的同名列来比快慢。
- `avg_tool_calls` / `avg_tool_steps` 只是旁证：`multi_step` 类样本没有单独判定模型是否真的分了两次查询（单条子查询 SQL 也能一次做对）。
- 评估集支持多轮：样本可带 `history`（`[role, content]` 成对轮次），评估时原样传给 `agent.run` 的 history 参数，报告用 `history_turns` 标出哪几条走了多轮；不带该字段的样本传空列表，与单轮等价。加载时校验 role 必须 user/assistant 交替、content 非空，且 history 里不得出现 evidence 原文——那等于把答案喂进提示词。
- `both_sides_hit` 是 v7.4 新增的双侧取证读数：样本可用 `expected_sources` 声明「文档侧 + SQL 侧」，声明的每一侧都要在全量引用列表里出现才判 1；它扫全量引用、不套 `@3` 窗口，与 `source_hit` 不同口径，未声明双侧的样本不参与其均值。
- 评估运行设置 `RAG_DISABLE_AGENT_TRACE=1`，不写 `storage/traces/agent.jsonl`，避免把评估样本混进演示轨迹；因此报告里的 `trace_id` 不能逐条回放。
