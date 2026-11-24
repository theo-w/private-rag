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
| `v6_scan_table.jsonl` | v6 扫描件版面表格消融评估集（5 可回答 + 1 应拒答，`ingestion`/`multi_step`/`refusal`） |
| `fixtures/v6_scan_table.pdf` | 合成扫描件表格 PDF（`scripts/make_scan_table_fixture.py` 生成，入库进评估库） |
| `fixtures/v6_scan_table.md` | 上表的文字底稿，仅作 evidence 逐字校验，**不入库** |
| `reports/v6_scan_table_on.md` / `.json` | 版面表格**开启**侧的消融报告（独立集合 `rag_scan_table_on`） |
| `reports/v6_scan_table_off.md` / `.json` | 版面表格**关闭**侧的消融报告（独立集合 `rag_scan_table_off`） |

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

# 8. v6 版面表格消融：ON/OFF 两个独立评估库（串行跑，避免本地 Ollama 争抢）
python3 scripts/make_scan_table_fixture.py   # 生成 eval/fixtures/v6_scan_table.pdf
RAG_COLLECTION_NAME=rag_scan_table_on RAG_STORAGE_DIR=storage/eval/qdrant_scan_on \
  RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_scan_on/bm25.pkl \
  python3 scripts/prepare_eval_kb.py --config config/sources.eval.scan.yaml --recreate
RAG_DISABLE_SCAN_TABLE=1 RAG_COLLECTION_NAME=rag_scan_table_off RAG_STORAGE_DIR=storage/eval/qdrant_scan_off \
  RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_scan_off/bm25.pkl \
  python3 scripts/prepare_eval_kb.py --config config/sources.eval.scan.yaml --recreate
RAG_COLLECTION_NAME=rag_scan_table_on RAG_STORAGE_DIR=storage/eval/qdrant_scan_on \
  RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_scan_on/bm25.pkl \
  python3 scripts/evaluate.py --dataset eval/v6_scan_table.jsonl --configs full --skip-ragas \
  --output eval/reports/v6_scan_table_on.md --json-output eval/reports/v6_scan_table_on.json
RAG_COLLECTION_NAME=rag_scan_table_off RAG_STORAGE_DIR=storage/eval/qdrant_scan_off \
  RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_scan_off/bm25.pkl \
  python3 scripts/evaluate.py --dataset eval/v6_scan_table.jsonl --configs full --skip-ragas \
  --output eval/reports/v6_scan_table_off.md --json-output eval/reports/v6_scan_table_off.json

# 可选：用官方 7B judge（更快；先 ollama pull qwen2.5:7b-instruct）
python3 scripts/evaluate.py --configs dense,hybrid,full --ragas-configs dense,full \
  --judge-model qwen2.5:7b-instruct --cache-dir storage/eval_cache_qwen25_7b \
  --output eval/reports/v4_report.md --json-output eval/reports/v4_report.json

# 9. v8 解析基准 A/B：自建扫描件解析栈 vs docling / MinerU（整页替换后端）
#    第三方解析器要 Python >=3.10/3.11，装不进本仓库钉死的 3.9 → 独立虚拟环境，见 requirements-parse-bench.txt
uv venv --python /opt/homebrew/bin/python3.11 .venv-parse
uv pip install --python .venv-parse/bin/python -r requirements-parse-bench.txt
# MinerU 4.2.3 的模型要预下载（驱动走 mineru-kit parse，不启文档库服务、不用 --remote）
.venv-parse/bin/mineru-kit models download --tier standard -s modelscope
python3 scripts/make_parse_bench_fixture.py    # 四页纯图片 PDF + 文字底稿 + golden JSON
python3 scripts/parse_bench.py --preflight --backends docling,mineru   # 验 venv/库/模型/驱动输出，失败早停
python3 scripts/parse_bench.py --backends selfbuilt,docling,mineru \
  --output eval/reports/v8_parse_baseline.md --json-output eval/reports/v8_parse_baseline.json
# 三侧各建一个评估库再各跑一遍（串行）。开关只在入库时生效：检索与作答读的是已入库的块
for backend in selfbuilt docling mineru; do
  RAG_SCAN_PARSER=$backend RAG_COLLECTION_NAME=rag_parse_bench_$backend \
    RAG_STORAGE_DIR=storage/eval/qdrant_parse_bench_$backend \
    RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_parse_bench_$backend/bm25.pkl \
    python3 scripts/prepare_eval_kb.py --config config/sources.eval.parse.yaml --recreate
  RAG_COLLECTION_NAME=rag_parse_bench_$backend \
    RAG_STORAGE_DIR=storage/eval/qdrant_parse_bench_$backend \
    RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_parse_bench_$backend/bm25.pkl \
    python3 scripts/evaluate.py --dataset eval/v8_parse_bench.jsonl --configs full --skip-ragas \
    --run-note "解析后端 $backend" \
    --output eval/reports/v8_parse_ab_bench_$backend.md --json-output eval/reports/v8_parse_ab_bench_$backend.json
done
# v6 那 6 条在同三侧下复现一遍（语料是 v6 fixture，所以是另外三个库）
for backend in selfbuilt docling mineru; do
  RAG_SCAN_PARSER=$backend RAG_COLLECTION_NAME=rag_parse_scan_$backend \
    RAG_STORAGE_DIR=storage/eval/qdrant_parse_scan_$backend \
    RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_parse_scan_$backend/bm25.pkl \
    python3 scripts/prepare_eval_kb.py --config config/sources.eval.scan.yaml --recreate
  RAG_COLLECTION_NAME=rag_parse_scan_$backend \
    RAG_STORAGE_DIR=storage/eval/qdrant_parse_scan_$backend \
    RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_parse_scan_$backend/bm25.pkl \
    python3 scripts/evaluate.py --dataset eval/v6_scan_table.jsonl --configs full --skip-ragas \
    --run-note "解析后端 $backend" \
    --output eval/reports/v8_parse_ab_scan_$backend.md --json-output eval/reports/v8_parse_ab_scan_$backend.json
done
```

评估库的语料就是仓库文档，因此**先定稿文档和数据集，再重建评估库并跑最终报告**。报告生成后如果又改了 README/docs，需要重跑 `prepare_eval_kb.py --recreate` 和评估。v5 主报告与 v4 回归共用同一个评估 Qdrant 路径，必须**串行运行**，否则会争抢本地锁。

## 在演示页查看

`/demo` 右侧的“v4 评估”面板读取 `GET /api/v1/eval/summary`，展示三组配置的检索指标与 Ragas 指标。接口只返回汇总数字和模型名，不返回逐样本上下文、答案正文或本机绝对路径；报告缺失或损坏时返回 `available: false`，演示页会提示先生成报告，不影响问答服务。v5 四组对比保存在独立报告里，不覆盖 v4 基线，也不接入该面板。

## 局限

- v4 只有 40 条、v5 只有 18 条样本，且语料是项目文档，分数不代表其他领域或生产流量。
- v6 消融集只有 6 条、语料是一张合成扫描表格，`evidence` 采用「整行连续」短语（ON 侧表格文档为逗号拼接的行文本，OFF 侧正文是 OCR 乱序），量的是「版面表格提取有没有把单元格结构送进检索」这一件事；`source_hit` 两侧恒为 0——证据底稿 `fixtures/v6_scan_table.md` 按设计不入库，检索命中的是 PDF 文档本身。
- v5 的 4 组配置差值是描述性结果，样本少且未做显著性检验；某一组没有增益时会如实呈现，不把假设写成结论。
- 父块会放大索引体积（约 2–3 倍），多查询/HyDE 会多一次本地 LLM 调用与多次检索，延迟相应增加。
- 生成与判分默认使用同一个本地模型 `qwen3.8:27b-mlx`，存在自评偏差；分数主要用于同一模型下的相对比较。用 `--judge-model` 换 judge 后（例如官方 `qwen2.5:7b-instruct`），分数只能在该 judge 下比较，不能与 27B 的分数直接混比。
- Ragas 0.4.4 的 `evaluate` 与 LangChain wrapper 属于 legacy API，已固定版本并屏蔽弃用警告。
- Ragas 判分调用本地 27B 模型较慢；7B judge 更快但能力较弱。可用 `--ragas-limit` 跑子集，但报告中会标注样本数，不能把子集结果写成全量结论。换 judge 后应关闭缓存或使用独立缓存目录。

## v7 agentic 评估集（`v7_agent_qa.jsonl`）

v4/v5 的评估集量的是“固定检索链路”的质量；v7 这份量的是**加了 agentic 循环以后到底多出了什么能力**，
所以按“回答这个问题需要哪个工具”分类，而不是按文档章节分类：

| 类别 | 条数 | 需要 | 固定链路的表现 |
|---|---:|---|---|
| `knowledge` | 7 | 只查项目文档 | 应两组的差值接近 0 |
| `database` | 6 | 只查运行时 SQL（`products` 表） | 必然拒答：评估库不入库业务数据 |
| `mixed` | 5 | 文档 + SQL 各出一段证据 | 只能答出一半 |
| `multi_step` | 4 | 先拿结果再决定下一步（比平均价、排序后取保存说明等） | 无法作答 |
| `coreference` | 3 | 先靠对话历史还原指代，再决定查哪一侧（v7.3 新增） | 指代还原不了：固定链路压根不接 history 参数（只调 `retrieve`/`generate_answer`），c02/c03 的库侧必然查不到，c01 只能靠字面蒙 |
| `refusal` | 4 | 文档和库里都没有 | 两组都应拒答 |

共 29 条：25 可回答 + 4 应拒答。evidence 仍做逐字校验，出处为
`README.md` / `docs/beginner-guide.md` / `docs/ROADMAP.md` / `scripts/init_demo_db.py`。

### `expected_source`（本数据集新增的可选字段）

SQL 样本的证据权威文件与“引用应该指向谁”不是同一个东西：业务数据的原文逐字写在
`scripts/init_demo_db.py` 的种子数组里（用它做逐字校验），而 agentic 的引用是
`demo.products#sql_query`。因此这类样本写：

