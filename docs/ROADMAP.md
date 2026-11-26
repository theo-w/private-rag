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
| v8.1 | `v8-latency-planner-nothink` | 决策调用的思考开关：`RAG_AGENT_PLANNER_THINKING=off\|route\|step\|all` 按环节控制是否让模型先思考（默认 `route`：路由保留思考、步进关掉），并把单条耗时拆成「工具执行」与「其余」 | ✅ 已合入 main（PR #26 → `ded5cb4`） | 536 测试通过（新增 7）；单条 agentic 110.1s → 56.6s（约 -49%），`answer_rate` / `refusal_accuracy` / `both_sides_hit` 回到基线值，代价是 `context_precision@3` 0.560 → 0.533；**全关那一档不能用**：24.5s 但三条 `multi_step` 被误拒（`answer_rate` 1.000 → 0.880）、`evidence_hit@3` 0.800 → 0.720 |
| v8.2 | `v8-latency-final-nothink` | 最终作答的思考开关 `RAG_AGENT_ANSWER_THINKING=keep\|on\|off`（默认 `keep`：连 `reasoning` 参数都不传），只加开关，提示词、门控、步数与指标判定式都没动 | ✅ 已合入 main（PR #27 → `348c22d`） | 543 测试通过（新增 7）；同一进程交替对照：`off` 36.6s（约 -10%）但 `answer_rate` 1.000 → 0.880、`source_hit` 1.000 → 0.960（三条应回答的样本被误拒），省下的只有约 4 秒/条，所以默认保留 `keep` |
| v8.3 | `v8-eval-determinism` | 先修测量再量改动：采样参数收进 `private_rag/llm_params.py` 由四条调用共用（`RAG_GENERATION_TEMPERATURE`、`RAG_LLM_SEED`），并量出噪声底与三条对照口径 | ✅ 已合入 main（PR #28 → `47ff404`） | 554 测试通过、CI 4 通过；同配置重复三轮：九项质量指标完全一致，答案措辞每对轮次有 4~7 条不同但一条都没改变指标（**措辞变化不算证据**）、第一轮含模型冷启动作废、固定链路两个暖轮之间耗时只差 1.6%；这台 MLX 后端温度 0 + 关思考只是「基本一致」（9 次调用 1 次给出不同文本），逐字比对不能当证据 |
| v8.4 | `v8-eval-determinism` | 延迟归属与路由思考长度：`scripts/profile_agent_calls.py` 量清时间住在哪，路由提示词**末尾**加一句「思考控制在两三句以内」（`AGENT_THINKING_BRIEF` 默认开，`RAG_DISABLE_AGENT_THINKING_BRIEF=1` 退回） | ✅ 已合入 main（PR #28 → `47ff404`） | 564 测试通过；归属：路由 48.1%、步进决策 35.1%、最终作答 14.8%、工具执行约 2%；同一进程 `off → on → off` 暖轮 23.7s → 20.0s（**-15.8%**），四项门禁指标不退、`source_hit` 0.960 → 1.000；两个试过没用的方向已排除（模型侧思考档位、把步进规则挪到证据前） |
| v8.5 | `v8-parse-baseline-ab` | 解析基准 A/B：扫描件解析后端可换（`RAG_SCAN_PARSER=selfbuilt\|docling\|mineru`），第三方走独立虚拟环境 + subprocess；四页新 fixture（带边框 / 合并单元格 / 无框线 / 六列）与 8 条评估集；解析层 golden 指标 | ✅ 本分支完成（读数已出，结论「不切换默认后端」） | 577 测试通过（既有 529 + 新增 48，全部打桩，CI 不装第三方解析器）；解析层三侧：docling 4/4 张表、`table_recall` 1.00 / `cell_accuracy` 1.00，selfbuilt 3/4、0.75 / 0.64，mineru（standard 档）3/4、0.75 / 0.75，自建与 MinerU 的缺口都在无框线那一页；端到端三侧同模型：新集 8 条 `evidence_hit@3` selfbuilt 0.571（未命中三条与解析层三个缺陷一一对应）vs docling 1.000 / mineru 1.000，v6 那 6 条三侧同分 1.000 / 0.500 / 0.900 与 v6.2 ON 侧逐字一致；结论与代价见下面的 v8.5 详细验收与 `eval/reports/v8_parse_*` |
| v8.6 | `claude/v8-tree-index-7ac6d0` | 章节树索引第二路：命中块不再只送固定字符的父块窗口，而是还原成「`章节路径: 文档 › 章 › 节` + 该节正文」；树从已入库的 `parent_index` 拼回结构文本再扫 Markdown 标题重建，离线落盘成独立 pickle（**不新建 Qdrant 集合、不改块写入格式**，已入库的库不用重新 ingest）；开关 `RAG_ENABLE_TREE_INDEX`（默认关）与 `RAG_DISABLE_TREE_KNOWLEDGE_ONLY`（默认只在路由判成纯知识的问题上启用） | ✅ 已合入 main（PR #29；两轮读数已出，结论「默认保持关闭」，开关默认关） | 687 测试通过（main 的 655 + 新增 32，全部内存假数据，不打模型、不联网、不开真实 Qdrant）；评估库实测结构：节点 109 / 文档 9 / 245 个块全部归属成功且能在章节正文里定位；同一口径跑了两轮各 `off,on,off` 三轮（29 条 × agentic，`eval/reports/v8_5_tree_ab_*` 与 `v8_5_tree_know_ab_*`）——第一轮任何意图都启用：三项命中指标与引用两项三轮不变，`both_sides_hit` 回退 1 条（`c03` 读到带路径的整节正文就判定证据够了、少查一次数据库），每块送出量 542 → 675 字符；第二轮按意图收窄：回退消失（三轮都 1.000）、生效面正好是 10 条纯知识样本（`used_tree_rate` 0.345），但开档轮三项命中指标与暖「关」轮逐字相同，收益没出现；两轮耗时都判不可判定（同设置的两个「关」轮自己差 17.9%～32.6%，且变快最多的几条是数据库意图、这一路没作用于它们）。结论与成因见下面的 v8.6 详细验收 |
| 语料接入 | `corpus-gdrive-sync` | Google Drive 语料预下载：本机 CLI（rclone / gdrive）把 Drive 文件落到 gitignore 的本地目录，再复用 `type: file` 源入库；联网只在下载这一步，不引入 Google SDK，`source_type` 不扩张 | ✅ 本分支完成（未接真实 Google 账号，见下方详细验收的未完成项） | 456 测试通过（+26 mock 单测，不联网、不打模型）；下载命令拼装与失败信息可测（rclone 导出格式钉 `pdf`、`--mode sync`/`--include`/`--dry-run`、缺 CLI 给安装提示、gdrive 在下载目录里执行且拒绝 dry-run）；仓库内下载目录未被 gitignore 时拒绝同步（ADR 0002 边界，默认目录另有真实 `git check-ignore` 探测）；假下载产物走现有 `run_ingest` 得 `added=2`、BM25 建成、二次运行整批跳过，引用 `source_type=file` + `tags=["gdrive"]`；README 有可操作的授权与「下载 → 入库」步骤，决策见 `docs/adr/0003-google-drive-corpus-cli-prefetch.md` |
| v8.7 | `v8-agentic-expanded` | 合成测试集扩展：评估专用 SQLite（`data/sql/demo_eval.db`，演示库的超集、沿用 `demo.products` 引用标签）+ agentic 评估集 29→40 条 + `--dataset` 逗号分隔可以把多份集合并成一次跑 | ✅ 本分支完成（基线读数已出） | 647 测试通过（既有 638 + 新增 9，不打模型、不碰数据库）；新集 40 条 = 35 可回答 + 5 应拒答，分布 `knowledge 9 / database 8 / mixed 7 / multi_step 6 / coreference 5 / refusal 5`，id 全 `x` 前缀；`--dataset eval/v7_agent_qa.jsonl,eval/v8_agentic_expanded.jsonl` 合并 69 条且跨文件 id 与问题唯一，只有一份时 `dataset_sha256` 逐字节不变；评估库前 4 行与 `scripts/init_demo_db.py` 逐字段相等、新增 8 行的类别不含「水果」；CI 由「校验四份评估集」改为「校验五份评估集」并加合并校验；基线读数：新集 agentic `evidence_hit@3` 0.571 / `source_hit` 0.714 / `both_sides_hit` 0.875、单条 26.2s，合并 69 条 agentic 0.600 / 0.833 / 0.929、单条 24.4s，单条样本对均值的影响从 ±3.4pt 降到 ±1.7pt；决策见 `docs/adr/0008`，限制见 `eval/README.md` |
| v8.8 | `claude/kind-allen-a8810a` | 数据侧路由修复：SQL 工具描述里那段「库内数据」目录把「扫多少行」与「展示几个值」拆开（短取值尽量列全、长文本列只留一个例子、纯数字与日期整列跳过），路由提示词补第 10 条把「那条记录自己的字段内容」与「通用常识」划开 | ✅ 已合入 main（PR #31 → `73f0507`，补测读数 PR #32 → `74a250a`） | 655 测试通过（既有 647 + 新增 8，不打模型）；两个可单独退回的开关 `RAG_DISABLE_AGENT_CATALOG_ENUM` / `RAG_DISABLE_AGENT_ROUTE_DATA_SIDE`，一起退回时目录与提示词逐字节等于改动前；同进程 `off → on → off`（新集 40 条）：`database` 类别 8 条从「意图全判 `knowledge`、`evidence_hit@3` 与 `source_hit` 都 0/8、7 条拒答」变成「7 条判 `database`、两项都 7/8、只剩 1 条拒答」，暖轮之间整轮 `evidence_hit@3` +7 条 / `source_hit` +10 条 / `answer_rate` +7 条（±1 条 = ±2.9pt），`refusal_accuracy` 5/5 与 `both_sides_hit` 7/8 三轮不变；护栏复查（文档定稿重建的 339 向量库，`knowledge`/`refusal`/`mixed` 21 条）文档侧意图 9/9 仍 `knowledge`、应拒答 5 条全拒；v7 那 29 条补测**没有可见影响**（会动的三项差 1 条，等于同设置两轮之间的漂移，判不可判定）；仍剩 1 条数据侧样本判 `knowledge`（没改标注），耗时不作收益依据（同设置两轮差 30%） |
| v8.9 | `claude/kind-allen-a8810a` | mixed 双侧门控认「这一侧查过没有」的依据换成工具执行记录：原来拿证据块的标签文字找工具名，而真实链路的标签是「SQL 查询（表 …）」与文档标题，里面没有工具名，等于永远判「一侧零证据」；现在改记 `kb_ran` / `sql_ran`，并加两条护栏——每一侧最多逼一次、那一侧没挂工具就不逼 | ✅ 已合入 main（PR #34 → `8e11a98`） | 701 测试通过（main 的 687 + 新增 14，不打模型）；开关 `RAG_DISABLE_AGENT_GATE_SIDE_TRACKING=1` 逐字退回按标签认的老行为；12 条双侧 `mixed` 样本同进程 `off → on → off`：8 条从「`stop_reason=max_steps`、4 次工具」变成「`final`、2–3 次工具」，`xm03` 从拒答变作答（`answer_rate` 0.917 → 1.0，这条在合入前复查里**没复现**），另外八项指标三轮**逐字相同**（`source_hit` 0.917、`both_sides_hit` 0.833、`evidence_hit@3` 0.667、引用两项 1.000），平均工具调用 3.58 → 2.58、轨迹步数 7.08 → 5.08；同设置的第 1 轮与第 3 轮逐字相同（这批样本这一轮漂移为 0），所以翻动可以归因；耗时 41.2s vs 61.0s 只记方向，不作收益；**合入前复查**（文档定稿重建的 388 向量库 + 最终代码，29 条同进程 `off → on → off`）复现了主体：`xm01 xm02 xm03 xm05` 仍从「`max_steps`、4 次工具」变成「`final`、2–3 次工具」，九项指标 `on` 与参照轮全等（平均工具 1.66 → 1.45），但同设置的第 1 轮与第 3 轮这次漂移 1 条（`xm03` 在暖 `off` 轮拒答、参照轮作答），所以作答率不算提升，能归因的只有「不再把步数烧光」；报告 `eval/reports/v8_9_gate_side_ab_*`、`v8_9_gate_side_final_*`，经过见下面的 v8.9 详细验收 |
| v8.10 | `claude/kind-allen-a8810a` | 拒答之前的兜底查询：意图判成 `knowledge`、模型要说「证据不足」，而问题指名的实体出现在「库内数据」的取值样例里时，先按那个实体查一次业务表再判（一条问题只兜一次，SQL 侧已经查过就不兜） | ✅ 已合入 main（PR #34 → `8e11a98`） | 与 v8.9 同一分支、各自可单独退回（`RAG_DISABLE_AGENT_KB_MISS_SQL_GATE=1`）；701 测试通过（新增的 14 条里 8 条是这道门控）；把提示词第 10 条关掉模拟「路由判错」时 `xd01–xd08` 8 条：`answer_rate` 0.625 → 1.0、`xd05` 与 `xd08` 从拒答变作答、`xd02` 也转为作答；出厂设置下 `xd08` 连跑三轮，同一条提示词在温度 0 下仍两轮判成 `knowledge`（措辞修不干净），门控三轮都兜住，答案内容正确并引用了那一行 SQL；护栏 21 条（`knowledge` 9 + 应拒答 5 + `mixed` 7）九项指标 `off` 与 `on` 全等，唯一行为翻动是 `xr04` 多花一次查询、仍然正确拒答；耗时不作收益依据；**合入前复查**（文档定稿重建的 388 向量库 + 最终代码，同 29 条同进程 `off → on → off`，第 3 轮与参照轮逐字相同、漂移 0）：`answer_rate` 0.958 → 1.0、引用两项 0.958 → 1.0，逐条翻动只有两条——`xd08` 从「拒答 / `insufficient_evidence` / 1 次工具」变成「作答 / `final` / 2 次」，`xr04` 多花一次查询仍然正确拒答；`source_hit` 不变（兜住的那次 SQL 引用排在第 4 位，仍出 `@3` 窗口）；报告 `eval/reports/v8_10_kbmiss_data_ab_*`、`v8_10_kbmiss_guard_ab_*`、`v8_10_kbmiss_xd08_*`、`v8_10_kbmiss_final_*`，经过见下面的 v8.10 详细验收 |
| v8.11 | `claude/v8-eval-set-expansion-74416b` | 评估集边界题与可比口径：补 11 条现在没有的边界类型（历史里有一句错值、一侧查无此物、SQL 查出来是空集、要的是字段原文不是描述里的说法、字面词撞得上但语料与库里没答案），并加两个离线工具——`scripts/check_eval_dataset.py`（一次跑完就报每条证据能否逐字找到、类别配比、双侧声明条数、可回答/应拒答配比、跨文件重复与证据句互为子串）与 `scripts/eval_subset_metrics.py`（从同一份报告按 id 子集重算指标，并把比率差换算成「几条样本」） | ✅ 本分支完成（基线读数已出） | 664 测试通过（既有 655 + 新增 10，全部不打模型、不联网、不开真实 Qdrant）；旧 69 条的 id/问题/参考答案/证据一字未改（单测钉住「合并后前 29 个 id 与 v7 逐字相同」与「新 8 条可回答样本的证据与新旧全部证据两两不互为子串」），合并 80 条 = 68 可回答 + 12 应拒答，分布 `knowledge 17 / database 15 / mixed 14 / multi_step 11 / coreference 11 / refusal 12`，声明双侧仍 14 条；语料与评估库都没动，所以 v8.7/v8.8 的基线不需要因为这一轮重测；基线读数（测于 `e0f2065`、`git_dirty=False`、346 个向量、精排 80/80 生效）：全集 80 条 `evidence_hit@3` 0.676 / `source_hit` 0.956 / `both_sides_hit` 0.857 / `answer_rate` 0.926 / `refusal_accuracy` 1.000，可比子集 69 条 0.683 / 0.983 / 0.857 / 0.950 / 1.000，边界 11 条 0.625 / 0.750 / — / 0.750 / 1.000；同子集与 v8.7 合并报告比 `source_hit` +10.4 条、`answer_rate` +9.2 条、`evidence_hit@3` +5.8 条，但涨幅主因是已合入的 v8.8 路由修复（7 条 `database` 样本从误拒变作答）而不是本轮加题，跨轮只当方向；新样本单条 104.1s vs 可比子集 38.8s（边界题更容易撞 `max_steps` / `context_budget`），耗时不作依据；两条必须动语料或评估库才能成立的边界（SQL 侧 NULL、文档与 SQL 说法冲突）没做，口径与逐条见 `eval/README.md` 的 v8.11 |

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
        的量级也在噪声范围内；要说「稳定降到多少」需要更大的评估集（测量能力已由 v8.7 补齐：新集 40 条、
        与 v7 集合并一次跑 69 条；本节这组数字仍测于 29 条，照这条口径读）。
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
      「更大的评估集」到 v8.7 才落地，本节这组 29 条读数不受影响。
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
        （样本量已由 v8.7 扩到 40 条 / 与 v7 集合并 69 条，后续要坐实这三个指标可以用新集重测。）
      - **换机器、换模型、换后端就不可比**：测于本机 Ollama 0.40.16 + `qwen3.8:27b-mlx` + `bge-m3`
        + `BAAI/bge-reranker-v2-m3` + MLX 后端，串行单并发，评估链路钉死 `temperature=0`。
      - 三轮都在同一进程里交替跑并作废第一轮（v8.3 口径 2）；`measure_thinking_ab.py` 首轮打印的
        「对上一轮」只在第 3 轮以后才比，第 2 轮的上一轮是冷启动，不能拿来当差值。
