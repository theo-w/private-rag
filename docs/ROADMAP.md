# ROADMAP

本项目的演进路线：每个阶段一个独立分支，`main` 始终保持可运行；阶段完成后用对比数据说明收益，而不是只描述“实现了 RAG”。

| 阶段 | 分支 | 内容 | 状态 | 验收标准 |
|---|---|---|---|---|
| v1 | `main`（历史脚本在 `legacy/`） | FAISS 基础 RAG：单文件、向量召回、LLM 问答 | ✅ 已完成，仅作演进参考 | 能对一个 txt 文件完成切分、检索、问答 |
| v2 | `v2-qdrant-rerank-citation` | Qdrant 持久化、bge-reranker、混合检索（BM25 + 向量 + RRF）、标签/来源过滤、多源接入（文件/网页/SQL）、页码与引用溯源、增量与 prune | ✅ 本分支完成 | 三源可入库；重复入库跳过；更新只替换对应 doc_id；引用含类型/标题/URL 或页码；过滤生效；pytest 通过 |
| v3 | `v3-fastapi-session` | FastAPI 服务化、多轮会话（session + 历史裁剪）、流式输出、统一错误处理 | ✅ 本分支完成 | API 可并发处理多会话；历史不串号；有接口级测试与启动说明 |
| v4 | `v4-ragas-eval` | Ragas 自动评估：30–50 条标注问答、faithfulness / answer relevancy / context precision / recall、调参对比报告 | ✅ 本分支完成 | 固化评估集与脚本；给出 v2→v4 的量化对比（至少 2 组参数/策略） |
| v5 | `v5-parent-rag-query-rewrite` | Parent-Document RAG（子块召回、父块送上下文）、查询改写（多查询 / HyDE）、去重与上下文预算 | ✅ 本分支完成（评估结果见报告） | 支持父子块、多查询/HyDE、预算与去重；有 `full` / `parent` / `parent_multi` / `parent_hyde` 前后对比、Ragas 子集与失败案例分析，见 `eval/reports/v5_report.md` |
| v6 | `v6-ocr-parser` | 扫描件 OCR、表格解析、版面处理，接入现有连接器与元数据 | ✅ 本分支完成 | 扫描 PDF 与表格可检索；页码/表格名进入引用 |
| v6.1 | `v6.1-readonly-render` | 只读公网演示：v6 入库产物导出快照，小服务暴露语料与 BM25 检索，问答明确关闭 | ✅ 已部署 (<https://private-rag-readonly.onrender.com>) | 公网可看到扫描件 OCR 文本、表格文档与页码/表格名引用；不加载模型，问答返回 409 |
| v6.2 | `claude/serene-tharp-761b47` | 扫描件版面表格提取：OpenCV 形态学检测网格线定位表格矩形，rapid-table（SLANet-Plus ONNX）识别结构，自建 OCR 文本框平移到 crop 坐标后传给引擎拼 HTML；`filter_ocr_result` 按表格矩形过滤正文 OCR 框去重；开关 `RAG_DISABLE_SCAN_TABLE` | ✅ 本分支完成 | 410 测试通过；合成扫描件 fixture 端到端入库得「表格文档 + 过滤后正文」两文档；消融评估 6 条 `full`：`evidence_hit@3` 0.2→1.0、`context_precision@3` 0.2→0.5，两侧 `answer_rate`/`refusal_accuracy` 持平，报告 `eval/reports/v6_scan_table_on.md` / `v6_scan_table_off.md` |
| v7 | `claude/heuristic-edison-ebcf0b` | Agentic RAG：意图路由、SQL/工具调用、检索-行动循环、可观测 trace | ✅ 本分支完成 | 业务问题能自动选择知识库或工具；trace 可回放每条决策 |
| v7.1 | `v7.1` | SSE 流式 Agentic 输出、演示页 Agentic 开关、列表/表格感知切分、v7 评估集与量化对比 | ✅ 本分支完成 | 311 测试通过；SSE 端点可流式输出 agentic 轨迹并写回会话历史；demo.html 支持 Agentic 模式；列表/表格切分测试通过；v7 agentic 评估集 26 条 + `agent`/`full` 对比报告（agentic `answer_rate` 0.909 vs 0.318、`source_hit` 0.591 vs 0.318、单条耗时 71.2s vs 17.0s），Ragas 子集 6 条另见 `eval/reports/v7_agent_ragas_report.md` |
| v7.2 | `v7.2` | 意图路由与拒答策略：数据可得性驱动路由、证据充分性拒答 | ✅ 本分支完成 | 343 测试通过（同日收紧 `none` 判据后）；`sql_query` 工具描述带上库内数据目录（列名 + 有界样例值，`RAG_AGENT_CATALOG_CHARS` / `RAG_AGENT_CATALOG_SAMPLES` 可关），路由判据改为「回答所需的事实住在哪一侧」；证据充分性由步进决策声明，判不足直接拒答并给 `stop_reason=insufficient_evidence`；v7 评估集 26 条复跑 `refusal_accuracy` 0.250→1.000、`source_hit` 0.591→1.000、`evidence_hit@3` 0.409→0.864、单条耗时 71.2s→51.9s，`knowledge` 类 7 条持平无回归，报告 `eval/reports/v7_2_agent_report.md`；同日在同一条线上收紧 `none` 判据（分支 `v7.2-none-gate`：判 `none` 先做一次验证检索，拒答必须建立在证据上，`RAG_DISABLE_AGENT_NONE_PROBE` 可关），复跑 26 条 `none`/`no_retrieval` 双双归零、`refusal_accuracy` 1.000 持平、`citation_rate` 0.955→1.000，报告 `eval/reports/v7_2_nonegate_report.md`；指代消解见下一行 v7.3 |
| v7.3 | `v7.3-coreference` | 指代消解：提示词写死「本项目/本系统」的消解规则，评估补上真正带对话历史的多轮样本 | ✅ 本分支完成 | 350 测试通过；`ROUTER_PROMPT`/`STEP_PROMPT`/`FINAL_PROMPT` 三处写死「『这个项目』『本系统』指本系统自身、它的说明文档就在知识库里」「没有对话历史绝不是选 `none` 的理由」；评估集 26→29 条（新增 `coreference` 3 条带 `history`），`EvalSample.history` 做严格校验并真的传给 `agent.run(..., history=...)`，报告新增 `history_turns`；三条指代样本全部还原（c01 文档侧、c02 走 `sql_query`、c03 判 `mixed`），同 26 条子集 `answer_rate`/`source_hit`/`refusal_accuracy` 与上一轮持平（0.955/0.955/1.000），`evidence_hit@3` -0.045 是 k06 与 m04 各 -1、m01 +1 的净额（三条都是 `@3` 窗口口径、答案本身正确），`citation_rate` -0.045 全部来自 d05 一条被生成预算截断在 18 字；4 条应拒答样本仍全部哨兵拒答，报告 `eval/reports/v7_3_coreference_report.md` |
| v7.4 | `v7.4-two-sided-source` + `worktree-fix-c-runtime-gate` | 评估口径改双侧：混合问题「到底查了几侧」变成可测读数 | ✅ 本分支完成 | 357 测试通过（+4 修法 A 单测）；评估集加可选字段 `expected_sources`（声明「文档侧 + SQL 侧」），新确定性读数 `both_sides_hit` 要求声明的每一侧都在**全量**引用列表里出现才判 1（不套 `@3` 窗口，只统计声明双侧的 6 条，其余记 `None` 不进均值），`source_hit`/`evidence_hit@3`/`citation_rate` 的定义与逐条取值一字未改，v7 / v7.2 / v7.3 报告仍可比；只引到一侧的双侧样本单独进「失败与关注案例」并写「只取到一侧证据」；按新口径回算 v7.3 那轮得 5/6 = 0.833（判 0 的正是 c03，同一条在旧指标下 `source_hit` 与 `evidence_hit@3` 全满分），本轮 29 条实跑 6/6 = 1.000（c03 这轮真的回查了 SQL，缺口是间歇的；c03 与 m04 的 SQL 引用都排在第 4 位，坐实了新读数不套 `@3` 窗口的必要性），`source_hit` 0.960 持平（两轮同为 m04 一条未命中），`mrr@3` 0.747→0.727 的变化全部来自 k05 一条排位后移，单条平均 89.8 秒→82.6 秒，报告 `eval/reports/v7_4_two_sided_report.md`；修法 A（提示词硬规则）已做：STEP_PROMPT 规则 1 限定 sufficient 只看「已收集的证据」（不含历史），新增规则 10 禁止 mixed 查询在任一侧无证据时选 final；FINAL_PROMPT 新增规则 7 要求历史里的事实必须先查证据，历史段渲染加上「不能作为事实依据」标注。A/B 重复采样 5×修法 A vs 5×无修法 A：修法 A 将 c03 的 SQL 回查率从 20%（1/5）提升到 100%（5/5），修法 A 有效；修法 C（运行时硬门禁）已合入 PR #19：`config.py` 新增 `AGENT_MIXED_GATE` 开关，`runtime.py` 新增 `_has_evidence_for()` + `run()`/`run_stream()` 中的双侧证据门控，mixed 意图任一侧零证据时拒绝 final 强制调用缺失工具；修法 C 合入后 373 测试通过（+6 修法 C 单测 +4 修法 A 单测），评估新增 7 个 per-intent 工具调用指标（`avg_tool_calls_knowledge/database/mixed` + `dual_tool_rate_*`），报告 `eval/reports/v7_agent_tool_calls_report.json`。**读数出处复核（2026-11-21）**：上面「本轮 29 条实跑 6/6 = 1.000」测于 `2e912379f`，是 `d43b314` 的祖先、早于修法 A，不能当修法 A 的依据；A/B 两组的 `meta.git_commit` 同为 `d43b314`，记录本身区分不了两组提示词，而被验证的规则又在 `978a63f` 里被顺手删掉、本分支已恢复，经过见下方「修法 A 的读数出处与一次提示词回退」。**门禁可观测性与测量出处基建（2026-11-22）**：门控拦截步骤从 `kind=stop` 改为 `kind=gate`（循环并没有停，是被强制继续取证，旧 kind 会把轨迹误读成「循环停了」），`AgentResult` 加 `gate_intercepts` 计数，评估新增确定性读数 `gate_intercept_rate`（intent 非空的 agentic 样本里至少被拦一次 final 的占比，固定链路不进分母）；`evaluate.py` 新增 `git_dirty()` 与 `--run-note`，报告头把 dirty 标记与跑批备注写进读数出处，汇总 JSON（`eval_report.py`）同步带 `git_dirty`/`run_note`；生成缓存键加提示词指纹 `_prompt_fingerprint()`（`generation` + `agent.prompts` 源码哈希；缓存目前只在进程内，指纹是防御性钉死，将来缓存落盘或进程内热改提示词时不会再复用旧答案）。门控单测改断言 `kind=gate` 与 `gate_intercepts`，另加 4 项单测（拦截计数进评估 / 占比口径 / dirty+备注渲染 / 指纹稳定），全量 430 passed。**修法 C 双通道复跑（2026-11-22）**：同一代码 `944ea77` 上 29 条 × gate ON/OFF 各一轮（ON 轮 dirty=False；OFF 轮 dirty=True 只是多了未提交的 ON 报告文件，两轮代码完全一致）——两轮 `both_sides_hit` 都是 1.000，差别在确定性：OFF 这轮 d02（意图 mixed 却只查了 SQL 一侧）把 `dual_tool_rate_mixed` 拉到 0.889，ON 把双工具率钉死 1.000、`gate_intercept_rate` 0.207（6/29 全在 mixed 意图，含 d02 这种未声明 `expected_sources` 但意图判 mixed 的题）；代价如实记录——单条均耗时 59.3s→88.1s（+49%），ON 有 6 条跑到 `max_steps` 才收口但答案质量未受损（d02 两轮 `source_hit` 均 1.0，SQL 证据恰好带出了保存文本），修法 A 兜住的缺口是概率性的、修法 C 是确定性的，与 A/B 结论一致；报告 `eval/reports/v7_5_gate_on.md` / `v7_5_gate_off.md` |
| v7.5 | `claude/serene-tharp-761b47` | function calling 决策输出对比：`RAG_AGENT_PLANNER_MODE=json|function` 实验开关（`ask_tool_json`，同一提示词换输出通道），JSON 模式保持默认 | ✅ 本分支完成 | 420 测试通过（+10 mock 单测）；同一提示词双通道实跑读数（`eval/reports/fc_comparison_report.md`）：主模型 `qwen3.8:27b-mlx` FC 路由 12/12 可用（均耗时 18.8s vs JSON 27.0s）、步进形状 3/3 一致，两通道意图一致率 0.75；对照 `qwen2.5:7b-instruct` FC 成功 4/12（提示词仍写着「只输出 JSON」的混淆已如实写明）；结论与逐条读数见 v7 详细验收的 function calling 对比项 |
| v8 | `v8-mcp-kb-search-ask` | MCP 暴露：手写最小 stdio JSON-RPC server（零新增依赖），对外只读工具 `kb_search` / `ask`，`--allow-sql` 才加 `sql_query` | ✅ 本分支完成 | 529 测试通过（既有 430 + 新增 99，不打模型、不打开真实 Qdrant）；`--list-tools` 与 `tools/list` 能看到工具与 input/output schema 并声明只读；本机实跑 `tools/call kb_search` 返回带 `[n]` 编号的证据与含 `page` 的引用载荷、`ask` 返回带 `[1][2]` 的回答与 `meta`，`--allow-sql` 用演示库授权跑通 `sql_query`（3 行结果 + `source_type=sql` 引用）且 `UPDATE` 被护栏拒；同一 `storage/` 被别的进程占用时退出码 3 并给出可操作提示；不暴露入库/写库，运行时 SQL 要过 `--allow-sql` + `allow_runtime_query` + `sql_guard` 三重门（与 agentic 共用 `build_tool_registry`）；选型与代价见 `docs/adr/0004`，边界见 `docs/adr/0005` 与取代其 SQL 一条的 `docs/adr/0006` |

## v2 详细验收（已完成）

- [x] `sources.yaml` 支持 file / web / sql，`${ENV_VAR}` 插值，凭证不落库
- [x] 文件按内容 hash 幂等，PDF 按页生成稳定 `doc_id`
- [x] 网页抓取失败逐 URL 容错，保留 URL、标题、发布日期
- [x] SQL 按行生成文档，DSN 脱敏，`max_rows` 限制
- [x] 内容更新先删旧向量再写新向量；确定性 UUID 避免重复
- [x] `--prune` 清理已消失的文件/页面/行（source 加载出错时跳过，避免误删）
- [x] dense + BM25 双路召回，RRF 融合，bge-reranker 精排
- [x] `--source-type` / `--tag` 过滤；BM25 索引缺失自动降级
- [x] 回答带 `[n]` 编号引用，CLI 输出来源类型、标题、URI、页码/表名
- [x] pytest 单测（不依赖 Ollama）+ 冒烟脚本（可选 e2e）

## v3 详细验收（本分支）

- [x] `RagEngine` 收敛检索/精排/生成，CLI 与 API 共用同一条链路
- [x] FastAPI 应用工厂、lifespan 延迟加载、请求 ID 与统一错误结构
- [x] 内存多会话：TTL、容量上限、消息上限、逐会话锁、历史按轮次/字符预算裁剪
- [x] 同步 `/api/v1/chat` 与 SSE `/api/v1/chat/stream`；`session → citations → token → done/error`
- [x] 只有完整生成成功才写入会话；客户端断开不留下半截历史
- [x] `/health` 与 `/ready`；BM25/reranker 缺失时降级并给出 warning
- [x] 未知会话 404、参数校验 422、知识库未就绪/容量 503、内部异常 500，均带 `request_id`
- [x] API 级测试：同步/流式/错误/多会话隔离/并发；不依赖 Ollama 与真实 Qdrant
- [x] README 启动与 curl 说明，明确单 worker、内存会话与本地无鉴权的边界

## v4 详细验收（本分支）

- [x] 固定评估集 40 条：36 条可回答 + 4 条应拒答，覆盖架构/入库/检索/API/边界/拒答 6 类
- [x] `evidence` 使用来源文档原文并做规范化校验；证据漂移会在加载时报错，防止分数悄悄失真
- [x] `dense` / `hybrid` / `full` 三组策略对比，`full` 明确标注为 v2/v3 默认基线
- [x] 四个 Ragas 指标：faithfulness、answer relevancy、context precision、context recall
- [x] 确定性指标：evidence hit/recall、context precision、MRR、source hit、拒答准确率、引用率/有效性、延迟
- [x] 评估库独立路径（`rag_eval_kb` + `storage/eval`），不与演示服务争抢 Qdrant 本地锁
- [x] Ragas NaN 与指标覆盖率如实展示，不把解析失败替换成 0
- [x] Markdown/JSON 报告包含基线差值、分类别汇总、失败案例、复现命令与限制说明
- [x] `evaluate.py --validate-only` 与不依赖 Ollama/Ragas 的单元测试
- [x] README 与 `eval/README.md` 说明安装、重建、冒烟、全量运行与自评偏差
- [x] 评估语料就是仓库文档：文档定稿后重建评估库并重跑，报告与仓库内容一致

## v5 详细验收（本分支）

- [x] 父子块切分：父块 2000/0，父块内子块 700/100；向量与 BM25 只索引子块
- [x] 子块 payload / BM25 documents 携带父块 `id/index/count/text`，父块 ID 确定性生成
- [x] 命中后按父块去重扩展，`RAG_CONTEXT_MAX_CHARS` 默认 6000；超预算回退子块并保留命中片段
- [x] 旧库没有 `parent` 字段时自动回退子块，无需迁移
- [x] 查询改写 `off` / `multi` / `hyde`：多查询最多 2 个变体、HyDE ≤400 字符；失败回退原问题且不泄露问题正文
- [x] 多查询各自 dense + BM25 后按子块做跨查询 RRF，rerank 始终使用原问题
- [x] API / SSE / 演示页可开关父块与改写，并展示 `used_parent / query_rewrite / query_count / context_chars`
- [x] 生成缓存 key 含配置名、上下文哈希与模型，避免跨配置/跨父块复用错误答案
- [x] 新增 v5 18 条口语化/复杂问题集（15 可答 + 3 拒答），证据逐字校验；v4 40 条回归
- [x] 四组配置前后对比；Ragas 子集 12 条并标注样本数；失败案例与限制写入报告
- [x] 父子块、预算、跨查询融合、改写回退、API 元数据与旧数据兼容的单测覆盖
- [x] README、beginner-guide、eval/README 说明参数、数据流、命令与局限

## v6 详细验收（本分支）

- [x] 新增 `pdfplumber` + `rapidocr-onnxruntime` 依赖，Python 3.9 macOS arm64 wheel 可用
- [x] 扫描件 PDF 经 OCR 后可被检索，页码进入引用（OCR 渲染 200 dpi；72 dpi 下中文识别明显出错）
- [x] 文本型 PDF 的表格被结构化提取，表格名/内容进入引用（依赖页面文字与边框线）
- [x] 扫描件上的表格结构化提取（版面表格识别，依赖位图上的表格检测）：`private_rag/ingestion/table_layout.py` 用 OpenCV 形态学（横/纵结构线开运算）检测网格线定位表格矩形；`rapid-table==3.0.2`（SLANet-Plus ONNX，包内模型离线可用，`RAG_SCAN_TABLE_MODEL` 可预置路径）在裁剪区域识别结构，输入先白色补齐成方形以避免引擎 488×488 非等比拉伸扭曲；把整页 RapidOCR 文本框按表格区域裁剪、平移到 crop 坐标后以 `ocr_results` 传给引擎拼 HTML（引擎不跑内部 OCR）；`filter_ocr_result` 按表格矩形中心点过滤正文 OCR 框，避免正文与表格内容重复；总开关 `RAG_DISABLE_SCAN_TABLE`。消融评估（`eval/v6_scan_table.jsonl` 6 条，`full` 配置，两库 ON/OFF 对照）：`evidence_hit@3` 0.2→1.0、`context_precision@3` 0.2→0.5，报告见 `eval/reports/v6_scan_table_on.md` 与 `eval/reports/v6_scan_table_off.md`
- [x] 文本型 PDF 继续正常工作（不退回 v5 功能）
- [x] 依赖可选：未安装 OCR 后端时 PDF 仍按现有方式处理（文本提取），不阻塞服务启动
- [x] `generation.py` 的 `_source_brief` / `build_citations` 无需改动即可展示页码/表格名
- [x] 全部单测不依赖 Ollama/Ragas/Qdrant

## v6.1 详细验收（只读公网演示）

- [x] `scripts/build_demo_snapshot.py` 用真实加载器导出快照，含扫描件 OCR 文本与表格文档
- [x] 构建器白名单校验：file 源只允许 `data/docs`，sql 源只允许 `data/sql/demo.db`，web 源拒绝构建
- [x] 拆出 `retrieval/tokenizer.py`，只读服务复用同一套分词而不依赖 qdrant-client
- [x] 只读服务不加载模型：依赖清单不含 torch/transformers/sentence-transformers/FlagEmbedding/
      qdrant-client/pdfplumber/rapidocr，可在免费实例（512 MB）上运行
- [x] `/ready` 的 `checks` 显式给出 `embedding_model/reranker/llm = false`，不让只读状态被误读成就绪的完整服务
- [x] 引用条目字段与线上 `CitationOut` 一致，页码与表格名在公网演示页可见
- [x] `/api/v1/chat*` 恒返回 409 `generation_disabled`，不假装能回答
- [x] 只读索引与 `LexicalIndex` 的排序对齐测试；共 12 条只读接口测试，不依赖 Ollama/Qdrant/网络
- [x] 可选令牌 `RAG_DEMO_TOKEN`：设置后 `/api/v1/*` 要求 `X-Demo-Token`
- [x] README 说明只读部署不能代表完整效果（无语义召回、无精排、无父子块、无问答）

## v7 详细验收（本分支）

- [x] 意图路由：`knowledge` / `database` / `mixed` / `none`，由模型顺带给出首个动作，运行时不再预设走哪条链路
- [x] 工具调用：`kb_search`（复用 `RagEngine.retrieve_with_trace` 的全链路）与 `sql_query`（运行时只读查询业务库）
- [x] 运行时 SQL 显式授权：只有 `sources.yaml` 里 `allow_runtime_query: true` 的 sql 源才挂上工具，默认关闭；关掉时模型连这个工具都看不到
- [x] 只读护栏：单条 `SELECT`/`WITH`、写操作与元数据关键字拒绝、表白名单（CTE 名不算表）、强制 `LIMIT` 上限；护栏改写后的 SQL 写进轨迹
- [x] 检索-行动循环：读观察后再决策，三条边界为工具步数（`AGENT_MAX_STEPS`）、证据字符预算（`AGENT_CONTEXT_CHARS`）、SQL 行数（`AGENT_SQL_ROWS`）
- [x] 证据统一编号：不同工具的证据块合并后全局编号，回答里的 `[n]` 与引用列表一一对应（SQL 结果复用 `Citation.table`）
- [x] 失败降级：决策模型不可用退回关键词路由；工具失败作为观察回灌给模型；无证据按既有约定返回拒答文本；agentic 装配失败只让 `/chat/agent` 返回 503，不影响 `/chat`
- [x] 可观测 trace：每请求一行 JSON 追加到 `storage/traces/agent.jsonl`，记录每步理由/参数/观察/行数/耗时，`scripts/replay_trace.py` 与 `GET /api/v1/traces/{trace_id}` 可逐条回放
- [x] `Planner` 可注入：护栏、决策器、工具、循环、轨迹、接口六组共 121 条单测都不依赖 Ollama 与 Qdrant（全量 `pytest tests` 295 passed）
- [x] 决策 JSON 的输出预算独立可配（`RAG_AGENT_PLANNER_TOKENS`，默认 2000）：本机实跑发现默认模型是推理型，400 token 只吐思考不给正文，路由整段回退到关键词判断
- [x] 本机实跑端到端：`init_demo_db` → `ingest` → `chat_agent` → `replay_trace` 全链路跑通，knowledge/database/mixed/none 四类问题各验一次；预算修好后 mixed 问题的意图与 SQL 都由模型自己给出（2 次工具调用，`stop_reason=final`）
- [x] 同一份 JSON 决策输出走原生 function calling 对比（默认模型是 MLX 构建，工具调用格式不稳定，先用 JSON 模式 + 关键词回退）：
      落地实验开关 `RAG_AGENT_PLANNER_MODE=json|function`——`ask_tool_json` 用同一份提示词与同一预算，把决策字段声明成
      工具 schema 交给 `bind_tools`，`tool_calls.args` 直接进 `parse_route`/`parse_decision`；模型没返回 `tool_calls`
      时抛 `PlannerError`，运行时照旧回退关键词路由。本机实跑对比（`scripts/compare_fc_decisions.py`，读数
      `eval/reports/fc_comparison_report.md`，2026-11-22）：主模型 `qwen3.8:27b-mlx` 路由 12/12、步进 3/3 全部拿到可用
      `tool_calls`（动作合法率 1.00），路由单条均耗时 18.8 秒、快于 JSON 模式的 27.0 秒；两通道意图一致率 0.75
      （d02/r01/r03 三条判得不同，该子集没有对错标注，不作优劣判断）、步进动作一致率 3/3。对照组 `qwen2.5:7b-instruct`
      走 FC 只有 4/12 解析成功——提示词里仍写着「只输出 JSON」，该模型多数情况直接吐 JSON 文本而不调用工具，
      这条失败数含「提示词没有改成要工具调用」的混淆，不能读成该模型不支持工具调用。结论：主模型的原生工具调用
      在这份样本上稳定，但 JSON 模式仍是默认通道（任何模型可用、失败可回退，切默认需先用 29 条评估确认无回归），
      `function` 仅作实验开关；+10 条 mock 单测（全量 420 passed）。

## v7.1 详细验收（本分支）

- [x] Agentic SSE 流式端点：`POST /api/v1/chat/agent/stream`，事件顺序 `session → route → step → citations → token → done`
- [x] `AgentRuntime.run_stream()` 返回事件流，与 `run()` 逻辑一致
- [x] `stream_final_answer()` 使用 `ChatOllama.stream()` 逐 token yield
- [x] demo.html 新增 Agentic 模式按钮，Agentic 示例芯片自动切换模式
- [x] 修复流式端点漏写会话历史：`/chat/agent/stream` 原先只读历史不写回，同一 session 的 Agentic
      多轮追问看不到上一轮；现在 `done` 后按非流式 `/chat/agent` 的口径写入，引用从 `citations` 事件还原成
      `Citation`（`done` 事件的 `citations` 可能为空），异常那一轮不写
- [x] demo.html Agentic 模式展示 max_steps / allow_sql 参数，意图/工具调用/决策步数/证据块等 meta
- [x] SSE 端点测试：`/chat/agent/stream` 覆盖完整事件流、会话写回、下一轮历史透传、异常轮不写历史共 4 条
      （先前只写了 `FakeRuntime.run_stream()`，没有任何测试真的请求这个端点，这条勾当时是虚的）
- [x] 列表/表格感知切分：`_split_by_markdown_structure()` 将列表/表格/代码块视为原子单元
- [x] 切分测试：`test_list_preserved_as_unit`、`test_table_preserved_as_unit`、`test_heading_forces_boundary`
- [x] v7 agentic 评估集 `eval/v7_agent_qa.jsonl` 26 条（knowledge 7 / database 6 / mixed 5 / multi_step 4 / refusal 4；v7.3 起 29 条，新增 `coreference` 3 条带对话历史的指代样本），
      evidence 逐字校验；按“需要哪个工具”分类，而不是按文档章节分类
- [x] 评估框架接入 agentic 配置组：`ConfigSpec.is_agent` + `CONFIGS["agent"]` + `collect_agent_results`，
      agentic 与固定链路共用同一套确定性指标；`SampleResult` 增加 `intent/stop_reason/tool_calls/tool_steps/trace_id`
- [x] 新增可选字段 `expected_source`，`source_hit` 忽略引用的 `#` 片段：SQL 证据权威文件（种子脚本）
      与期望引用（`demo.products`）必须能分开表达，否则该指标恒为 0
- [x] 运行时数据源与入库配置分离：`config/sources.eval.agent.yaml` 只挂只读 SQL 工具，评估库仍只入库项目文档；
      `data/sql/demo.db` 缺失时直接失败退出，评估运行不写演示轨迹文件
- [x] 报告区分口径：策略配置表加“链路”列，agentic 组的耗时/查询次数/证据命中与固定链路不同义，逐条写进限制
- [x] 单测覆盖类别扩展、`#` 片段忽略、`expected_source` 校验、agentic 采集与单条失败续跑、报告渲染（全量 `pytest tests` 311 passed）
- [x] agentic vs 固定链路的量化对比报告（`eval/reports/v7_agent_report.md`）：26 条 × 两组实跑，
      agentic `answer_rate` 0.909 / `source_hit` 0.591 / `citation_rate` 0.864，`full` 对应 0.318 / 0.318 / 0.591；
      15 条业务库样本里 9 条真的走 `sql_query`，`full` 组 0 条接触业务数据，`knowledge` 类两组持平（无回归）
- [x] 口径差值如实写入报告限制小节：`@3` 窗口只看前 3 条上下文（SQL 证据常排在第 4 条以后）、
      `evidence_hit` 受 SELECT 列影响、拒答判定只认哨兵文本（agentic `refusal_accuracy` 0.250 是行为差异）
- [x] Ragas 子集复核（`eval/reports/v7_agent_ragas_report.md`）：6 条可回答样本 × `agent`/`full`，
      faithfulness 0.659 vs 0.600、context_recall 0.750 vs 0.250；judge 断连造成的 NaN 与覆盖率如实列出，
      并写明 `full` 组 6 条里拒答 5 条，其 relevancy/recall 低分不能读成同等条件下的答案质量差值
- [x] CI 工作流 `.github/workflows/ci.yml`：`tests` 作业只装 `requirements-ci.txt`（不含 torch/transformers/
      FlagEmbedding）跑全量单测并用 `--validate-only` 校验 v4/v5/v7 三份评估集；`readonly` 作业只装
      `requirements-readonly.txt` 验证 Render 依赖清单够用，并断言只读链路不加载模型依赖
      （`rapidocr-onnxruntime` 带非 headless opencv，`tests` 作业会先试 `import cv2`、缺 libGL 才补装）
- [x] 路由受问法影响：6 条业务库样本被判成 `knowledge`/`none` 而没查库；证据不足时倾向作答而非拒答
      （d06、r04 补了通用知识）→ 已在 v7.2 处理，见下节

## v7.2 详细验收（意图路由与拒答策略）

v7.1 报告量出的两条短板都在「模型看不见它需要的东西」上：路由器只能读到工具的 `description`，而描述里只有
「精确数值查询」这类抽象说明，它不知道 `products.description` 存的就是保存建议，于是「香蕉到底能不能放冰箱」
被判成 `knowledge`；关键词回退同样瞎——`DATABASE_HINTS` 清一色是数字词。第二段短板是答与不答没有判据，
证据不够时模型倾向于硬凑（d06 补了苹果氧化的通用知识，r04 补了商业版定价）。

- [x] 路由判据改成「回答所需的事实住在哪一侧」，而不是问法像不像技术查询：问一条业务记录自身的描述、备注、
      状态、时间，即使一个数字词都没有也算 `database`；只要有一侧被漏掉就选 `mixed`；拿不准且 `sql_query`
      可用时选 `mixed`（漏查库的代价高于多查一次）
- [x] 数据可得性进工具描述：装配 `sql_query` 时对白名单表做**有界采样**（列名 + 每个文本列少量样例值），
      写进 `description` 让路由器看得见库里到底存着什么。刻意不排序、不去重下推（扫到 `LIMIT` 行就停），
      换成真有数据的库也不会把「给路由看一眼」变成全表扫描；`RAG_AGENT_CATALOG_CHARS`（默认 420）限总长，
      `RAG_AGENT_CATALOG_SAMPLES=0` 只列列名不取样例。两点限制一并写明：样例值会进入 prompt（业务数据
      敏感的库应设 0），且本评估集的 evidence 与种子数据同源，d01/d04/d06 的提升幅度在这份数据集上偏乐观
- [x] 采样出的短词同时作为关键词回退的补充线索（`heuristic_route(extra_hints=…)`）：模型不可用时，
      口语化问题也能靠库内实体名落到 `sql_query`
- [x] 证据充分性由步进决策一并声明（`sufficient` / `missing`），不额外加一次判断调用：判不足就不进生成，
      直接返回拒答哨兵并写明缺哪一项，`stop_reason` 新增 `insufficient_evidence`；判部分不足则把缺口带进
      最终提示词，要求回答第一行就是哨兵
- [x] 生成侧禁掉两条偷懒路径：不得用通用常识补证据里没有的事实，不得把「资料里没提到」当成「不存在」
- [x] 路由提示词的 few-shot 全部用虚构表（`orders` / `employees` / 库位编号 / 天气），不出现评估集里的
      实体与表名，并有单测断言这些词没被写进提示词（防评估集泄漏进提示词）
- [x] 单测：充分性解析与拒答短路、缺口透传进最终提示词、目录进工具描述、库内实体驱动回退路由、
      提示词无评估集泄漏（全量 `pytest tests` 338 passed）
- [x] 受控重跑 v7.1 里判错的 9 条（`eval/reports/v7_2_routing_report.md`）：六条业务库样本
      （d01/d04/d06/m01/m04/s01）全部改判为 `database`/`mixed` 并真的引用 `demo.products#sql_query`，
      三条应拒答样本（r01/r02/r04）输出哨兵文本。同样本子集均值
      `evidence_hit@3` 0.000→1.000、`source_hit` 0.000→1.000、`context_precision@3` 0.000→0.889、
      `citation_rate` 0.500→1.000（子集只有 9 条，不能与 26 条的整体口径直接比）
- [x] 全量 26 条复跑（`eval/reports/v7_2_agent_report.md`，同生成模型 `qwen3.8:27b-mlx`、同确定性口径、
      总耗时 1348.6 秒）：`answer_rate` 0.909→1.000、`refusal_accuracy` 0.250→1.000、
      `source_hit` 0.591→1.000、`evidence_hit@3` 0.409→0.864、`mrr@3` 0.348→0.811、
      `citation_rate` 0.864→0.955，单条平均耗时 71.2→51.9 秒（平均检索候选 3.65→2.77、工具调用 1.50→1.31，
      耗时下降的方向与「更少把业务问题送进完整检索链路」一致，但没做归因）
- [x] 回归检查通过：`knowledge` 类 7 条 `source_hit` 与 `evidence_hit@3` 两轮都是 1.000，且这 7 条一次都没
      引用业务库——路由改动没把纯文档问答带偏。分类别 `source_hit`：`database` 0.167→1.000、
      `mixed` 0.400→1.000、`multi_step` 0.750→1.000。15 条业务库类样本里真的引用 `demo.products` 的从
      9 条升到 15 条；意图分布从 `knowledge`15 / `mixed`9 / `none`2（一条 `database` 都没有）变成
      `database`10 / `knowledge`9 / `mixed`6 / `none`1
- [x] `stop_reason` 分布：v7.1 `final`18 / `no_retrieval`2 / `planner_unavailable`6 → v7.2 `final`21 /
      `insufficient_evidence`2 / `no_retrieval`1 / `context_budget`1 / `planner_unavailable`1。决策 JSON
      解析失败从 6 条降到 1 条只作为观察记录，不作归因（提示词改动与模型随机性同时在里面）
- [x] 4 条应拒答样本全部以哨兵文本开头，但路径不同：r03/r04 走 `insufficient_evidence`（先检索、判证据不足），
      r02 走 `context_budget`（12 段证据用尽字符预算后仍按缺口拒答），r01 走 `none`（根本没检索）
- [x] `none` 判据已收紧（同日，`v7.2-none-gate`）：`none` 不再是免检通行证。路由判成 `none` 时不再直接
      拒答，而是先用原问题做一次知识库验证检索——查到证据就照常作答、把上报意图改写成 `knowledge`，查不到
      才返回拒答哨兵、`stop_reason` 记 `no_evidence`（与压根没检索的 `no_retrieval` 区分开），开关
      `RAG_DISABLE_AGENT_NONE_PROBE`；提示词把 `none` 的准入收紧到实时信息、天气、新闻、外部网站内容、
      主观偏好这类明确在本系统之外的事实，问到业务记录、字段或本系统自身文档一律先取证。全量 26 条复跑
      （`eval/reports/v7_2_nonegate_report.md`）：`none` 与 `no_retrieval` 各从 1 条降到 0 条，「一次都没
      检索就拒答」这条路径在全量评估里不再出现；r01 从「判 `none`、零检索、闷头拒答」变成「查两次、按缺口
      拒答并写明缺什么」；4 条应拒答样本仍全部拒答（`refusal_accuracy` 1.000 持平），
      `citation_rate`/`citation_validity` 0.955→1.000。观察记录（只作观察、不归因）：`planner_unavailable`
      1→4；`evidence_hit@3` 0.864→0.818 等检索类五项的 -0.045 逐条核下来来自 m01 一条（五项 1.000→0.000——它这一轮
      两半都答对，但 SQL 证据排在第 4 块，`evaluation.py:486` 只取前 3 块），`answer_rate` 的 -0.045 来自 m03 改成
      拒答（可答样本 22 条，1/22≈0.045），两条判的都是 `mixed`、`none` 探测不在它们身上触发；`no_evidence` 由单测
      覆盖、这轮 26 条没触发。逐条对比与读数限制见 `eval/README.md`
- [x] m01 的文档那一半与指代消解已覆盖（`v7.3-coreference`）：机制侧写死三处提示词规则——「这个项目」「本系统」指本系统自身、
      它的说明文档就在知识库里；没有对话历史绝不是选 `none` 的理由；指代词在历史里找不到先行词时按本系统自身的行为与文档理解，
      不得以「缺少上下文」「无法确定所指」为由停止取证。评估侧补上真正的多轮——`EvalSample.history`（成对 `[role, content]` 严格
      校验，可回答样本禁止 history 含 evidence 原文）由 `collect_agent_results` 传给 `agent.run(..., history=...)`，评估集加 3 条
      `coreference` 样本（c01 文档侧指代、c02 库侧指代、c03 混合），报告用 `history_turns` 标出走了多轮的条目。
      29 条复跑（`eval/reports/v7_3_coreference_report.md`，`n_errors` 0）：c02 把「它」还原成橙子并走 `sql_query`（检索五项全 1.000）、
      c01 按上一轮的精排答对（决策 JSON 解析失败走关键词回退，答案仍正确）、c03 判 `mixed`；m01 这轮 SQL 证据排到第 1 块，
      上一轮被 `@3` 判掉的五项全部回到 1.000；同 26 条子集 `answer_rate`/`source_hit`/`refusal_accuracy` 与上一轮持平
- [x] v7.4 评估口径改双侧：混合问题「查了几侧」现在量得到（诊断见 `docs/v7.4-history-as-evidence.md`
      第三节，本条是它的修法 B）。评估集加可选字段 `expected_sources`（声明「文档侧 + SQL 侧」两侧
      应取证来源），新确定性读数 `both_sides_hit` 要求声明的每一侧都在**全量**引用列表里出现才判 1，
      只统计声明了双侧的 6 条（m01–m05 + c03），其余条目记 `None` 不进均值。两处口径与 `source_hit`
      刻意不同：扫全量引用不套 `@3` 窗口（m04 的 SQL 引用常排在第 4 条），按声明的来源标签匹配而不是
      按 `#sql_query` 片段判侧（这样文档侧也能成为声明的一侧）。**`source_hit`/`evidence_hit@3`/
      `citation_rate` 的定义与逐条取值一字未改**，v7 / v7.2 / v7.3 三轮报告仍可比；`both_sides_hit` = 0.0
      的双侧样本会单独进「失败与关注案例」，现象写「只取到一侧证据」，不再因为 `evidence_hit@3` 满分而隐形。
      353 测试通过。按新口径回算 v7.3 那轮的既有引用数据即得 `both_sides_hit` 5/6 = 0.833，判 0 的正是
      c03，而同一条在那轮 `source_hit` 与 `evidence_hit@3` 都是 1.000——缺口此前不可见的直接证据。
      本轮 29 条实跑读数与逐条对比见 `eval/reports/v7_4_two_sided_report.md` 与 `eval/README.md`
- [x] 修法 A 提示词硬规则：STEP_PROMPT 规则 1 限定 sufficient 只看「已收集的证据」（不含历史），新增规则 10 禁止 mixed 查询在任一侧无证据时选 final；FINAL_PROMPT 新增规则 7 要求历史里的事实必须先查证据，历史段渲染加上「不能作为事实依据」标注（与固定链路一致）。c03 问「刚才那条记录的更新时间」，回答直接抄历史里的 2026-01-15（回答里明写
      「对话历史中已给出」），整条只调了 1 次 `kb_search`、没回查 SQL，SQL 侧没有任何证据。v7.3 保证的是指代能还原，
      不是还原后一定重新取证。（v7.4 先把口径改成双侧，`expected_sources` + `both_sides_hit` 让这个缺口可测；本条
      修的是「抄历史不回查」这个行为本身，不是让它可测，所以 `d43b314` 落地这三条硬规则后才勾上。这句话是条目
      还 unchecked 时写的草稿，勾打上后没跟着改，读数出处复核见下一条）
- **修法 C（运行时硬门禁）**：合入 PR #19（`worktree-fix-c-runtime-gate` 分支）。
  - `config.py` 新增 `AGENT_MIXED_GATE = not _env_flag("RAG_DISABLE_AGENT_MIXED_GATE")`
  - `runtime.py` 新增 `_has_evidence_for(blocks, tool_name)` 辅助方法 + `run()`/`run_stream()` 中的门控逻辑
  - mixed 意图下 `decision.action == "final"` 时检查两侧证据，任一侧零证据则拒绝 final、设置 pending 强制下一次循环
  - 6 个修法 C 单测通过（`TestMixedGate` 类：拒绝/允许/禁用/非 mixed/流式）
  - 三层防御体系完成：修法 B（可观测）→ 修法 A（软约束）→ 修法 C（硬约束）
- **修法 A 的读数出处与一次提示词回退**（复核于 2026-11-21，本分支只改了 `private_rag/agent/prompts.py` 一个文件）：
  - **规则被回退过**：上一条那三条硬规则在 `978a63f`（标题是「A/B 重复采样验证修法 A 效果」）里被删掉了——该提交
        往仓库加了 20 个 `eval/reports/c03_[ab]_run*.{json,md}` 文件，同时把 `prompts.py` 改回 `d43b314` 父提交的
        逐字节相同内容，即 STEP 规则 1 的「对话历史不算事实依据」、整条规则 10、FINAL 规则 7、历史段那句
        「不能作为事实依据」标注。该状态下 `tests/test_agent_planner.py::TestCoreferenceResolution` 四条单测失败、
        全量 353 通过（本表 v7.4 那行写的「357 测试通过（+4 修法 A 单测）」当时不成立）。已用
        `git checkout d43b314 -- private_rag/agent/prompts.py` 恢复，恢复后全量 357 通过。
  - **独立复现**：误删状态复采 c03 五次得 1/5（`tool_calls` 全为 1），恢复后复采五次得 5/5（`tool_calls` 全为 2），
        与已提交的 A/B 两组一致，修法 A 的方向与幅度复现了。但每边只有 5 次，且下面第三条说明无规则时测出过
        满分，所以这条读数只能读成「缺口有无受这三条规则影响很大」，不能读成稳定速率。
  - **出处记不清**：A/B 十个文件的 `meta.git_commit` 都是 `d43b314`，B 组是在工作区临时删掉规则跑出来的，而
        `git_commit` 只写 HEAD、全仓没有工作区是否干净的标记，光看仓库区分不了两组，只能靠行为差异反推。头部读数
        `eval/reports/v7_4_two_sided_report.json` 记 `2e912379f`，其 `prompts.py` 里三条硬规则的计数全为 0，而
        `both_sides_hit` 字段要到后代提交 `28481a7` 才入库，说明这份报告跑在未提交的工作区上。结论：
        6/6 = 1.000 测于修法 A 存在之前，不能当它的依据，只能佐证本表已写的「缺口是间歇的」；支撑修法 A 的只有
        c03 那十个 A/B 文件。
  - **隐患**：生成缓存键是 `(配置名, 问题, 召回 chunk id 元组, 上下文摘要, 模型)`，不含提示词
        （`private_rag/evaluation.py:727`）。已提交的 A/B 十次 `generation_cached` 全为 False，那批数是真的；但以后
        只要召回不变，改完提示词重跑会直接命中缓存里的旧答案，A/B 会得出「改了没效果」的假结论。提示词类 A/B
        一律带 `--no-cache`，并把两组各自的提交号与提示词摘要写进 `run_note`，`git_commit` 另加一条 dirty 标记。
  - 不受影响的读数：v4 / v5 / v7 / v7.2 / v7.3 五份报告都测于修法 A 之前、`both_sides_hit` 均为 `None`，
        测的就是当时的代码，与这次回退无关。

## v8 详细验收（本分支）

把本机知识库暴露成 MCP tools，让外部 MCP 客户端（Claude Desktop / Claude Code）能把检索与问答当工具调用。
链路与 CLI / HTTP 完全同一条：`RagEngine.readiness()` → `retrieve_with_trace()` → `answer()`，没有第二套检索。

- [x] 选型核实：官方 `mcp` 包从 0.9.1 到 2.3.0 的 `requires_python` 一律 `>=3.10`，`pip install mcp` 在 3.9
      被解析器直接拒绝 → 手写最小 stdio JSON-RPC，零新增依赖，`requirements-ci.txt` 与 CI 都不用改，
      见 `docs/adr/0004-mcp-stdio-without-sdk.md`
- [x] `private_rag/mcp/server.py`：`initialize`（三个协议版本日期协商，客户端带未知日期时回落到我方版本）、
      `ping`、`tools/list`、`tools/call`；`notifications/*` 不回应；`resources/*` 与 `prompts/list` 返回空列表
      而不是 method not found（客户端会顺手调）；漏 `id` 的请求也回应，否则客户端会等一个不会来的响应
- [x] 通道约定：stdout 只写协议（每行一个 JSON 对象、`ensure_ascii=False` 保证中文可读），日志与启动提示走 stderr；
      坏行按 `parse_error` / `invalid_request` 回应而不退出进程
- [x] `private_rag/mcp/tools.py`：只读工具 `kb_search`（只检索）与 `ask`（检索 + 作答），
      入参校验与 HTTP 请求模型同一口径（`limit` 1–10、`tags` ≤10 个、单个 ≤50 字、`source_type` 枚举、
      未知参数直接拒），工具定义声明 `readOnlyHint: true` / `destructiveHint: false`
- [x] `sql_query` 包 agentic 那颗运行时查询工具（不重写护栏）：`sql` 必填、≤2000 字，返回护栏改写后
      实际执行的 SQL、行列结果与 `source_type=sql` 的引用；写操作、多语句、白名单外的表按 `sql_guard`
      原样拒绝，`LIMIT` 超过上限会被压缩（测试用临时 SQLite 跑真护栏）
- [x] 装配共用一处：把 `build_agent_runtime` 里的 sources.yaml 授权装配抽出为 `build_tool_registry`，
      MCP 入口取它的 `sql_query`，`build_agent_runtime` 变成薄委托（签名不变，agentic 430 条测试无回归）
- [x] 引用口径复用 `agent/tools.py` 的 `citation_label` 与 `build_citations`：文本里 `[n]` 与 `citations[].index`
      一一对应，`page` / `table` 能回溯；证据字符预算随段数缩小，一次调用的总字符有界
- [x] 失败面：知识库未就绪给与 `/ready` 同源的原因；Qdrant 本地模式被占用翻译成可操作的下一步
      （停掉 `serve.py` 或用 `RAG_STORAGE_DIR` 换目录）；工具层失败按 MCP 约定回 `isError` 结果而不是协议错误；
      工具内部没兜住的异常不打死服务
- [x] 只读边界：没有入库/写库入口（读与写是两条边界）；运行时 SQL 默认不暴露，要过三重门——
      `--allow-sql` 显式开、`sources.yaml` 的 `allow_runtime_query: true`、`sql_guard` 只读护栏，
      见 `docs/adr/0006-mcp-sql-query-behind-three-gates.md`（取代 `docs/adr/0005` 的「不暴露 SQL」一条）
- [x] `scripts/serve_mcp.py` 保持薄（照 `scripts/sync_gdrive.py` 的写法）：`--no-rerank`（在导入 config 前设环境变量）、
      `--top-k`、`--log-level`、`--list-tools`（不打开知识库、不需要 Ollama，只打印工具定义后退出，也不装配
      `sql_query`）、`--allow-sql`、`--sources`；打不开知识库时退出码 3 并在 stderr 写明原因；
      开了 `--allow-sql` 但配置缺授权或缺文件/连不上库，服务照样起、只暴露两个知识库工具并在 stderr 说清原因，
      不静默降级
- [x] 测试 99 条新增（`tests/test_mcp_tools.py` + `tests/test_mcp_server.py` + `tests/test_agent_loop.py`）：
      全部注入假引擎与打桩的 `build_engine`，不打模型、不联网、不打开真实 Qdrant；入口脚本用 importlib 按路径加载；
      SQL 用临时 SQLite 跑真的 `sql_guard` 与真引用。
      全量 `python3 -m pytest tests -q` = **529 passed**（既有 430 + 新增 99），0 failed / 0 skipped
- [x] 本机实跑（2026-11-22，演示库 + 本机 Ollama `qwen2.5:7b-instruct`，`--no-rerank`）：`initialize` 协商
      `2025-06-18` 并回显 `serverInfo`；`tools/list` 只有 `kb_search` / `ask`；`tools/call kb_search` 返回
      `[1]`/`[2]` 编号证据与带 `page` 的 `structuredContent.citations`；`ask` 返回带 `[1][2]` 的回答、
      `meta.retrieved=2`、`elapsed_ms≈11200`。该轮 trace 显示 `used_lexical=false`、`used_rerank=false`：
      前者是那份 `storage/` 的词法索引不在、按既有规则降级为纯向量，后者是 `--no-rerank` 显式关掉，都不是本分支引入
- [x] 单进程限制的降级实测：同一 `storage/` 被另一个 MCP server 占着时，第二个进程退出码 3，
      stderr 给出「请先停掉 scripts/serve.py…或用 RAG_STORAGE_DIR」
- [x] 文档：README「MCP 暴露（v8）」（客户端配置片段、参数表、三重门、与 HTTP API 的区别、单进程限制与降级、
      只读边界、`--list-tools` 自检）、`docs/adr/0004`、`docs/adr/0005`（含被 0006 取代的状态标注）、
      `docs/adr/0006`、`CONTEXT.md` 新增「工具面」「协议通道」两个术语
- [x] `--allow-sql` 的本机实跑（2026-11-23，`config/sources.demo.yaml` + `data/sql/demo.db` + 主仓 `storage/`）：
      `tools/list` = `kb_search` / `ask` / `sql_query`；`tools/call sql_query` 用演示库的授权位跑通，
      `isError=false`、`row_count=3`、`columns=[name, price]`、`structuredContent.sql` 回显护栏后的 SQL、
      引用 `source_type=sql` / `table=demo.products` / `source_uri=demo.products#sql_query`；
      `UPDATE products SET price = 1` 被护栏拒（`isError=true`，原因「只允许 SELECT 或 WITH 开头的查询」）
- [x] 与 PR #22（分支 `claude/v8-mcp-server`，同样以 `main` 为基线）的冲突已裁决（2026-11-23）：以本分支为主干，
      #22 已关闭并留下说明。两边在 `sql_query` 加回以后只剩引用载荷一处差异——那边每条引用附块级 `locator`
      （`doc_id` / `chunk_index` / `snippet_offset`，为此改了 `agent/tools.py`），本分支沿用既有 `Citation` 字段、
      只把 `agent/runtime.py` 的装配抽出成 `build_tool_registry`（行为等价，没碰 `agent/tools.py`）。
      分支与提交 `6d24ac2` 保留在远端，`locator` 从那里取
- [ ] 块级 `locator`（从已关闭的 PR #22 的 `6d24ac2` 取）：`ToolBlock.locator` + `chunk_locator()` / `sql_locator`，
      让引用能回溯到 `doc_id` 与块内偏移。与本分支的三重门互不影响，尚未做
- [ ] 流式工具结果与进度通知：手写实现只支持一次性结果，长调用（`ask` 十几秒）在客户端侧只能表现为「这次调用慢」
- [ ] 鉴权与多用户：当前与本机 HTTP 服务同为「能启动进程的人即可读」，开了 `--allow-sql` 之后还包括白名单内的
      业务表，要给别人用需要先加鉴权或换 Qdrant server 模式

## v8.1 详细验收（决策调用的思考开关与单条 agentic 延迟）

v7.5 双侧门控打开以后 agentic 单条 88.1s，PR #24 给运行时挂上 `sql_query` 以后涨到 110.1s。
本轮只动延迟，答案口径（提示词、门控、步数、指标判定式）一律没动。

- [x] 耗时归属：报告把单条耗时分成「工具执行」与「其余」两块，基线 110.1s 里 `avg_retrieval_ms` 只有
      1.2s，其余 108.9s 全在 LLM 调用（路由 + 每次步进决策 + 最终作答）。轨迹里只记路由耗时
      （`storage/traces/agent.jsonl` 均值 29.3s），步进决策的时间没有单独记录，因此用
      `scripts/measure_planner_latency.py` 直接量单次调用：同一条真实证据文本、`temperature=0`、
      `num_predict=AGENT_PLANNER_TOKENS`。实测路由开思考 40.4 秒（245 输出 token）→ 关思考 6.3 秒
      （61 token），步进决策 62.8 秒 → 5.1 秒，最终作答 52.8 秒 → 5.7 秒。
      注意最终作答那一格当时带的是 `num_predict=AGENT_PLANNER_TOKENS`（运行时不带这个上限），
      后来按运行时参数重测是 10.3 秒 → 2.95 秒，最终作答的数字以 v8.2 为准。
- [x] FC 通道不是提速点：同一条提示词换成原生 function calling，路由关思考 6.9 秒、步进关思考 8.1 秒，
      比 JSON + 关思考更慢，输入还多约 320 token（工具 schema 占用），且实测 m01 步进有一次没返回
      `tool_calls`（MLX 模型的工具调用格式不稳，正是 v7 选 JSON 通道的理由）。方向与上面 v7.5 那组
      FC 对比读数一致。
- [x] 改动：`RAG_AGENT_PLANNER_THINKING` 按环节开关，取值 `off` / `route` / `step` / `all`（真值等价 `all`，
      假值等价 `off`，拼错回退默认），默认 `route`。`ask_json` 增加 `kind`，`ask_json` / `ask_tool_json`
      按环节传 `reasoning`，`LlmPlanner._ask` 把环节透传给两条输出通道。最终作答不读这个开关。
- [x] 为什么默认保留路由的思考，而不是全关：先跑两个环节全关（`off`）那一轮，单条 110.1s → 24.5s（-77.7%）
      但 `answer_rate` 1.000 → 0.880 —— s01 / s02 / s03 三条 multi_step 的路由意图从 `database` 变成
      `mixed`，v7.5 的双侧门控于是禁止提前 `final`，四次工具调用把关键 SQL 结果挤出
      `AGENT_CONTEXT_CHARS=7000` 的证据预算，最终按缺证拒答。保留路由的思考、只关步进决策，
      29 条意图逐条与基线一致，四项指标不回退。
- [x] 前后对比（`eval/v7_agent_qa.jsonl` 29 条、生成模型 `qwen3.8:27b-mlx`、串行单并发、同一隔离评估库
      `rag_eval_v8_latency` + `storage/eval/qdrant_v8_latency` + `storage/eval/lexical_v8_latency/bm25.pkl`
      共 245 个向量，入库侧三轮都没动；`--skip-ragas`，只比确定性指标）：

| 轮次 | 出处 | 单条均耗时 | 检索 | 生成 | answer_rate | source_hit | refusal_accuracy | both_sides_hit | evidence_hit@3 | context_precision@3 | mrr@3 | avg_tool_calls | 均步数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 基线：决策全开思考 | `530418c`（dirty 仅测试文件） | 110.1s | 1.2s | 108.9s | 1.000 | 0.960 | 1.000 | 1.000 | 0.800 | 0.560 | 0.7267 | 1.931 | 4.62 |
| `off`：两个环节全关 | `e46f59b`（测时提交，分支上已合成一条） | 24.5s | 0.8s | 23.7s | **0.880** | 0.960 | 1.000 | 1.000 | **0.720** | **0.427** | **0.6533** | 1.931 | 4.38 |
| **默认 `route`** | `8704ef5`（测时提交，分支上已合成一条） | **56.6s（约 -49%）** | 1.0s | 55.6s | 1.000 | 0.960 | 1.000 | 1.000 | 0.800 | **0.533** | 0.7267 | 1.931 | 4.45 |

- [x] 默认档唯一的一项指标回退：`context_precision@3` 0.560 → 0.533（-0.027），同时 `avg_context_chars`
      1481 → 1575。步进决策不思考以后取证顺序略有变化，塞进最终上下文的块里无关块占比略升；
      `evidence_hit@3` 与 `mrr@3` 没动，说明该召回的还是召回到，只是排在前面的噪声多了一点。
      29 条样本上 -0.027 是 1 个量级的差别，只能当方向看。
- [x] 逐条变化（默认档 vs 基线）：意图 29/29 全部一致；`planner_unavailable` 三条（c01 / d05 / k05）全部变成
      `final`——思考不再吃掉 `AGENT_PLANNER_TOKENS` 的输出预算，决策 JSON 不再被截断，这是本改动附带的
      稳健性收益；d02 从 `max_steps` 变 `final`、门控拦截 3 → 1；m03 多一次门控、m04 门控 2 → 1、
      s03 多一次工具调用但都照常作答；r02 少一次调用；逐条 `source_hit` 没有变化；`n_errors` 0。
- [x] `off` 档的回退点名（这就是不把全关当默认的依据，逐条见 `eval/reports/v8_latency_thinking_off.md`）：
      s01 / s02 / s03 三条 multi_step 的路由意图 `database` → `mixed`、`stop_reason` `final` → `max_steps`、
      拒答 False → True，即三条应回答的样本被误拒（`answer_rate` 22/25 = 0.880）；k05 的 `source_hit`
      1.0 → 0.0（`evidence_hit@3` 与 `mrr@3` 因此掉到 0.720 / 0.6533），m04 的 `source_hit` 0.0 → 1.0；
      r01 / r03 两条应拒答样本的意图从 `mixed` 变成 `knowledge` / `database`，`stop_reason` 从 `max_steps`
      变 `insufficient_evidence`（拒答仍然正确，所以 `refusal_accuracy` 保持 1.000）。
- [x] `avg_tool_calls` 三轮都是 1.931：关掉步进思考并没有让循环提前停止取证（门控与提示词仍守着两侧），
      省下的时间全部来自决策调用本身，没有靠少查换速度。
- [x] 测试 7 条新增（`tests/test_agent_planner.py`、`tests/test_agent_function_calling.py`）：默认档路由保留
      思考 / 步进关思考、`all` 与 `off` 的生效、最终作答不带 `reasoning`、`_env_stages` 取值解析含非法值回退、
      function calling 两条通道的同一环节判断；全部注入假 `ChatOllama`，不打模型、不联网。
      全量 `python3 -m pytest tests -q` = **536 passed**（既有 529 + 新增 7），0 failed / 0 skipped。
- [x] 报告：`eval/reports/v8_latency_baseline_thinking_on.{md,json}`、
      `eval/reports/v8_latency_thinking_off.{md,json}`、`eval/reports/v8_latency_route_thinking.{md,json}`。
      基线那轮是在工作区把两个延迟相关文件临时退回 `530418c` 跑的（`git diff > /tmp/v8_thinking.patch`
      再 `git checkout --`，跑完 `git apply` 还原），所以它的 `meta.git_dirty=true` 来自未提交的测试文件；
      `route` 档那轮 `meta.git_dirty=true` 来自正在写入的报告文件本身，代码与本 PR 的提交一致。
      三份报告头的 `meta.git_commit` 记的是测量当时的提交，其中 `e46f59b` / `8704ef5` 后来合成一条，
      只在这些报告里留痕——引用出处时以测时状态为准，别拿它们当分支上的提交。
      第三轮开头精排模型加载要做联网 HEAD 校验而当时 `huggingface.co` 不通，重试 5 次以后 transformers
      回退本地缓存继续跑，没有换模型也没换检索配置。
- [x] 口径与限制，引用这组数字时一并带上：
      - **29 条样本只支撑方向**，不支撑百分比：±1 条就是 ±4 个百分点，`context_precision@3` 那种 -0.027
        的量级也在噪声范围内；要说「稳定降到多少」需要更大的评估集。
      - **`avg_latency_ms` 是端到端单条耗时**，含路由、每次步进决策、工具执行、双侧门控的重试与最终作答，
        不是纯模型时间；本轮 56.6s 里工具执行 1.0s，其余都是 LLM 调用。
      - **换机器、换模型、换精排就不可比**：这组数测于本机 Ollama 0.40.16 + `qwen3.8:27b-mlx` + `bge-m3`
        + `BAAI/bge-reranker-v2-m3` + MLX 后端，串行单并发。模型或后端任一变化都要重测基线再比。
      - 三轮同条件（同一隔离评估库、同 `--skip-ragas`、串行单并发），入库侧没动，语料快照一致。
- [x] 最终作答的思考：已加开关并按运行时参数重测，结论是「省约 7 秒但换来三条误拒」，默认保留思考，
      读数与逐条变化见下面的 v8.2。
- [ ] 换成更小的决策模型（`RAG_AGENT_LLM_MODEL`）本轮没测：关掉思考以后决策调用只剩 5–6 秒，
      换模型的收益要连同意图判定稳定性一起量。

## v8.2 详细验收（最终作答的思考开关）

v8.1 把决策调用的思考按环节关掉以后，单条 56.6 秒；这一轮量剩下的最大项——最终作答要不要先思考。
只加开关，提示词、门控、步数与指标判定式都没动。

- [x] 开关：`RAG_AGENT_ANSWER_THINKING` 取 `keep` / `on` / `off`，默认 `keep`——`keep` 连 `reasoning`
      都不传，保持模型默认；只有显式 `on` / `off` 才写死这个参数。`generate_final_answer` 与
      `stream_final_answer`（MCP 服务与 demo 的流式路径）共用 `_final_llm`，其余生成参数一字未改。
      经典 RAG 链路在 `private_rag/generation.py` 里另有一处 `ChatOllama`，不受这个开关影响。
- [x] 单次实测（按运行时真实参数，同一证据文本、`temperature=0`、不带 `num_predict`）：
      `keep` 10.30 秒、`on` 10.30 秒、`off` 2.95 秒。`keep` 与 `on` 相同，说明默认确实在思考；
      关掉一条省约 7.4 秒。v8.1 里记的「52.8 秒 → 5.7 秒」是测量脚本带 `num_predict=2000` 那一格，
      高估了这一项，以本节为准。
- [x] 前后对比（同一提交 `8cb27ad`、同一隔离评估库 `rag_eval_v8_latency`、同模型、串行单并发、
      `--skip-ragas`、离线加载精排）：

| 轮次 | 最终作答 | 单条均耗时 | 检索 | 生成 | answer_rate | source_hit | refusal_accuracy | both_sides_hit | citation_rate | citation_validity | evidence_hit@3 | mrr@3 | context_precision@3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | `keep`（默认） | 40.6s | 0.9s | 39.6s | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.840 | 0.7667 | 0.547 |
| B | `off` | 36.6s（约 -10%） | 1.4s | 35.2s | **0.880** | **0.960** | 1.000 | 1.000 | 1.000 | 1.000 | **0.800** | **0.7267** | 0.560 |

- [x] 逐条变化（B vs A）：`c03` / `m01` / `m04` 三条应回答样本从作答变成拒答（`answer_rate` 22/25 = 0.880），
      `m04` 同时 `source_hit` 1.0 → 0.0（拖低 `evidence_hit@3` 与 `mrr@3`）；`d02` 意图 `mixed` → `database`、
      工具调用 3 → 1；`r01` 意图 `knowledge` → `mixed`、`insufficient_evidence` → `max_steps`（拒答仍正确）;
      `n_errors` 0。引用两项（`citation_rate` / `citation_validity`）保持 1.000，说明关思考不影响引用格式，
      影响的是「要不要承认证据不足」。
- [x] 结论：默认保留 `keep`。引用指标守住了，但三条误拒是不能接受的代价，而省下的只有约 4 秒/条（-9.7%）。
      开关与读数留下，想换速度用 `RAG_AGENT_ANSWER_THINKING=off`，并自己承担误拒。
      （两条数字都在 v8.3 里更正过：误拒实际是 1 条，耗时收益在交替复核下是 -8.3%（4 条短样本），
      全量 29 条不可判定。结论不变。）
- [x] 抖动这条限制必须一起看：A 轮与 PR #26 里同配置的 route 档（同默认行为）测得 40.6s vs 56.6s、
      `source_hit` 1.000 vs 0.960、`d02` 意图 `mixed` vs `database`。`temperature=0` 下 MLX 仍有
      非确定性，取证次数与拒答都会随之变，29 条样本上 ±3 条拒答可能就是噪声。要判这种量级的差异，
      需要更大的评估集或同配置重复轮——下一轮先解决这个，再谈继续压延迟（已做，见下面的 v8.3）。
- [x] 测试 7 条新增（默认 `keep` 不传 `reasoning`、`on`/`off` 显式传、显式参数优先于配置、
      流式路径共用开关、无证据不调用模型、取值归一含非法值回退），全量 `python3 -m pytest tests -q` = 543 passed。

## v8.3 详细验收（评估的可复现性：先修测量）

背景：v8.2 的前后对比没法归因。同一条默认配置的两轮读数（v8.2 的 A 轮与 PR #26 的 route 档）
对不上——单条均耗时 40.6s vs 56.6s、`source_hit` 1.000 vs 0.960、`d02` 意图 `mixed` vs `database`，
抖动和要测的效果同量级，所以先修测量本身。

根因：最终作答的 `ChatOllama` 从来不传 `temperature`（改动前只传 `num_ctx` 与 `keep_alive`），
用的是服务端默认抽样；决策调用虽然传了 `temperature=0`，但整条链路没有一处传 `seed`。

- [x] 新增 `RAG_AGENT_ANSWER_TEMPERATURE`、`RAG_GENERATION_TEMPERATURE` 与 `RAG_LLM_SEED`：默认都不传，
      运行时行为一字未改；`configure_eval_env()` 写入默认值 `0`、`0` 与 `7`，只影响评估链路
      （serve/demo 不读这里），已有值优先。采样参数集中在新模块 `private_rag/llm_params.py`，
      从 `config` 现读（不是导入时抓快照），所以四条调用共用同一处逻辑：agentic 的最终作答、
      步进/路由决策、经典链路的生成、查询改写。
- [x] 同一证据文本重复调用（输出取 SHA256 指纹）：

| 参数组合 | 重复次数 | 唯一输出数 | 结论 |
| --- | --- | --- | --- |
| 不传 temperature（改动前的最终作答） | 4 | 4 | 每次都不同——就是两轮读数对不上的原因 |
| `temperature=0` + 关思考 | 4 | 1 | 输出一致 |
| `temperature=0` + 保持思考（默认档） | 2 | 2 | 仍有抖动 |
| `temperature=1` + 固定 seed | 3 | 3 | 这台 MLX 后端不接受 seed |
| 步进决策（`temperature=0`、默认关思考） | 3 | 1 | 可复现 |

**更正（同日，把经典链路一起钉上以后重测）**：`temperature=0` + 关思考下的 agentic 最终作答是
「基本一致」而不是「逐字保证」——累计 9 次调用里 1 次给出不同文本（指纹 `55d0eeabdeba`，其余 8 次同为
`25da4c7c958b`），同一进程内连续四次则完全一致。也就是这台 MLX 后端的贪心解码（每次挑概率最高的词）
偶发不稳定，复现是大概率一致。所以逐字比对不能当证据，可复现性只在指标层成立（同配置三轮九项指标完全一致）。

- [x] 两条口径由此确定：复现只能靠 `temperature=0`，`seed` 在这台后端上没有实际作用（写进代码注释，
      别指望它）；默认档带思考，仍会抖，所以单次跑出的结论不能直接当因果。
- [x] 范围覆盖两条链路（用户随后要求「经典链路也钉上」）：经典 RAG 的生成
      （`generate_answer` / `stream_answer`）以前同样不传温度，跑 `full` / `none` 这些配置的评估
      照样重跑不一致，现在与 agentic 共用同一套参数；查询改写原本就传 `temperature=0`，现在带上
      同一个 seed。噪声底先量了 agentic 链路（见下面），随后补跑固定链路 `full` 同配置三轮
      （报告 `eval/reports/v8_noise_floor_classic_{1,2,3}`），两条链路都有整轮对照了。
- [x] 钉上以后重测同一件事（`--thinking off`，同一条证据重复调用，`check_generation_determinism.py`
      现在同时量三处调用）：

| 调用 | 钉死（温度 0 + seed 7） | 改动前（都不传） |
| --- | --- | --- |
| 经典链路生成 | 重复 4 次 → 1 个输出 | 重复 2 次 → 2 个输出 |
| 步进决策 | 重复 4 次 → 1 个输出 | 重复 2 次 → 1 个输出（本来就传温度 0） |
| agentic 最终作答 | 重复 4 次 → 1 个输出 | 重复 2 次 → 2 个输出 |

      钉上温度让经典链路从「每次都不同」变成同一输出，跑 `full` / `none` 的前后对比从此可比；
      步进决策一直可复现，没变化。
- [x] 修掉测量脚本自身的两个坑（否则读数会误导）：思考开关 `RAG_AGENT_ANSWER_THINKING` 在
      `private_rag/agent/prompts.py` 里是导入时读的值，脚本只改 `config` 不会被读到（那次「2 次 2 个输出」
      其实测的是默认档而不是关思考）；`generate_answer` 返回「答案 + 引用列表」，取指纹要只取答案文本。
- [x] 同配置重复三轮量噪声底（默认档、同一隔离评估库、串行、同一提交，报告
      `eval/reports/v8_noise_floor_repeat_{1,2,3}`）：
      - 九项质量指标三轮完全一致：`answer_rate` 1.000、`source_hit` 0.960、`refusal_accuracy` 1.000、
        `both_sides_hit` 1.000、`citation_rate` 1.000、`citation_validity` 1.000、`evidence_hit@3` 0.800、
        `mrr@3` 0.7333、`context_precision@3` 0.5333，`n_errors` 0。钉死温度以后指标级别是可复现的。
      - 答案措辞每对轮次有 4~7 条不同（29 条里），但一条都没改变指标 —— 措辞变化不能当证据。
      - 逐条行为（意图 / 停止原因 / 取证次数）只有 `r01` 在第 1 轮翻动
        （`knowledge` → `mixed`、`insufficient_evidence` → `max_steps`、工具调用 1 → 4），第 2、3 轮一致。
      - 耗时：第 1 轮比第 2、3 轮慢 52%（47.9s vs 31.5s / 31.4s），这是冷启动——第 1 轮要把生成与精排
        模型加载进来；第 2 轮与第 3 轮只差 0.1s（0.2%）。
- [x] 固定链路（`full`：混合召回 + 精排，一次检索一次生成）同配置重复三轮量噪声底（默认档、同一隔离
      评估库、串行、同一提交，报告 `eval/reports/v8_noise_floor_classic_{1,2,3}`）：
      - 九项指标里八项三轮完全一致：`answer_rate` 0.440、`source_hit` 0.320、`refusal_accuracy` 1.000、
        `both_sides_hit` 0.000、`evidence_hit@3` 0.240、`mrr@3` 0.220、`context_precision@3` 0.080，
        `n_errors` 0。
      - 只有 `citation_rate` / `citation_validity` 从 0.920 变到 0.960（第 3 轮），差的正好是 1 条样本
        `d03`：它三轮都拒答、都引用同样三条来源，只是第 3 轮的拒答文案里带了 `[编号]`。这两个指标数的是
        「答案里有没有引用编号、编号在不在引用条数范围内」，所以措辞一动它们就会动——这是整套口径里唯一
        一处措辞能直接推动指标的地方，比对时要把这类样本单独拎出来看。
      - 逐条行为（拒答 / 意图 / 停止原因 / 引用命中 / 取证命中 / 证据字数）三轮一条都没翻动，差别只在
        答案措辞：第 1 轮与第 2、3 轮各 11 条不同，第 2 与第 3 轮 5 条不同。
      - 耗时：第 1 轮 31.0s 比第 2 轮 22.1s、第 3 轮 21.7s 慢 40.7% / 42.9%（同样是冷启动，18 条慢过 5 秒）；
        第 2 与第 3 轮只差 0.3s（1.6%），逐条只有 `c03` 差 6.6s。检索只占 1.3s，其余全是生成。
      - 这套数字不能与 agentic 链路横向比：`eval/v7_agent_qa.jsonl` 是按 agentic 设计的（多步、混合问题、
        要查库），固定链路一次检索一次生成，25 条可答里只答了 11 条，所以 `answer_rate` 只有 0.440、
        `evidence_hit@3` 只有 0.240；`both_sides_hit` 0.000 也是同一原因（它不会去查 SQL 那一侧）。
- [x] 判定规则（按上面的噪声底写死，以后每轮对照都照这个来）：
      1. 质量指标可比：同配置三轮里 agentic 九项完全一致、固定链路八项一致，指标变化 ≥ 1 条样本
         （29 条上约 3.4pt）才值得当真，更小的变化记为不可判定。
      2. 耗时读数要作废第一轮，用连续跑的第二轮及以后；连续轮次之间 agentic 差 0.2%、固定链路差 1.6%，
         比这更小的提速不算读数。
      3. 措辞变化不作为证据（agentic 每对轮次 4~7 条/29、固定链路 5~11 条/29），只有指标与逐条行为
         （拒答、意图、停止原因）才算；例外是上面 `d03` 那种引用编号——它数的是文本本身，措辞会推动它。
- [x] 顺带回答 v8.2 悬着的问题：同一提交、同一钉死设置下重跑「关思考」
      （`eval/reports/v8_latency_final_thinking_off_pinned`），与暖轮基线（repeat_2 / repeat_3）两两比对：
      `answer_rate` 1.000 → 0.960，只有 `c03` 一条从作答变成拒答，`m01` 与 `m04` 这次都正常作答；
      其余八项指标与基线完全一致（`source_hit` 0.960、`refusal_accuracy` 1.000、`both_sides_hit` 1.000、
      `citation_rate` / `citation_validity` 1.000、`evidence_hit@3` 0.800、`mrr@3` 0.7333、
      `context_precision@3` 0.5333、`n_errors` 0）。所以 v8.2 记的「三条误拒」里有两条是噪声，
      关思考的真实代价是一条误拒、`answer_rate` 降 4pt——按口径 1（≥ 1 条样本当真），这个代价仍然成立，
      默认保留 `keep` 的结论不变。
- [x] 耗时要交替着量，单轮跨配置不可信。off 轮测出 34.8s，比暖轮基线 31.5s 还慢 10%，与单次实测
      （最终作答 10.3s → 2.95s）方向相反。用 4 条子集在同一进程里跑 `keep → off → keep` 复核：
      两次 `keep` 的每条耗时完全相同（16.2 / 11.8 / 6.9 / 8.1 秒），`off` 均耗时 9.9s 对 `keep` 10.8s
      （-8.3%）。所以钉死温度以后耗时也是可复现的，但整轮对照会受进程冷热与取证内容差异影响，方向都可能反——
      口径 2 补一句：跨配置比耗时要在同一进程里交替跑（A/B/A），不能各跑一轮就下结论。
      v8.2 记的「关思考省约 10%」是两轮各自冷启动下测的，本轮交替复核给的是 -8.3%（4 条短样本），
      全量 29 条的耗时收益目前不可判定。

## v8.4 详细验收（agentic 延迟：先量清时间住在哪，再压路由的思考长度）

背景：v8.1 只关步进决策的思考，默认档 56.6s；v8.2 试过关最终作答的思考（换来一条误拒，默认保留）。
剩下的时间到底住在哪个环节，此前没有单独量过（报告只分「工具执行」与「其余」两块）。本轮先量再改，
只动延迟：提示词的规则文本、双侧门控、步数、指标判定式一字未改。

- [x] 新增 `scripts/profile_agent_calls.py`（只测量、不改行为）：包住决策与最终作答的模型调用，
      按环节记耗时、输入/输出 token 与提示词长度，第一次决策算作路由。用法与读数：

| 环节 | 合计 | 占整轮 | 次数 | 平均 | 输入 token | 输出 token | 最长提示词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 路由 `route` | 566.5s | 48.1% | 29 | 19.5s | 36368 | 12256 | 2667 字符 |
| 步进决策 `step` | 413.1s | 35.1% | 48 | 8.6s | 68584 | 3110 | 5328 字符 |
| 最终作答 `final` | 174.0s | 14.8% | 7 | 24.9s | 10579 | 3057 | 4321 字符 |

      整轮 1177.2s、29 条、平均 40.6s（`/tmp/profile_full_baseline.json`，测于 `4cb9fb6`，
      工作区只多了未提交的测量脚本）。工具执行只占约 2%（v8.1 记的 `avg_retrieval_ms` 1.0s），
      所以能压的就是这三块。
- [x] 两个环节的成因不同，要分开治（逐条看输出 token）：路由是「写得太多」——中位 292 个输出 token、
      约 46ms/token，可同一条路由提示词的输出从 60 个到 1215 个不等（r01 59.3s 写 1215 个、
      r03 54.3s 写 1150 个），而决策 JSON 本身只要几十个字；步进决策是「读得太多」——中位只有 60 个
      输出 token，单条最长 17.3s 里 14.8s 花在读提示词（2525 token），真正写 JSON 只 2.5s。
- [x] 路由限长的第一种做法（模型侧档位）实测无效，没进代码：给决策调用传 `reasoning="low"` / `"minimal"`
      是同一条提示词各跑两遍，思考长度只变长或差不多（k01 不加 245/237 个输出 token → low 285/308、
      minimal 337/262；r01 不加 1079/810 → low 773/841、minimal 860/824）。方向都不稳定，所以档位这个
      旋钮只做了一半就回退，代码里 `_planner_reasoning` 仍只返回布尔值。
- [x] 路由限长的第二种做法（采纳）：在路由提示词**末尾**加一句「思考控制在两三句以内，不要复述题目和工具
      说明」，开关 `RAG_DISABLE_AGENT_THINKING_BRIEF=1` 退回不加这句的老提示词。同一条提示词各跑两遍的
      实测：k01 245/237 → 171/171 个输出 token（10.1s/9.6s → 8.4s/7.7s）、s01 514/517 → 263/263 个
      （23.2s/23.5s → 11.5s/10.7s）。这句只加在末尾，前面的规则与工具说明一字不动，模型的提示词前缀
      仍然复用（实测前缀 1265 token 里复用 1264）。
- [x] 步进决策的提示词重排（把静态规则挪到证据前面，让它成为可复用的固定前缀）量了，**没有收益，不改**：
      按真实累积证据造三步序列，现状 37.6s（读提示词 30.4s）对规则在前 37.8s（读 31.1s），两边字符数只差
      7；缓存复用也没按「静态前缀更长就多复用」走（规则在前第 2 步只复用 320 token，现状第 3 步反而复用
      922）。附带一条：同一份提示词原样重发时读提示词几乎不花时间（复用 1425/2206/2561，整段 2.0~2.6s），
      也就是说这台 Ollama 的前缀缓存只在整段前缀与上一次相同的地方生效，重排提示词换不来复用。
- [x] 前后对照按 v8.3 的口径在同一进程里交替跑（`scripts/measure_thinking_ab.py`，`off → on → off` 三轮、
      29 条、串行单并发、同一隔离评估库、`--skip-ragas`；报告 `eval/reports/v8_latency_thinking_brief_ab_{1,2,3}`，
      三轮都测于 `4cb9fb6`，`meta.git_dirty=true` 来自正在写入的报告文件与未提交的测量脚本）：

| 轮次 | 设置 | 单条均耗时（检索+生成） | 端到端 | answer_rate | source_hit | refusal_accuracy | both_sides_hit | evidence_hit@3 | mrr@3 | context_precision@3 | 平均工具调用 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 第 1 轮 | 不加（冷启动，按口径作废） | 33.0s | 33.0s | 1.000 | 0.960 | 1.000 | 1.000 | 0.800 | 0.7267 | 0.5333 | 1.793 |
| **第 2 轮** | **加限长句（新默认）** | **20.0s** | 20.0s | 1.000 | **1.000** | 1.000 | 1.000 | **0.840** | **0.7933** | **0.5733** | 1.724 |
| 第 3 轮 | 不加（暖轮，参照） | 23.7s | 23.7s | 1.000 | 0.960 | 1.000 | 1.000 | 0.800 | 0.7267 | 0.5333 | 1.793 |

      暖轮对照 **23.7s → 20.0s（-15.8%）**，远超 v8.3 量到的噪声底（同配置连续两轮只差 0.2%）。
      两个 off 暖冷轮的九项指标完全一致、逐条行为一条都没翻动，说明钉死采样参数以后「不加」这一档是可复现的，
      第 2 轮的差异来自限长句本身。
- [x] 四项门禁指标一条都没退：`answer_rate`、`refusal_accuracy`、`both_sides_hit` 三轮都是 1.000；
      `source_hit` 0.960 → 1.000，带动它的是 `m04` 一条（`source_hit` 与 `evidence_hit@3` 都从 0 变 1，
      `mrr@3` 与 `context_precision@3` 同一条带动）。也就是这轮是「更快且多命中一条」，不是拿质量换速度。
- [x] 逐条行为只有 3 条翻动，都不改变作答与拒答：`d02` 意图 `mixed` → `database`、工具 3 → 1（该样本不计入
      `both_sides_hit`，`source_hit` 与 `evidence_hit@3` 都保持 1.0，仍作答）；`s03` 工具 2 → 1；
      `r04` 工具 1 → 2（仍正确拒答，耗时反而 +4.4s）。`d02` 的意图在 v8.3 的量噪声底轮次里就翻过，
      所以这一条不能确定是限长句带来的。此前实测会翻的 `r01` 这轮没翻。
- [x] 提速不是靠少查换的：平均工具调用 1.793 → 1.724（净少 2 次，来自 `d02` 与 `s03`，`r04` 反而多一次），
      把这 3 条翻动样本剔除以后仍然 **-13.2%**（23.3s → 20.2s，26 条）；逐条看 22 条单独省超过 1 秒、
      5 条基本不动、只有 2 条变慢超过 1 秒（`m01` +2.5s、`r04` +4.4s）。省下的时间落在路由那一次调用上，
      与上面单条探针的方向一致。
- [x] 默认取值：限长句默认加（`AGENT_THINKING_BRIEF=True`），要退回老提示词用
      `RAG_DISABLE_AGENT_THINKING_BRIEF=1`。依据是上面这组：门禁四项不退、耗时 -15.8%。
- [x] 测试新增 3 条（`tests/test_agent_planner.py`）：路由提示词默认带这句、开关能关掉、步进提示词不带这句
      （步进默认不思考，加这句省不到时间），并断言这句在「硬性规则」之后——前缀缓存靠开头那部分复用。
      全部注入假 `ChatOllama`，不打模型、不联网。全量 `python3 -m pytest tests -q` = **564 passed**，
      0 failed / 0 skipped。
- [x] 口径与限制，引用这组数字时一并带上：
      - **29 条只支撑方向**，百分比别当精确值；「加限长句」这一档只跑了一轮，同设置重复轮没做，
        所以四项门禁「不退」是硬的（三轮都一致），三项命中指标的改善只能当方向看。
      - **换机器、换模型、换后端就不可比**：测于本机 Ollama 0.40.16 + `qwen3.8:27b-mlx` + `bge-m3`
        + `BAAI/bge-reranker-v2-m3` + MLX 后端，串行单并发，评估链路钉死 `temperature=0`。
      - 三轮都在同一进程里交替跑并作废第一轮（v8.3 口径 2）；`measure_thinking_ab.py` 首轮打印的
        「对上一轮」只在第 3 轮以后才比，第 2 轮的上一轮是冷启动，不能拿来当差值。
- [ ] 剩下的两块还没动：步进决策 35%（读提示词为主，重排提示词实测无效，唯一的下一条是换更小的决策模型）
      与最终作答 15%（v8.2 已确认关思考会换来一条误拒）。

## 分支与提交约定

- `main`：稳定可演示版本，只通过阶段分支合并更新。
- 每个 `vN-*` 分支只做该阶段范围，完成后先在本地验证，再确认是否推送/合并。
- 阶段收尾时更新本文件的“状态”，并补充 README 中的使用方式与对比命令。