```json
{"source": "scripts/init_demo_db.py", "expected_source": "demo.products", "...": "..."}
```

`source` 只管证据校验，`source_hit` 改按 `expected_source` 判定；比较时会忽略 `#` 之后的片段
（SQL 入库文档是 `label#主键`，运行时查询引用是 `label#sql_query`，来源身份都是 `label`）。
省略该字段时行为与 v4/v5 完全一致。

### `expected_sources`（v7.4 新增的可选字段，配 `both_sides_hit` 读数）

`expected_source` 只能标一个来源，于是混合问题「到底查了几侧」在指标里完全隐形：
c03 只查了文档、SQL 那一半直接抄对话历史，`source_hit` 与 `evidence_hit@3` 却都是 1.000
（`docs/v7.4-history-as-evidence.md` 把这条列为第三层成因）。样本可以额外声明双侧来源：

```json
{"expected_source": "demo.products", "expected_sources": ["demo.products", "docs/beginner-guide.md"]}
```

新读数 `both_sides_hit` 按声明判定：**声明的每一侧都要在引用列表里出现**才记 1.0，否则 0.0。
口径与 `source_hit` 有三处刻意的差别：

- 它**扫全量引用列表，不套 `@3` 窗口**。`source_hit` 只看前 3 条引用，混合问题常把 SQL 侧排到
  第 4 条（v7.3 报告的 m04 即如此），那是已知的窗口口径限制，不该让新读数一上手就带上。
- **只统计声明了 ≥2 侧的样本**（本轮 6 条：m01–m05 + c03）。其余样本该键为 `None`，
  `_mean` 自动跳过，所以汇总值就是「6 条双侧样本上的命中率」，不会被 23 条单侧样本稀释。
- 允许出现声明之外的来源（顺带引了 `docs/ROADMAP.md` 不算错），语义是「该取的两侧都取到了」。

加载时校验：必须是字符串数组、每项非空、去重保序（同一侧写两遍不算两侧）；
应拒答样本该字段必须为空。`expected_source` 与样本的 `evidence` 一字未改，
所以 `source_hit` / `evidence_hit@3` / `citation_rate` 的逐条取值与 v7、v7.2、v7.3 三轮仍可比。
只引到一侧的双侧样本会进报告的「失败与关注案例」，现象写「只取到一侧证据」——
它 `evidence_hit@3` 可能是满分，不进这张表就仍然看不见。

### `history`（v7.3 新增的可选字段）

指代消解要有真实的前置轮次才量得出来——之前 26 条里 0 条带历史，所以「覆盖指代」只是文档上的说法。
样本可以带 `history`，每项是一个 `[role, content]`：

```json
{"id": "c02", "question": "那它的保存方式是啥？",
 "history": [["user", "橙子现在多少钱？"],
             ["assistant", "橙子当前单价 12.5 元，记录更新时间是 2026-01-10。"]],
 "...": "..."}
```

加载时的硬校验，不通过就是 `DatasetError` 而不是警告：项数必须是偶数、role 严格 `user`/`assistant`
交替、content 非空、每项必须是二元组；可回答样本的 history 里还不得出现该样本 `evidence` 的原文——
那等于把答案喂进提示词，指标就不干净了。

运行时 history 原样传给 `agent.run(..., history=...)`：`run` / `run_stream` 早就支持历史，路由器、
步进决策、最终作答三处提示词也都留着 `{history_section}`，缺的只是评估侧没人传。报告用 `history_turns`
标出每条走了几轮（`samples_meta[i]` 与逐条 `samples[i]` 都有；固定链路没有这个概念，保持 `—`）。
省略该字段的样本传空列表，与单轮等价，v7.3 之前那 26 条的行为一字未变。

### 为什么评估库不入库业务数据

评估库（`config/sources.eval.yaml`）刻意只入库项目文档，因此 `database` 类问题在固定链路上**必然**查不到——
这正是本次要量的差值本身，不是数据泄漏。agentic 组仍要能真的读到业务库，所以 SQL 工具由一份
**只供运行时、不参与入库**的配置单独挂上：`config/sources.eval.agent.yaml`
（`allow_runtime_query: true`，`runtime_tables: [products]`）。它由 `evaluate.py` 传给
`build_agent_runtime(sources_path=...)`；`data/sql/demo.db` 不存在时直接报错退出，不会出一份“看起来能跑”的报告。

### 跑法

```bash
python3 scripts/init_demo_db.py
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --validate-only
# agentic vs v4 基线（报告差值表以 full 为基线）
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent,full \
  --output eval/reports/v7_agent_report.md --json-output eval/reports/v7_agent_report.json
# 只看“会检索但不让查库”的能力上限
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent --no-agent-sql --skip-ragas
```

`--agent-max-steps`（默认 `AGENT_MAX_STEPS`）与 `--agent-sources` 可覆盖循环边界与运行时数据源。

### agentic 组的指标口径与已知短板

- **耗时**：整条循环的墙钟时间。`avg_retrieval_ms` 记工具执行耗时之和，`avg_generation_ms` 记其余开销
  （意图路由 + 最终作答），两者相加才是总耗时；不要拿固定链路的同名列比快慢。
- **`avg_tool_calls` / `avg_tool_steps`**：只有 agentic 组有值，固定链路为 `—`（不是 0）。
- **`evidence_hit` 偏保守**：取决于模型 SELECT 了哪些列——只取 `name`/`price` 而没取 `description` 时，
  答案可以完全正确但证据判 0。
- **路由受问法影响**：像“香蕉能不能放冰箱”这种读起来像生活常识的问题，决策模型会判成 `knowledge`
  而去查文档（保存方法其实在 `products.description` 里）；措辞带“价格/表/记录”的问题才会走 SQL。
  这是本数据集量出来的真实短板，不是评估脚本的问题。（v7.2 已按“库内数据可得性”重做路由，见本文末小节）
- **多轮**：样本可带 `history`（见上面小节），agentic 组会真的收到这段历史；不带该字段的样本仍是单轮，固定链路则始终不读历史。
- 评估运行设置 `RAG_DISABLE_AGENT_TRACE=1`，不写 `storage/traces/agent.jsonl`，
  所以报告里的 `trace_id` 不能在演示轨迹里回放。
- **`@3` 窗口截断**：`evidence_*@3`、`context_precision@3`、`mrr@3`、`source_hit` 都只看前 3 条上下文，
  而 agentic 一次可以产出 4–9 条证据块。SQL 证据常排在 `kb_search` 结果之后（本次 d03 落在第 5 块、
  d05 落在第 7 块，回答里引用编号正确），因此这两条在 `evidence_hit@3` 上判 0——是窗口口径，不是没查到。
- **拒答判定只看哨兵文本**：`is_refusal()` 判断答案里是否含生成侧的 `【资料中没有相关信息】`。
  agentic 组在证据不足时倾向给“带引用的否定回答”，本口径下算未拒答，因此 `refusal_accuracy` 反而低于固定链路。
  下表的 0.250 记录的是 v7.1 的行为；v7.2 起判定证据不足会直接以哨兵文本作答，本口径下 `refusal_accuracy` 复跑为 1.000。

### 结果摘要（2026-11-19，26 条 × `agent`/`full`，确定性指标，Ragas 关闭）

> 全量 26 条这次没跑 Ragas，只另外跑了 6 条子集，见本文最后一小节。

单条平均耗时 agentic 71.2 秒（工具执行 2.4 秒 + 路由与作答 68.8 秒）、`full` 17.0 秒；
总耗时 2291.6 秒，`git_commit = a6e2bd8`，报告见 `eval/reports/v7_agent_report.md`。

| 指标 | agent | full | 差值 |
|---|---:|---:|---:|
| `answer_rate` | 0.909 | 0.318 | +0.591 |
| `source_hit` | 0.591 | 0.318 | +0.273 |
| `citation_rate` / `citation_validity` | 0.864 | 0.591 | +0.273 |
| `evidence_hit@3` | 0.409 | 0.318 | +0.091 |
| `context_precision@3` | 0.197 | 0.106 | +0.091 |
| `mrr@3` | 0.348 | 0.295 | +0.053 |
| `refusal_accuracy` | 0.250 | 1.000 | -0.750 |
| `avg_context_chars` | 1676 | 1278 | +398 |
| `avg_tool_calls` / `avg_tool_steps` | 1.50 / 3.73 | — | — |

