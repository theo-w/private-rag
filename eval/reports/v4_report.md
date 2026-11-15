# private-rag v4 Ragas 评估报告

本报告比较同一评估集上的三组检索策略；`full` 是 v2/v3 默认链路，作为 v2→v4 的基线。所有分数都来自本地实际运行，NaN 表示 judge 失败或输出无法解析，未用 0 替代。

## 运行信息

- 生成时间：2026-11-15T13:59:57+08:00
- 评估集：`eval/project_rag_qa.jsonl`（40 条：36 可回答 + 4 应拒答）
- 数据集 SHA256：`72995df92024f54837819d8ce975d549be9a83a486bf778483edee772d46294d`
- 生成模型：`qwen3.8:27b-mlx`
- Ragas judge：`qwen2.5:7b-instruct`
- Embedding：`bge-m3`
- Ragas 配置：['dense', 'full']，指标 ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall']，strictness=3，max_workers=1
- Git commit：`a6ab3ba3db9dcca84c730051c347a765aa905bc1`
- 总耗时：3801.1 秒

## 确定性指标汇总

| 指标 | dense | hybrid | full |
|---|---:|---:|---:|
| `evidence_hit@3` | 0.583 | 0.806 | 0.917 |
| `evidence_recall@3` | 0.583 | 0.806 | 0.917 |
| `context_precision@3` | 0.204 | 0.287 | 0.324 |
| `mrr@3` | 0.426 | 0.597 | 0.782 |
| `source_hit` | 0.972 | 0.917 | 0.972 |
| `answer_rate` | 0.889 | 0.972 | 0.944 |
| `refusal_accuracy` | 1.000 | 0.750 | 1.000 |
| `citation_rate` | 0.917 | 0.972 | 0.972 |
| `citation_validity` | 0.917 | 0.972 | 0.972 |
| `avg_latency_ms` | 11077.9 | 9473.3 | 10240.0 |
| `avg_retrieved` | 3.000 | 3.000 | 3.000 |

说明：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 只统计可回答样本；`refusal_accuracy` 只统计应拒答样本；生成耗时含缓存命中（命中按 0 计），缓存命中数见 JSON。

## Ragas 指标汇总

| 指标 | dense | hybrid | full |
|---|---:|---:|---:|
| `faithfulness` | 0.883 (0.97, 35/36) | 未运行 | 0.936 (1.00, 36/36) |
| `answer_relevancy` | 0.695 (1.00, 36/36) | 未运行 | 0.724 (1.00, 36/36) |
| `context_precision` | 0.771 (1.00, 36/36) | 未运行 | 0.905 (1.00, 36/36) |
| `context_recall` | 0.838 (1.00, 36/36) | 未运行 | 0.963 (1.00, 36/36) |

括号内为有效样本覆盖率与有效数/该配置实际尝试数；配置显示“未运行”表示本次没有对它执行 Ragas。

## Ragas 缺失指标（NaN）

| 样本 | 配置 | 缺失指标 |
|---|---|---|
| q31 | dense | `faithfulness` |

这些指标没有产生有效值，平均分只基于有效样本，覆盖率见上方汇总表；具体原因请结合本次运行日志中的 judge 输出解析失败提示。

## 相对 full 基线的差值

| 指标 | dense | hybrid | full |
|---|---:|---:|---:|
| `evidence_hit@3` | -0.333 | -0.111 | 基线 |
| `evidence_recall@3` | -0.333 | -0.111 | 基线 |
| `context_precision@3` | -0.120 | -0.037 | 基线 |
| `mrr@3` | -0.356 | -0.185 | 基线 |
| `source_hit` | +0.000 | -0.056 | 基线 |
| `answer_rate` | -0.056 | +0.028 | 基线 |
| `refusal_accuracy` | +0.000 | -0.250 | 基线 |
| `citation_rate` | -0.056 | +0.000 | 基线 |
| `citation_validity` | -0.056 | +0.000 | 基线 |
| `avg_latency_ms` | +837.932 | -766.703 | 基线 |
| `ragas.faithfulness` | -0.053 | NaN | 基线 |
| `ragas.answer_relevancy` | -0.029 | NaN | 基线 |
| `ragas.context_precision` | -0.134 | NaN | 基线 |
| `ragas.context_recall` | -0.125 | NaN | 基线 |