- [ ] 剩下的两块还没动：步进决策 35%（读提示词为主，重排提示词实测无效，唯一的下一条是换更小的决策模型）
      与最终作答 15%（v8.2 已确认关思考会换来一条误拒）。

## v8.5 详细验收（解析基准 A/B，本分支）

这是 v8 优先级的第 2 项，编号按合进 `main` 的先后排，所以落在延迟那几节（v8.1–v8.4）以后。

量出自建扫描件解析链路（渲染 → RapidOCR → OpenCV 形态学找表格框 → rapid-table SLANet → 按框过滤正文 OCR）
与成熟解析器之间的位置关系。**完成判据是三侧读数 + 报告 + 复现命令齐备，不等于换掉自建解析器**：
是否切换由读数决定，另记一条。三项口径由用户 2026-11-23 拍板——候选 = docling + MinerU（PaddleOCR-VL 要
CUDA 上的 vLLM/SGLang 服务，本机 Apple Silicon 跑不了，如实记为未测）；对照口径 = **整页替换后端**
（第三方连正文 OCR 一起换，不是只换表格模块）；样本 = `eval/v6_scan_table.jsonl` 6 条原样不动 + 新建复杂集。

- [x] 后端开关与接缝：`private_rag/config.py` 新增 `SCAN_PARSER`（`selfbuilt|docling|mineru`，默认 `selfbuilt`）、
      `PARSE_VENV_PYTHON`、`PARSE_BACKEND_TIMEOUT_SECONDS`（默认 300）、`PARSE_CACHE_DIR`；开关在**调用时**取
      `config`，与 `SCAN_TABLE_ENABLED` 一样支持「入库前才设环境变量」的既有约定
- [x] `private_rag/ingestion/scan_parse.py`：`ScanPageParse` / `ScanBackend.parse_page`（整份 PDF 一次解析、按页取用）、
      驱动输出契约的严格校验（自报后端不符、零页、页号非法、`text` 非字符串都抛 `ScanBackendError`）、
      按 PDF 字节内容缓存（键含契约版本与影响结果的 `variant`，MinerU 的档位就是它）、
      缺解释器给可操作提示、超时提示先跑 preflight
- [x] `files.py` 只在扫描件分支开头插一个分支：外部后端给 `(正文, 表格)` 就跳过 render/OCR/版面表格/`filter_ocr_result`；
      后端缺某页 → 回退自建并把这件事写进 `errors`；页数不对齐另记一条。**表格与正文的文档装配、逗号序列化、
      `doc_id`、`extra.table` 全部复用未改**，自建路径逐字节不变——这是与 v6.2 ON 侧可比的前提
- [x] 第三方走独立虚拟环境 + subprocess（本仓库与 CI 钉死 3.9，docling 要 `>=3.10`、MinerU 要 `>=3.11`）：
      `scripts/parse_drivers/{docling,mineru}_driver.py` 只用标准库 + 各自的库，`scripts/parse_drivers/_grid.py`
      把三种表格形态（docling 的 span 单元格、MinerU 的 HTML、markdown 管道）统一收敛成 `list[list[str]]`，
      合并单元格展开成占位副本；取舍与代价见 `docs/adr/0007-scan-parse-backend-out-of-process.md`