分类别 `evidence_hit@3`：`knowledge` 1.000 / 1.000（回归检查通过，agentic 没把纯文档问题做坏）、
`database` 0.000 / 0.000、`mixed` 0.000 / 0.000、`multi_step` 0.500 / 0.000。
`database`/`mixed` 两组都是 0：固定链路本来就查不到业务数据，agentic 则是被上面的 `@3` 窗口与
SELECT 列口径吃掉，所以这两类的收益要看 `answer_rate` 与引用，而不是 `evidence_hit@3`。

按证据是否真的进了上下文（绕开 `@3` 窗口重算）：agentic 22 条可回答样本里 11 条上下文含逐字证据
（9 条落在前 3 条内、2 条落在第 4 条以后），`full` 只有 7 条。

- **收益是真的**：15 条 DB 类样本里 agentic 有 9 条真的引用了 `demo.products#sql_query` 并给出正确数据
  （价格、排序、维 C 对比、比平均价）；`full` 组 26 条样本一条都没接触过业务数据，隔离前提成立。
  `knowledge` 类 7 条两组逐项持平（`evidence_hit`、`source_hit`、`citation_*` 全同），说明工具循环没有引入回归。
- **短板也是真的**：6 条 DB 类样本没查到库（d01/d04/d06/m01/m04/s01），其中 5 条被路由判成 `knowledge`、
  1 条判成 `none`；d06 与 r04 在没有知识库依据的情况下补了通用知识（苹果氧化），属于该拒答却作答。
- **代价明确**：单条耗时约 4.2 倍（71.2 秒 vs 17.0 秒），其中大部分是多次决策调用的模型开销，
  工具本身只占 2.4 秒。

### Ragas 子集（2026-11-19，26 条里的 6 条可回答样本 × `agent`/`full`，`git_commit = 9147bd3`）

样本 `k01`/`d01`/`d03`/`m03`/`s02`/`s04`，judge 与生成模型同为 `qwen3.8:27b-mlx`，
`strictness=3`、`max_workers=1`、总耗时 3965.1 秒，报告见 `eval/reports/v7_agent_ragas_report.md`。
括号内是「有效样本数 / 该配置实际尝试数」，平均分只基于有效样本。

| 指标 | agent | full |
|---|---:|---:|
| `faithfulness` | 0.659（6/6） | 0.600（5/6） |
| `answer_relevancy` | 0.853（5/6） | 0.138（5/6） |
| `context_precision` | 0.492（6/6） | 0.306（6/6） |
| `context_recall` | 0.750（6/6） | 0.250（6/6） |

- **只是子集**：6 条 / 26 条，且这 6 条都是可回答样本（应拒答的 4 条一条都没进来），
  `refusal_accuracy` 无法在此评估；报告头与运行信息里都印了「不能当作全量结论」。
- **`full` 的低分主要复述「没作答」**：它在这 6 条里拒答了 5 条（子集 `answer_rate` 0.167），
  空答会让 `answer_relevancy` 与 `context_recall` 一起塌下去，不能读成同等条件下的答案质量差值。
  两边都能作答时，可读的是 `faithfulness` 0.659 vs 0.600：agentic 多带了 SQL 证据但没有更容易编造。
- **judge 稳定性**：跑动中有 3 个评测任务被 `RemoteProtocolError`（服务端断连）打断，
  对应 `m03`/`full` 的 `faithfulness`+`answer_relevancy` 与 `d01`/`agent` 的 `answer_relevancy`
  记为 NaN 并单列在报告的缺失指标小节，`ragas_errors` 为空。重跑一次即可补齐，缓存会复用有效结果。
- **子集的确定性指标与全量那份不可混读**：本 6 条上 agent `answer_rate` 1.000 / `source_hit` 0.667 /
  `evidence_hit@3` 0.333，full 0.167 / 0.167 / 0.167；单条平均耗时 agent 71.9 秒、`full` 17.2 秒，
  与全量 26 条的方向一致。

## v7.2 / v7.3 路由、拒答与指代消解复跑（2026-11-19 起）

v7.1 报告里那 6 条没查到库的样本（d01/d04/d06/m01/m04/s01）与 3 条该拒答却作答的样本
（r01/r02/r04）单独重跑，命令与产物：

```bash
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
  --agent-sources config/sources.eval.agent.yaml \
  --ids d01,d04,d06,m01,m04,s01,r01,r02,r04 --skip-ragas --skip-distribution --max-workers 1 \
  --output eval/reports/v7_2_routing_report.md --json-output eval/reports/v7_2_routing_report.json
```

`--ids` 只跑指定样本（与 `--limit` 互斥，同时给会报错）；`--output` / `--json-output` 必须显式给，
否则默认写到 `eval/reports/v4_report.*`。类别分布校验只作用于默认的 v4 数据集（`--skip-distribution`
也只对它生效），跑这份 v7 子集时加不加都一样，命令里留着它只是换数据集时不必改参数。

结果（9 条子集，模型 `qwen3.8:27b-mlx`，与 v7.1 全量同一生成模型，口径可比）：

| 样本 | 问题 | 意图（旧→新） | 结果 |
|---|---|---|---|
| d01 | 香蕉到底能不能放冰箱保存？ | `knowledge`→`database` | 引用 `demo.products#sql_query`，给出「不适合放冰箱」 |
| d04 | 橙子常温下大概能放多久？ | `knowledge`→`database` | 引用业务库，「常温一周左右」 |
| d06 | 苹果切开后为什么建议尽快吃完？ | `knowledge`→`database` | 引用业务库 `description`，不再补通用氧化知识 |
| m01 | 香蕉能不能放冰箱 + 引用列表从哪来 | `knowledge`→`mixed` | 库里那一半答对并引用；文档那一半仍答「查不到」 |
| m04 | 苹果怎么保存 + 支持哪几种意图 | `knowledge`→`mixed` | 库 + 文档两处证据（4 条引用） |
| s01 | 单价高于平均价的水果有哪些 | `none`→`database` | 算出橙子 12.5 / 猕猴桃，并带保存方式 |
| r01 | 橙子的产地和供应商联系方式 | `knowledge`→`none` | 哨兵文本拒答 |
| r02 | 这个项目有 iOS 客户端吗 | `knowledge`→`none` | 哨兵文本拒答 |
| r04 | private-rag 商业版的定价 | `knowledge`→`knowledge` | `insufficient_evidence`，哨兵文本 + 缺少的信息 |

子集均值（同样本前后对比，**不能**与上表 26 条的整体值直接比）：`evidence_hit@3` 0.000→1.000、
`source_hit` 0.000→1.000、`context_precision@3` 0.000→0.889、`citation_rate`/`citation_validity`
0.500→1.000；单条平均耗时 51.4 秒。

一条要如实记下的口径问题：r01 与 r02 是靠 `none` 路由拒答的，也就是根本没检索——r01（库里没有的列）
判 `none` 还算合理，r02「这个项目有 iOS 客户端吗」本应先查文档再依据证据拒答。结果对但拒答的理由不对，
9 条里只有 r04 走的是证据不足判定（`insufficient_evidence`）。评估集本身没有为这次改动调整，报告按原口径出。

再记一条口径限制：写进工具描述的样例值来自 `scripts/init_demo_db.py` 的种子数据，而这份评估集的
`evidence` 也从同一份种子逐字校验——所以 d01/d04/d06 的改判里，有一部分等于「把标注答案搬到路由器眼前」。
真实业务库的样例值不会刚好是用户要问的那项事实，因此这 9 条的提升幅度在本数据集上偏乐观：能确定的是
机制本身（让路由器看见库里存着什么）有效，不是提升的具体数字。
`none` 判据偏松的问题同日已经收紧，机制与复跑见下一节。另外 m01 的文档那一半仍然没查出来——模型把「这个项目」当成缺少对话上下文，而评估按单轮执行、本来就没覆盖指代消解（这一条 v7.3 已补：提示词写死消解规则，评估集补上带 `history` 的样本，见本文 v7.3 小节）。

### 全量 26 条复跑（2026-11-19，v7.2）

```bash
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
  --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
  --output eval/reports/v7_2_agent_report.md --json-output eval/reports/v7_2_agent_report.json
```

同一份评估集、同一生成模型（`qwen3.8:27b-mlx`）、同一确定性指标口径，只改代码不改数据；总耗时 1348.6 秒。

| 指标 | v7.1 `agent` | v7.2 `agent` | 差值 |
|---|---:|---:|---:|
| `answer_rate` | 0.909 | 1.000 | +0.091 |
| `refusal_accuracy` | 0.250 | 1.000 | +0.750 |
| `source_hit` | 0.591 | 1.000 | +0.409 |
| `evidence_hit@3` / `evidence_recall@3` | 0.409 | 0.864 | +0.455 |
| `context_precision@3` | 0.197 | 0.621 | +0.424 |
| `mrr@3` | 0.348 | 0.811 | +0.462 |
| `citation_rate` / `citation_validity` | 0.864 | 0.955 | +0.091 |
| `avg_context_chars` | 1676 | 1358 | -318 |
| `avg_tool_calls` / `avg_tool_steps` | 1.50 / 3.73 | 1.31 / 3.38 | -0.19 / -0.35 |
| 单条平均耗时 | 71.2 秒 | 51.9 秒 | -19.3 秒 |

