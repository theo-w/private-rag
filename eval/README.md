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

## v7 agentic 评估集（`v7_agent_qa.jsonl`）

v4/v5 的评估集量的是“固定检索链路”的质量；v7 这份量的是**加了 agentic 循环以后到底多出了什么能力**，
所以按“回答这个问题需要哪个工具”分类，而不是按文档章节分类：

| 类别 | 条数 | 需要 | 固定链路的表现 |
|---|---:|---|---|
| `knowledge` | 7 | 只查项目文档 | 应两组的差值接近 0 |
| `database` | 6 | 只查运行时 SQL（`products` 表） | 必然拒答：评估库不入库业务数据 |
| `mixed` | 5 | 文档 + SQL 各出一段证据 | 只能答出一半 |
| `multi_step` | 4 | 先拿结果再决定下一步（比平均价、排序后取保存说明等） | 无法作答 |
| `refusal` | 4 | 文档和库里都没有 | 两组都应拒答 |

共 26 条：22 可回答 + 4 应拒答。evidence 仍做逐字校验，出处为
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
- **单轮**：评估不传会话历史，指代消解类能力未覆盖。
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

## v7.2 路由与拒答复跑（2026-11-19）

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
`none` 判据偏松的问题同日已经收紧，机制与复跑见下一节。另外 m01 的文档那一半仍然没查出来——模型把「这个项目」当成缺少对话上下文，而评估按单轮执行、本来就没覆盖指代消解。

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
| m01 | `database`→`mixed` | `final` | 1→2 | 否 |
| d02 | `mixed`→`database` | `final` | 1 | 否 |
| k02 | `knowledge` | `final`→`planner_unavailable` | 1 | 否，答案与基线一致 |
| r03 | `mixed` | `insufficient_evidence`→`planner_unavailable` | 1→2 | 仍拒答 |

分布：意图 `knowledge`9 / `database`10 / `mixed`6 / `none`1 → `knowledge`10 / `database`10 / `mixed`6 / `none`0；`stop_reason` `final`21 / `no_retrieval`1 / `insufficient_evidence`2 / `context_budget`1 / `planner_unavailable`1 → `final`19 / `insufficient_evidence`2 / `context_budget`1 / `planner_unavailable`4。改动要达到的效果到位了：`none` 与 `no_retrieval` 双双归零，也就是「一次都没检索就拒答」这条路径在全量评估里不再出现，4 条应拒答样本仍全部以哨兵文本开头。r01 从「判 `none`、零检索、闷头拒答」变成「查了两次、按缺口拒答并写明缺什么」（回答里点出 `demo.products` 只有六列、不含产地与供应商字段）。

四条读数限制，引用这轮数字时一并带上：

- 整体指标的回落恰好等于 m03 一条的分量（可答样本 22 条，1/22≈0.045，与四项 -0.045 对得上），不是普遍退化。
- m03 判的是 `mixed`，`none` 探测在这条上不会触发；它的退化发生在 JSON 解析失败那次运行里，SQL 只回了一条记录（香蕉 6.5）后模型按 v7.2 的充分性判定拒答「最便宜」这一半。与本轮改动没有因果路径，但也不把「无关」写成结论。
- `planner_unavailable` 1→4 只作观察记录，不作归因（模型随机性与提示词改动同时在里面）；其中 k02 解析失败仍给出了与基线一致的答案，说明解析失败本身不必然导致退化。
- 耗时上升同样含生成侧随机性（检索均值反而从 2.4 秒降到 1.9 秒），机制上的成本只有「真正无关的问题多一次 `kb_search`」，全局体现为平均工具调用 1.31→1.42。`no_evidence` 这个新增取值由单测覆盖，这一轮 26 条没有触发（r01 的验证检索有命中）。