- [x] 新 fixture 四页（`scripts/make_parse_bench_fixture.py`，规格单一来源）：带边框 3×3、rowspan/colspan 合并单元格、
      **无框线**（自建靠形态学结构线找框，这是已知短板，主要区分点）、六列表格；同批产出文字底稿（只做 evidence
      逐字校验、不入库）与 golden JSON；`--smoke` 用真实自建链路逐页核对
- [x] 新评估集 `eval/v8_parse_bench.jsonl` 8 条（`ingestion` 5 + `multi_step` 2 + `refusal` 1），evidence 写成
      **整行连续短语**：`normalize_text` 丢掉标点与空白，自建侧逗号拼接行与第三方 markdown 行规范化后等价，
      两侧都能逐字命中——这是口径能对齐的关键。`config/sources.eval.parse.yaml` 只挂新 fixture
- [x] `scripts/parse_bench.py`：`--preflight`（解释器 → 驱动 → import → 真跑一遍，逐步打印可操作提示，失败早停）
      + golden 指标 `table_recall` / `cell_accuracy`（只比对期望非空地格）/ `structure_ok` / `body_purity`
      + 报告渲染。这层不打生成模型、不入库，用来把「解析真的差」与「evidence 口径没命中」分开
- [x] 测试 48 条新增（`tests/test_scan_parse_backend.py` + `tests/test_parse_bench.py`）：全部打桩子进程输出与自建
      OCR，不装第三方解析器、不起子进程、不打模型；断言外部后端产物的 `doc_id` / `extra.table` / 逗号序列化与
      自建侧一致、缺页回退并记错误、装配失败仍走自建、页数不对齐记错误、`_grid` 的 rowspan/colspan 展开、
      `RAG_MINERU_TIER` 换档必然重解析（档位参与缓存键）。
      全量 `python3 -m pytest tests -q` = **577 passed**（既有 529 + 新增 48）。CI 不装任何第三方解析器、
      `requirements-ci.txt` 不动，只在「校验四份评估集」里加 `eval/v8_parse_bench.jsonl` 一条 `--validate-only`
- [x] 文档：README「解析后端与解析基准（v8）」（可选后端、独立 venv、不在默认依赖、不静默降级、换 collection 要连
      词法索引路径一起换）、`eval/README.md` 第 9 步复现命令与「v8 解析基准集」小节、`docs/adr/0007`、
      `CONTEXT.md` 新增「解析后端」「整页替换」「解析层读数」三个术语
- [x] 解析层三侧读数（`eval/reports/v8_parse_baseline.md`）与端到端三侧读数
      （`eval/reports/v8_parse_ab_bench_<后端>.md` × 3、`v8_parse_ab_scan_<后端>.md` × 3）——复现命令见
      `eval/README.md` 第 9 步。**解析层**（四页一次解析，不打模型）：selfbuilt 检出 3/4、`table_recall` 0.75、
      `cell_accuracy` 0.64、`body_purity` 0.92、3344 ms；docling 4/4、1.00、1.00、1.00、10880 ms；
      mineru（standard 档）3/4、0.75、0.75、0.75、13024 ms。缺口都落在同一处：无框线那一页自建与 mineru 都是
      0 张检出，而自建还在页 1 丢了一个数字（`1299.0` 前的 `8`）、页 2 把 `1` 读成 `L`。**端到端**（`full`、Ragas 关闭、
      三侧同 `qwen3.8:27b-mlx` + reranker，`git_commit = 530418c`）：新集 8 条 selfbuilt `evidence_hit@3` 0.571
      （未命中 v8pb-01 / v8pb-03 / v8pb-07，与解析层三个缺陷一一对应）、docling 1.000、mineru 1.000；
      v6 那 6 条三侧完全同分（1.000 / `context_precision@3` 0.500 / `mrr@3` 0.900），与 v6.2 的 ON 侧逐字一致。
      MinerU 只测了 `standard` 档，走 `mineru-kit parse`（4.2.3 的 `mineru parse` 是文档库 + 本地服务那套 CLI），
      档位由 `RAG_MINERU_TIER` 决定并参与缓存键
- [x] 按读数写「是否切换后端」的结论：**不切换默认后端，`selfbuilt` 保持默认**，理由三条。
      ①唯一被证实的自建短板是「无框线页找不出框」与两处 OCR 丢字，而 MinerU 同样没能把无框线页认成表格
      （`table_recall` 与自建同为 0.75）——说明这不是「自建 vs 成熟解析器」的通用差距，而是形态学结构线这一条
      具体做法的短板，换后端买不到普适提升。②端到端两侧与新集都是 7/7，切换在本轮两个集上买不到命中提升；
      而且 mineru 那页表格没认出来却仍逐字命中（行被按正确顺序读进正文），可见 `table_recall` 不是切换判据本身。
      ③切换代价是硬性的：独立 3.11 虚拟环境 + 数 GB 模型权重 + 首次解析 10–13 秒 / 4 页，而 docling 的正文还有
      字符损失（「配件库存表（第一页）」读成 `配件库存表 (第 页`，`body_purity` 量不到这一项）。
      真正值得做的下一步是**按页型混合**：有结构线的页继续自建，整页没有任何线条的页交给 docling（本轮两侧在
      这一页上的差读数是 0 张 vs 1 张，判据可测）。但这要在真实语料上验一次再动默认值，不做合成 fixture 上的混合。

## v8.6 详细验收（章节树索引：命中块还原成带层级路径的章节上下文）

背景：父块扩展解决「召回准但读得太少」，可它的边界是按字符数切出来的，跟文档结构无关——一段话正好断在
「这里有五个关键角色：」后面，那五个角色就掉进下一个父块，模型读到的永远是半句话（v5 报告里 q07 就是这个形状）。
本轮加一条**与混合检索并列的第二路**：命中块换成「它所在的那一节 + 这节在文档里的路径」。

编号按合进 `main` 的先后排，接在 v8.1–v8.4（延迟与测量分支 PR #26/#27/#28）与 v8.5（解析基准 PR #25）之后。
v8.7（合成测试集扩展，PR #30）与 v8.8（数据侧路由，PR #31/#32/#33）都比这一节先合进 `main`，
但 main 在 v8.7 的节里明确把 `v8.6` 留给本分支（PR #29），所以这一节仍是 v8.6，物理上排在 v8.7 之前。
本分支把 main 合并进来，所以这一轮的对照是在「采样参数已钉死 + 路由思考限长句已默认加」的基线上测的，
与 v8.4 的读数可以直接对比。

- [x] 先确认结构信息到底有什么：块元数据只有 `doc_id`/`title`/`page`/`table`/`file_name`/`format`/
      `parent_id`/`parent_index`/`parent_count`/`chunk_index`，**没有标题层级**，标题只存在于块文本里。
      所以树是重建的：沿 `parent_index` 把同一文档的父块拼回整篇正文（父块不重叠，拼起来就是原文顺序），
      在拼回来的正文里扫 Markdown 标题行切出互不重叠的章节片段，再按偏移把每个块归到它正文所在的最深一级章节。
- [x] `private_rag/retrieval/tree.py`：`parse_heading()`（≤6 级、去掉 ATX 收尾 `#`、空标题不算标题）、
      `build_index(records)`（纯函数，不碰存储）、`TreeIndex`（节点表 + 块→节点映射，`path_label()` 用 ` › ` 连接）、
      `locate_chunk()`（容忍空白差异，判「这个块的正文能不能在章节正文里找到」）、`window_around_hit()`（以命中块
      为中心在章节内开窗，一侧被堵住时额度让给另一侧）、`attach_tree_context()`（替换上下文文本、同章节去重）。
      归属与查询用同一个「能不能定位」判定，避免归到一个还原不出上下文的节点上。
- [x] 存放与 BM25 词法索引同一套做法（照 `retrieval/lexical.py`）：独立落盘一个 pickle、`tempfile` + `os.replace`
      原子替换、带版本号，版本不匹配/损坏/类型不对一律当没有（返回 `None`）。
      **不新建 Qdrant 集合、不改块的写入格式**，已入库的知识库不用重新 ingest。
      `scripts/build_tree_index.py` 离线从 Qdrant payload 构建（`--eval` 走评估库，环境变量先于 config 导入）。
- [x] 接线：`RagEngine` 多一个 `tree` 参数，`build_engine()` 只要文件在就加载；`retrieve_with_trace()` 里
      树这一路成功还原时**不再做父块扩展**（两次替换文本会互相覆盖，上下文口径就说不清了），还原不出来
      （块正文跨出片段、定位不到）就退回与 `expand_parents` 同一口径的父块/子块回退。
      两路共用 `CONTEXT_MAX_CHARS`，不会把上下文撑大；`snippet_text` 仍是命中的子块原文，引用口径不变。
- [x] 开关 `RAG_ENABLE_TREE_INDEX`（`private_rag/config.py`，默认关）：用 ENABLE 而不是 DISABLE，是为了默认值
      保持现有行为不变。开关在**每次检索时**读（`RetrievalOptions.use_tree=None` 表示「按配置」），
      所以同一个进程里可以开/关交替对照；`readiness()` 只在开关打开又缺索引时警告。
      另有 `RAG_TREE_INDEX_PATH` 与 `RAG_TREE_SECTION_CHARS`（默认等于 `PARENT_CHUNK_SIZE`，
      这样 A/B 只差在边界语义——标题边界 vs 固定窗口——块数与总预算都对得上）。
- [x] **按意图收窄启用范围**（`config.TREE_KNOWLEDGE_ONLY` 默认开，`RAG_DISABLE_TREE_KNOWLEDGE_ONLY=1` 退回）：
      开关打开以后这一路只作用在路由判成 `knowledge` 的问题上。`RetrievalOptions.intent` 是新字段，
      `qa.tree_enabled_for_intent()` 每次检索时判定：总开关关着一律不启用；`database` / `mixed` / `none` 走原来的
      父块扩展；**没有意图也当知识处理**（经典链路与固定检索一次检索一次生成，没有工具循环，不存在提前收手）。
      意图是路由之后才有的，而工具注册表在路由之前装配，所以 `AgentRuntime._sync_tool_intent()` 在 `run()` 与
      `run_stream()` 里各调一次，把意图写进 `KnowledgeSearchTool`（`set_intent()`），检索时才拿得到。
      副作用：路由判 `none` 的那次验证检索此刻意图仍是 `none`，不走这一路——拒答路径上没必要换上下文口径。
      动机是下面实跑里 `c03` 那条回退：混合问题读到一整节带路径的正文就判定证据够了，少查一次数据库。