回归检查（这一轮真正要看的部分）：`knowledge` 类 7 条的 `source_hit` 与 `evidence_hit@3` 两轮均为 1.000，
且这 7 条都没有引用业务库。分类别 `source_hit`：`database` 0.167→1.000、`mixed` 0.400→1.000、
`multi_step` 0.750→1.000；15 条业务库类样本引用 `demo.products` 的从 9 条升到 15 条。

`stop_reason` 分布：v7.1 `final`18 / `no_retrieval`2 / `planner_unavailable`6 → v7.2 `final`21 /
`insufficient_evidence`2 / `no_retrieval`1 / `context_budget`1 / `planner_unavailable`1（JSON 解析失败
变少只作观察，不归因）。4 条应拒答样本都以哨兵文本开头，但 r03/r04 是 `insufficient_evidence`、
r02 是 `context_budget`（12 段证据后用尽字符预算）、r01 是 `none`（未检索）。

两点读数限制：`mixed` 类 `evidence_hit@3` 仍只有 0.400，原因还是上面写的 `@3` 窗口与 SELECT 列口径；
模型有随机性，9 条子集那次与全量这轮的逐条路由不完全一致（r02 一次判 `none`、一次判 `knowledge`），
所以逐条路径不要当稳定行为引用，整体指标才是可比的。

### `none` 判据收紧复跑（2026-11-20，`v7.2-none-gate`）

只改了一处机制加一段提示词：路由判成 `none` 时不再直接拒答，而是先用原问题做一次知识库验证检索——查到证据就照常作答、并把上报意图改写成 `knowledge`，查不到才返回拒答哨兵、`stop_reason` 记 `no_evidence`（与压根没检索的 `no_retrieval` 区分开）；提示词把 `none` 的准入收紧到「明确在本系统之外」的实时信息、天气、新闻、外部网站内容、主观偏好。同一份评估集、同一生成模型（`qwen3.8:27b-mlx`）、同一确定性指标口径，只改代码不改数据，26 条 `n_errors` 0。

```bash
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
  --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
  --output eval/reports/v7_2_nonegate_report.md --json-output eval/reports/v7_2_nonegate_report.json
```

| 指标 | v7.2 | none 收紧后 | 差值 |
|---|---:|---:|---:|
| `answer_rate` | 1.000 | 0.955 | -0.045 |
| `refusal_accuracy` | 1.000 | 1.000 | 0 |
| `source_hit` | 1.000 | 0.955 | -0.045 |
| `evidence_hit@3` / `evidence_recall@3` | 0.864 | 0.818 | -0.045 |
| `context_precision@3` | 0.621 | 0.576 | -0.045 |
| `mrr@3` | 0.811 | 0.765 | -0.046 |
| `citation_rate` / `citation_validity` | 0.955 | 1.000 | +0.045 |
| `avg_context_chars` | 1358 | 1432 | +74 |
| `avg_tool_calls` / `avg_tool_steps` | 1.31 / 3.38 | 1.42 / 3.62 | +0.11 / +0.23 |
| 检索均值 / 生成均值 | 2.4 秒 / 49.4 秒 | 1.9 秒 / 60.9 秒 | -0.5 / +11.5 秒 |
| 单条平均耗时 | 51.9 秒 | 62.8 秒 | +10.9 秒 |

这一轮真正要验收的是路径而不是分数，26 条里 20 条逐条完全没变，变了 6 条：

| 样本 | 意图 | `stop_reason` | 工具调用 | 是否拒答 |
|---|---|---|---|---|
| r01 橙子的产地和供应商联系方式 | `none`→`knowledge` | `no_retrieval`→`insufficient_evidence` | 0→2 | 仍拒答，但改为有据拒答 |
| m03 最便宜的水果 + 精排给几条 | `mixed` | `final`→`planner_unavailable` | 2 | 否→是（本轮唯一退化） |
| m01 香蕉能不能放冰箱 + 本项目引用列表从哪儿来 | `database`→`mixed` | `final` | 1→2 | 否（两半都答对） |
| d02 | `mixed`→`database` | `final` | 1 | 否 |
| k02 | `knowledge` | `final`→`planner_unavailable` | 1 | 否，答案与基线一致 |
| r03 | `mixed` | `insufficient_evidence`→`planner_unavailable` | 1→2 | 仍拒答 |

分布：意图 `knowledge`9 / `database`10 / `mixed`6 / `none`1 → `knowledge`10 / `database`10 / `mixed`6 / `none`0；`stop_reason` `final`21 / `no_retrieval`1 / `insufficient_evidence`2 / `context_budget`1 / `planner_unavailable`1 → `final`19 / `insufficient_evidence`2 / `context_budget`1 / `planner_unavailable`4。改动要达到的效果到位了：`none` 与 `no_retrieval` 双双归零，也就是「一次都没检索就拒答」这条路径在全量评估里不再出现，4 条应拒答样本仍全部以哨兵文本开头。r01 从「判 `none`、零检索、闷头拒答」变成「查了两次、按缺口拒答并写明缺什么」（回答里点出 `demo.products` 只有六列、不含产地与供应商字段）。

四条读数限制，引用这轮数字时一并带上：

- 整体指标的回落逐条核下来是两条样本各背一项，不是普遍退化：检索类五项（`evidence_hit@3`/`evidence_recall@3`/`context_precision@3`/`mrr@3`/`source_hit`）的 -0.045 全部来自 m01 一条（它这五项各自 1.000→0.000），`answer_rate` 的 -0.045 来自 m03 由不拒答变成拒答（可答样本 22 条，1/22≈0.045）。d05 的 `citation_rate` 0.000→1.000 是反向变化，也正是 `citation_rate` 0.955→1.000 的全部来源。
- m01 那五个 0 是 `@3` 窗口口径判的，不是答错。这一轮它两半都答对（`mixed`、2 次工具调用，引用 `docs/beginner-guide.md`、`README.md` 与 `demo.products`），但 SQL 证据排在第 4 块，而 `evaluation.py:486` 只取 `contexts[:3]`、`source_hit` 只取 `source_uris[:3]`；基线那轮它只检索到 1 块证据、恰好就是 SQL 那一块，所以五项全 1.000。混合问题答两侧时证据块更多，后一侧常掉出前 3——答得更全反而被判 0。
- m03 判的是 `mixed`，`none` 探测在这条上不会触发；它的退化发生在 JSON 解析失败那次运行里，SQL 只回了一条记录（香蕉 6.5）后模型按 v7.2 的充分性判定拒答「最便宜」这一半。与本轮改动没有因果路径，但也不把「无关」写成结论。
- `planner_unavailable` 1→4 只作观察记录，不作归因（模型随机性与提示词改动同时在里面）；其中 k02 解析失败仍给出了与基线一致的答案，说明解析失败本身不必然导致退化。
- 耗时上升同样含生成侧随机性（检索均值反而从 2.4 秒降到 1.9 秒），机制上的成本只有「真正无关的问题多一次 `kb_search`」，全局体现为平均工具调用 1.31→1.42。`no_evidence` 这个新增取值由单测覆盖，这一轮 26 条没有触发（r01 的验证检索有命中）。

### 指代消解复跑（2026-11-20，`v7.3-coreference`）

这一轮要量的不是分数是指代能不能还原：三处提示词（`ROUTER_PROMPT` / `STEP_PROMPT` / `FINAL_PROMPT`）写死
「『这个项目』『本系统』一律指本系统自身，它的说明文档就在知识库里」「没有对话历史绝不是选 `none` 的理由」，
步进决策另加一条——指代词在历史里找不到先行词时按本系统自身的行为与文档理解，不得以「缺少上下文」为由停止取证。
评估侧补上真正的多轮：样本可带 `history`（见上面小节），`collect_agent_results` 把它传给
`agent.run(..., history=...)`，报告用 `history_turns` 标出哪几条走了多轮；评估集从 26 条加到 29 条（3 条 `coreference`）。
同一生成模型（`qwen3.8:27b-mlx`）、同一确定性指标口径，`n_errors` 0。

```bash
# 评估库要用本分支的文档重建（评估语料就是 README + docs/*.md，见下面限制最后一条）
python3 scripts/init_demo_db.py && python3 scripts/prepare_eval_kb.py --recreate

# Qdrant 本地模式独占 storage/eval/qdrant_storage，29 条无法并发，分 4 批顺序执行、每批独立落盘
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
  --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
  --ids k01,k02,k03,k04,k05,k06,k07 \
  --output /tmp/v73_batch1.md --json-output /tmp/v73_batch1.json
# 其余三批只换 --ids 与输出名：d01,d02,d03,d04,d05,d06,m01 / m02,m03,m04,m05,s01,s02,s03 / s04,c01,c02,c03,r01,r02,r03,r04
```

