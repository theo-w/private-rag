# v4 / v5 评估集与报告

本目录保存 Ragas 自动评估所用的固定标注集和报告产物。评估知识库只由项目自身文档构建（`README.md`、`docs/*.md`），与 `data/docs` 的演示库互相隔离。

## 文件

| 文件 | 说明 |
|---|---|
| `project_rag_qa.jsonl` | v4 40 条固定标注问答（36 条可回答 + 4 条应拒答） |
| `v5_paraphrase_qa.jsonl` | v5 18 条口语化/复杂问题（15 条可回答 + 3 条应拒答） |
| `reports/v4_report.md` | v4 全量评估生成的 Markdown 报告 |
| `reports/v4_report.json` | v4 同一次运行的机器可读结果 |
| `reports/v5_report.md` | v5 主报告：新数据集、4 组配置、Ragas 子集 |
| `reports/v5_report.json` | v5 主报告的机器可读结果 |
| `reports/v5_v4_regression.md` | v5 配置在 v4 40 条集上的回归报告 |
| `reports/v5_v4_regression.json` | 同一次回归的机器可读结果 |

`storage/eval/`（Qdrant 与 BM25）和 `storage/eval_cache/`（Ragas 判分缓存）由脚本生成，已被 `.gitignore` 忽略，不提交。

## 字段

| 字段 | 说明 |
|---|---|
| `id` | 唯一编号；v4 为 `q01`–`q40`，v5 为 `v01`–`v18` |
| `question` | 用户问题，直接送入检索与生成 |
| `reference` | 标准答案，用于 Ragas 的 context recall / precision；拒答样本为空 |
| `evidence` | 来源文档中的短原文片段，用于确定性命中与覆盖率；拒答样本为空 |
| `source` | 期望来源文件（`README.md` / `docs/ROADMAP.md` / `docs/beginner-guide.md`）；拒答样本为空 |
| `category` | `architecture` / `ingestion` / `retrieval` / `api` / `boundary` / `refusal` |
| `answerable` | 是否应回答；`false` 表示应输出 `【资料中没有相关信息】` |

`evidence` 必须能在 `source` 文件中**逐字找到**（校验时忽略空白、标点、大小写）。这样文档改动导致标注失效时，加载数据集会直接报错，而不是悄悄产生错误分数。v4 固定集额外校验 40 条/类别分布；v5 集只有 18 条，不足以做严格分布约束，因此只做结构与证据逐字校验，运行时会打印“跳过分布校验”。

## 标注规则

1. 类别分布固定为：架构与流程 8、入库与多源 8、检索与精排 8、API/会话/流式 8、边界与安全 4、应拒答 4。
2. 可回答样本的答案必须能在来源文档中找到，不能依赖模型自身知识。
3. `evidence` 尽量短且唯一，通常是一句话或一个关键短语；同一题可给多条，用于计算 evidence recall。
4. 拒答样本的问题要“看起来合理但文档确实没有”，且不能与任何来源段落产生直接答案。
5. 修改 `README.md` 或 `docs/*.md` 后，先跑 `python scripts/evaluate.py --validate-only`；若有 evidence 失效，修正数据集后再重建评估库。
6. v5 集覆盖口语化、多跳、降级与边界问题；每个类别 3 条，另有 3 条应拒答。新增样本时保持与 v4 相同的字段和证据规则。

## 指标

确定性指标（不依赖 Ragas，所有配置都计算）：

| 指标 | 含义 |
|---|---|
| `evidence_hit@k` | top-k context 中是否至少命中一条 evidence |
| `evidence_recall@k` | 所有 evidence 片段被 top-k context 覆盖的比例 |
| `context_precision@k` | top-k context 中含 evidence 的片段占比 |
| `mrr@k` | 第一个含 evidence 的 context 的倒数排名 |
| `source_hit` | 期望来源文件是否出现在 context 的 `source_uri` |
| `refusal_accuracy` | 应拒答样本中正确拒答的比例 |
| `answer_rate` | 可回答样本中未拒答的比例 |
| `citation_rate` | 答案是否带 `[n]` 引用 |
| `citation_validity` | 答案中的编号是否都落在实际 citations 范围内；反引号/代码块中的示例、“带 [1][2][3] 编号”这类格式说明，以及 citations 不足以支撑的连续 1..N 编号示例不计入 |
| `avg_latency_ms` | 单条样本的平均检索 + 生成耗时 |
| `avg_context_chars` | 送入 LLM 的上下文字符数均值（父块扩展后） |
| `avg_query_count` | 每条样本实际检索的查询数均值 |
| `used_parent_rate` | 实际使用父块上下文的样本比例 |
| `rewrite_fallbacks` | 改写失败、回退原问题的样本数；报告会单列这些样本与原因 |

