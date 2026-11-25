# private-rag 检索策略评估报告：v7_agent_qa+v8_agentic_expanded+v8_agentic_edge

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

> **agentic 组口径不同**：`agent` 不是“一次检索 + 一次生成”，而是模型自己决定调用 `kb_search` 与只读 `sql_query` 的循环。同名指标含义相通（都看证据是否进上下文、引用是否指对来源），但延迟与查询次数不能与固定链路当同义指标比较。

## 运行信息

- 生成时间：2026-11-26T01:10:00+08:00
- 评估集：`eval/v7_agent_qa.jsonl,eval/v8_agentic_expanded.jsonl,eval/v8_agentic_edge.jsonl`（80 条：68 可回答 + 12 应拒答）
- 数据集 SHA256：`435165490875477b92a71120b4d1cea3ecad3974e06352f71e71d9297d82b61c`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`e0f2065f30334e0a0cfbd9e1d882cf8ac03193c9`
- 跑批备注：v8.9 合并跑 80 条（v7 29 + v8 扩充 40 + 边界 11），仅 agentic，语料为 e0f2065 的 README/docs
- 总耗时：3823.4 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `agent` | agentic | Agentic：意图路由 + 工具循环（kb_search + 运行时只读 SQL） | 开 | 开 | 开 | off | 3 |

## 确定性指标汇总