- [x] 评估侧读数：`SampleResult.used_tree`（确定性链路取 `RetrievalTrace.used_tree`，agentic 那一栏按
      上下文里有没有 `章节路径: ` 前缀判），聚合出 `used_tree_rate`，进 `DETERMINISTIC_METRICS`；
      `kb_search` 的结构化结果也带 `used_tree`。`eval_env.configure_eval_env()` 给评估链路单独一份索引路径
      （`storage/eval/tree/`）。`scripts/measure_tree_index_ab.py` 按 v8.3 的口径在同一进程交替跑。
- [x] 测试 27 条新增（`tests/test_tree_index.py`）：标题解析、嵌套路径、更深一级归属、章节正文只到下一个标题为止、
      跨片段块退回父块章节且还原不出上下文、没有父块/没有 `doc_id` 的退化、`build_from_store`（打桩 `scroll_all`，
      不打开真实 Qdrant）、落盘读回与三种坏文件、定位与开窗的边界、上下文替换与去重与回退与截断、
      检索链路接线（默认关仍走父块扩展、打开后 `used_tree`/`used_parent=False`、同进程可来回切、缺索引退回父块）、
      按意图收窄（`knowledge`/无意图启用、`mixed`/`database`/`none` 不启用、退回开关、`mixed` 仍走父块扩展、
      工具透传意图、运行时只写给能吃意图的工具）。
      全部内存假数据，不打模型、不联网、不开真实 Qdrant。
      三次合并 main 以后全量 `python3 -m pytest tests -q` = **687 passed**（main 的 655 + 新增 32，
      其中 main 的 655 含解析基准那 48 条、合成测试集扩展那 9 条、数据侧路由那 8 条），0 failed / 0 skipped
- [x] 评估库上的实测结构（构建日志，不是估计）：节点 109 / 文档 9 / 有块的章节 100 / 块 245；
      245 个块**全部归属成功且能在章节正文里定位**（100.0%）；块所在层级分布 1 级 24、2 级 133、3 级 88；
      章节长度平均 755 字符、**中位数 376 字符**、超过 2000 的只有 7 个。
      开工前据此预判「章节比父块短 ⇒ 送进模型的文本更少」，**这个预判被下面的实跑否掉了**：索引里按标题
      边界算的窗口平均 1039 字符，可实跑时父块扩展在共用预算里常被截断，两边比的是「实际送出量」而不是标题长度。
- [x] 本机实跑读数（2026-11-24，`eval/v7_agent_qa.jsonl` 29 条 × `agent`，同一进程 `off,on,off` 三轮、
      串行单并发、隔离评估库、`--skip-ragas`、评估链路钉死 `temperature=0` 与 `seed=7`；
      报告 `eval/reports/v8_5_tree_ab_{1,2,3}_{off,on,off}`，三轮都记 `git_commit = b85cb92`、`git_dirty = false`）：

| 轮次 | 设置 | 单条均耗时 | answer_rate | source_hit | refusal_accuracy | both_sides_hit | citation_rate / validity | evidence_hit@3 | mrr@3 | context_precision@3 | used_tree_rate | avg_context_chars | avg_retrieved | avg_tool_calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 第 1 轮 | 关（冷启动，耗时作废） | 41.3s | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 / 1.000 | 0.840 | 0.7933 | 0.5733 | 0.000 | 1498 | 2.69 | 1.724 |
| 第 2 轮 | **开** | 51.3s | 1.000 | 1.000 | 1.000 | **0.833** | 1.000 / 1.000 | 0.840 | 0.7933 | 0.5733 | 0.621 | 1700 | 2.52 | 1.483 |
| 第 3 轮 | 关（暖轮，参照） | 54.8s | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 / 1.000 | 0.840 | 0.7933 | 0.5733 | 0.000 | 1403 | 2.59 | 1.621 |

- [x] 噪声底：两个「关」轮的九项指标逐轮完全一致（`compare_eval_noise.py` 判「一致」），逐条行为只有
      `r03` 翻动（意图 `mixed` → `database`、停止原因 `max_steps` → `insufficient_evidence`、工具 4 → 1，
      仍是正确拒答，`refusal_accuracy` 不变）。这条属于噪声而不是这一路的效果：`r03` 是拒答样本，
      逐条的 `source_hit` / `both_sides_hit` / `evidence_hit@3` 都记 `None`，不进任何均值；
      另外引用两项三轮都是 1.000，这一轮不存在「答案措辞推动指标」的问题。
      剩下的启发是 `mixed` / `database` 类样本的意图与步数本来就不完全钉得住，单条行为翻动要谨慎读。
- [x] **一项门禁回退：`both_sides_hit` 1.000 → 0.833，回退的是 `c03` 一条，丢的是数据库那一侧**
      （达到「≥1 条才算数」的门槛，且两个「关」轮里 `c03` 都是 `max_steps`、4 次工具、两侧都引到，所以不是噪声）。
      逐条轨迹把成因指得很清楚：开档那一轮 `c03` 的工具序列只有 `['kb_search']`、1 次查询就 `final` 作答；
      暖「关」轮是 `['kb_search', 'sql_query']`、3 次查询，上下文里带一段 SQL 结果表
      （`name | updated_at 香蕉 | 2026-01-15`），开档那轮的上下文三段全是带 `章节路径: ` 的文档正文，
      没有任何 SQL 结果。也就是说这一路让模型读到一整节带路径的文档正文以后判定「证据够了」，**少查了数据库那一侧**，
      按「声明的每一侧都要引到」的口径判 0。它的 `source_hit` 与 `evidence_hit@3` 仍是 1.0、答案没答错，
      是「少取一侧」不是「答错」。这与 v7.4 记下的「把检索到的文本当足够、不回查」间歇行为同源，
      本轮被这一路变成稳定触发。另外两条同样提前收手的样本 `m01` / `m04`（4 → 2 次工具、`max_steps` → `final`）
      两侧都还调到了（`['kb_search', 'sql_query']`），不计入回退。**这条按代价记，不记成噪声**，
      因此默认档只能是关；要打开得先把门控补强（见下面两条待办）。
- [x] 没有质量收益：`answer_rate`、`source_hit`、`refusal_accuracy`、引用两项、三项命中指标在三轮完全相同，
      `n_errors` 0。也就是说这一轮是「多送了一段章节正文 + 一条路径」换不来命中与引用的提升。
- [x] **耗时不可判定（方向有，幅度没有）**：暖轮开 51.3s 对暖轮关 54.8s 是 -6.4%，但同一种设置下的两轮
      「关」本身就差 32.6%（第 1 轮冷 41.3s 对第 3 轮暖 54.8s），逐条从 -69%（`c03`）到 +87%（`r03`）不等，
      而每个设置只有 1 个可用暖轮——没有「暖关对暖关」这一对来界定同配置的耗时噪声底，所以 -6.4% 只能算方向。
      把三条提前收手的样本剔掉以后剩下 26 条是 50.9s 对 45.4s（+12.3%），同样缺同配置暖轮对照，只当观察值。
      有一点是确定的：省下的时间靠少跑几步，而少跑几步正是 `c03` 回退的成因，所以「更快」即使成立也不是收益。
- [x] 上下文实际送出量与预判相反，且必须和块数一起读：每样本 `avg_context_chars` 暖关 1403 / 开 1700（+21%），
      同轮 `avg_retrieved` 暖关 2.59 / 开 2.52——块数由模型的查询与步数决定，不是这一路直接控制的，
      所以换算成每块送出量才是这一路自己的读数：542 字符/块 → 675 字符/块（+24%），逐块中位 322 → 629 字符。
      父块扩展在共用预算里经常被截断，「章节中位 376 字符 ⇒ 上下文更少」没成立，这一路反而送得更多。
- [x] 机制确实生效（生效证据，不是收益）：`used_tree_rate` 0.621、18/29 条的上下文出现 `章节路径: `、
      全轮共 55 行章节路径、18 条样本至少有一条。没有路径的 11 条是 `d01`–`d06`、`s01`–`s04`、`c02`：
      这些样本只调 `sql_query`、不调 `kb_search`，属预期而不是漏装索引。
- [x] 默认保持关闭（`RAG_ENABLE_TREE_INDEX` 默认关）：以上回退只在开关打开时出现，main 的现有行为不变。

### v8.6 追加读数：按意图收窄以后（2026-11-24，同一口径重跑）

上面那轮唯一的代价是混合问题提前收手，所以按意图收窄以后重跑一遍（同一评估集、同一隔离评估库、
同一进程 `off,on,off` 三轮、串行单并发、`--skip-ragas`、`temperature=0` 与 `seed=7`）。
报告 `eval/reports/v8_5_tree_know_ab_{1,2,3}_{off,on,off}`，三轮 `git_commit = 7f07afe`（本轮唯一改动就是它；
`git_dirty = true` 只是跑批时新写的报告文件与本轮文档改动，代码就是那个提交）。

| 轮次 | 设置 | 单条均耗时 | answer_rate | source_hit | refusal_accuracy | both_sides_hit | citation_rate / validity | evidence_hit@3 | mrr@3 | context_precision@3 | used_tree_rate | avg_context_chars | avg_retrieved | avg_tool_calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 第 1 轮 | 关（冷启动，耗时作废） | 30.9s | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 / 1.000 | 0.800 | 0.7533 | 0.5333 | 0.000 | 1562 | 2.83 | 1.793 |
| 第 2 轮 | **开**（只在纯知识意图） | 27.1s | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 / 1.000 | 0.840 | 0.7933 | 0.5733 | 0.345 | 1683 | 2.69 | 1.724 |
| 第 3 轮 | 关（暖轮，参照） | 36.4s | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 / 1.000 | 0.840 | 0.7933 | 0.5733 | 0.000 | 1403 | 2.59 | 1.621 |

