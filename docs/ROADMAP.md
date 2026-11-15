# ROADMAP

本项目的演进路线：每个阶段一个独立分支，`main` 始终保持可运行；阶段完成后用对比数据说明收益，而不是只描述“实现了 RAG”。

| 阶段 | 分支 | 内容 | 状态 | 验收标准 |
|---|---|---|---|---|
| v1 | `main`（历史脚本在 `legacy/`） | FAISS 基础 RAG：单文件、向量召回、LLM 问答 | ✅ 已完成，仅作演进参考 | 能对一个 txt 文件完成切分、检索、问答 |
| v2 | `v2-qdrant-rerank-citation` | Qdrant 持久化、bge-reranker、混合检索（BM25 + 向量 + RRF）、标签/来源过滤、多源接入（文件/网页/SQL）、页码与引用溯源、增量与 prune | ✅ 本分支完成 | 三源可入库；重复入库跳过；更新只替换对应 doc_id；引用含类型/标题/URL 或页码；过滤生效；pytest 通过 |
| v3 | `v3-fastapi-session` | FastAPI 服务化、多轮会话（session + 历史裁剪）、流式输出、统一错误处理 | ✅ 本分支完成 | API 可并发处理多会话；历史不串号；有接口级测试与启动说明 |
| v4 | `v4-ragas-eval` | Ragas 自动评估：30–50 条标注问答、faithfulness / answer relevancy / context precision / recall、调参对比报告 | ✅ 本分支完成 | 固化评估集与脚本；给出 v2→v4 的量化对比（至少 2 组参数/策略） |
| v5 | `v5-parent-rag-query-rewrite` | Parent-Document RAG（子块召回、父块送上下文）、查询改写（多查询 / HyDE）、去重与上下文预算 | ✅ 本分支完成（评估结果见报告） | 支持父子块、多查询/HyDE、预算与去重；有 `full` / `parent` / `parent_multi` / `parent_hyde` 前后对比、Ragas 子集与失败案例分析，见 `eval/reports/v5_report.md` |
| v6 | `v6-ocr-parser` | 扫描件 OCR、表格解析、版面处理，接入现有连接器与元数据 | 计划中 | 扫描 PDF 与表格可检索；页码/表格名进入引用 |
| v7 | `v7-agentic-rag` | Agentic RAG：意图路由、SQL/工具调用、检索-行动循环、可观测 trace | 计划中 | 业务问题能自动选择知识库或工具；trace 可回放每条决策 |

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

## 分支与提交约定

- `main`：稳定可演示版本，只通过阶段分支合并更新。
- 每个 `vN-*` 分支只做该阶段范围，完成后先在本地验证，再确认是否推送/合并。
- 阶段收尾时更新本文件的“状态”，并补充 README 中的使用方式与对比命令。