| 指标 | agent |
|---|---:|
| `evidence_hit@3` | 0.676 |
| `evidence_recall@3` | 0.676 |
| `context_precision@3` | 0.404 |
| `mrr@3` | 0.613 |
| `source_hit` | 0.956 |
| `both_sides_hit` | 0.857 |
| `answer_rate` | 0.926 |
| `refusal_accuracy` | 1.000 |
| `citation_rate` | 0.971 |
| `citation_validity` | 0.971 |
| `avg_latency_ms` | 47791.4 |
| `avg_retrieved` | 2.962 |
| `avg_context_chars` | 1703.3 |
| `avg_query_count` | 1.462 |
| `avg_tool_calls` | 1.738 |
| `avg_tool_steps` | 4.100 |
| `avg_tool_calls_knowledge` | 1.182 |
| `avg_tool_calls_database` | 1.107 |
| `avg_tool_calls_mixed` | 3.632 |
| `dual_tool_rate` | 0.250 |
| `dual_tool_rate_knowledge` | 0.030 |
| `dual_tool_rate_database` | 0.036 |
| `dual_tool_rate_mixed` | 0.947 |
| `gate_intercept_rate` | 0.212 |
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
| knowledge | 0.765 / 0.255 |
| database | 0.733 / 0.733 |
| mixed | 0.714 / 0.238 |
| multi_step | 0.545 / 0.439 |
| coreference | 0.545 / 0.364 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| xd08 | agent | 酸奶胀包了还能喝吗？ | 拒答 | 胀包就直接扔掉 | 【资料中没有相关信息】 缺少的信息：证据中未包含关于酸奶胀包后是否可饮用的食品安全判断或相关说明 |
| xe02 | agent | 那过期一天到底还能不能喝？ | 拒答 | 过期一天未拆封一般还能喝 | 【资料中没有相关信息】 缺少的信息：证据中未包含关于酸奶过期后是否可食用的食品安全判断标准或具体建议 |
| xe07 | agent | 荔枝的保质期是几天？另外精排最后留几张卡片？ | 拒答 | 选出真正最相关的 3 张 | **荔枝的保质期是几天？** 【资料中没有相关信息】已查阅全部证据，其中不包含任何关于荔枝或食品保质期的内容；证据仅涉及本系统的检索、会话、测试等技术文档，无法回答该事实。 **精排最后留几张卡片？** 精排（reran |
| xm03 | agent | 芒果的供应商是哪家？改写出来的那些查询文本会不会被写进最终提示词？ | 拒答 | 海南果园 | **芒果的供应商** 芒果的供应商是**海南果园**[1]。 **改写出来的查询文本会不会被写进最终提示词** 【资料中没有相关信息】证据只说明了查询改写的检索与排序机制——改写是"多找几条路，最后仍按原问题选"[2]， |
| xs02 | agent | 库存不足 40 的有哪些？其中需要常温催熟的那种，什么时候必须吃掉？ | 拒答 | 捏着发软就要当天吃 | **子问题 1：库存不足 40 的有哪些？** 【资料中没有相关信息】证据 [1] 所示的 `demo.products` 表仅包含 `name`、`storage`、`description` 三列，未提供库存/数量字 |
| c02 | agent | 那它的保存方式是啥？ | 未命中证据 | 表皮较厚，常温可存放一周左右 | 橙子的保存方式是常温阴凉 [1]。 |
| d02 | agent | 猕猴桃现在单价是多少，买回来应该怎么保存？ | 未命中证据 | 猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化 | 猕猴桃现在的单价是15.0元，买回来应保存在冷藏 0-4℃的环境中 [1]。 |
| d03 | agent | 把这四种水果按价格从高到低列出来，并说明各自的保存方式。 | 未命中证据 | 苹果适合冷藏保存，可以延长保鲜时间 | 按价格从高到低排列，这四种水果及其保存方式如下： 1. 猕猴桃：价格 15.0 元，保存方式为冷藏 0-4℃ [1]。 2. 橙子：价格 12.5 元，保存方式为常温阴凉 [1]。 3. 苹果：价格 8.0 元，保存方式 |
| d05 | agent | 哪种水果的维生素 C 含量比较高，它的单价是多少？ | 未命中证据 | 猕猴桃维生素 C 含量高 | 根据数据库记录，猕猴桃的维生素 C 含量较高，其单价为 15.0 元 [5]。 |
| k06 | agent | v7 的意图路由分成哪几种意图？ | 未命中证据 | 意图路由：`knowledge` / `database` / `mixed` / `none` | v7 的意图路由分成 knowledge、database、mixed、none 四种意图 [1]。 |
| m02 | agent | 橙子多少钱？这个项目的 embedding 和向量存储分别由什么负责？ | 未命中证据 | 橙子富含维生素 C，适合日常补充维 C | **橙子多少钱？** 橙子价格为 12.5 [1]。 **这个项目的 embedding 和向量存储分别由什么负责？** Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储 [2]。 |
| m03 | agent | 最便宜的水果是什么、单价多少？精排后最终给大模型几条资料？ | 未命中证据 | 香蕉不适合放冰箱，低温会让表皮变黑 | 最便宜的水果是香蕉，单价为 6.5 元 [1]；精排后最终给大模型 3 条资料 [2]。 |
| m05 | agent | 橙子的单价是多少？扫描件 PDF 入库时每页 OCR 大概多花时间？ | 未命中证据 | 橙子富含维生素 C，适合日常补充维 C | **橙子的单价：** 橙子单价为 12.5（来源：demo.products 表）[1]。 **扫描件 PDF 入库时每页 OCR 耗时：** 安装 OCR 依赖后，扫描件 PDF 每页会多花 **1–5 秒**做 OC |
| s01 | agent | 单价高于这四种水果平均价的水果有哪些？分别多少钱、该怎么保存？ | 未命中证据 | 表皮较厚，常温可存放一周左右 | 单价高于这四种水果（橙子、苹果、香蕉、猕猴桃）平均价的水果有：鳕鱼（58.0元，冷冻 -18℃）、腰果（45.0元，密封冷藏）、核桃（32.0元，密封干燥常温）、蓝莓（28.0元，冷藏 0-4℃）、桂圆干（26.0元，阴 |
| s03 | agent | 单价低于 10 元的水果有哪几种？它们分别该怎么保存？ | 未命中证据 | 苹果适合冷藏保存，可以延长保鲜时间 | 根据查询结果，单价低于10元的水果有苹果和香蕉两种。其中，苹果的保存方式是冷藏 0-4℃，香蕉的保存方式是常温阴凉 [1]。 |
| s04 | agent | 最近更新的是哪条水果记录？它的保存建议是什么？ | 未命中证据 | 成熟后应尽快食用，也可以冷藏延缓软化 | 最近更新的水果记录是猕猴桃，其保存建议为冷藏 0-4℃ [1]。 |
| xc01 | agent | 那一步用的是哪个问题？ | 未命中证据 | 所有查询最终仍用原问题做 reranker 精排 | 那一步（reranker 重排）使用的是原问题，而不是改写后的查询，以避免检索跑偏 [1]。 |
| xc03 | agent | 它的供应商是谁？证据编号的口径又写在文档的哪一节？ | 未命中证据 | 切开吃不完需冷藏并淋柠檬汁 | 牛油果的供应商是南美果品 [1]。关于证据编号口径的说明，写在文档 README.md 的“可用工具与参数”一节中 [3]。 |
| xe03 | agent | 那改写到底默认开不开？失败了会怎么办？ | 未命中证据 | 默认关闭；改写失败就退回原问题 | 查询改写默认是关闭的（off），并非默认开启[1][3]。当改写调用失败、JSON 解析失败或没有可用变体时，系统会自动退回使用原问题进行检索[1]。 |
| xk03 | agent | agentic 那一轮拿到工具结果以后，模型要做的是什么？ | 未命中证据 | 读完观察决定继续查还是作答 | 在 agentic 流程中，模型拿到工具返回的结果（即“观察”）后，会基于这些结果进行决策，判断是否需要继续调用其他工具或结束检索并生成最终回答；这一过程构成“检索-行动循环”，直到满足终止条件（如达到最大步数、证据字符 |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl,eval/v8_agentic_expanded.jsonl,eval/v8_agentic_edge.jsonl \
  --configs agent \
  --agent-sources config/sources.eval.agent.v8.yaml \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --output eval/reports/v8_9_edge_merged_baseline.md \
  --json-output eval/reports/v8_9_edge_merged_baseline.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 80；分数不代表其他领域或生产流量。
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