- [x] **上一轮那条门禁回退消失了**：`both_sides_hit` 三轮都是 1.000，四项门禁全持平。逐条看，`c03`、`m01`、`m04`
      在开档轮仍是 4 次工具、`sql_query` 与 `kb_search` 都调到、上下文里没有 `章节路径: `（它们的意图是 `mixed`，
      这一路不再作用于）——收窄确实把触发面堵住了。
- [x] 生效面正好等于纯知识那一侧：开档轮只有 10 条带章节路径（`used_tree_rate` 0.345、32 行路径），
      就是判成 `knowledge` 的 `k01`–`k07`、`c01`、`r02`、`r04`；其余 19 条一条路径都没有。
- [x] **纯知识这一侧也量不出收益**：开档轮的 `evidence_hit@3` / `mrr@3` / `context_precision@3` 与暖「关」轮
      **逐字相同**（0.840 / 0.7933 / 0.5733）。第 1 轮冷是 0.800 / 0.7533 / 0.5333，看着像「开档涨了 0.04」，
      但同设置的两个「关」轮自己就把这三项判为不一致（`compare_eval_noise.py`），差异全来自 `d05` 一条
      （意图 `database`，这一路根本不作用于它）。所以这三项的变化属于冷轮与同设置噪声，不是这一路的效果。
- [x] 耗时仍不可判定：开档 27.1s 对暖关 36.4s（-25.6%）幅度看着大，可同设置的两个「关」轮差 17.9%，
      逐条从 -70%（`d06`）到 +143%（`r03`），而且「变快最多」的 `d06`/`d05`/`s01` 三条都是 `database` 意图、
      这一路没作用于它们——说明这个幅度里绝大部分不是这一路带来的。
- [x] 结论：按意图收窄把唯一的代价堵住了（代价没了），但打开也换不来可测的收益，`RAG_ENABLE_TREE_INDEX`
      **仍然默认关**（已随 PR #29 合进 `main`，合进去的是「默认关的这条第二路」，现有行为一字未变）。
      真要打开，作用范围现在只有纯知识问题，风险面比上一轮小得多；是否打开、以及要不要再做门控那一半，等你拍板。
- [x] 「提前收手」解决了一半：这一路改成只在纯知识意图上启用（见上面那条），混合与数据库问题的检索
      不再被它影响，`c03` 那条回退的触发面就没了。另一半——在 v7.5 的双侧门控里把「声明了 SQL 侧却
      没回查」当硬条件——没做：属门控范围，与 #28 的提示词改动相互影响，需要单独拍板。
- [ ] **这两轮读数测于 v8.8（数据侧路由修复）之前**：对照基线是「采样参数钉死 + 路由思考限长句」，
      不含库内取值列全与路由第 10 条规则。合进 `main` 以后若要再量这一路，得按同一口径重跑
      （同进程 `off/on/off`、第一轮作废、指标变化 ≥1 条才算数），别拿这两轮当 v8.8 之后的读数。
- [ ] 短叶子章节补上级章节正文（章节长度低于阈值时把父节点正文一起带上）：本轮实测说明它不是瓶颈，
      真正的瓶颈是送得更多 + 更早收手。

## 语料接入详细验收（Google Drive 预下载）

边界与选型见 [docs/adr/0003](adr/0003-google-drive-corpus-cli-prefetch.md)，用法见 README「Google Drive 语料接入（预下载）」。

- [x] 下载工具是本机 CLI（默认 `rclone`，可切 `gdrive`），Python 侧零新增依赖——仓库钉 Python 3.9 且尽量少依赖
- [x] 联网只在下载这一步：入库与检索链路不碰 Google API，仓库不引入 Google SDK
- [x] 凭据只留本机（`~/.config/rclone/rclone.conf`、`~/.gdrive/`），不进命令行、不进日志、不进仓库
- [x] 下载目录默认 `data/gdrive` 并写进 `.gitignore`；仓库内的目录未被忽略时拒绝同步（ADR 0002），逃生口 `--allow-tracked-dest`
- [x] 复用现有链路：下载目录就是 `type: file` + `tags: [gdrive]` 的源，分块与嵌入一处未动；`source_type` 不扩张（API schema 与 CLI 仍是 file/web/sql）
- [x] Google 原生文档导出格式钉成 `pdf`（入库链路只认 txt/md/pdf）；同步后清点目录、列出不支持的后缀，一个可入库文件都没有时明确失败而不是静默入库 0 条
- [x] `--mode sync --ingest --prune` 让知识库跟着 Drive 的删除收缩；`--skip-sync` 只用已下载内容；`--` 之后的参数透传给下载工具
- [x] 26 条 mock 单测：命令拼装与失败信息、目录安全边界（含真实 `git check-ignore` 探测）、目录清点、假下载产物走 `run_ingest` 的幂等；不依赖真实 Google 账号与 Ollama
- [x] 真机冒烟（2026-11-22，本机 Ollama）：假下载产物（2 个 md/txt + 1 个 xlsx）走 CLI 用真实 `bge-m3` 入库到隔离 collection（`RAG_COLLECTION_NAME` / `RAG_STORAGE_DIR` / `RAG_LEXICAL_INDEX_PATH` 指向 /tmp，演示库未被动）——`新增 2 / 写入 2 chunk`、xlsx 被清点为不支持、BM25 建成；二次运行 `新增 0 / 跳过 2`；「橙子有什么营养」的向量检索第一条是橙子，metadata 为 `source_type=file` + `tags=["gdrive"]`
- [ ] 真实 Google 账号端到端（授权 → 下载 → 入库 → 提问）没有跑过：仓库里不存在任何真实账号凭据，这一步只能由使用者在自己机器上按 README 验证
- [ ] Drive 侧的权限粒度没有测：只读 scope 能否覆盖共享盘（shared drive）需要 `rclone config` 时另行确认

## v8.7 详细验收（合成测试集扩展：评估专用 SQLite 与多数据集合并）

这一项补的是**测量能力**，不是新链路。v8.1 / v8.2 / v8.4 三处读数都写着同一句话：「29 条样本只支撑方向，
不支撑百分比——±1 条就是 ±4 个百分点」。v8.3 修的是可复现性（钉温度、量噪声底），样本数没动，所以每次延迟
收益都只能说方向。编号约定是「按合进 `main` 的先后编号」，`v8.6` 留给还没合进 `main` 的树索引分支（PR #29），
本节排到 v8.7。

- [x] 评估专用 SQLite `data/sql/demo_eval.db`（`scripts/init_eval_db.py`）：单表 `products` 12 行，比演示库多
      `storage / supplier / stock` 三列。做成**超集**——前 4 行与 `scripts/init_demo_db.py` 逐字段相等（单测钉住），
      新增 8 行的 `category` 一律不含「水果」二字；`scripts/init_demo_db.py` 与 `data/sql/demo.db` 一字未改，
      v7 那几轮的读数仍然可复现
- [x] 引用标签沿用 `demo.products`（新运行时配置 `config/sources.eval.agent.v8.yaml` 只把 DSN 指向新库），
      不给新库单独起标签。原因是硬约束：`ToolRegistry` 按工具名登记，一份 sources 配置里两个 sql 源会互相覆盖
      （`private_rag/agent/tools.py:283`），两个标签只能活一个，另一半样本的 `source_hit` 会恒为 0。
      决策与被否方案见 [docs/adr/0008](adr/0008-eval-db-superset-shared-label.md)
- [x] 新评估集 `eval/v8_agentic_expanded.jsonl`：40 条 = 35 可回答 + 5 应拒答，id 全部 `x` 前缀（与 v7 的
      `k/d/m/s/c/r` 不撞车，合并跑才合法），分布 `knowledge 9 / database 8 / mixed 7 / multi_step 6 /
      coreference 5 / refusal 5`——补的正是现有集最薄的几类（v7 集 `coreference` 3、`mixed` 5、`database` 6）
- [x] `--dataset` 支持逗号分隔多份（新增 `parse_dataset_arg` / `load_multi_dataset` / `datasets_fingerprint`）：
      每份文件内部的结构与证据校验完全不变，合并后跨文件检查 id 与问题唯一、报错带上撞车的两个文件名；
      只有一份时指纹与 `dataset_fingerprint` 逐字节相同，历史报告里的 `dataset_sha256` 不受影响。
      v7 集 + 新集合并 = 69 条，单条样本对均值的影响从 ±3.4pt 降到 ±1.4pt
- [x] 两条标注口径进单测：evidence 必须是权威文件里的一段**连续**文本（`normalize_text` 只保留字母和数字，
  `12.5, "2026-01-10"` 会粘成 `12520260110`，跨字段拼法永远校验不过，所以 SQL 侧证据只能取 `description` /
  `storage` / `supplier` 里的连续句子）；证据句两两不得互为子串，否则一次检索命中能同时糊掉两条样本的判定
- [x] 拒答样本的挑选规则：事实既不落在评估库语料（`README.md` + `docs/*.md`）里，也不落在 `products` 的任何列里。
      `supplier` 取值会进工具目录的样例，所以「某供应商的结算条件是什么」这类问法正是我们要量的「证据不足」，
      不会误命中。注意本节与 `eval/README.md` 不能把拒答样本的原问题写进来——`docs/*.md` 会被入库成评估语料，
      引用了原问题就等于给那条拒答样本自己造了证据
- [x] 新增 9 项单测（新集形状、证据不重叠、建库超集、数据集↔库一致、运行时配置指向、参数切分、合并顺序、
      跨文件重复的两条负例、指纹向后兼容与顺序敏感），全量 `python3 -m pytest tests -q` = **647 passed**
- [x] CI 从「校验四份评估集」改为「校验五份评估集」，并额外校验 v7 + 新集的合并跑（只验结构与跨文件唯一，
      不碰模型也不碰数据库）
- [x] 文档：`eval/README.md` 新小节（分布、证据权威、复现命令、限制）、README 的 `--dataset` 多份用法与目录结构、
      `CONTEXT.md` 新增「评估专用 SQLite」「合并评估运行」两个术语、`docs/adr/0008`
