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
| v7 | `claude/heuristic-edison-ebcf0b` | Agentic RAG：意图路由、SQL/工具调用、检索-行动循环、可观测 trace | ✅ 本分支完成 | 业务问题能自动选择知识库或工具；trace 可回放每条决策 |
| v7.1 | `v7.1` | SSE 流式 Agentic 输出、演示页 Agentic 开关、列表/表格感知切分、v7 评估集与量化对比 | ✅ 本分支完成 | 311 测试通过；SSE 端点可流式输出 agentic 轨迹并写回会话历史；demo.html 支持 Agentic 模式；列表/表格切分测试通过；v7 agentic 评估集 26 条 + `agent`/`full` 对比报告（agentic `answer_rate` 0.909 vs 0.318、`source_hit` 0.591 vs 0.318、单条耗时 71.2s vs 17.0s），Ragas 子集 6 条另见 `eval/reports/v7_agent_ragas_report.md` |
| v7.2 | `v7.2` | 意图路由与拒答策略：数据可得性驱动路由、证据充分性拒答 | ✅ 本分支完成 | 338 测试通过；`sql_query` 工具描述带上库内数据目录（列名 + 有界样例值，`RAG_AGENT_CATALOG_CHARS` / `RAG_AGENT_CATALOG_SAMPLES` 可关），路由判据改为「回答所需的事实住在哪一侧」；证据充分性由步进决策声明，判不足直接拒答并给 `stop_reason=insufficient_evidence`；v7 评估集 26 条复跑 `refusal_accuracy` 0.250→1.000、`source_hit` 0.591→1.000、`evidence_hit@3` 0.409→0.864、单条耗时 71.2s→51.9s，`knowledge` 类 7 条持平无回归，报告 `eval/reports/v7_2_agent_report.md`；`none` 判据偏松与指代消解未覆盖仍留在验收清单 |

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
- [ ] 扫描件上的表格结构化提取（版面表格识别，依赖位图上的表格检测）→ 归入 v7 版面处理
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
- [ ] 同一份 JSON 决策输出走原生 function calling 对比（默认模型是 MLX 构建，工具调用格式不稳定，先用 JSON 模式 + 关键词回退）

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
- [x] v7 agentic 评估集 `eval/v7_agent_qa.jsonl` 26 条（knowledge 7 / database 6 / mixed 5 / multi_step 4 / refusal 4），
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
- [ ] `none` 判据偏松：r01（橙子产地/供应商）判成 `none` 就直接拒答，绕过了「先检索、再依据证据拒答」这条
      正路；r02（有没有 iOS 客户端）在 9 条子集那次判 `none`、在全量这次判 `knowledge`。同一批样本两次的
      逐条路由并不一致，单次运行的逐条路径不能当稳定行为读，要收紧的是 `none` 的准入
- [ ] m01 的文档那一半仍没查出来（模型把「这个项目」当成缺少对话上下文）；评估按单轮执行，指代消解本就未覆盖

## 分支与提交约定

- `main`：稳定可演示版本，只通过阶段分支合并更新。
- 每个 `vN-*` 分支只做该阶段范围，完成后先在本地验证，再确认是否推送/合并。
- 阶段收尾时更新本文件的“状态”，并补充 README 中的使用方式与对比命令。
