# private-rag 检索策略评估报告：v7_agent_qa

本报告比较同一评估集上的多组检索策略；`full` 是 v4 基线（子块上下文、不查询改写），`parent` / `parent_multi` / `parent_hyde` 依次增加父块上下文、多查询和 HyDE。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

## 运行信息

- 生成时间：2026-11-24T11:33:55+08:00
- 评估集：`eval/v7_agent_qa.jsonl`（29 条：25 可回答 + 4 应拒答）
- 数据集 SHA256：`a9a7415a5c748a60b08f77d2629ab8e90b2d8b4b20e73a62640d01436803eaf5`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen3.8:27b-mlx`
- Embedding：`bge-m3`
- Ragas 配置：[]，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`d6ee9611921753328976df5d09ab9c7ac7a96c49`（工作区有未提交改动，读数对应的是这份混合代码）
- 跑批备注：固定链路同配置重复轮 2，量固定链路噪声底：默认档、评估链路已钉死两条链路的生成温度与 seed、同一评估库 rag_eval_v8_latency、qwen3.8:27b-mlx、串行单并发、离线加载精排
- 总耗时：640.0 秒

## 策略配置

| 配置 | 链路 | 说明 | BM25 | 精排 | 父块 | 查询改写 | top_k |
|---|---|---|---|---|---|---|---:|
| `full` | 固定 | 混合召回 + 精排（v4 基线） | 开 | 开 | 关 | off | 3 |

## 确定性指标汇总

| 指标 | full |
|---|---:|
| `evidence_hit@3` | 0.240 |
| `evidence_recall@3` | 0.240 |
| `context_precision@3` | 0.080 |
| `mrr@3` | 0.220 |
| `source_hit` | 0.320 |
| `both_sides_hit` | 0.000 |
| `answer_rate` | 0.440 |
| `refusal_accuracy` | 1.000 |
| `citation_rate` | 0.920 |
| `citation_validity` | 0.920 |
| `avg_latency_ms` | 22068.8 |
| `avg_retrieved` | 3.000 |
| `avg_context_chars` | 1335.7 |
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
| knowledge | 0.714 / 0.238 |
| database | 0.000 / 0.000 |
| mixed | 0.000 / 0.000 |
| multi_step | 0.000 / 0.000 |
| coreference | 0.333 / 0.111 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| d01 | full | 香蕉到底能不能放冰箱保存？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | 【资料中没有相关信息】 上下文中多次提到"香蕉能不能放冰箱"这一问法，但仅将其作为**路由测试用例**来讨论——指出保存建议实际存储在 `products.description` 字段中，而模型曾错误地将其判为 `kn |
| d02 | full | 猕猴桃现在单价是多少，买回来应该怎么保存？ | 拒答 | 猕猴桃维生素 C 含量高，成熟后应尽快食用，也可以冷藏延缓软化 | 【资料中没有相关信息】 上下文中未包含猕猴桃的单价数据，也未包含猕猴桃的保存方法。上下文仅提及了橙子单价（12.5 元）[1] 以及"香蕉能不能放冰箱"作为路由误判的示例（保存方法实际存储在 `products.desc |
| d03 | full | 把这四种水果按价格从高到低列出来，并说明各自的保存方式。 | 拒答 | 苹果适合冷藏保存，可以延长保鲜时间 | 【资料中没有相关信息】 |
| d04 | full | 橙子常温下大概能放多久？ | 拒答 | 表皮较厚，常温可存放一周左右 | 【资料中没有相关信息】 |
| d05 | full | 哪种水果的维生素 C 含量比较高，它的单价是多少？ | 拒答 | 猕猴桃维生素 C 含量高 | 【资料中没有相关信息】 上下文 [1] 仅提到"维 C 对比"是 agentic 组通过 `demo.products#sql_query` 能给出正确数据的样本类型之一 [1]，但并未列出具体哪种水果维生素 C 含量最 |
| d06 | full | 苹果切开后为什么建议尽快吃完？ | 拒答 | 切开后容易氧化，建议尽快食用 | 【资料中没有相关信息】 上下文 [1] 中虽提及"苹果氧化"一词，但仅作为 v7.1 报告中模型在证据不足时"硬凑"答案的举例（"d06 补了苹果氧化的通用知识"），并未给出苹果切开后建议尽快吃完的具体原因或解释。其余上 |
| k05 | full | 会话历史能当事实依据吗？会话数据存在哪里？ | 拒答 | 但提示词明确规定历史不能当事实依据；会话在内存里，服务重启就清空 | **会话历史能否当事实依据？** 不能。根据上下文，对话历史**仅用于理解当前问题的指代，不能作为事实依据** [1]。这一约束在多处规则中均有体现： - 固定链路 `generation.py:97` 的完整措辞为：「 |
| m01 | full | 香蕉能不能放冰箱？另外这个项目的引用列表是从哪儿来的？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑、口感变差 | **关于"香蕉能不能放冰箱"：** 上下文中没有直接给出香蕉能否放冰箱的结论。资料仅说明：保存方法这类信息实际存储在 `products.description` 字段中，但"香蕉能不能放冰箱"这种问法容易被路由误判为生 |
| m03 | full | 最便宜的水果是什么、单价多少？精排后最终给大模型几条资料？ | 拒答 | 香蕉不适合放冰箱，低温会让表皮变黑 | **关于"最便宜的水果是什么、单价多少"：** 【资料中没有相关信息】 **关于"精排后最终给大模型几条资料"：** 精排（reranker）后最终只给大模型 **3 条**资料。[1] 具体而言，配置项 `TOP_K_ |
| m04 | full | 苹果适合怎么保存？v7 的 agentic 问答支持哪几种意图？ | 拒答 | 苹果适合冷藏保存，可以延长保鲜时间 | **关于"苹果适合怎么保存"：** 【资料中没有相关信息】。所提供的上下文均为 private-rag 项目的技术文档，未涉及苹果保存方法。 --- **关于"v7 的 agentic 问答支持哪几种意图"：** 上下文 |
| s01 | full | 单价高于这四种水果平均价的水果有哪些？分别多少钱、该怎么保存？ | 拒答 | 表皮较厚，常温可存放一周左右 | 【资料中没有相关信息】 上下文仅提到"比平均价再筛"是 agentic 组在 15 条业务库样本中能够正确引用 `demo.products#sql_query` 给出正确数据的一类测试场景 [1]，但并未列出具体是哪些 |
| s02 | full | 按价格看最贵的是哪种水果？它的维生素 C 情况怎么样？ | 拒答 | 猕猴桃维生素 C 含量高 | 【资料中没有相关信息】 上下文仅提及"价格排序""维 C 对比"等作为测试样本类型的描述 [1]，但并未给出任何具体水果的单价数据或维生素 C 含量数据，无法回答"最贵的是哪种水果"及其维生素 C 情况。 |
| s03 | full | 单价低于 10 元的水果有哪几种？它们分别该怎么保存？ | 拒答 | 苹果适合冷藏保存，可以延长保鲜时间 | 【资料中没有相关信息】 上下文仅提到测试中涉及"单价""价格排序"等查询场景，以及 `products.description` 字段存储保存建议、`demo.products#sql_query` 工具可检索业务数据  |
| s04 | full | 最近更新的是哪条水果记录？它的保存建议是什么？ | 拒答 | 成熟后应尽快食用，也可以冷藏延缓软化 | 【资料中没有相关信息】 上下文仅涉及系统开发文档（ROADMAP、测试样本 c02/c03 等），其中虽提到"橙子当前单价 12.5 元，记录更新时间是 2026-01-10"等测试用例片段 [2]，以及 c03 中"刚 |
| c02 | full | 那它的保存方式是啥？ | 未命中证据 | 表皮较厚，常温可存放一周左右 | 根据上下文，**会话的保存方式**如下： - 会话保存在**进程内存**中，默认 TTL 为 30 分钟，最多 500 个会话，每个会话最多 60 条消息；**服务重启即清空**。[1] 如果需要多 worker 或横向 |
| c03 | full | 那这个项目的引用列表是从哪儿来的？还有刚才那条记录的更新时间是什么时候？ | 只取到一侧证据 | 引用不是模型编的；系统直接把这些元数据整理成引用列表 | **关于引用列表从哪儿来：** 这个项目的引用列表来自检索到的资料本身。具体流程是：系统先把用户的文件、网页、数据库整理成"小卡片"（快速查找）和"大卡片"（完整段落）两层；提问时先用两套目录找出候选小卡片，精读筛出最相 |
| k06 | full | v7 的意图路由分成哪几种意图？ | 未命中证据 | 意图路由：`knowledge` / `database` / `mixed` / `none` | v7 的意图路由分成 **四类**：`knowledge`、`database`、`mixed`、`none`。[1] 其中 `none` 表示与知识库无关，直接按既有约定返回拒答文本，不做任何检索。[1] |
| m02 | full | 橙子多少钱？这个项目的 embedding 和向量存储分别由什么负责？ | 只取到一侧证据 | 橙子富含维生素 C，适合日常补充维 C | **关于"橙子多少钱"：** 上下文中仅在测试样本的描述里提到过一句模拟数据——c02 的历史里写着"橙子当前单价 12.5 元，记录更新时间是 2026-01-10"[3]，但这是 v7.4 版本用于验证"历史是否会被 |
| m05 | full | 橙子的单价是多少？扫描件 PDF 入库时每页 OCR 大概多花时间？ | 只取到一侧证据 | 橙子富含维生素 C，适合日常补充维 C | **橙子的单价：** 根据上下文中的测试样本记录，橙子当前单价为 **12.5 元**（记录更新时间 2026-01-10）[2]。 **扫描件 PDF 每页 OCR 耗时：** 安装 OCR 依赖后，扫描件 PDF 每 |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl \
  --configs full \
  --skip-ragas \
  --judge-model qwen3.8:27b-mlx \
  --max-workers 1 \
  --cache-dir storage/eval_cache \
  --output eval/reports/v8_noise_floor_classic_2.md \
  --json-output eval/reports/v8_noise_floor_classic_2.json
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
- 评估链路会把两条链路的生成温度钉成 0（`RAG_AGENT_ANSWER_TEMPERATURE` / `RAG_GENERATION_TEMPERATURE`）并传固定 seed，同配置重跑的指标应当一致；运行时（demo/serve）仍然不传这些参数。带思考的作答即使温度为 0 仍会让措辞抖，判定口径见 docs/ROADMAP.md 的 v8.3；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