- [x] 基线读数（2026-11-25，本机单并发，`git_commit = 7eeb0c5`；新集轮 `git_dirty=False`，合并轮 `True` 只是多了
      正在落盘的报告文件；两轮都 `reranker=有`）。逐条指标与限制见 `eval/README.md` 的「基线读数」小节，
      报告 `eval/reports/v8_agentic_expanded_baseline.md`（新集 40 条 × `agent,full`）与
      `eval/reports/v8_agentic_merged_v7_baseline.md`（合并 69 条 × agentic）：

      | 跑法 | n（可答） | evidence_hit@3 | context_precision@3 | mrr@3 | source_hit | both_sides_hit | answer_rate | refusal_accuracy | 上下文字符 | 工具调用 / 步数 | 单条均耗时 |
      |---|---|---|---|---|---|---|---|---|---|---|---|
      | 新集 40 · agentic | 40（35） | 0.571（20/35） | 0.305 | 0.505 | 0.714（25/35） | 0.875（7/8） | 0.743（26/35） | 1.000（5/5） | 1961 | 1.78 / 4.18 | 26.2s（检索 0.8 + 生成 25.4） |
      | 新集 40 · 固定 `full` | 40（35） | 0.229（8/35） | 0.076 | 0.157 | 0.286（10/35） | 0.000（0/8） | 0.286（10/35） | 1.000（5/5） | 1173 | — | 14.2s |
      | 合并 69 · agentic | 69（60） | 0.600（36/60） | 0.325 | 0.533 | 0.833（50/60） | 0.929（13/14） | 0.817（49/60） | 1.000（9/9） | 1886 | 1.84 / 4.26 | 24.4s |

      第一轮读数作废过一次：本机 `huggingface-hub` 被升到 1.8.0 而 `transformers` 只认 `<1.0`，精排加载失败、
      每条只花 50 毫秒（等于没调模型），降回 0.36.2 后才重跑。固定链路的低分有结构性原因：35 条可答里 21 条
      必须查 SQL，而 `full` 没有 SQL 工具，`answer_rate` 天花板是 14/35 = 40%，不能读成链路质量差
- [x] **单条样本的影响已经小到可以说百分比**：40 条跑里 ±1 条 = ±2.9pt（分母 35 条可答样本），合并 69 条跑里
      ±1 条 = ±1.7pt（分母 60）。所以「新集 `database` 类别 0/8 命中」这类离噪声底很远的判定可以说百分比——
      这正是新集第一次量出来的缺口：`xd01`–`xd08` 有 7 条被路由判成 `knowledge`，只调 `kb_search` 就按证据不足
      拒答，而同一轮里 `multi_step` 6 条意图全判对。**根因在本轮之后定位**：不是问法不像数据题，而是喂给路由器的
      信息缺了——SQL 工具描述里那段「库内数据」目录每个文本列只扫前 4 行，而评估库故意把新实体放在第 5–12 行
      （前 4 行与演示库逐字相同以保住 v7 读数），这些实体永远进不了目录。修法与同进程交替读数见下面 v8.8
- [ ] **跨配置的前后对比仍然不勾**：本轮只是基线，没有可比的「改动前后」；而且新库让工具目录变长
      （12 行样例进 `_data_catalog`），新集基线与 v7 各轮不做跨轮均值比较。合并跑里逐条盯了当初预判会翻转的
      v7 `d03` / `s01`——**确实翻转**：v7 子集 `evidence_hit@3` 从 v7.5 单跑的 0.800 降到 0.640（25 条可答，−4 条 =
      −16pt，超出 ±1 条的噪声底），`s01` 与 `s04` 还变成拒答，另有 `s03` / `c02` 掉证据、`m04` 反而 +1。
      同 commit 同库下新集 40 条在单跑与合并跑之间逐条一致（0/40 翻转），说明翻转来自那 8 行新数据而不是合并
      本身；但这是跨轮比较（不同日期、不同库、没同进程交替），只能当方向，坐实要演示库 vs 评估库交替重跑

## v8.8 详细验收（数据侧路由：把库内取值列全，再补一条「记录自身字段」规则）

v8.7 那条「两种解释都成立」的缺口在本轮收口：**同进程交替 A/B 显示是目录缺信息，不是问法不像数据题**
（根因见上面 v8.7 那条）。改动分两半，加两个各自可退回的开关。

- [x] **目录把「扫多少行」与「展示几个值」拆开**，并按路由器真正需要的信息来组织：短取值（实体名、类别、
      储存方式这类）尽量列全，长文本列只留一个例子（路由器要知道「这一列存说明文字」，不需要读完整段落），
      纯数字与日期时间整列跳过（问题不会用 `2026-01-10` 指认一条记录，它们只会占掉字符预算与回退线索名额）。
      新增 `AGENT_CATALOG_SCAN_ROWS` 单独控制扫描行数（默认 40），`AGENT_CATALOG_SAMPLES` 回到它本来的含义
      （展示条数，默认 12）。同一份评估库的目录文本从 398 字符变成 393 字符，但 12 个实体名与 8 个储存方式
      全部可见，回退路由的线索词从 24 个到 40 个（上限）——模型不可用时靠关键词兜底的那条路一起受益
- [x] **路由提示词补第 10 条**（`private_rag/agent/prompts.py` 的 `DATA_SIDE_RULE`）：光把实体列全还不够，
      目录修好后重测，8 条里仍剩 2 条判 `knowledge`，理由是把「拆开以后敞着放会出什么问题」当成食品常识去文档侧找。
      规则 1 与规则 2 已经蕴含「记录自身的字段算 database」，但「先怀疑」不够硬，所以补一条把「那条记录自己的
      字段内容」与「通用常识」明确划开。这条同时守住两侧：问的是**本系统自己的行为**（这类数据怎么入库、怎么检索）
      时仍按第 6 条走文档侧，避免把 v7 那批文档样本带偏；真有两个子问题的仍按第 3 条选 `mixed`
- [x] 两个开关各管一半、可单独退回，且**退回逐字等于改动前**：`RAG_DISABLE_AGENT_CATALOG_ENUM=1` 退回旧目录
      （每列只看前 4 行、长文本照抄、数字日期进目录、连列名头一行也一起回来，退完目录文本与改动前逐字节相同），
      `RAG_DISABLE_AGENT_ROUTE_DATA_SIDE=1` 退回没有第 10 条的提示词。旧行为连「只看前 4 行」一起退
      （`LEGACY_CATALOG_ROWS`），否则「旧行为」只退了组织方式、取值条数还是新的
- [x] `_data_catalog` 的四个参数默认值改成 `None` 并在调用时读模块属性：写成默认参数会在导入时抓快照，
      同一进程里换设置（交替对照脚本正是这么换的）不会生效——与 `prompts.py` 的同一处坑
- [x] `scripts/measure_thinking_ab.py` 新增 `--setting data_side_routing`（两半一起开关，分开量就不是在量这次要
      ship 的行为）与 `--ids`
- [x] 新增 8 项单测（实体在第 8 行也要进目录与线索、长文本列只留一个例子、数字与日期不进目录、字符预算不够时
      先牺牲长文本例子保住实体名单、`enumerate_values=False` 逐字回到旧目录、`max_samples=0` 只剩列名线索、
      第 10 条在默认提示词里、关掉开关后第 9 条仍然完整），全量 `python3 -m pytest tests -q` = **655 passed**
- [x] **同进程交替读数（2026-11-25，本机单并发，新集 40 条，`off → on → off`）**。逐条指标与口径见
      `eval/README.md` 的「v8.8 数据侧路由的前后对比」，一句话结论：`database` 类别 8 条从「意图全判 `knowledge`、
      `evidence_hit@3` 与 `source_hit` 都 0/8、7 条拒答」变成「7 条判 `database`、两项都 7/8、只有 1 条拒答」；
      整轮 `evidence_hit@3` +7 条、`source_hit` +10 条、`answer_rate` +7 条（暖轮之间比，±1 条 = ±2.9pt），
      而 `refusal_accuracy`（5/5）与 `both_sides_hit`（7/8）三轮不变
- [x] **文档定稿后重建库做护栏复查**：把读数写进本文与 `eval/README.md` 以后语料变长（334 → 339 个向量），
      用改动默认开启重跑护栏三类 21 条（`xk01–xk09`、`xr01–xr05`、`xm01–xm07`）：文档侧 9 条意图仍是 9/9
      `knowledge`（没有一条被推到 `database`）、`evidence_hit@3` 6/9、`source_hit` 8/9；应拒答 5 条全部拒答；
      两侧都要的 7 条 `evidence_hit@3` 7/7。替代了原来「护栏测于改动前语料」这条保守说明
- [ ] **有一条数据侧样本仍然判错**：交替 A/B 之后 `database` 类别仍有 1 条判 `knowledge`，模型把它当成食品安全
      判断而不是某条记录的字段内容。没有为了让数字好看去改标注，也没有把那条问法写进提示词或文档
      （`docs/*.md` 会被入库成评估语料）
- [x] **量了对 v7 那 29 条的影响：没有可见影响**（同一进程 `off → on → off`，用 v7 原本那份演示库，不是超集库）。
      `source_hit` / `answer_rate` / `refusal_accuracy` / `citation_rate` / `both_sides_hit` 三轮都是 1.000
      （v7 本来就在天花板，只能测有没有变差）；会动的三项里 `on` 比暖 `off` 少 1 条证据，而同设置的两个 `off`
      轮自己就差 1 条、`mrr@3` 差 0.040 且方向相反 → 等于漂移量级，判不可判定。逐条只有一处与改动有关：
      一条应拒答样本的意图从 `knowledge` 变 `database`，但它查完仍然拒答，4/4 仍全拒
- [ ] **仍然看不到的实体**：目录每列只扫前 `AGENT_CATALOG_SCAN_ROWS` 行，更大的库里排在后面的实体照样进不了目录，
      那种情形只能靠规则 4（拿不准且 `sql_query` 可用时选 `mixed`）兜底，不是靠目录列全。换成大表应该调这个参数，
      或者用 `RAG_AGENT_CATALOG_SAMPLES=0` 只列列名不取样例
- [ ] **耗时不作收益依据**：同设置的第 1 轮与第 3 轮差 30%（43.3s vs 56.5s），比 `on` / `off` 之间的差还大，
      所以这轮只能说方向——`on` 的工具调用 1.55 次 vs 1.73 次、上下文字符 1529 vs 2097（−27%），机制是数据侧
      样本从 3–4 次调用变成一次 SQL。要量耗时得再单独做几对暖轮交替

## v8.9 详细验收（mixed 门控认侧别的依据：标签文字换成执行记录）

- [x] **现象**：旧报告里每条 `intent=mixed` 的样本都是 `tools=4`、`gate_intercepts=2`、
      `stop_reason=max_steps`——门控认定「有一侧零证据」，反复强制取证直到步数用尽