四批落盘后用一次性脚本重建 `SampleResult` 再调 `aggregate_results` / `build_report` 重算聚合（脚本未入库，
报告 `meta.run_note` 记了这件事），产物 `eval/reports/v7_3_coreference_report.md` / `.json`。之所以这样切：
`evaluate.py` 只在进程结束时写报告，中途被打断什么都留不下——上一轮就是这么丢的。

| 指标 | none 收紧后（26 条） | v7.3 全量（29 条） | v7.3 同 26 条子集 | 子集差 |
|---|---:|---:|---:|---:|
| `answer_rate` | 0.955 | 0.960 | 0.955 | 0 |
| `refusal_accuracy` | 1.000 | 1.000 | 1.000 | 0 |
| `source_hit` | 0.955 | 0.960 | 0.955 | 0 |
| `evidence_hit@3` / `evidence_recall@3` | 0.818 | 0.800 | 0.773 | -0.045 |
| `context_precision@3` | 0.576 | 0.560 | 0.561 | -0.015 |
| `mrr@3` | 0.765 | 0.747 | 0.742 | -0.023 |
| `citation_rate` / `citation_validity` | 1.000 | 0.960 | 0.955 | -0.045 |
| `avg_context_chars` | 1432 | 1696 | 1800 | +368 |
| `avg_tool_calls` / `avg_tool_steps` | 1.42 / 3.62 | 1.38 / 3.55 | 1.42 / 3.58 | 0 / -0.04 |
| 检索均值 / 生成均值 | 1.9 秒 / 60.9 秒 | 2.1 秒 / 87.7 秒 | 2.1 秒 / 87.0 秒 | +0.2 / +26.0 秒 |
| 单条平均耗时 | 62.8 秒 | 89.8 秒 | 89.1 秒 | +26.3 秒 |
| 可回答 / 应拒答 / 错误 | 22 / 4 / 0 | 25 / 4 / 0 | 22 / 4 / 0 | — |

三条新样本是本轮的验收对象，全都还原成功了：

| 样本 | 指代 | 意图 | `stop_reason` | 实际取证 | 读数 |
|---|---|---|---|---|---|
| c01 | 「那一步」←上一轮的精排 | `knowledge` | `planner_unavailable` | 1 次 `kb_search` | `evidence_hit@3` 1.000、`mrr@3` 0.333、`citation_rate` 1.000；答案正确（精排始终用原问题是「避免检索跑偏」，引 [2][3]） |
| c02 | 「它」←上一轮的橙子 | `database` | `final` | 1 次 `sql_query` | 检索五项全 1.000，答案「表皮较厚，常温可存放一周左右」[1] 指向 `demo.products#sql_query` |
| c03 | 「这个项目」+「刚才那条记录」 | `mixed` | `final` | 1 次 `kb_search`（没回查 SQL） | `evidence_hit@3` 1.000（该样本 `expected_source` 只标文档侧）；引用列表那一半正确，时间戳那一半抄的历史 |

同 26 条子集 22 条逐条没变，变了 4 条，四项指标各自的变化都能落到具体一条上：

| 样本 | 变化 | 机制 |
|---|---|---|
| m01 | 检索五项 0.000→1.000（含 `source_hit`） | 上一轮它 SQL 证据排第 4 被 `@3` 判 0，这轮 SQL 证据排到第 1 块，五项一起回来 |
| m04 | `source_hit` 与检索三项 1.000→0.000 | 同一条 `@3` 口径反过来：它的 SQL 证据（苹果那条 `description`）这轮排在第 4 块，回答里 `[4]` 引用正确、两半都答对 |
| k06 | `evidence_hit@3`/`mrr@3`/`context_precision@3` →0.000 | gold 块（`docs/ROADMAP.md` 的「## v7 详细验收（本分支）」小节）本身一字未改，用 agent 组同参（`top_k=3`、混合召回+精排+父块）复探，它排第 4；`top_k=8` 时能召回，`top_k=3` 就被窗口切掉。答案仍正确且引了 ROADMAP |
| d05 | `citation_rate`/`citation_validity` 1.000→0.000 | 回答被截断在 18 个字（「根据知识库记录，猕猴桃的描述明确提到」），没吐引用标记；`generate_final_answer` 没设 `num_predict`，走 Ollama 服务端默认输出预算，思考型模型的推理 token 会吃掉它——基线那轮同一条也只有 91 字、同样在半句处断 |

分布：意图 26 条子集 `knowledge`10 / `database`10 / `mixed`6（与基线同分布），加 3 条新样本后 `knowledge`11 / `database`11 / `mixed`7；
`stop_reason` 子集 `final`19 / `insufficient_evidence`3 / `planner_unavailable`3 / `context_budget`1（基线 `final`19 / `planner_unavailable`4 /
`insufficient_evidence`2 / `context_budget`1），全量 `final`21 / `planner_unavailable`4 / `insufficient_evidence`3 / `context_budget`1。
4 条应拒答样本仍全部以哨兵文本开头（r01/r02/r03 走 `insufficient_evidence`，r04 走 `context_budget`），
`history_turns` 只有 c01/c02/c03 为 2，其余为 0。

六条读数限制，引用这轮数字时一并带上：

- **条目从 26 变 29，整体值不可直接比。** 上表并列了「同 26 条子集」，与基线的比较只认那一列。
- **`@3` 窗口这轮反过来咬了两口。** m01 因为 SQL 证据从第 4 块挪到第 1 块而五项全回，m04 因为同一机制从第 1 挪到第 4 而五项判 0，
  k06 的 gold 块实测排第 4——三条都是窗口口径，答案本身都对。这条口径本轮明确不动。
- **混合样本的 `expected_source` 只标一侧**，c03 标的是文档侧，所以 SQL 那一半没有任何检索指标量得到，
  它的 `evidence_hit@3` 1.000 不等于两半都查过。（本轮如实记录，不作修订；该口径限制自 v7.4 起由
  `expected_sources` + `both_sides_hit` 覆盖，见上面字段小节与下面 v7.4 复跑小节）
- **c03 的时间戳那一半是从对话历史直接抄的**（回答自己写着「对话历史中已给出」），没有回查 SQL——历史被当成事实来源
  而不是指代线索。这是本轮新暴露的行为，v7.3 没修，ROADMAP 上留了一条未勾项。
- **同子集耗时 +26.3 秒**（62.8→89.1），成本几乎全在生成侧（60.9→87.0 秒），检索均值只 +0.2 秒；提示词变长与模型
  随机性同时在里面，只作观察记录、不作归因。`planner_unavailable` 全量 4 条与基线持平，c01 落在这一档但走关键词回退仍答对。
- **评估语料就是本仓库文档**（`config/sources.eval.yaml` 只入库 `README.md` + `docs/*.md`），本分支改过 README 与 ROADMAP
  的几行并重建了评估库，所以这轮与基线不是同一份语料快照，检索排名的差异里含这一项——k06/m04 的排名变化不能全部记到提示词改动头上。

### 双侧取证复跑（2026-11-20，`v7.4-two-sided-source`）

只加读数、不改定义的那一轮：新可选字段 `expected_sources` + 新读数 `both_sides_hit`（口径见上面的字段小节）。

```bash
python3 scripts/init_demo_db.py
python3 scripts/prepare_eval_kb.py
```

```bash
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 --ids k01,k02,k03,k04,k05,k06,k07 --output /tmp/v7_4_b1.md --json-output /tmp/v7_4_b1.json
# 其余三批只换 --ids 与输出名：d01,d02,d03,d04,d05,d06,m01,m02,m03,m04,m05 / s01,s02,s03,s04,c01,c02,c03 / s04 之外的 4 条
```

Qdrant 磁盘模式独占 `storage/eval/qdrant_storage`，四批只能串行；报告在进程结束时才写，所以四份批次
JSON 合并后再聚合（合并是一次性脚本，聚合走仓库内 `aggregate_results`/`build_report`/`render_markdown`，
批次划分记在报告 `meta.run_note`）。全量约 41 分钟。

29 条（25 可答 + 4 应拒答）× `agent`，`n_errors` 0，报告 `eval/reports/v7_4_two_sided_report.md`：

| 指标 | v7.3 | 本轮 |
|---|---:|---:|
| `both_sides_hit` | —（本轮新增） | **1.000** |
| `source_hit` | 0.960 | 0.960 |
| `evidence_hit@3` | 0.800 | 0.800 |
| `evidence_recall@3` | 0.800 | 0.800 |
| `context_precision@3` | 0.560 | 0.560 |
| `mrr@3` | 0.747 | 0.727 |
| `citation_rate` / `citation_validity` | 0.960 | 0.960 |
| `answer_rate` | 0.960 | 0.960 |
| `refusal_accuracy` | 1.000 | 1.000 |
| 单条平均耗时 | 89.8 秒 | 82.6 秒 |

双侧取证的 6 条逐条（`工具` = agentic 循环里的工具调用次数；排位指声明的 SQL 侧在那条样本引用列表里的位置）：