Ragas 指标（仅对可回答样本）：

- `faithfulness`：答案中的陈述是否都能由检索上下文支持；
- `answer_relevancy`：答案与问题的相关程度；
- `context_precision`：检索上下文中与参考答案相关的内容占比；
- `context_recall`：参考答案中的信息被检索上下文覆盖的比例。

NaN 表示 judge 调用失败或输出无法解析；报告会同时显示每个指标的有效样本数，不会用 0 掩盖失败。

本机 judge 固定为单 worker，并限制 `num_predict=4096`、关闭流式。原因：本地 27B MLX runner 在无输出上限时会持续生成直到上下文耗尽，表现为连接断开或 JSON 截断；`OLLAMA_NUM_PARALLEL=1` 下并发只会排队，不能加速。单条样本四项指标实测约 6–7 分钟，36 条 × 2 配置的全量判分预计数小时，建议后台运行；时间不够时用 `--ragas-limit` 跑子集，报告会标注样本数。

## 检索策略（v4 三组 + v5 四组）

| 配置 | BM25 | reranker | 父块 | 查询改写 | 用途 |
|---|---|---|---|---|---|
| `dense` | 否 | 否 | 否 | `off` | 纯向量召回，观察关键词信号的价值 |
| `hybrid` | 是 | 否 | 否 | `off` | 向量 + BM25 + RRF，观察融合效果 |
| `full` | 是 | 是 | 否 | `off` | v4 基线；v5 中显式关闭父块，保持同口径 |
| `parent` | 是 | 是 | 是 | `off` | 单独观察父块上下文 |
| `parent_multi` | 是 | 是 | 是 | `multi` | 父块 + 多查询改写 |
| `parent_hyde` | 是 | 是 | 是 | `hyde` | 父块 + HyDE |

报告中的差值均相对 `full` 计算；`full` 即 v2→v4 对比中的 v2 基线，在 v5 报告中仍作为对照。v5 配置只改变父块/改写，不改变 top20 → RRF top10 → rerank top3 的召回与精排链路。

## 结果摘要（2026-11-15）

两份正式报告均使用 `qwen3.8:27b-mlx` 生成与判分，`full` 为 v4 基线；Ragas 每个配置只跑 12 条可回答样本。

- v5 口语化集（18 条）：`parent_multi` / `parent_hyde` 把 `evidence_hit@3` 从 0.800 提到 0.867，`evidence_recall@3` 从 0.767 提到 0.867，`context_precision@3` 从 0.311 提到 0.389 / 0.378，`mrr@3` 从 0.522 提到 0.567；平均延迟从 13.7 秒升到 23.6 / 23.2 秒。Ragas `parent_multi` 的 faithfulness 0.938→0.958、context_recall 0.833→0.875，answer_relevancy 0.720→0.706，context_precision 持平；faithfulness 覆盖率从 9/12 降到 8/12。
- v4 回归（40 条）：`parent_hyde` 的确定性指标最好，`evidence_hit@3` 0.861→0.917、`context_precision@3` 0.315→0.380、`mrr@3` 0.727→0.773；`parent_multi` 把 `answer_rate` 从 0.944 提到 0.972。平均延迟从 13.5 秒升到 26.1 / 26.5 秒。Ragas `parent_multi` 的 faithfulness 0.884→0.945（覆盖率 11/12→10/12），answer_relevancy 0.760→0.753，context_precision / context_recall 持平。
- 失败与口径：v09、q07、q24 在四组配置下仍误拒答；v04、v08、q16、q21 的答案语义基本正确，但 evidence 逐字匹配未命中，属于确定性指标的口径限制。q07 暴露固定父块边界问题：父块正好结束在“这里有五个关键角色：”，角色列表落在下一个父块；q24 的模型全名只在 README 安装说明的一行出现，检索没有召回。
- 查询改写回退：v5 的 v05/parent_hyde 一次、v4 回归的 q27/parent_multi 与 parent_hyde 两次，原因都是 `ValueError: 模型输出中没有 JSON 对象`；回退后仍按原问题检索与生成。
- 结论：父块带来小幅但一致的检索增益，多查询/HyDE 在部分样本上再加一点；代价是上下文长度约 2.5–3 倍、延迟约 +5–13 秒。当前默认保持“父块开、查询改写关”；`parent_hyde` 只作为可选实验配置，暂不作为默认。
- 解读限制：18 / 40 条样本、Ragas 12 条子集且生成与 judge 同模型；NaN 表示 judge 输出无法解析，平均分只基于有效样本，不能把子集结果写成全量结论。