- [x] **成因**：门控调用 `_has_evidence_for(blocks, "sql_query")`，在证据块的**标签文字**里找工具名。
      真实链路里 `sql_query` 的块标签是「SQL 查询（表 demo.products）」，`kb_search` 的块标签是引用标题
      （文档名或页码），两处都不含工具名，子串匹配恒为 False。也就是说老行为等于「两侧永远零证据」，
      那个判断只在测试替身（块标签写着工具名）里成立——所以 5 条旧单测全绿而真实链路一直误拦
- [x] **改法**：行动循环里记 `kb_ran` / `sql_ran` 两个执行标记，门控看「这一侧到底跑过没有」。
      两条护栏：一条问题每一侧最多逼一次（SQL 侧每次都失败也只逼一次，否则同样烧到 `max_steps`）；
      那一侧压根没挂工具（关掉 SQL）就不逼，逼了只会白烧一步。「查过但返回为空」也算查过——
      「库里确实没有这项事实」本身就是可以作答或拒答的依据，再逼一次只会稀释上下文
- [x] `RAG_DISABLE_AGENT_GATE_SIDE_TRACKING=1` 逐字退回按标签文字认的老行为，`_has_evidence_for`
      留着只服务这条退回路径（docstring 写清它在真实链路里恒为 False）
- [x] 12 条双侧 `mixed` 样本（新集 `xm01`–`xm07` + v7 的 `m01`–`m05`）同进程 `off → on → off`
      （测于文档定稿前的 339 向量评估库，`reranker=有`，单并发）：

      | 轮 | 设置 | answer_rate | source_hit | both_sides_hit | evidence_hit@3 | citation / validity | mrr@3 | context_precision@3 | 工具调用 / 步数 | 单条均耗时 |
      |---|---|---|---|---|---|---|---|---|---|---|
      | 第 1 轮 | `off` | 0.917（11/12） | 0.917 | 0.833（10/12） | 0.667 | 1.000 / 1.000 | 0.667 | 0.222 | 3.58 / 7.08 | 65.3s（冷启动，作废） |
      | 第 2 轮 | `on` | **1.000（12/12）** | 0.917 | 0.833 | 0.667 | 1.000 / 1.000 | 0.667 | 0.222 | **2.58 / 5.08** | 41.2s |
      | 第 3 轮 | `off` | 0.917（11/12） | 0.917 | 0.833 | 0.667 | 1.000 / 1.000 | 0.667 | 0.222 | 3.58 / 7.08 | 61.0s |

      逐条翻动 8 条，全是同一形状：`xm01` `xm02` `xm05` `m01` `m02` `m04` `m05` 从
      「`max_steps`、4 次工具」变成「`final`、2–3 次工具」，`xm03` 从拒答变作答（它是可回答样本，
      旧行为把步数烧光以后只能按「证据不足」收场）。其余指标三轮逐字相同，包括
      `both_sides_hit`——少调用没有少引用某一侧
- [x] **噪声底**：同设置的第 1 轮与第 3 轮九项指标与逐条行为**逐字相同**（这批样本这一轮漂移为 0），
      所以 8 条翻动与 1 条转为作答可以归因到这次改动
- [ ] **耗时只记方向**：`on` 41.2s vs 暖 `off` 61.0s（−32%），机制清楚（每条少 1 次强制调用），
      但单次交替对照不足以报百分比，要量耗时得再单独做几对暖轮交替
- [x] **合入前复查**（在 HEAD `1a94bac`（代码与文档都定稿）以后重建的评估库：388 个向量、`reranker=有`；
      被测代码是最终提交的代码——`runtime.py` md5 `1098933f…`、`config.py` `39f56c6…`、`huggingface-hub` 0.36.2；
      单并发；29 条 = `xd01–xd08` + `xk01–xk09` + `xr01–xr05` + `xm01–xm07`，同进程 `off → on → off`；
      补写本节以后语料又长一点，长出来的都是门控说明与读数表，不含样本实体或应拒答样本的原问题）：

      | 轮 | 设置 | answer_rate | source_hit | both_sides_hit | evidence_hit@3 | citation / validity | mrr@3 | context_precision@3 | 工具调用 / 步数 | 单条均耗时 |
      |---|---|---|---|---|---|---|---|---|---|---|
      | 第 1 轮 | `off` | 1.000（29/29） | 0.958 | 0.714 | 0.792 | 1.000 / 1.000 | 0.743 | 0.486 | 1.66 / 4.07 | 74.0s（冷启动，作废） |
      | 第 2 轮 | `on` | 1.000（29/29） | 0.958 | 0.714 | 0.792 | 1.000 / 1.000 | 0.743 | 0.486 | **1.45 / 3.62** | 32.1s |
      | 第 3 轮 | `off` | 0.958（28/29） | 1.000 | 0.714 | 0.833 | 1.000 / 1.000 | 0.785 | 0.500 | 1.72 / 4.14 | 38.9s |

      主体复现：`xm01` `xm02` `xm03` `xm05` 四条仍是「`max_steps`、4 次工具、门控拦 2 次」→「`final`、
      2–3 次工具、门控拦 0–1 次」，`on` 轮的九项指标与参照轮**全等**（少调用没有少取证、也没有少引用）
- [x] **但作答率这条没复现**：这一批 29 条里同设置的第 1 轮与第 3 轮**不再逐字相同**——漂移正好落在
      `xm03` 身上（参照轮作答、暖 `off` 轮拒答，两轮都是 `max_steps`），另有 `xm07` 与 `xr03` 各多一次工具、
      `xd06` 的停止原因从 `planner_unavailable` 变 `final`。也就是说上一轮那条「`xm03` 拒答→作答」是漂移，
      不是这次改动的收益。这次能归因的只有结构性的那条：mixed 样本不再被反复逼到步数用尽
      （平均工具调用 1.66 → 1.45），指标没有回归也没有提升

## v8.10 详细验收（拒答之前先按库内实体查一次业务表）

- [x] **现象**：v8.8 把新集 8 条数据侧样本从全判错修到只剩 `xd08` 一条——它的问题指名的实体就在
      「库内数据」的取值样例里，路由器却仍判 `knowledge`，文档侧查不到那项事实就按「证据不足」拒答
- [x] **为什么不能再靠措辞**：出厂设置下同一条样本连跑三轮，同一条路由提示词在温度 0 下三轮里
      两轮判成 `knowledge`（第 1 轮判 `database`）。这台后端的路由决策本身会翻动，加提示词规则
      只能降低频率，单条样本证不了稳定
- [x] **改法**：`intent=knowledge` + 模型声明证据不足 + 问题指名的实体出现在 SQL 工具描述的取值样例里
      → 拒答之前按该实体查一次业务表。实体认的是**同一份目录**（`_catalog_entities` 解析
      「`products.name` 取值样例：橙子 / 苹果」这类行），顺带拿到所在的表与列，强制的那次查询按实体
      `LIKE` 过滤而不是整表 `SELECT`，省字符也不会被行数与字符预算截掉靠后的记录；一条问题只兜一次，
      SQL 侧已经查过就不兜（v8.9 的执行标记在这里派上用场）
- [x] `RAG_DISABLE_AGENT_KB_MISS_SQL_GATE=1` 退回「文档侧缺那项事实就拒答」的原行为
- [x] **收益读数**（把提示词第 10 条一起关掉，模拟「路由判错」这一道兜底要兜的情形，`xd01`–`xd08` 8 条，
      同进程 `off → on → off`）：`answer_rate` 0.625 → 1.000、`source_hit` 0.500 → 0.625、
      `evidence_hit@3` 0.500 → 0.625、引用两项 0.625 → 1.000；逐条上 `xd05` 与 `xd08` 从
      「拒答 / `insufficient_evidence`」变成「作答 / `final`」，`xd02` 也从拒答变作答
- [x] **护栏复查**（出厂设置，`knowledge` 9 条 + 应拒答 5 条 + `mixed` 7 条，同进程 `off → on → off`）：
      九项指标 `off` 与 `on` 全等（`answer_rate` 0.9375、`source_hit` 0.9375、`refusal_accuracy` 1.000、
      `both_sides_hit` 0.714、引用两项 1.000、`evidence_hit@3` 0.75、`mrr@3` 0.635、`context_precision@3` 0.25、
      `n_errors` 0），第 1 轮与第 3 轮逐条行为没有翻动；唯一行为差别是 `xr04` 多花一次查询（它的问题
      指名的实体在目录里），仍然正确拒答
- [x] **兜住的那条指标仍记 0**：`xd08` 门控兜住后答案内容正确、也引用了那一行 SQL，但那次 SQL 引用排在
      靠后的位置（旧一轮第 10 位、合入前复查那轮第 4 位），而 `source_hit` 与 `evidence_hit@3` 只看前 3 位
      → 判 0。这是指标口径（`@3` 窗口），不是又答错；要让这类样本计入，得改口径或让引用排序偏向最新
      取回的证据，本次没动
- [x] **合入前复查**（在 HEAD `1a94bac`（代码与文档都定稿）以后重建的评估库：388 个向量、`reranker=有`；
      被测代码是最终提交的代码——`runtime.py` md5 `1098933f…`、`config.py` `39f56c6…`、`huggingface-hub` 0.36.2；
      单并发；同 29 条、出厂设置、同进程 `off → on → off`）：

      | 轮 | 设置 | answer_rate | source_hit | both_sides_hit | evidence_hit@3 | citation / validity | mrr@3 | context_precision@3 | 工具调用 / 步数 | 单条均耗时 |
      |---|---|---|---|---|---|---|---|---|---|---|
      | 第 1 轮 | `off` | 0.958（28/29） | 0.958 | 0.714 | 0.792 | 0.958 / 0.958 | 0.743 | 0.458 | 1.48 / 3.66 | 30.0s（第一轮，作废） |
      | 第 2 轮 | `on` | **1.000（29/29）** | 0.958 | 0.714 | 0.792 | **1.000 / 1.000** | 0.743 | 0.458 | 1.55 / 3.79 | 22.9s |
      | 第 3 轮 | `off` | 0.958（28/29） | 0.958 | 0.714 | 0.792 | 0.958 / 0.958 | 0.743 | 0.458 | 1.48 / 3.66 | 23.2s |

      同设置的第 1 轮与第 3 轮九项指标与逐条行为**逐字相同**（这一批漂移为 0），所以 `on` 轮的两条翻动可以
      归因：`xd08`「拒答 / `insufficient_evidence` / 1 次工具」→「作答 / `final` / 2 次工具」，
      `xr04` 多花一次查询（它的问题指名的实体在目录里）仍然正确拒答。`source_hit` 没动，原因见上一条