| 样本 | 声明的两侧 | `both_sides_hit` | 意图 | 工具 | SQL 侧排位 |
|---|---|---:|---|---:|---:|
| m01 | `demo.products` + `docs/beginner-guide.md` | 1.0 | `mixed` | 2 | 1 |
| m02 | `demo.products` + `README.md` | 1.0 | `mixed` | 2 | 1 |
| m03 | `demo.products` + `docs/beginner-guide.md` | 1.0 | `mixed` | 2 | 1 |
| m04 | `demo.products` + `README.md` | 1.0 | `mixed` | 2 | 4 |
| m05 | `demo.products` + `docs/beginner-guide.md` | 1.0 | `mixed` | 2 | 1 |
| c03 | `docs/beginner-guide.md` + `demo.products` | 1.0 | `mixed` | 2 | 4 |

六条读数限制，引用这轮数字时一并带上：

- **这轮 c03 回查了 SQL，缺口没复现。** 它 `tool_calls=2`、两侧都引到，所以 `both_sides_hit` 6/6 = 1.000；
  按同一口径回算 v7.3 那轮的既有引用数据是 5/6 = 0.833（判 0 的正是 c03）。「把对话历史当事实来源、
  还原指代后不回查」是**间歇行为**，不是每轮都触发；本轮只能说明新读数测得到「有没有回查」，
  不能说明它已被修好——运行时与提示词那两层成因未动（`docs/v7.4-history-as-evidence.md` 第一、二节）。
- **c03 与 m04 的 SQL 引用都排在第 4 位**，这轮实测坐实了「新读数不套 `@3` 窗口」这条口径的必要性：
  若沿用 `source_hit` 的前 3 条窗口，这两条会被误判成只取一侧。
- **既有指标逐条只有 4 条变化，都不是口径改动造成的**（本轮只加读数，不改任何判定式）：
  k05 `mrr@3` 1.000→0.500（汇总 `mrr@3` 的 -0.020 全部来自这一条，答案仍正确，是命中块排位后移）；
  k02 `stop_reason` `planner_unavailable`→`final`；r03 `intent` `database`→`mixed`（仍按缺口拒答，
  `refusal_accuracy` 不变）；r04 `stop_reason` `context_budget`→`insufficient_evidence`（仍是拒答，
  且写明缺什么，方向更好）。
- **`source_hit` 0.960 两轮同分、同一条**：v7.3 与本轮都是 m04 判 0，同一原因——SQL 引用排在第 4 位，
  而 `source_hit` 只看前 3 条引用。这条窗口口径本轮明确不动。
- **m03 连续第三轮在可答样本上拒答**（`stop_reason=planner_unavailable`，回答里写明只查得香蕉一条、
  无法确认它是全库最便宜）。它两侧证据其实都取到了（`both_sides_hit` 1.000），拒的是「最便宜」这个
  需要全表比较的结论，本轮 `answer_rate` 0.960 与上轮同分。
- **评估语料是本仓库文档，这轮不是 v7.3 的语料快照**：`docs/v7.4-history-as-evidence.md` 已并入 main
  并被 `config/sources.eval.yaml` 入库，m01/m05/c03 等条目的引用列表里正常出现它。检索排位的差异里
  含这一项，不能全部记到代码改动头上。

### 单条延迟复跑（2026-11-23，`v8-latency-planner-nothink`）

量的是 agentic 单条耗时，只动决策调用的思考开关（`RAG_AGENT_PLANNER_THINKING`），提示词、门控、步数与
指标判定式都没动。读数表与逐条变化见 `docs/ROADMAP.md` 的 v8.1 一节，复现命令：

```bash
# 三元组一起换到独立库，建一次给三轮共用（入库侧本轮没动，语料快照因此一致）
export RAG_COLLECTION_NAME=rag_eval_v8_latency
export RAG_STORAGE_DIR=storage/eval/qdrant_v8_latency
export RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_v8_latency/bm25.pkl
python3 scripts/prepare_eval_kb.py

python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
  --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
  --output eval/reports/v8_latency_route_thinking.md \
  --json-output eval/reports/v8_latency_route_thinking.json \
  --run-note "决策环节 RAG_AGENT_PLANNER_THINKING=route（路由保留思考、步进决策关思考）"
```

基线（决策全开思考）与全关档（`RAG_AGENT_PLANNER_THINKING=off`）的同一批读数在
`eval/reports/v8_latency_baseline_thinking_on.{md,json}` 与 `eval/reports/v8_latency_thinking_off.{md,json}`；
基线是在工作区把 `private_rag/config.py` 与 `private_rag/agent/prompts.py` 临时退回基线提交跑的，
报告头的 `git_dirty` 因此为 true（未提交的只有测试文件）。

口径与限制，引用这轮数字时一并带上：

- **三轮必须同条件**：同一三元组、同 `--configs agent`、同 `--agent-sources`、同 `--skip-ragas --max-workers 1`、
  同生成模型 `qwen3.8:27b-mlx`。串行跑是为了不抢本机 Ollama，同时也是 Qdrant 本地模式的要求。
- **报告里「检索」与「生成」两块不足以定位决策调用**：`avg_retrieval_ms` 只含工具执行，路由与步进决策
  都落在 `avg_generation_ms` 里。要拆开用量 `scripts/measure_planner_latency.py`（同一证据文本逐格计时）。
- **`avg_latency_ms` 是端到端单条耗时**：含路由、每次步进决策、工具执行、双侧门控的重试与最终作答，
  不是纯模型时间。默认档 56.6s 里工具执行 1.0s，其余都是 LLM 调用。
- **29 条样本只支撑方向**：±1 条就是 ±4 个百分点，`context_precision@3` 那类 -0.027 的量级也在噪声里。
  要把「降到多少」当结论，需要更大的评估集。
- **换机器 / 换模型 / 换精排不可比**：这组数测于本机 Ollama 0.40.16、`qwen3.8:27b-mlx`、`bge-m3`、
  `BAAI/bge-reranker-v2-m3`、MLX 后端、串行单并发；任一变化都要重测基线再比。
- **精排模型加载会做联网 HEAD 校验**：`BAAI/bge-reranker-v2-m3` 在网络不通时报 `Read timed out`，重试 5 次
  以后 transformers 回退本地缓存继续跑。想跳过校验带 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`，
  模型与权重都没变、不影响可比性，但要写进 `--run-note`。
- **关掉决策思考会改变路由判定**：全关那轮三条 multi_step 样本的意图从 `database` 变成 `mixed`，
  双侧门控接着禁止提前 `final`，`answer_rate` 因此从 1.000 掉到 0.880。默认档保留路由的思考就是这个原因。

### 最终作答思考的前后对比（2026-11-23，`v8-latency-final-nothink`）

量 `RAG_AGENT_ANSWER_THINKING`（`keep` / `on` / `off`，默认 `keep`）；结论与逐条变化见 `docs/ROADMAP.md`
的 v8.2：关思考省约 10% 但换来三条误拒，所以默认保留思考。复现命令是上面同一批参数，加一个环境变量，
两轮都要跑（A 不带、B 带 `off`），报告写到新文件：

```bash
# A：保持模型默认（keep）
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
  --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
  --output eval/reports/v8_latency_final_thinking_keep.md \
  --json-output eval/reports/v8_latency_final_thinking_keep.json \
  --run-note "最终作答 RAG_AGENT_ANSWER_THINKING=keep"

# B：关思考
RAG_AGENT_ANSWER_THINKING=off python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl \
  --configs agent --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
  --output eval/reports/v8_latency_final_thinking_off.md \
  --json-output eval/reports/v8_latency_final_thinking_off.json \
  --run-note "最终作答 RAG_AGENT_ANSWER_THINKING=off"
```

两轮都带 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`（精排走本地缓存，权重没变）。
这一对读数是同一提交上跑的，所以前后对比只用这一对：同配置跨轮抖动很大（本轮 A 40.6s，
PR #26 里同默认行为的 route 档 56.6s，`source_hit` 也差 0.04），29 条上 ±3 条拒答可能就是噪声。

### 读数可复现吗（2026-11-23，`v8-eval-determinism`）

上面那句「±3 条可能就是噪声」需要变成可判定的东西，所以先量复现性，再谈归因。
另外那一节的耗时结论要按下面的口径重读：两轮各跑一次时冷热启动会让方向都反过来（v8.3 复核结果是
关思考 -8.3%，且全量 29 条不可判定；误拒实际是 1 条不是 3 条）。

```bash
# 同一输入重复调用，数唯一输出个数（要打到本机 Ollama，不打网络）
python3 scripts/check_generation_determinism.py --repeat 4
python3 scripts/check_generation_determinism.py --repeat 4 --thinking off
python3 scripts/check_generation_determinism.py --repeat 4 --unpinned   # 复现改动前：不传温度与 seed
```