差值按 `配置 - full` 计算；确定性指标为正表示优于基线，延迟为负表示更快。Ragas 分差只用于同一 judge/语料下的相对比较。

## 按类别：evidence_hit@3 / context_precision@3

| 类别 | dense | hybrid | full |
|---|---:|---:|---:|
| architecture | 0.750 / 0.292 | 0.875 / 0.333 | 1.000 / 0.375 |
| ingestion | 0.375 / 0.125 | 0.750 / 0.250 | 1.000 / 0.333 |
| retrieval | 0.625 / 0.208 | 0.625 / 0.208 | 0.750 / 0.250 |
| api | 0.625 / 0.208 | 0.875 / 0.333 | 0.875 / 0.333 |
| boundary | 0.500 / 0.167 | 1.000 / 0.333 | 1.000 / 0.333 |

## 失败与关注案例（最多 20 条）

| 样本 | 配置 | 问题 | 现象 | 证据 | 答案摘要 |
|---|---|---|---|---|---|
| q16 | dense | 网页来源用什么组件抽取正文和标题？ | 拒答 | trafilatura 抽取正文、标题与发布日期 | 【资料中没有相关信息】 上下文中虽然提到网页 URL 作为来源之一会被转为统一文档，并"保留 URL、标题、发布日期"、"抓取失败逐 URL 容错"[2][3]，但并未说明具体使用哪个组件（如 readability、t |
| q22 | dense | tags 过滤是要求命中全部标签还是任意一个？ | 拒答 | 命中任意一个即可 | 【资料中没有相关信息】 |
| q24 | dense | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 |
| q26 | dense | /ready 会检查哪些组件？ | 拒答 | 检查 Qdrant 集合、BM25 与 reranker | 【资料中没有相关信息】 |
| q03 | dense | 为什么同时使用向量检索和 BM25？ | 未命中证据 | 向量检索懂同义表达；BM25 抓关键词很准；两个一起用，互相补位 | 同时使用向量检索和 BM25 的原因如下： - **向量擅长语义相似**，而 **BM25 擅长专有名词、型号、精确词命中**，两者互补，能覆盖不同类型的查询需求。[1] - 两路召回后通过 **RRF（Reciproc |
| q08 | dense | CLI 和 HTTP API 如何保证问答链路一致？ | 未命中证据 | 检索、精排、生成与引用链路完全一致 | 根据上下文，CLI 和 HTTP API 通过**共享同一个问答引擎**来保证问答链路一致： - 项目中的 `qa.py` 被明确定义为"共享问答引擎（CLI/API 同一条链路）"[1]。也就是说，无论是通过 `scr |
| q09 | dense | 默认的 chunk 大小和重叠是多少？ | 未命中证据 | 每张约 700 字符，相邻重叠 100 字符 | 默认的 chunk 大小为 **700 个字符**（`CHUNK_SIZE` = 700），相邻卡片之间的重叠为 **100 个字符**（`CHUNK_OVERLAP` = 100），目的是避免一句话被切断。[1] |
| q10 | dense | 重复执行入库时，什么情况下会跳过？ | 未命中证据 | 未变化时跳过，不重复调用 embedding | 根据上下文，重复执行入库时的跳过条件如下： 当**同一个 `doc_id` 已经入库，且其内容哈希（`content_hash`）没有变化**时，系统会直接跳过，不再重复存储。[1] 具体逻辑为：系统会先查询该 `doc |
| q14 | dense | SQL 接入时如何避免数据库凭据进入日志或元数据？ | 未命中证据 | 连接串只保留主机和库名，用户名密码不会写进日志或元数据 | 根据上下文，SQL 接入时避免数据库凭据进入日志或元数据的做法如下： 1. **DSN 脱敏**：日志与元数据中的 DSN 只保留驱动、主机和库名，不包含用户名和密码 [1]。SQL 的 `source_uri` 只使用 |
| q39 | hybrid | 过去一年这个项目的月活跃用户增长曲线是怎样的？ | 应拒答但未拒答 | — | 您好！我已经接收到您提供的上下文资料（包括 `beginner-guide.md` 和 `ROADMAP.md` 的内容），随时准备为您解答。 请告诉我您想问的具体问题，我会严格根据上述带编号的上下文来回答，并在相关句子 |
| q24 | hybrid | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 |
| q03 | hybrid | 为什么同时使用向量检索和 BM25？ | 未命中证据 | 向量检索懂同义表达；BM25 抓关键词很准；两个一起用，互相补位 | 根据设计说明，同时使用向量检索和 BM25 的原因如下： - **向量检索擅长语义相似**，能够捕捉问题与文档之间的语义关联； - **BM25 擅长专有名词、型号、精确词命中**，能补足向量检索在精确匹配上的不足； - |
| q10 | hybrid | 重复执行入库时，什么情况下会跳过？ | 未命中证据 | 未变化时跳过，不重复调用 embedding | 重复执行入库时，如果**同一个 `doc_id` 已入库，且内容哈希（`content_hash`）没有变化**，系统会直接跳过，不会重复存储 [1]。 具体逻辑如下： - 入库函数 `ingest_one(docume |
| q16 | hybrid | 网页来源用什么组件抽取正文和标题？ | 未命中证据 | trafilatura 抽取正文、标题与发布日期 | 网页来源使用 **trafilatura** 组件来抽取正文和标题（以及日期）。[3] 具体流程是：先抓取 HTML，再用 trafilatura 从中提取正文和标题/日期等信息，最终与其他来源（文件、数据库）统一转换成 |
| q18 | hybrid | RRF 如何计算候选的融合分数？ | 未命中证据 | RRF：某条卡片在某个榜单排第 rank，就加 1 / (60 + rank) | 根据现有上下文，关于 RRF 的融合方式只有如下描述： - `HybridRetriever.retrieve` 同时跑向量检索和 BM25，**再用 RRF 合并**，输出融合后的候选卡片。[1] - 设计说明中指出： |
| q21 | hybrid | BM25 索引缺失时问答还能工作吗？ | 未命中证据 | 问答时缺失会自动降级为纯向量检索并提示 | 可以。根据上下文，BM25 索引缺失时问答仍能工作，系统会自动退化为纯向量检索。 具体而言，检索采用"两路召回 + RRF 合并"的机制：路线 A 是语义（向量）检索，路线 B 是关键词（BM25）检索 [1]。在代码逻 |
| q31 | hybrid | 前端看到统一错误时会包含哪些字段？ | 未命中证据 | 错误统一展示 code / message / details / request_id | 根据上下文 [1]，前端看到的统一错误格式是一个 JSON 对象，外层键为 `error`，其中包含以下四个字段： - **`code`**：错误代码，如 `session_not_found`、`invalid_req |
| q18 | full | RRF 如何计算候选的融合分数？ | 拒答 | RRF：某条卡片在某个榜单排第 rank，就加 1 / (60 + rank) | 根据上下文，RRF（Reciprocal Rank Fusion，倒数排名融合）的计算逻辑可以从以下层面理解： **基本思路（通俗类比）** RRF 合并被描述为"像两个排行榜按名次折算积分"：某张卡片在向量榜排第 1， |
| q24 | full | 默认的 reranker 使用哪个模型？ | 拒答 | BAAI/bge-reranker-v2-m3 | 【资料中没有相关信息】 |
| q31 | full | 前端看到统一错误时会包含哪些字段？ | 未命中证据 | 错误统一展示 code / message / details / request_id | 根据上下文，前端看到的**统一错误格式**是一个包含 `error` 对象的 JSON，其中包含以下字段 [2]： ```json { "error": { "code": "session_not_found", "m |

## 复现

```bash
python3 -m pip install -r requirements-eval.txt
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --configs dense,hybrid,full \
  --ragas-configs dense,full \
  --judge-model qwen2.5:7b-instruct \
  --max-workers 1 \
  --cache-dir storage/eval_cache_qwen25_7b \
  --output eval/reports/v4_report.md --json-output eval/reports/v4_report.json
```

## 限制

- 评估语料仅为项目文档，本次样本数 40；分数不代表其他领域或生产流量。
- 生成模型为 `qwen3.8:27b-mlx`，Ragas judge 为 `qwen2.5:7b-instruct`；这不是同模型自评，但不同 judge 的分数不能直接混比，分数只能在该 judge 与当前语料下作相对比较。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本；升级 Ragas 需要迁移到 collections API。
- 若只跑了 `--ragas-limit` 子集，报告中会明确标注覆盖数，不能写成全量结论。
- 生成调用未固定采样参数（`ChatOllama` 默认温度/随机种子），同一问题重跑可能得到不同答案；Ragas 磁盘缓存只在 prompt 完全一致时命中，因此重跑耗时不会归零。