- [x] **耗时不作收益依据**：暖 `off` 23.2s vs `on` 22.9s（+1.4%），本来这道门控每条最多多一次查询，
      省与多互相抵消；两轮差值也在这个量的噪声里

## v8.11 详细验收（评估集边界题与「全集 / 可比子集」两套口径）

这一项补的还是**测量能力**，不是新链路。v8.7 把集扩到 69 条以后，单条样本对均值的影响已经降到 ±1.7pt，
分辨率够了，缺的是**类型**和**加样本这件事本身的工具**：v7 与 v8 那 69 条里没有「历史里有一句错值」
（`coreference` 只考「这个项目指谁」）、没有「问两件事但其中一侧根本没有这个东西」、
没有「SQL 查出来是空集」、也没有「要的是字段原文而不是描述里的说法」。编号照「按合进 `main` 的先后排」，
树索引那支（PR #29）已合进 `main` 并占掉 `v8.6`，两道门控（PR #34）占掉 `v8.9` 与 `v8.10`，所以本节排到 **v8.11**。

- [x] 新增 `eval/v8_agentic_edge.jsonl`（11 条，id 前缀 `xe`），**旧 69 条的 id、问题、参考答案、证据句一字未改**：
      单测钉住「三份合并后前 29 个 id 与 v7 逐字相同」，以及「新 8 条可回答样本的证据与新旧全部证据两两
      不互为子串（双向断言）」——后者是这一轮最容易踩的坑：两条样本的证据句互为子串时，一次检索命中
      会同时满足两条的判定，读数就看不出是谁答对的
- [x] 覆盖的边界类型：`coreference` 3 条（历史里有一句**错值**，参考答案明写「是错的」，必须回查证据而不是照抄）、
      `refusal` 3 条（字面词能在检索里撞上——`bge-m3`、「内存」、「渤海渔港」都在语料或库里——但没有任何可答内容）、
      `mixed` 2 条（一侧查无此物或查无此内容：库里没有荔枝、语料没有导入音频，只有单侧可逐字引用）、
      `multi_step` 1 条（SQL 查出来是空集，那一半应回答「没有」而不是编出条目）、`database` 1 条
      （问 `storage` 字段原文，答成 `description` 里的说法不算命中）、`knowledge` 1 条（near-miss 干扰：
      语料里另有「改写失败就退回原问题」，与「放不下就退回命中的子块」都含「退回」）
- [x] 新集的两条 `mixed` **刻意不声明** `expected_sources`：它们只有一侧有可逐字引用的原文，声明双侧会让
      `both_sides_hit` 的分母里出现一条永远不可能命中的样本，把那个门禁指标读歪。合并 80 条的配比：
      `knowledge 17 / database 15 / mixed 14 / multi_step 11 / coreference 11 / refusal 12`，
      可回答 68 / 应拒答 12，声明双侧仍是 14 条
- [x] 新增 `scripts/check_eval_dataset.py`（自查，纯离线）：一次跑完就报告每条 `evidence` 能否在其 `source`
      文件里逐字找到、类别分布、可回答/应拒答配比、声明双侧的条数与清单、带历史的清单、跨文件的 id/问题重复、
      证据句两两互为子串的清单，以及 SQL 侧样本的证据是否真能在评估专用 SQLite 里查到（验这条时把库建到
      临时文件，不碰 `data/sql/demo_eval.db`）。结构性问题退出码非 0，`--strict` 把警告也升级为失败，
      `--json` 给机器可读输出。「互为子串」只当警告：v7 那 29 条里有既有重叠（`d05` 的证据句是 `d02` 的一部分、
      `c02`/`d04`/`s01` 三句完全相同），历史读数钉住了它们，不能因为这个卡住 CI
- [x] 新增 `scripts/eval_subset_metrics.py`（按 id 子集重算，纯离线）：把报告 JSON 里的逐条读数反向重建为
      `SampleResult`，用同一套 `aggregate_results` 再聚合一次。于是**一次跑**就能同时给出全集与可比子集两套
      读数，并把比率差换算成「几条样本」，直接套用「变化不足 1 条不当真」。单测用
      `eval/reports/v8_agentic_merged_v7_baseline.json` 钉住已记录的 0.600 / 0.833 / 0.929 与 v7 子集 0.640，
      保证这个工具的口径与当初聚合时一致
- [x] CI 由「校验五份评估集」改为「校验六份评估集」，加边界集单独校验与三份合并校验，并新增一步
      「评估集自查」；新增 10 项单测（边界集的分布与 SQL 侧 id 清单、证据不撞车、SQL 侧证据临时建库后
      只落在 1 行、拒答题的字面词能在语料/库里撞上而答案不能、合并 80 条与前 29 个 id 逐字相同、
      自查工具默认通过 / `--strict` 失败 / 坏样本报「找不到」、子集工具复现已记录读数），
      全量 `python3 -m pytest tests -q` = **664 passed**
- [x] **一轮新基线读数（2026-11-26，测于 `e0f2065`、`git_dirty=False`、语料 346 个向量、评估库 12 条记录、
      精排 80/80 生效）**。三个口径同时报，都从同一份报告 JSON 里重算：
      全集 80 条 `evidence_hit@3` 0.676、`source_hit` 0.956、`both_sides_hit` 0.857、`answer_rate` 0.926、
      `refusal_accuracy` 1.000、单条 47.8s；可比子集 69 条（v7 + v8 那两份的 id）0.683 / 0.983 / 0.857 / 0.950 / 1.000、
      38.8s；边界 11 条自己 0.625 / 0.750 / — / 0.750 / 1.000、104.1s。逐条与口径见 `eval/README.md` 的 v8.11
- [x] **可比性按任务书的要求保住**：全集数字与旧报告**不可**横向比（可回答分母 60 → 68，类别配比也变了）；
      能与 v8.7 合并报告对的是那 69 条子集。同子集对比给到 `source_hit` +10.4 条、`citation_rate` +10.4 条、
      `answer_rate` +9.2 条、`evidence_hit@3` +5.8 条，但**归因不是本轮加题**（新样本不在子集里）：
      逐条显示 7 条 `database` 样本（`xd01`–`xd07`）的意图从 `knowledge` 变 `database`、从 `insufficient_evidence`
      误拒变 `final` 作答，整轮意图 `database` 17 → 28 条、`insufficient_evidence` 17 → 11 条，
      主因是已合进 `main` 的 v8.8 路由修复；上一轮报告还记 `git_dirty=True`，出处不够硬。按项目口径
      跨轮只当方向，要坐实得在同一进程里交替跑
- [x] 语料与评估库都没动（新题全部用现有语料与现有 12 行评估库），所以 v8.7/v8.8 记录的默认档基线
      不需要因为这一轮重测。**但本文与 `eval/README.md` 的新章节会让语料变长**（`docs/*.md` 就是评估语料），
      因此本轮读数注明「测于 `e0f2065`」，复现要 `git checkout e0f2065` 再 `prepare_eval_kb.py --recreate`
- [ ] **一条边界题暴露的是判定式的脆弱，不是模型行为差异**：`xe07` 是「一侧查无此物」的 `mixed` 题，
      按参考答案答对就必须写出「查不到」，而 `is_refusal` 的判式是「答案里出现拒答标记字符串就算拒答」，
      于是这条答对的样本被记成拒答、`answer_rate` 扣 1；同样只答对一半的 `xe08` 因为措辞里没出现那个标记，
      被记成已回答。两条的差别只在措辞，按「措辞不算证据」不算行为差异。本轮**没有**改判定口径
      （改了会让历史读数不可比），只把这条写进 `eval/README.md` 的限制：这类样本进集以后，
      `answer_rate` 与 `refusal_accuracy` 的解读都要加一句「单侧缺证据的样本可能被记成拒答」
- [ ] **有一条真答错**：`xe02`（「那过期一天到底还能不能喝？」）被拒答。机制清楚——问题被指代省掉了商品名，
      路由器看不到「酸奶」，判了 `knowledge` 就没去查 SQL，而那句说法只存在于评估库那一行里。
      这是链路的真实缺陷，没有为了让数字好看去改题或把问法写进提示词
- [ ] **耗时这一项扩集以后更不可靠**：边界 11 条单条 104.1s，是可比子集 38.8s 的 2.7 倍，全集均值因此被抬到
      47.8s。最慢的 8 条里有 4 条来自新集，本轮仅有的 2 条 `context_budget` 收口也都是新样本；机制与新样本
      形态一致（一条问题两个子问题、又有一侧查不到，循环反复补取证，上下文字符 2702 vs 子集 1544）
- [ ] **测于 PR #29（章节树索引）与 PR #34（两道门控）合入之前**：那一轮跑的时候树索引默认关闭、两道门控
      还没合进 `main`。这两件事都不影响这 11 条边界题的口径（新题的证据与判定都不依赖树索引或门控），
      但会改变旧题的命中与步数，所以与这两个改动之后的轮次比较只能看方向，不能当改动证据。
- [ ] **两种边界没做**，因为必须动语料或评估库才能成立，而代价是历史读数不可比：
      ①「两个事实分属两侧但其中一个在 SQL 里是 NULL」——评估库 12 行没有任何 NULL，加一行置 NULL 会让
      工具目录多一行、v8.7/v8.8 已记录的 SQL 侧读数对应的库不再是 12 行，要重新量默认档；
      ②「文档侧与 SQL 侧说法冲突时以哪侧为准」——语料里没有互相矛盾的陈述，加一句会改变所有旧题的检索
      行为，而且现在没有任何规则说两侧同时命中时以哪侧为准。本轮用「查空集不许编造」（`xe09`）与
      「一侧查无此物」（`xe07`）当近邻替身：能说明「查不到时会不会编」，说明不了「字段是 NULL 时会不会编」

## 分支与提交约定

- `main`：稳定可演示版本，只通过阶段分支合并更新。
- 每个 `vN-*` 分支只做该阶段范围，完成后先在本地验证，再确认是否推送/合并。
- 阶段收尾时更新本文件的“状态”，并补充 README 中的使用方式与对比命令。