脚本覆盖三处调用：agentic 的最终作答、步进决策、经典 RAG 的生成（`generate_answer`）。
实测结论（细节见 `docs/ROADMAP.md` 的 v8.3）：不传 `temperature` 时四次调用四个输出——改动前的
最终作答正是这样；`temperature=0` 且关思考时四次同一输出；`temperature=1` 加固定 `seed` 仍然每次都不同，
也就是**这台 MLX 后端不接受 seed**，复现只能靠温度为 0；默认档带思考，温度为 0 也还是会抖。
`configure_eval_env()` 因此默认写入 `RAG_AGENT_ANSWER_TEMPERATURE=0`、`RAG_GENERATION_TEMPERATURE=0`
与 `RAG_LLM_SEED=7`（seed 留着表达意图，实际不起作用），只影响评估链路，想跑采样版 `export` 覆盖即可。
采样参数集中在 `private_rag/llm_params.py` 一处，四条调用共用：agentic 最终作答、决策调用、
经典链路的生成、查询改写。跑 `full` / `none` 这些固定链路配置时同样会被钉住，重跑才可比
（钉上前后各测一次：经典链路从「重复 2 次 2 个输出」变成「重复 4 次 1 个输出」）。
一个要说清的限制：温度为 0 时输出是「基本一致」而不是逐字保证——累计 9 次调用里 1 次给出不同文本，
所以逐字比对不能当证据，可复现性只在指标层成立。

噪声底用同配置重复三轮量（默认档，参数与前几轮完全一致）：

```bash
export RAG_COLLECTION_NAME=rag_eval_v8_latency
export RAG_STORAGE_DIR=storage/eval/qdrant_v8_latency
export RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_v8_latency/bm25.pkl
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

for n in 1 2 3; do
  python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent \
    --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
    --output "eval/reports/v8_noise_floor_repeat_${n}.md" \
    --json-output "eval/reports/v8_noise_floor_repeat_${n}.json" \
    --run-note "同配置重复轮 ${n}"
done
```

三轮跑完按逐条差异比对（拒答、意图、`stop_reason`、`source_hit`、工具调用数、答案文本、耗时），
噪声底是：九项质量指标三轮完全一致，答案措辞每对轮次有 4~7 条不同但不改变指标，逐条行为只有 `r01`
在第 1 轮翻动，而耗时第一轮比后两轮慢 52%（冷启动）——第 2、3 轮之间只差 0.2%。
所以对照的口径写死为三条：**指标变化 ≥ 1 条样本（29 条上约 3.4pt）才当真；耗时读数作废第一轮；
措辞变化不算证据**。细节见 `docs/ROADMAP.md` 的 v8.3。三轮跑完用这个脚本算噪声底：

```bash
python3 scripts/compare_eval_noise.py eval/reports/v8_noise_floor_repeat_1.json \
  eval/reports/v8_noise_floor_repeat_2.json eval/reports/v8_noise_floor_repeat_3.json
```

它会列出「指标是否轮轮一致」和每一对轮次的逐条行为差异与耗时差。退出码非 0 表示有指标在轮次之间变了。

固定链路（`full`：一次检索一次生成）也照同一套办法重复三轮量过，命令只差 `--configs` 与报告名
（固定链路不需要 `--agent-sources`）：

```bash
for n in 1 2 3; do
  python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs full \
    --skip-ragas --max-workers 1 \
    --output "eval/reports/v8_noise_floor_classic_${n}.md" \
    --json-output "eval/reports/v8_noise_floor_classic_${n}.json" \
    --run-note "固定链路同配置重复轮 ${n}"
done
```

固定链路的噪声底：八项指标三轮完全一致（`answer_rate` 0.440、`source_hit` 0.320、`refusal_accuracy` 1.000、
`both_sides_hit` 0.000、`evidence_hit@3` 0.240、`mrr@3` 0.220、`context_precision@3` 0.080），逐条行为
一条都没翻动，答案措辞每对轮次 5~11 条不同；耗时第一轮比后两轮慢 41%~43%（同样是冷启动），
第 2、3 轮之间只差 1.6%。两个地方要注意：

- `citation_rate` / `citation_validity` 是唯一的例外（0.920 → 0.960）。它们数的是「答案文本里有没有引用编号、
  编号在不在引用条数范围内」，所以措辞会直接推动它们——第 3 轮 `d03` 的拒答文案带了 `[编号]` 就被记成有效。
  引用类指标变了先确认是不是某一条样本的文本变动，别当成功能差异。
- 这些数字不能与上面 agentic 那组横向比：`eval/v7_agent_qa.jsonl` 是按 agentic 设计的（多步、混合问题、
  要查库），固定链路一次检索一次生成，25 条可答里只答了 11 条，所以 `answer_rate` 只有 0.440，
  `both_sides_hit` 0.000 也是同一原因（它不会去查 SQL 那一侧）。

比耗时（A/B 两种设置）要在同一进程里交替跑，别各跑一轮：

```bash
# 4 条子集，keep → off → keep；两次 keep 的每条耗时应当相同，否则说明读数不可信
grep -E '"id": ?"(s01|s02|s04|r02)"' eval/v7_agent_qa.jsonl > /tmp/subset.jsonl
for mode in keep off keep; do
  RAG_AGENT_ANSWER_THINKING=$mode python3 scripts/evaluate.py --dataset /tmp/subset.jsonl \
    --configs agent --agent-sources config/sources.eval.agent.yaml --skip-ragas --max-workers 1 \
    --output "/tmp/sub_${mode}.md" --json-output "/tmp/sub_${mode}.json" \
    --run-note "子集交替读数 $mode"
done
```

### agentic 延迟住在哪一段，以及路由思考限长（2026-11-24，`v8-eval-determinism`）

v8.1 / v8.2 之后剩下的延迟具体住在哪个环节，此前没单独量过（报告只把一条分成「工具执行」与「其余」两块）。
新脚本只做观察、不改行为：把「其余」再拆成路由 / 步进决策 / 最终作答三段，并记下每次调用的提示词长度与
输入输出 token。

```bash
export RAG_COLLECTION_NAME=rag_eval_v8_latency
export RAG_STORAGE_DIR=storage/eval/qdrant_v8_latency
export RAG_LEXICAL_INDEX_PATH=storage/eval/lexical_v8_latency/bm25.pkl
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

python3 scripts/profile_agent_calls.py --dataset eval/v7_agent_qa.jsonl --json /tmp/profile_full_baseline.json
```

29 条一轮的归属（测于 `4cb9fb6`）：路由 566.5s（48.1%）、步进决策 413.1s（35.1%）、最终作答 174.0s（14.8%），
工具执行只占约 2%。两段的毛病不一样：路由是「想得太多」——同一条提示词的输出从 60 个字到 1215 个字，
而决策 JSON 本身只要几十个字；步进决策是「读得太多」——单条最长 17.3s 里 14.8s 花在读提示词，真正写 JSON 只 2.5s。

本轮采纳的改法是在路由提示词**末尾**加一句「思考控制在两三句以内，不要复述题目和工具说明」（默认生效，
`RAG_DISABLE_AGENT_THINKING_BRIEF=1` 退回老提示词）。模型自带的思考档位（`reasoning=low` / `minimal`）实测管不住
思考长度——同一条提示词只会变长或差不多——所以没做那个旋钮。按 v8.3 的口径在同一进程里交替量：

```bash
python3 scripts/measure_thinking_ab.py --setting thinking_brief --values off,on,off \
  --prefix v8_latency_thinking_brief_ab --keep-reports
```

脚本按轮改 `private_rag.agent.prompts` 的模块属性再调用 `evaluate.main`，报告口径与平时跑评估完全一致，
跑完直接打印每轮均耗时（第一轮标为作废）、指标差与逐条行为翻动。读数：暖轮 23.7s → 20.0s（-15.8%），
四项门禁指标不退，`source_hit` 0.960 → 1.000（`m04` 一条带动 `evidence_hit@3`、`mrr@3`、
`context_precision@3`）；逐条只有 3 条行为翻动，都不改变作答与拒答。细节见 `docs/ROADMAP.md` 的 v8.4。

一个试过但没用的方向，记在这里免得再试：把步进提示词的静态规则挪到证据前面（想让模型复用固定前缀，少读提示词）
实测三步 37.6s 对 37.8s，没有差别。这台 Ollama 的前缀缓存只在「整段开头与上一次请求完全相同」时才复用
——同一份提示词原样重发才能把读提示词从 30s 压到 0.4s——重排段落顺序换不来复用。

## v8 解析基准集（`v8_parse_bench.jsonl`）

要量的问题：v6.2 的消融证明自建版面表格把 `evidence_hit@3` 从 0.2 抬到 1.0，但那只是「比自己不开这个模块」
——自建那套（RapidOCR + OpenCV 形态学找框 + rapid-table SLANet）与成熟解析器相比处在什么位置，从来没有读数。
这一集与 `scripts/parse_bench.py` 负责给出这个位置。**目的是量出自建链路的位置，不是把它换掉**；
是否切换由读数决定，单独记一条。

### 口径：整页替换后端

`RAG_SCAN_PARSER=selfbuilt|docling|mineru`（默认 `selfbuilt`）。非自建时第三方解析器自己做 OCR，
某一页的**正文与表格都换来源**，不是只换表格模块——所以两侧命中差异里含 OCR 差异，不能全记到表格模块头上。
下游 `doc_id` / 页码 / 表格名 / 逗号序列化 / 父子块切分一字未改，自建侧逐字节还是 v6.2 ON 侧那条链路，
这是新读数能与 `v6_scan_table_on.md` 对齐的前提。为什么第三方走独立虚拟环境 + subprocess，见 `docs/adr/0007`。