## 重建与运行

```bash
# 1. 安装评估依赖（Ragas + judge 依赖）
python3 -m pip install -r requirements-eval.txt

# 2. 重建评估知识库（独立路径，不影响演示库）
python3 scripts/prepare_eval_kb.py --recreate

# 3. 只校验数据集（v4 / v5）
python3 scripts/evaluate.py --validate-only
python3 scripts/evaluate.py --dataset eval/v5_paraphrase_qa.jsonl --validate-only

# 4. 冒烟（先确认 judge 能返回合法 JSON）
python3 scripts/evaluate.py --configs full --limit 3 --ragas-configs full --ragas-limit 2 \
  --output /tmp/v4_smoke.md --json-output /tmp/v4_smoke.json
python3 scripts/evaluate.py --dataset eval/v5_paraphrase_qa.jsonl \
  --configs full,parent,parent_multi --limit 3 \
  --ragas-configs parent_multi --ragas-limit 2 \
  --output /tmp/v5_smoke.md --json-output /tmp/v5_smoke.json

# 5. v4 全量
python3 scripts/evaluate.py --configs dense,hybrid,full --ragas-configs dense,full \
  --output eval/reports/v4_report.md --json-output eval/reports/v4_report.json

# 6. v5 主报告（新数据集，4 组配置；Ragas 只跑 12 条子集）
python3 scripts/evaluate.py --dataset eval/v5_paraphrase_qa.jsonl \
  --configs full,parent,parent_multi,parent_hyde \
  --ragas-configs full,parent_multi --ragas-limit 12 \
  --output eval/reports/v5_report.md --json-output eval/reports/v5_report.json

# 7. v5 配置在 v4 40 条集上的回归
python3 scripts/evaluate.py --configs full,parent,parent_multi,parent_hyde \
  --ragas-configs full,parent_multi --ragas-limit 12 \
  --output eval/reports/v5_v4_regression.md --json-output eval/reports/v5_v4_regression.json

# 可选：用官方 7B judge（更快；先 ollama pull qwen2.5:7b-instruct）
python3 scripts/evaluate.py --configs dense,hybrid,full --ragas-configs dense,full \
  --judge-model qwen2.5:7b-instruct --cache-dir storage/eval_cache_qwen25_7b \
  --output eval/reports/v4_report.md --json-output eval/reports/v4_report.json
```

评估库的语料就是仓库文档，因此**先定稿文档和数据集，再重建评估库并跑最终报告**。报告生成后如果又改了 README/docs，需要重跑 `prepare_eval_kb.py --recreate` 和评估。v5 主报告与 v4 回归共用同一个评估 Qdrant 路径，必须**串行运行**，否则会争抢本地锁。

## 在演示页查看

`/demo` 右侧的“v4 评估”面板读取 `GET /api/v1/eval/summary`，展示三组配置的检索指标与 Ragas 指标。接口只返回汇总数字和模型名，不返回逐样本上下文、答案正文或本机绝对路径；报告缺失或损坏时返回 `available: false`，演示页会提示先生成报告，不影响问答服务。v5 四组对比保存在独立报告里，不覆盖 v4 基线，也不接入该面板。

## 局限

- v4 只有 40 条、v5 只有 18 条样本，且语料是项目文档，分数不代表其他领域或生产流量。
- v5 的 4 组配置差值是描述性结果，样本少且未做显著性检验；某一组没有增益时会如实呈现，不把假设写成结论。
- 父块会放大索引体积（约 2–3 倍），多查询/HyDE 会多一次本地 LLM 调用与多次检索，延迟相应增加。
- 生成与判分默认使用同一个本地模型 `qwen3.8:27b-mlx`，存在自评偏差；分数主要用于同一模型下的相对比较。用 `--judge-model` 换 judge 后（例如官方 `qwen2.5:7b-instruct`），分数只能在该 judge 下比较，不能与 27B 的分数直接混比。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本并屏蔽弃用警告。
- Ragas 判分调用本地 27B 模型较慢；7B judge 更快但能力较弱。可用 `--ragas-limit` 跑子集，但报告中会标注样本数，不能把子集结果写成全量结论。换 judge 后应关闭缓存或使用独立缓存目录。