### fixture 与 golden

`eval/fixtures/v8_parse_bench.pdf` 是四页纯图片（`scripts/make_parse_bench_fixture.py` 生成，四页规格单一来源）：

| 页 | 形态 | 想量的点 |
| --- | --- | --- |
| 1 | 带边框 3×3 | 与 v6 fixture 同形，作对照基线 |
| 2 | rowspan / colspan 合并单元格 | 自建的 span 展开与第三方的展开是否等价 |
| 3 | **无框线**（只有文字对齐，画面里没有任何线条） | 自建靠形态学结构线找框，这是已知短板，主要区分点 |
| 4 | 六列表格 | 多列时行列会不会错位 |

`eval/fixtures/v8_parse_bench.md` 是文字底稿，只做 evidence 逐字校验、**不入库**（与 v6 同设计，
所以 `source_hit` 恒 0 是口径而非检索失败）；`eval/fixtures/v8_parse_bench.golden.json` 是解析层 golden。
评估集 8 条 = `ingestion` 5 + `multi_step` 2 + `refusal` 1。

### evidence 的写法是口径能对齐的关键

evidence 写成**整行连续短语**（如 `恒达 键盘 50 15天`），不写带标点的单元格内容。`normalize_text`
是 NFKC + 小写 + 只保留 alnum，所以自建侧的逗号拼接行文本 `恒达,键盘,50,15天` 与第三方的 markdown
管道行规范化后完全等价，两侧都能逐字命中。若 evidence 写成「起订量 50」这种跨格短语，某一侧的序列化方式
就能单独让它失配，读数分不清是解析差还是口径不命中。

### 两层读数

- **解析层**（`scripts/parse_bench.py`，不打生成模型、不入库、秒级）：`table_recall`（单元格匹配率 ≥ 0.5
  算认出这张表）、`cell_accuracy`（按行列位置比对，只统计期望非空的地格）、`structure_ok`（行列数对不对）、
  `body_purity`（1 − 混入正文的期望行占比）。它的作用是把「解析真的差」与「evidence 口径没命中」分开——
  端到端只看到命中与否，分不清是哪一种。
- **端到端**（两个数据集 × 三侧 = 6 次，`full` 配置、Ragas 关闭）：沿用既有指标。`avg_latency_ms`
  只含检索与生成，**解析耗时发生在入库侧**，在解析层报告里单独记。

合并单元格的延续格两侧填法不同（自建填 `None`、第三方驱动填占位副本），golden 只比对期望非空的地格，
这个差异不会进读数。

### 三侧读数（2026-11-23，`git_commit = 530418c`，三侧 dirty 都 True）

生成侧三侧完全一致（`qwen3.8:27b-mlx` + `bge-m3` + reranker，与 v6.2 ON 侧同配置），所以下面的差异只可能来自入库侧的解析。

**解析层**（`eval/reports/v8_parse_baseline.md`，四页一次解析，不打模型）：

| 后端 | 表格检出 | table_recall | cell_accuracy | structure_ok | body_purity | 一次解析耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| selfbuilt | 3/4 | 0.75 | 0.64 | 0.75 | 0.92 | 3344 ms |
| docling | 4/4 | 1.00 | 1.00 | 1.00 | 1.00 | 10880 ms |
| mineru（standard 档） | 3/4 | 0.75 | 0.75 | 0.75 | 0.75 | 13024 ms |

逐页区分点都落在同两处：页 1/2 自建把数字读丢（`1299.0` 前丢了 `8`、`1` 读成 `L`），第三两侧逐格全对；
页 3（无框线）自建与 mineru 都是 0 张检出，docling 认出 `["恒达","键盘","50","15天"]`。mineru 那页把两行
按正确顺序读进正文，所以 `body_purity` 0.75 是「表格行混进正文」而非「读丢内容」。

**端到端**（`full` 配置、Ragas 关闭；三侧 `answer_rate`/`refusal_accuracy`/`citation_rate` 均 1.000，未列）：

| 数据集 | 后端 | evidence_hit@3 | context_precision@3 | mrr@3 | 平均上下文字符 | 单条均耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| v8_parse_bench 8 条 | selfbuilt | 0.571 | 0.190 | 0.571 | 130.6 | 18196 ms |
| v8_parse_bench 8 条 | docling | **1.000** | 0.333 | 0.905 | 117.1 | 21583 ms |
| v8_parse_bench 8 条 | mineru | **1.000** | 0.333 | **1.000** | 132.8 | 10150 ms |
| v6_scan_table 6 条 | selfbuilt | 1.000 | 0.500 | 0.900 | 50.0 | 8040 ms |
| v6_scan_table 6 条 | docling | 1.000 | 0.500 | 0.900 | 53.0 | 17466 ms |
| v6_scan_table 6 条 | mineru | 1.000 | 0.500 | 0.900 | 51.0 | 8673 ms |

两条读数方向：

- **自建侧新集 3 条未命中，与解析层的缺陷一一对应**：`v8pb-01`/`v8pb-07`（无框线页 0 张检出，正文里那两行
  还被 OCR 倒序成 `15天 50 恒达 键盘`）、`v8pb-03`（`研发 张三 1` 读成 `L`）。第三两侧 7 条全命中，
  所以这 0.429 的差距是解析造成的，不是 evidence 口径造成的。
- **v6 那 6 条三侧完全同分**（1.000 / 0.500 / 0.900），与 v6.2 的 ON 侧逐字一致——口径对齐成立，
  第三方换掉整页 OCR 也没有把那 6 条的命中与排位改动。这说明 fixture 越简单，后端差异越不出读数；
  区分点只在复杂页型上出现。
- **mineru 的 `table_recall` 与自建同为 0.75，端到端却与 docling 一样 1.000**：无框线页它也没认成表格，
  但它把那两行按正确顺序读成了正文，evidence 又是整行连续短语，逐字命中照样成立。所以「表格有没有认出来」
  与「证据能不能召回」在这轮不是同一件事，切换决策不能只看 `table_recall`。

耗时的三侧差异不进结论：`avg_latency_ms` 只含检索 + 生成，而三侧的生成耗时本身就相差很大（bench 侧
17768 / 20878 / 9671 ms），模型冷热与并发争抢的影响比解析后端大。解析耗时在入库侧，见上面的解析层表。

### 限制（引用这轮数字时一并带上）

- **整条栈对比，不是只换表格模块**：正文 OCR 的来源也换了，命中差异里含 OCR 差异，不能全记到表格模块头上。
- **fixture 是合成件**：四页都由 `scripts/make_parse_bench_fixture.py` 用本机字体绘制，不代表真实扫描件，
  且 Linux 上字体不同、绘制不可复现——换机器只能复现指标算法，不能复现这张 PDF 的样子。
- **`source_hit` 恒 0 是口径**：底稿 `v8_parse_bench.md` 只做 evidence 逐字校验、不入库，所以引用来源永远对不上底稿。
- **样本量只支撑方向**：新集 8 条、v6 集 6 条，`full` 单配置、Ragas 关闭，不足以支撑结论。
- **解析耗时不进 `avg_latency_ms`**：那是检索 + 生成的耗时，解析发生在入库侧，只在解析层报告里记。
- **三侧耗时不能互相比较**：`avg_latency_ms` 里生成占九成，而这轮三侧的生成耗时本身就相差很大，模型冷热与
  Ollama 争抢的影响大于解析后端本身。要量解析开销只看解析层报告的耗时列。
- **无框线页自建 0 检出是设计出来的区分点**，不是回归；这一页正是用来分辨两侧高下的那一页。
- **docling 的正文有字符损失，`body_purity` 量不到**：它把「配件库存表（第一页）」读成 `配件库存表 (第 页`、
  把备注顺序打乱并多出杂散数字（`备注 人数为在编人数 0`）。`body_purity` 只统计「期望表格行有没有混进正文」，
  所以它给 docling 1.00 并不等于正文无损。本轮 evidence 全是表格行与一句完整正文，这个损失没反映到命中上。
- **MinerU 只测了 `standard` 档**：走 `mineru-kit parse --tier standard`（流水线小模型 + llama.cpp 的 VLM），
  `basic` 档未单独测。档位由 `RAG_MINERU_TIER` 决定并参与解析缓存键，换档必然重解析，不要把两档的读数混看。
  另外 MinerU 4.2.3 的批量解析入口不是 `mineru parse`（那是文档库 + 本地服务的 CLI），驱动用的是 `mineru-kit parse`。
- **PaddleOCR-VL 未测**：要 CUDA 上的 vLLM/SGLang 服务，本机 Apple Silicon 跑不了，不要写成「三侧齐了」。
- **换机器要重装**：`.venv-parse/` 不入库，模型权重首次解析才下载（MinerU 的 standard 档要 `mineru-kit models
  download --tier standard`，basic 档实测 863 MB）；第三方与本机 3.9 完全隔离。
