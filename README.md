# private-rag

完全本地离线的中文 RAG：支持**文件 / 网页 / 数据库**多源知识库，使用**向量 + BM25 混合召回、RRF 融合、bge-reranker 精排**，回答带**引用溯源**。v5 增加 **Parent-Document 父子块上下文**与**查询改写（多查询 / HyDE）**：子块负责召回，父块负责给模型完整上下文。Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储。v7 起额外提供 **agentic 问答**：模型按问题意图在知识库检索与只读 SQL 之间自动选择，边看结果边决定要不要继续查，并把每条决策写成可回放的 trace。

## 架构

```
data/docs (txt/md/pdf) ─┐
网页 URL ───────────────┼─→ connectors → SourceDocument → 父块(2000) → 子块(700/100) ─→ Qdrant（子块向量 + 父块原文）
SQL 数据库 ─────────────┘                                                          └─→ storage/lexical/bm25.pkl

问题 ─→ 查询改写（off / multi / HyDE，可选）
     ─→ 每个查询各跑 dense top20 + BM25 top20 → RRF 融合 top10（跨查询再融合）
     ─→ bge-reranker 用原问题精排 top3 → 父块去重/扩展（预算 6000 字符）→ Ollama 生成 [n] 引用
                                   ↑
                     CLI（chat_cli.py / chat_agent.py）/ HTTP API（serve.py，同步 + SSE + agentic）/ Web 演示页（/demo）
```

- 入库与问答彻底分离：`ingest.py` 是唯一写入入口，`chat_cli.py` 与 `serve.py` 只读。
- CLI 与 HTTP API 共用 `private_rag/qa.py` 的 `RagEngine`，检索、精排、生成与引用链路完全一致。
- 每个子块都带 `doc_id / source_type / source_uri / title / page / table / tags / content_hash`，另带父块 `parent_id / parent_index` 与父块原文，可直接生成引用；旧库没有父块字段时自动回退子块。
- 父块默认开启、查询改写默认关闭：日常问答默认不额外调用改写 LLM；需要时可通过 API 或演示页单独打开（CLI 使用默认值）。
- 前面的检索链路是固定流程：不问什么都先召回再回答。v7 在其上加一层循环（`agent/runtime.py`）：先判意图，再调 `kb_search` 或只读 `sql_query`，读完观察决定继续查还是作答，最后仍用统一编号的证据生成带引用回答。
- 阶段路线与验收标准见 [docs/ROADMAP.md](docs/ROADMAP.md)。
- 想先从零理解“建库 → 检索 → 生成”的完整流程，可读 [docs/beginner-guide.md](docs/beginner-guide.md)（小白版流程导读 + 示意代码）。

## 环境要求

- Python 3.9+
- [Ollama](https://ollama.com/) 已启动，且已拉取模型：

```bash
ollama pull bge-m3
ollama pull qwen3.8:27b-mlx
```

- 首次运行会自动从 HuggingFace 下载 `BAAI/bge-reranker-v2-m3` 重排模型。
- SQLite 开箱即用；MySQL/PostgreSQL 需要额外安装驱动（见下文“数据库接入”）。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 若希望复用本机已安装的依赖（避免重装 torch 等大包），可改用：
> `python3 -m venv --system-site-packages .venv`，然后直接激活使用。

开发与测试依赖：

```bash
pip install -r requirements-dev.txt
```

## 快速开始（文件 + 数据库演示）

```bash
# 1. 生成演示 SQLite 库（data/sql/demo.db 已 gitignore）
python3 scripts/init_demo_db.py

# 2. 按演示配置全量入库（文件 data/docs + 数据库 products 表）
python3 scripts/ingest.py --config config/sources.demo.yaml --recreate

# 3. 按来源过滤提问，回答会带编号引用
python3 scripts/chat_cli.py "橙子有什么营养？" --source-type sql
python3 scripts/chat_cli.py "香蕉要放冰箱吗？" --source-type file
```

常用参数：

```bash
python3 scripts/chat_cli.py "维生素 C 相关的记录有哪些？" --tag business
python3 scripts/chat_cli.py "苹果适合冷藏吗？" --dense-only        # 只走向量，用于对比
python3 scripts/chat_cli.py "苹果适合冷藏吗？" --no-rerank         # 跳过精排，便于观察召回
```

交互模式（`exit` 退出）：

```bash
python3 scripts/chat_cli.py --source-type sql
```

## HTTP API（v3）

启动服务（另开终端）：

```bash
python3 scripts/serve.py                  # 默认 127.0.0.1:8000
python3 scripts/serve.py --port 8766 --no-rerank
python3 scripts/serve.py --reload         # 开发模式，代码变更自动重启
```

- Swagger 文档：<http://127.0.0.1:8000/docs>
- `GET /health`：存活检查；`GET /ready`：检查 Qdrant 集合、BM25 与 reranker，未就绪返回 503。
- 默认只监听本机、无鉴权，请勿直接暴露公网。
- `--no-rerank` 等价于 `RAG_DISABLE_RERANK=1`；readiness 会显示“重排模型未加载”，问答仍按融合结果降级运行。

### Web 演示页

启动服务后打开 <http://127.0.0.1:8000/demo>。页面由 FastAPI 同源提供，只有一个 HTML 文件，无构建步骤、无 CDN、无外部资源，直接调用真实 API：

- 实时读取 `/health` 与 `/ready`，显示版本、向量数、BM25/reranker 状态、未就绪原因与 warning。
- 对话支持流式/同步切换、停止生成、示例问题；Enter 发送，Shift+Enter 换行，问题上限 2000 字符。
- 可调 `top_k`（1–10）、`source_type`、`tags`、`use_lexical`、`use_rerank`、`use_parent` 与 `query_rewrite`（关闭 / 多查询 / HyDE）；结果区的 `meta`、引用和会话历史会随参数变化。
- 本轮信息会展示实际送入的父块字符数、查询数与改写模式；引用片段优先显示命中的子块原文。
- 支持新建、复制、删除会话与刷新历史；流式生成会逐字显示，并展示 `session → citations → token → done` 的真实事件效果。
- 错误统一展示 `code / message / details / request_id`；流式中途停止不会留下半轮会话。
- 仅用于本机演示：服务默认无鉴权，请勿直接暴露公网。

### 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活检查，200 |
| GET | `/ready` | 知识库/BM25/reranker 就绪状态，不就绪返回 503 |
| POST | `/api/v1/sessions` | 创建会话，201；可选 `{"title": "..."}` |
| GET | `/api/v1/sessions/{id}` | 会话详情与历史消息 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话，204 |
| POST | `/api/v1/chat` | 同步问答，JSON |
| POST | `/api/v1/chat/stream` | SSE 流式问答 |
| POST | `/api/v1/chat/agent` | agentic 问答：意图路由 + 工具调用，返回回答与决策轨迹 |
| GET | `/api/v1/traces` | 最近的 agentic 决策轨迹索引 |
| GET | `/api/v1/traces/{trace_id}` | 回放一条决策轨迹（结构化 + 可读文本） |

### 多轮会话与 curl 示例

```bash
# 创建会话（也可以省略 session_id，由 /chat 自动创建）
SID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/sessions \
  -H 'Content-Type: application/json' -d '{"title":"演示"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')

# 第一轮：橙子的营养
curl -s -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"question\":\"橙子有什么营养？\"}"

# 第二轮：带上第一轮历史，理解“它”指橙子
curl -s -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"question\":\"它适合冷藏吗？\"}"

# 查看历史 / 删除会话
curl -s http://127.0.0.1:8000/api/v1/sessions/$SID
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE http://127.0.0.1:8000/api/v1/sessions/$SID
```

请求体字段：

- `question`：1–2000 字符，必填。
- `session_id`：可选；省略时自动创建新会话，响应回传 `session_id`；未知/过期 ID 返回 404。
- `top_k`：1–10，默认 3，表示最终送入 LLM 的片段数；父块开启时按“最多几个父块”计。
- `source_type`：`file` / `web` / `sql`；`tags`：最多 10 个，命中任意一个即可。
- `use_lexical` / `use_rerank`：是否启用 BM25 与 reranker，默认均为 `true`。
- `use_parent`：是否把命中的子块扩展为父块上下文，默认 `true`；关闭后只送命中子块。
- `query_rewrite`：`off` / `multi` / `hyde`，默认 `off`。`multi` 生成最多 2 个改写查询，`hyde` 生成一段假想文档；两者都只用于检索，最终仍用原问题精排。

同步响应包含 `session_id / answer / citations / meta / request_id`；`meta` 中有 `retrieved / used_lexical / used_rerank / used_parent / query_rewrite / query_count / context_chars / history_turns / elapsed_ms`。

### SSE 流式输出

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"香蕉要放冰箱吗？"}'
```

事件顺序为 `session → citations → token... → done`，异常时为 `error`：

```
event: session    data: {"session_id":"sess_...","request_id":"..."}
event: citations  data: {"citations":[...],"retrieved":3}
event: token      data: {"text":"香蕉"}
event: done       data: {"session_id":"...","answer":"...","citations":[...],"meta":{...},"request_id":"..."}
event: error      data: {"code":"generation_failed","message":"...","request_id":"..."}
```

- 流式响应头带 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`，便于经过反向代理时不被缓冲。
- 只有完整生成成功后才会把本轮问答写入会话；客户端中途断开不会留下半截历史。
- 响应头一旦发出，后续错误只能以 SSE `error` 事件返回，HTTP 状态码无法再修改；参数校验、未知会话、知识库未就绪等“发头前”错误仍是普通 JSON。

### 统一错误格式

```json
{
  "error": {
    "code": "session_not_found",
    "message": "会话不存在或已过期",
    "details": null,
    "request_id": "..."
  }
}
```

- `422 invalid_request`：参数校验失败，`details` 给出字段位置与原因，不回显问题正文。
- `404 session_not_found` / `404 not_found`：会话或路由不存在。
- `503 knowledge_base_not_ready` / `503 session_capacity_reached`：知识库未就绪或会话容量耗尽。
- `500 internal_error`：服务内部异常，堆栈只写日志，不回传客户端。
- 所有响应都带 `X-Request-ID`；客户端可传入合法 ID（字母、数字、`.`、`_`、`:`、`-`，≤128 字符），否则自动生成。

### 会话与并发模型

- 会话保存在进程内存：默认 TTL 30 分钟、最多 500 个会话、每个会话最多 60 条消息；服务重启即清空。
- 送入 LLM 的历史默认只取最近 6 轮，并按 3000 字符预算从新到旧裁剪，避免 prompt 无限增长。
- 同一会话的并发请求用会话锁串行，不同会话可并行；Qdrant 本地 client 与 reranker 由引擎锁保护，生成阶段不锁。
- 路由使用同步函数，由 FastAPI 放入线程池执行；SSE 使用同步生成器，不阻塞事件循环。
- Qdrant 本地磁盘模式同一路径只允许一个进程，因此 `serve.py` 固定单 worker。需要多 worker/横向扩展时，请切换 Qdrant server 模式，并把内存会话替换为 Redis/SQLite 等外部存储，同时补充鉴权与限流。

会话与服务参数可用环境变量覆盖：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `RAG_SESSION_TTL_SECONDS` | 1800 | 会话空闲过期时间 |
| `RAG_MAX_SESSIONS` | 500 | 进程内最大会话数 |
| `RAG_MAX_SESSION_MESSAGES` | 60 | 每个会话保留的消息条数（问答成对） |
| `RAG_MAX_STORED_MESSAGE_CHARS` | 4000 | 单条消息落库截断长度 |
| `RAG_MAX_HISTORY_TURNS` | 6 | 送入 prompt 的最大历史轮次 |
| `RAG_MAX_HISTORY_CHARS` | 3000 | 历史总字符预算 |
| `RAG_MAX_HISTORY_MESSAGE_CHARS` | 800 | 历史中单条消息截断长度 |
| `RAG_DISABLE_RERANK` | 未设置 | 设为 `1/true/yes/on` 时跳过 reranker |
| `RAG_DISABLE_AGENT_NONE_PROBE` | 未设置 | 设为 `1/true/yes/on` 时退回旧行为：路由判 `none` 就跳过检索直接拒答（默认先用原问题做一次验证检索） |
| `RAG_AGENT_PLANNER_MODE` | `json` | 设为 `function` 时路由/步进决策改走原生 function calling（同一提示词与预算，见 `ask_tool_json`）；非法值归一为 `json`。两条通道的对比读数见 `eval/reports/fc_comparison_report.md` |

## v4 自动评估（Ragas）

v4 用固定评估集把“检索策略有没有用”量化。评估知识库只使用项目自身文档（`README.md`、`docs/ROADMAP.md`、`docs/beginner-guide.md`），与 `data/docs` 演示库完全隔离：

| 评估资源 | 路径 |
|---|---|
| Qdrant 集合 | `rag_eval_kb` |
| Qdrant 存储 | `storage/eval/qdrant_storage` |
| BM25 索引 | `storage/eval/lexical/bm25.pkl` |
| Ragas 判分缓存 | `storage/eval_cache` |

这些路径通过 `RAG_COLLECTION_NAME`、`RAG_STORAGE_DIR`、`RAG_LEXICAL_INDEX_PATH` 环境变量覆盖，由 `private_rag/eval_env.py` 在导入配置前设置，因此**不会与正在运行的演示服务争抢 Qdrant 本地锁**。

评估集固定 40 条：36 条可回答 + 4 条应拒答，覆盖架构、入库、检索、API、边界与拒答 6 类；标注与字段说明见 [eval/README.md](eval/README.md)。v5 另有 18 条口语化问题集，v7 另有按「需要哪个工具」分类的 agentic 评估集（见下文「v7 评估」）。

三组策略：

| 配置 | BM25 | reranker | 用途 |
|---|---|---|---|
| `dense` | 否 | 否 | 纯向量召回基线 |
| `hybrid` | 是 | 否 | 观察混合召回 + RRF 的增益 |
| `full` | 是 | 是 | 当前 v2/v3 默认链路，作为 **v2 基线** |

四个 Ragas 指标：`faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`；同时计算不依赖 judge 的确定性指标（evidence hit/recall、context precision、MRR、source hit、拒答准确率、回答率、引用率/有效性、延迟）。报告会给出各配置与 `full` 的差值和覆盖率，NaN 不会被替换成 0。

安装评估依赖（不改变主依赖版本）：

```bash
pip install -r requirements-eval.txt
```

构建评估库并校验数据集：

```bash
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --validate-only
```

先跑冒烟，确认本地 judge 的 JSON 输出可解析：

```bash
python3 scripts/evaluate.py --configs full --limit 3 --ragas-configs full --ragas-limit 2 \
  --output /tmp/v4_smoke.md --json-output /tmp/v4_smoke.json
```

全量评估（确定性指标覆盖三组，Ragas 默认 `dense,full`）：

```bash
python3 scripts/evaluate.py --configs dense,hybrid,full --ragas-configs dense,full \
  --output eval/reports/v4_report.md --json-output eval/reports/v4_report.json
```

如果本机已安装官方 7B（`ollama pull qwen2.5:7b-instruct`），可换成更快的 judge；请使用独立缓存目录，避免复用 27B 的判分缓存：

```bash
python3 scripts/evaluate.py --configs dense,hybrid,full --ragas-configs dense,full \
  --judge-model qwen2.5:7b-instruct --cache-dir storage/eval_cache_qwen25_7b \
  --output eval/reports/v4_report.md --json-output eval/reports/v4_report.json
```

- `--skip-ragas` 只跑确定性指标；`--ragas-limit` 可先跑子集，报告会明确标注覆盖数。
- 生成与 judge 默认都是 `qwen3.8:27b-mlx`，存在自评偏差，分数只适合同模型下的相对比较。`--judge-model` 可换成本机已安装的其他模型；例如官方 `qwen2.5:7b-instruct`（GGUF，约 4.7GB）判分更快，但判分能力弱于 27B，**不同 judge 的分数不能直接混比**。
- Ragas judge 默认单 worker，并限制 `num_predict=4096`、关闭流式：本机 MLX runner 不限输出长度时会持续生成，导致连接断开或 JSON 截断。`OLLAMA_NUM_PARALLEL=1` 下调高 `--max-workers` 只会排队，不能真正加速。
- 27B judge 单条样本的四项指标实测约 6–7 分钟；36 条可回答样本 × 2 组配置的全量 Ragas 预计数小时，建议放后台执行。换成 7B judge 会明显加快，但仍应先跑冒烟确认四项指标都有数值。时间不够时可先用 `--ragas-limit 8` 跑子集并在报告中标注样本数，不要把子集结果写成全量结论。缓存位于 `storage/eval_cache`，重复运行会明显加快；**换 judge 后不要复用旧缓存**，可加 `--no-cache` 或删除缓存目录。
- **评估语料就是仓库文档**：改完 README/docs 或标注集后，必须重新执行 `prepare_eval_kb.py --recreate` 再跑报告，否则结果与仓库内容不一致。
- 报告与数据集会提交到仓库，评估库、BM25 与缓存不会（`storage/` 已 gitignore）。

## v5 Parent-Document RAG 与查询改写

v5 处理两个问题：子块召回准，但 700 字符上下文容易丢前因后果；用户换个说法或多跳提问时，单次检索容易漏。默认策略是**父块开、查询改写关**，日常问答不增加额外的 LLM 调用。

### 父子块：小块召回，大段阅读

- 入库先按 2000 字符、0 重叠切父块，再在每个父块内按 700/100 切子块；向量与 BM25 **只索引子块**。
- 子块 payload 带 `parent.id/index/count/text`，BM25 索引同样保存父块字段；命中后按父块去重，在 `RAG_CONTEXT_MAX_CHARS` 预算内送父块整段原文。
- 父块放不进剩余预算时回退到命中的子块（必要时截断），保证命中内容不丢；`snippet_text` 保存子块原文，用于引用展示。
- `top_k` 限制的是最终送入的父块数；`retrieved` 也按父块数计。
- 旧库没有 `parent` 字段时自动回退子块，无需迁移；重新执行 ingest 后生效。
- 父块文本随每个子块各存一份，Qdrant 与 BM25 体积约放大 2–3 倍；换来的是无需额外 docstore，也不会出现父子存储不一致。

| 环境变量 | 默认 | 说明 |
|---|---:|---|
| `RAG_PARENT_CHUNK_SIZE` | 2000 | 父块最大字符数；修改后需重建知识库 |
| `RAG_PARENT_CHUNK_OVERLAP` | 0 | 父块重叠字符数 |
| `RAG_CONTEXT_MAX_CHARS` | 6000 | 送入 LLM 的父块总字符预算 |

### 查询改写：换个说法再搜一次

| 模式 | 行为 | 额外成本 |
|---|---|---|
| `off` | 只用原问题检索；默认 | 无 |
| `multi` | 让本地 LLM 生成最多 2 个语义等价的查询，原问题 + 变体各跑一遍 dense + BM25，再按子块做跨查询 RRF | 1 次 LLM 调用 + 2 次检索 |
| `hyde` | 让 LLM 写一段“假想文档”，与原问题一起检索；HyDE 文本只用于检索，不进入最终上下文 | 1 次 LLM 调用 |

- 所有查询最终仍用**原问题**做 reranker 精排，避免改写文本把答案带偏。
- 改写失败（JSON 非法、模型不可用、没有可用变体）自动回退原问题，`meta.query_count` 为 1；错误信息最多 200 字符且不包含问题正文。
- 改写温度固定 0、`num_predict=512`，每个变体 ≤300 字符，HyDE ≤400 字符。

API 示例：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"香蕉要放冰箱吗？","use_parent":true,"query_rewrite":"multi"}'
```

响应 `meta` 会给出 `used_parent / query_rewrite / query_count / context_chars`；SSE 的 `citations` 与 `done` 事件携带同一组字段。

### v5 评估

评估集 `eval/v5_paraphrase_qa.jsonl` 共 18 条（15 条可回答 + 3 条应拒答），覆盖口语化、多跳、降级与边界问题；`evidence` 必须逐字出现在来源文档。确定性指标覆盖 4 组配置，Ragas 只跑 `full` 与 `parent_multi` 各 12 条子集：

| 配置 | 父块 | 查询改写 | 用途 |
|---|---|---|---|
| `full` | 否 | `off` | v4 基线（父块关闭，保持原口径） |
| `parent` | 是 | `off` | 单独观察父块上下文 |
| `parent_multi` | 是 | `multi` | 父块 + 多查询 |
| `parent_hyde` | 是 | `hyde` | 父块 + HyDE |

```bash
# v5 主报告（新数据集）
python3 scripts/evaluate.py --dataset eval/v5_paraphrase_qa.jsonl \
  --configs full,parent,parent_multi,parent_hyde \
  --ragas-configs full,parent_multi --ragas-limit 12 \
  --output eval/reports/v5_report.md --json-output eval/reports/v5_report.json

# v4 40 条回归（同一 4 组配置）
python3 scripts/evaluate.py --configs full,parent,parent_multi,parent_hyde \
  --ragas-configs full,parent_multi --ragas-limit 12 \
  --output eval/reports/v5_v4_regression.md --json-output eval/reports/v5_v4_regression.json
```

报告会列出 `avg_context_chars`、`avg_query_count`、`used_parent_rate`、`rewrite_fallbacks`、相对 `full` 的差值与失败案例；Ragas 只覆盖 12 条子集，不能写成全量结论。实际增益或负增益以报告为准，见 [eval/reports/v5_report.md](eval/reports/v5_report.md) 与 [eval/reports/v5_v4_regression.md](eval/reports/v5_v4_regression.md)。

- 评估语料仍是仓库文档：先定稿 README/docs，再 `prepare_eval_kb.py --recreate`，最后跑报告。
- 演示页的”v4 评估”面板继续读取 `v4_report`；v5 四组对比在报告里查看，避免一次运行覆盖 v4 基线。

## v6 扫描件 OCR 与表格解析

v6 为 PDF 文件接入 **OCR 与表格提取**：扫描件 PDF 经 OCR 后可被检索，PDF 中的表格被结构化提取为独立文档，页码与表格名进入引用。

### 原理

- 入库时优先使用 `pdfplumber` 打开 PDF：
  - **文本型 PDF**：直接提取页面文本与表格（`extract_text` / `extract_tables`），速度同 v5。
  - **扫描件 PDF**：页面文本为空或没有实质字符（汉字/拉丁字母）时，判定为扫描件，自动渲染页面 → OCR 识别文字。
- OCR 渲染固定 **200 dpi**（`OCR_RENDER_RESOLUTION`）：`pdfplumber` 默认 72 dpi，实测中文会认错（"产品库存" → "产品本存"，置信度 0.79），200 dpi 下整句正确、置信度约 0.98。
- 表格作为独立的 `SourceDocument` 入库，获得独立的向量与 BM25 索引；引用中显示表名与页码。
- **扫描件上的表格**走版面识别（`private_rag/ingestion/table_layout.py`）：OpenCV 形态学（横/纵结构线开运算）在位图上检测网格线、定位表格矩形；裁剪后的区域先白色补齐成方形再交给 `rapid-table`（SLANet-Plus ONNX，避免引擎 488×488 非等比拉伸扭曲结构）；整页 RapidOCR 文本框按表格区域裁剪、平移到 crop 坐标后以 `ocr_results` 传给引擎拼带文本的 HTML（引擎不跑内部 OCR），含 rowspan/colspan 的单元格由解析展开。正文侧用 `filter_ocr_result` 丢弃中心落在表格矩形内的 OCR 框，避免表格内容在正文里重复一份。总开关 `RAG_DISABLE_SCAN_TABLE`，模型路径可用 `RAG_SCAN_TABLE_MODEL` 预置（离线）。
- 父子块切分对 OCR 与表格透明：正文与表格各自走相同的父子块流程。

### 依赖安装

OCR 功能为**可选依赖**，不安装时 PDF 仍按现有方式处理（文本提取），不阻塞服务启动：

```bash
# 安装 OCR 依赖（可选）
pip install pdfplumber rapidocr-onnxruntime rapid-table
```

- `pdfplumber 0.11.7`：PDF 渲染 + 表格提取。
- `rapidocr-onnxruntime 1.4.4`：中英 OCR，ONNX 模型内置（约 100MB），无需联网下载。
- `rapid-table 3.0.2`：扫描件版面表格结构识别（SLANet-Plus ONNX），包内自带模型文件，离线可用。

### 降级行为

| 场景 | 行为 |
|---|---|
| 未安装 `pdfplumber` | 回退到 `PyPDFLoader`（文本提取，无 OCR/表格） |
| 未安装 `rapidocr-onnxruntime` | 扫描件页面跳过 OCR，文本为空则记录 warning |
| OCR 推理失败 | 该页跳过，记录 warning，不影响其他文件 |
| 未安装 `rapid-table` 或 SLANet 模型缺失 | 版面表格提取整体跳过（引擎初始化失败按进程缓存），扫描件正文 OCR 照常入库 |
| 单个表格区域识别失败 | 该区域跳过并记录 warning，其余区域与正文不受影响 |

降级行为在入库日志中输出 warning，不阻塞其他文件。

### 效果

- 扫描 PDF 入库后，OCR 文本进入向量与 BM25 索引，问答时可被召回。
- 表格作为独立文档入库，引用中显示 `表 {表名}_table_{N}` 与页码。
- 文本型 PDF 继续正常工作，不退回 v5 功能。
- 版面表格消融（`eval/v6_scan_table.jsonl` 6 条，`full` 配置，独立两库对照）：开启后 `evidence_hit@3` 0.2→1.0、`context_precision@3` 0.2→0.5，两侧 `answer_rate` 与 `refusal_accuracy` 持平；报告见 `eval/reports/v6_scan_table_on.md` 与 `eval/reports/v6_scan_table_off.md`。

### 已知限制

- **结构化表格需要边框线**：文本型 PDF 的 `extract_tables` 依赖页面文字对象与绘制的边框线；扫描件的版面识别依赖位图上可检测的网格线（OpenCV 形态学）。无边框、纯靠文字对齐排版的表格两条路都提不出，仍以整页 OCR 正文入库。
- **扫描件单元格文本按 OCR 框中心匹配**：合并单元格由 SLANet 的 HTML span 声明展开；网格过小（<60×20 px）、线条断裂或结构复杂时可能漏检或错位，消融 fixture 未覆盖复杂合并表格。
- **表格需要有边框线**：只是靠文字对齐排版的表格，`extract_tables` 默认策略识别不出来（实测返回 0 张）；这类内容仍会以整页正文的形式被检索到。
- **OCR 精度**：清晰印刷体效果好，手写体、低分辨率、倾斜页面识别率下降；200 dpi 渲染可缓解低分辨率这一类。

## v7 Agentic RAG（意图路由 + 工具调用 + 决策轨迹）

前面几版的流程是固定的：任何问题都先召回、精排、再生成。v7 在它上面加一层循环——模型先看问题决定用哪个工具，读完返回结果再决定要不要继续查，最后仍基于统一编号的证据生成带引用回答。

```text
问题 → 判意图 → 调工具 → 读观察 → 再决定：继续查还是作答
                  ↑__________|
       边界：工具步数、证据字符预算、SQL 返回行数
```

模型只有两个工具可选：

- `kb_search`：走 v2–v6 的完整检索链路（dense + BM25 + RRF + rerank + 父子块），入参 `query`。
- `sql_query`：运行时只读查询业务库，入参 `sql`。默认不挂这个工具，需要在 `sources.yaml` 里按 sql 源逐个显式授权：

```yaml
  - type: sql
    # ……入库字段同上……
    allow_runtime_query: true       # 开启运行时查询
    runtime_tables: [products]      # 留空则由入库 query 的 FROM 子句推导
```

默认配置文件 `config/sources.yaml` **不在仓库里**（仓库只放 `sources.demo.yaml` / `sources.example.yaml`），所以新克隆或新 worktree 里直接起服务时 `sql_query` 不会注册。这种情况不再静默：启动日志会打一条 WARNING，写明找不到的配置路径、`sql_query 未注册` 的后果，以及一条可直接照抄的修复命令

```bash
RAG_AGENT_SOURCES_CONFIG=config/sources.demo.yaml python3 scripts/serve.py --port 8001
```

只用文档知识库的用法本来就不需要这份配置，忽略这条提示即可（服务照常起、`/health` 仍可探活，不会因此启动失败）；如果显式指定的配置路径不存在、或配置存在但写坏了，则记 ERROR 并打出路径。

`sql_query` 执行前先过只读护栏（`private_rag/agent/sql_guard.py`）：只允许单条 `SELECT`/`WITH`，出现写操作或元数据关键字直接拒绝，表必须在白名单内（`WITH` 定义的临时视图名不算表），并强制一个不超过 `AGENT_SQL_ROWS` 的 `LIMIT`；护栏改写后的 SQL 与实际行数都会写进轨迹。**护栏是纵深防御的一层，不是沙箱**：这个 DSN 仍然应该使用只读数据库账号，`allow_runtime_query` 默认关闭也是这个原因。

意图分成 `knowledge` / `database` / `mixed` / `none` 四类。`none` 表示与知识库无关，直接按既有约定返回拒答文本，不做任何检索。

路由器只看得到工具的 `description`，所以库里存着什么必须写进描述才可能判准意图：装配 `sql_query` 时会对白名单表做一次**有界采样**（列名 + 每个文本列少量样例值，总长受 `RAG_AGENT_CATALOG_CHARS` 限制，`RAG_AGENT_CATALOG_SAMPLES=0` 则只列列名不取样例），采样出的词同时作为关键词回退的补充线索。判意图的标准是「回答所需的事实住在哪一侧」，不是问法像不像技术查询：问一条业务记录自身的描述、备注、状态、时间，即使一个数字词都没有也算 `database`；只要有一侧被漏掉就选 `mixed`。代价是**样例值会进入 prompt**：业务数据敏感的库请把 `RAG_AGENT_CATALOG_SAMPLES` 设为 0，只让路由器看列名。

证据够不够用由步进决策自己声明（`sufficient` / `missing`），不额外加一次判断调用：判不足时不进生成，直接返回拒答哨兵并写明缺少的信息，`stop_reason` 为 `insufficient_evidence`。生成侧同样写死「资料里没提到不等于不存在」，禁止用通用常识补全有据无据都行的事实；确实有缺口时回答第一行必须是拒答哨兵，好让「没查到」不被读成「不存在」。

`none` 不再是一张免检通行证：路由器判 `none` 只说明它「猜两侧都查不到」，而拒答的正当理由只有「查过了，证据里没有」。所以判成 `none` 时不再直接拒答，而是先用原问题做一次知识库验证检索——查到东西就照常作答，并把意图按实际作答依据报成 `knowledge`；查不到才返回拒答哨兵，`stop_reason` 记 `no_evidence`。代价是一条真正无关的问题多花一次 `kb_search`（仍受 `RAG_AGENT_MAX_STEPS` 约束）；`RAG_DISABLE_AGENT_NONE_PROBE=1` 退回「判 none 就直接拒答」的旧行为，知识库工具不可用时同样走旧行为。路由提示词同步收紧准入：`none` 只留给实时信息、天气、新闻、外部网站内容、主观偏好这类明确在本系统之外的事实，问到某条业务记录、某个字段或本系统自身的文档时一律先取证——字段里到底有没有，要查过才知道。v7 评估集 26 条复跑（`eval/reports/v7_2_nonegate_report.md`）：路由判 `none` 的样本从 1 条变成 0 条、`no_retrieval` 也从 1 条降到 0 条，4 条应拒答样本仍全部拒答（`refusal_accuracy` 1.000 持平），`citation_rate` 0.955→1.000，平均工具调用 1.31→1.42。

决策走 JSON 模式，路由与步进两次调用共用 `RAG_AGENT_PLANNER_TOKENS`（默认 2000）输出预算。本机默认模型是推理型，会先输出一段不可见的思考内容：预算给小了（实跑时用的 400）模型只吐思考、正文为空，路由就会整段回退到关键词判断。轨迹里会写明回退原因，看到「模型输出中没有 JSON 对象」就先怀疑这个预算。
同一份提示词也量过原生 function calling（2026-11-22，`scripts/compare_fc_decisions.py`，读数 `eval/reports/fc_comparison_report.md`）：默认模型 qwen3.8:27b-mlx 路由 12/12、步进 3/3 都返回了可用的 `tool_calls`，路由单条均耗时 18.8 秒（快于 JSON 模式的 27.0 秒），两通道意图一致率 0.75；对照组 qwen2.5:7b-instruct 只有 4/12 成功——提示词仍写着「只输出 JSON」，该模型多数情况直接吐 JSON 文本而不调工具，失败数含这个混淆，不能读成模型不支持工具调用。JSON 模式仍是默认：任何模型可用，切默认前需先跑 29 条评估确认无回归；`RAG_AGENT_PLANNER_MODE=function` 仅作实验开关。

### 命令行

```bash
# 前提与 chat_cli.py 相同：本机 Ollama 在跑，且已 ingest 过知识库
python3 scripts/chat_agent.py "最便宜的香蕉多少钱，应该怎么保存？"

# 只用知识库检索，不让模型碰数据库
python3 scripts/chat_agent.py --no-sql "增量入库怎么做"

# 限定标签与来源类型，并且只打印回答与引用（默认会打印每步决策）
python3 scripts/chat_agent.py --compact --tag business --source-type sql "库存里最便宜的香蕉多少钱"

# 交互模式，多轮追问会带上会话历史
python3 scripts/chat_agent.py -i
```

```bash
# 回放：先列最近的轨迹索引，再看某一条的完整决策过程
python3 scripts/replay_trace.py --list
python3 scripts/replay_trace.py --last 3
python3 scripts/replay_trace.py --trace-id <trace_id>
python3 scripts/replay_trace.py --trace-id <trace_id> --json
```

### HTTP 接口

```bash
# agentic 问答：比 /chat 多 max_steps（1–8）与 allow_sql 两个字段，其余同 /chat
curl -s -X POST http://127.0.0.1:8000/api/v1/chat/agent \
  -H 'Content-Type: application/json' \
  -d '{"question":"最便宜的香蕉多少钱？","max_steps":4,"allow_sql":true}' | python3 -m json.tool

# 最近的轨迹索引（limit 1–50，默认 10），以及回放某一条
curl -s 'http://127.0.0.1:8000/api/v1/traces?limit=5' | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/v1/traces/<trace_id> | python3 -m json.tool
```

响应在 `/chat` 的 `session_id / answer / citations / meta / request_id` 之外多一个 `trace`（完整步骤）；`meta` 里给出 `intent / stop_reason / tool_calls / steps / evidence_blocks / context_chars / history_turns / trace_id`。`citations` 与回答里的 `[n]` 仍是一一对应，SQL 结果的引用复用 `table` 字段。`stop_reason` 取值：`final`（模型认为证据够了）、`max_steps`（步数用尽）、`context_budget`（证据字符预算用完）、`no_retrieval`（判定与两侧都无关且没做验证检索，只在知识库工具不可用或显式关掉探测时出现）、`no_evidence`（检索过但没有可用证据，含路由判 `none` 后验证检索仍空手）、`insufficient_evidence`（判定证据不足，直接拒答）、`generation_failed`（生成失败）。

降级行为（任何一层挂掉都不会让整个请求 500）：

- 决策模型不可用 → 路由退回关键词判断，循环按启发式继续。
- 工具执行失败（含被护栏拒绝） → 失败原因作为观察回灌给模型，它可以换个 SQL 或改用知识库。
- 一条证据都没收集到 → 返回既有拒答文本，不编造答案。
- agentic 运行时装配失败（例如 `sources.yaml` 写坏了、或 SQL 源连不上）→ 只有 `/api/v1/chat/agent` 返回 503 `agent_unavailable`，`/chat` 与 `/chat/stream` 不受影响。连不上库时日志会先记一条 ERROR，写明是哪个源、缺哪个 SQLite 文件；演示库 `data/sql/demo.db` 被 gitignore 掉了，新克隆与新 worktree 要先跑 `python3 scripts/init_demo_db.py`——否则 `data/sql/` 目录已存在时 sqlite 会静默建出 0 字节空库，`sql_query` 看着注册成功但表一张都没有，运行时只报 `no such table`，日志同样会在连库前点破。
- `sources.yaml` 不存在（新克隆默认就没有）→ agentic 只注册 `kb_search`，启动日志给出缺失路径与修复命令（默认路径缺失记 WARNING，显式指定的路径不存在记 ERROR），服务不会因此起不来；查库类问题会答不出来。

每一次 agentic 问答追加一行 JSON 到 `storage/traces/agent.jsonl`（已 gitignore），记录意图、每步的工具与参数、观察、返回行数与耗时，以及最终引用。`trace_id` 同时出现在 `meta` 与响应头之外，可据此逐条回放。

### v7 评估（agentic vs 固定链路）

v4/v5 的评估集量的是固定链路的质量；v7 这份 `eval/v7_agent_qa.jsonl`（29 条 = 25 可回答 + 4 应拒答）按**回答需要哪个工具**分类，直接量 agentic 多出了什么：

| 类别 | 条数 | 需要 | 固定链路应该怎样 |
|---|---:|---|---|
| `knowledge` | 7 | 只查项目文档 | 与 agentic 组差值接近 0（回归检查） |
| `database` | 6 | 只查运行时 SQL（`products` 表） | 必然拒答 |
| `mixed` | 5 | 文档 + SQL 各出一段证据 | 只能答出一半 |
| `multi_step` | 4 | 先拿结果再决定下一步 | 无法作答 |
| `coreference` | 3 | 先靠对话历史还原指代（v7.3 新增，样本带 `history` 字段） | 指代还原不了：固定链路压根不接 history 参数（只调 `retrieve`/`generate_answer`），c02/c03 的库侧必然查不到，c01 只能靠字面蒙 |
| `refusal` | 4 | 文档与库里都没有 | 两组都应拒答 |

**评估库仍只入库项目文档**，SQL 工具由一份只供运行时、不参与入库的配置单独挂上（`config/sources.eval.agent.yaml`，`allow_runtime_query: true` + `runtime_tables: [products]`）。因此 `database` 类问题在固定链路上必然查不到——这正是要量的差值本身，不是数据泄漏；`data/sql/demo.db` 不存在时评估直接报错退出，不会出一份「看起来能跑」的报告。

```bash
python3 scripts/init_demo_db.py
python3 scripts/prepare_eval_kb.py --recreate
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --validate-only
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent,full \
  --output eval/reports/v7_agent_report.md --json-output eval/reports/v7_agent_report.json
# 只量「会检索但不让查库」的能力上限
python3 scripts/evaluate.py --dataset eval/v7_agent_qa.jsonl --configs agent --no-agent-sql --skip-ragas
```

`agent` 组的口径与固定链路不同义，报告的限制小节逐条写明：耗时是整条循环的墙钟时间（`avg_retrieval_ms` 只记工具执行耗时，其余归 `avg_generation_ms`）；`avg_tool_calls` / `avg_tool_steps` 只有 agentic 组有值；`evidence_hit` 取决于模型 SELECT 了哪些列（只取 `name`/`price` 时答案可以全对但证据判 0）；默认单轮，但带 `history` 字段的样本（v7.3 起 3 条指代消解样本）会把对话历史原样传给 agentic 链路，报告用 `history_turns` 标出哪几条走了多轮；v7.4 起混合样本可用 `expected_sources` 声明「文档侧 + SQL 侧」，新读数 `both_sides_hit` 要求声明的每一侧都被引用到（扫全量引用、不套 `@3` 窗口），旧指标定义未动。v7.4 首轮 29 条实跑里 6 条双侧样本两侧取证全中（`both_sides_hit` 1.000，含上一轮只查文档侧、没回查 SQL 的 c03，那轮的既有数据按新口径回算是 0.833），`source_hit` 0.960 与上轮同为 m04 一条未命中，`mrr@3` 的 -0.020 全部来自 k05 一条排位后移，单条平均 82.6 秒，报告 `eval/reports/v7_4_two_sided_report.md`。修法 A（提示词硬规则）已做：STEP_PROMPT 规则 1 限定 sufficient 只看「已收集的证据」（不含历史），新增规则 10 禁止 mixed 查询在任一侧无证据时选 final；FINAL_PROMPT 新增规则 7 要求历史里的事实必须先查证据，历史段渲染加上「不能作为事实依据」标注。A/B 重复采样 5×修法 A vs 5×无修法 A：修法 A 将 c03 的 SQL 回查率从 20%（1/5）提升到 100%（5/5），修法 A 有效。修法 C（运行时硬门禁）仍为未勾项，作为修法 A 不奏效时的兜底。读数出处复核（2026-11-21）：上面「6 条双侧样本两侧取证全中」测于 `2e912379f`，早于修法 A，不能当修法 A 的依据；A/B 两组记录的提交号同为 `d43b314`，区分不了两组提示词，而被验证的三条硬规则又曾在 `978a63f` 里被删掉、本分支已恢复并复采到 5/5，经过见 `docs/ROADMAP.md` 的「修法 A 的读数出处与一次提示词回退」。评估运行会设 `RAG_DISABLE_AGENT_TRACE=1`，不把评估样本写进演示轨迹。

**v7.3 指代消解实跑**（2026-11-20，29 条 = 25 可回答 + 4 应拒答，只跑确定性指标）：3 条带 `history` 的指代样本全部还原成功——c01 按上一轮的「精排」答对、c02 把「它」还原成橙子并走 `sql_query`、c03 判 `mixed`；同 26 条子集与上一轮比 `answer_rate`/`source_hit`/`refusal_accuracy` 持平，`evidence_hit@3` -0.045 与 `citation_rate` -0.045 各自都能落到单条样本上（`@3` 窗口与生成截断），逐条机制见 `eval/README.md`，报告 `eval/reports/v7_3_coreference_report.md`。

**本机实跑结果**（2026-11-19，26 条 × `agent`/`full`，只跑确定性指标，`git_commit = a6e2bd8`，完整报告 `eval/reports/v7_agent_report.md`）：

| 指标 | agentic | `full`（v4 基线） |
|---|---:|---:|
| `answer_rate` | 0.909 | 0.318 |
| `source_hit` | 0.591 | 0.318 |
| `citation_rate` | 0.864 | 0.591 |
| `evidence_hit@3` | 0.409 | 0.318 |
| `mrr@3` | 0.348 | 0.295 |
| `refusal_accuracy` | 0.250 | 1.000 |
| 单条平均耗时 | 71.2 秒 | 17.0 秒 |

15 条需要业务库的样本里 agentic 有 9 条真的引用 `demo.products#sql_query` 给出正确数据（单价、价格排序、维 C 对比、比平均价再筛），`full` 组 26 条一条都没碰过业务数据；`knowledge` 类 7 条两组逐项持平，工具循环没把纯文档问答做坏。代价是单条耗时约 4.2 倍（多次决策调用的模型开销占 68.8 秒，工具执行只占 2.4 秒）。

量出来的两条真实短板：一是路由受问法影响——像「香蕉能不能放冰箱」这种读起来像生活常识的问题，决策模型判成 `knowledge` 去查文档（保存方法其实在 `products.description` 里），6 条样本因此没查到库；二是证据不足时 agentic 倾向作答而不是拒答，`refusal_accuracy` 从 1.000 掉到 0.250，其中 d06、r04 在没有知识库依据的情况下补了通用知识（苹果氧化、商业版定价），属于该拒答却作答。

**v7.2 对这两条的改法与复跑**：路由判据换成「回答所需的事实住在哪一侧」，并在装配 `sql_query` 时把白名单表的
列名与少量样例值有界采样进工具描述——库里到底存着什么，模型看不见就判不准，这不是问法的错；答与不答改由步进
决策声明充分性，判不足就不进生成、直接返回拒答哨兵并写明缺哪一项（`stop_reason` 新增 `insufficient_evidence`），
生成侧同时禁掉「用通用常识补全」和「资料没提到就等于不存在」两条偷懒路径。把判错的 9 条单独重跑
（`eval/reports/v7_2_routing_report.md`）：6 条业务库样本全部改判 `database`/`mixed` 并真的引用
`demo.products#sql_query`，3 条应拒答样本输出哨兵文本；同样子集均值 `source_hit` 0.000→1.000、
`citation_rate` 0.500→1.000。全量 26 条复跑（同生成模型、同确定性口径，`eval/reports/v7_2_agent_report.md`）确认这不是子集效应：

| 指标（`agent` 组） | v7.1 | v7.2 |
|---|---:|---:|
| `answer_rate` | 0.909 | 1.000 |
| `refusal_accuracy` | 0.250 | 1.000 |
| `source_hit` | 0.591 | 1.000 |
| `evidence_hit@3` | 0.409 | 0.864 |
| `mrr@3` | 0.348 | 0.811 |
| `citation_rate` | 0.864 | 0.955 |
| 单条平均耗时 | 71.2 秒 | 51.9 秒 |

回归检查是这一轮最要紧的一条：`knowledge` 类 7 条的 `source_hit` 与 `evidence_hit@3` 两轮都是 1.000，
并且这 7 条一次都没引用业务库，说明路由改动没有把纯文档问答带向查库。15 条需要业务库的样本里真的引用
`demo.products` 的从 9 条升到 15 条，意图分布里第一次出现了 `database`（10 条）。仍未解决的两条：
`mixed` 类的 `evidence_hit@3` 只到 0.400（还是 `@3` 窗口与 SELECT 列的口径吃掉），`none` 判据偏松——
r01 直接判 `none` 拒答，绕过了「先检索再据证据拒答」这条正路。

**Ragas 子集复核**（2026-11-19，26 条里的 6 条可回答样本 `k01`/`d01`/`d03`/`m03`/`s02`/`s04` ×
`agent`/`full`，judge `qwen3.8:27b-mlx`，`git_commit = 9147bd3`，总耗时 3965 秒，
完整报告 `eval/reports/v7_agent_ragas_report.md`）：

| Ragas 指标 | agentic | `full`（v4 基线） |
|---|---:|---:|
| `faithfulness` | 0.659 | 0.600 |
| `answer_relevancy` | 0.853 | 0.138 |
| `context_precision` | 0.492 | 0.306 |
| `context_recall` | 0.750 | 0.250 |

四项 agentic 都更高，差距最大的是 `answer_relevancy` 与 `context_recall`。但 `full` 组在这 6 条里
拒答了 5 条（子集 `answer_rate` 0.167），所以这两项的低分主要复述了「没作答」，不是同等条件下的
答案质量差值；能读的是 `faithfulness` 0.659 vs 0.600：两边都只在有据可依时作答，agentic 多带了
SQL 证据却没有更容易编。子集不是全量，样本量不足以当结论；judge 走本机 Ollama，跑动中 3 个评测
任务被 `RemoteProtocolError` 断连打断，对应 `m03`/`full` 的 `faithfulness`+`answer_relevancy` 与
`d01`/`agent` 的 `answer_relevancy` 记为 NaN，平均分只基于有效样本。

## MCP 暴露（v8）

把本机知识库当工具给外部客户端用：`scripts/serve_mcp.py` 是一个 stdio 的 MCP server，
Claude Desktop / Claude Code 之类的客户端可以直接调 `kb_search` 与 `ask`（默认只有这两个），
加 `--allow-sql` 才会多一个 `sql_query`。知识库这条链路与 CLI / HTTP 完全同一条
（查询改写 → 混合召回 → 精排 → 父块扩展 → 生成），只是换了入口，没有第二套检索。

前提与 `chat_cli.py` 相同：本机 Ollama 在跑（嵌入模型必须有），且已经 `ingest` 过知识库。

### 接入

```bash
# Claude Code：注册为本地 server（--no-rerank 启动更快、占用更低）
claude mcp add private-rag -- python3 /absolute/path/to/private-rag/scripts/serve_mcp.py --no-rerank
```

Claude Desktop 写进 `claude_desktop_config.json` 的 `mcpServers`：

```json
{
  "mcpServers": {
    "private-rag": {
      "command": "python3",
      "args": ["/absolute/path/to/private-rag/scripts/serve_mcp.py", "--no-rerank"]
    }
  }
}
```

脚本按自身位置解析项目根目录，所以客户端从任何工作目录启动都能找到 `storage/` 与 `data/`；
`--top-k` 改默认证据数（客户端不传 `limit` 时用它）。

要额外暴露业务库只读查询，在启动命令上显式开（默认关，见下面「三重门」）：

```bash
python3 scripts/serve_mcp.py --no-rerank --allow-sql --sources config/sources.yaml
```

启动前先自检一次工具定义——不打开知识库、不需要 Ollama，也不会进入协议会话：

```bash
python3 scripts/serve_mcp.py --list-tools
```

这个模式只列 `kb_search` 与 `ask`：`sql_query` 的定义要从 `sources.yaml` 连业务库取表结构，自检不打开它们。

### 可用工具与参数

| 工具 | 必填 | 可选 | 返回 |
|---|---|---|---|
| `kb_search` | `query` | `limit`(1–10)、`tags` / `tag`、`source_type`(file/web/sql) | 编号证据块 + 引用元数据，不打生成模型 |
| `ask` | `question` | 同上 | 带 `[n]` 标注的回答 + 引用列表 |
| `sql_query` | `sql` | 无 | 护栏改写后执行的 SQL + 行列结果 + 引用，需要 `--allow-sql` |

三个工具都同时给两种结果：`content[0].text` 是给人和模型读的文本（证据带 `[n]` 与来源行、末尾有引用列表），
`structuredContent` 是结构化载荷（`evidence` / `citations` / `retrieved` / `used_rerank` / `context_chars`，
`ask` 另有 `answer` / `no_evidence` / `meta.elapsed_ms`，`sql_query` 另有 `sql` / `row_count` / `columns` /
`truncated` / `warnings`）。引用编号与 `[n]` 的口径与 CLI、HTTP 完全一致：只有带引用的块参与编号，
`citations` 里的 `page` / `table` 能回溯到页或行。参数越界或写了不认识的参数会以 `isError` 返回一句中文原因
（不是协议错误），客户端能直接把原因读给用户。

### 与 HTTP API 的区别

| | `scripts/serve.py`（HTTP） | `scripts/serve_mcp.py`（MCP） |
|---|---|---|
| 调用方 | curl / 前端 / 评估脚本 | MCP 客户端（模型自己决定何时调用） |
| 会话 | `session_id` + 历史裁剪 | 无会话，单次调用是一轮独立问答 |
| 流式 | `/chat/stream` SSE | 无流式，也不发进度通知 |
| 数据面 | `/chat` 固定链路、`/chat/agent` agentic | 固定链路的检索与问答；`--allow-sql` 才有业务库查询 |
| SQL | `/chat/agent` 按 `sources.yaml` 授权开只读 SQL 工具 | 默认不暴露；`--allow-sql` 才暴露，入库/写库两边都不暴露 |

`sql_query` 要过三重门，缺一门就不出现在 `tools/list`，理由见
[docs/adr/0006](docs/adr/0006-mcp-sql-query-behind-three-gates.md)（它取代 0005 的「不暴露 SQL」一条）：

1. 启动命令显式带 `--allow-sql`——默认关，由启动这个进程的人决定这次能不能读业务库；
2. `sources.yaml` 里对应 sql 源带 `allow_runtime_query: true`——与 `/chat/agent` 同一个授权位；
3. `sql_guard` 的只读护栏照旧——单条 `SELECT`、表白名单、强制行数上限，返回的是护栏改写后执行的那条 SQL。

装配也共用 `private_rag/agent/runtime.py` 的 `build_tool_registry`，两个入口的授权语义不会各写一套再漂移。
开了 `--allow-sql` 但配置没授权、或业务库连不上，服务照样起、只暴露两个知识库工具，并在 stderr 说清原因
（不静默降级）。不带 `--allow-sql` 时外部客户端读到的仍是「已入库的内容」：`source_type: sql` 能选到的
只是入库时生成的行级文档，不是当场查业务库。

### 单进程限制与降级

Qdrant 本地磁盘模式同一路径只允许一个进程打开，所以 **MCP server 与 `scripts/serve.py` 不能同时跑**
（也不能与任何打开同一 `storage/` 的脚本同时跑）。打不开时进程直接退出，码 3，并在 stderr 给出可操作的下一步：

```
❌ 知识库打不开：知识库存储被其他进程占用：本地磁盘模式同一路径只允许一个进程打开。请先停掉 scripts/serve.py（或其他打开同一 storage/ 的进程），或用环境变量 RAG_STORAGE_DIR 把 MCP server 指向另一个目录。
```

调用时才遇到占用（例如客户端启动后 HTTP 服务才被拉起来）不会杀掉进程：那一次调用以 `isError` 返回同样的提示。
需要两个服务并存，就给其中一个设 `RAG_STORAGE_DIR` 指向另一份库，或改用 Qdrant server 模式。

其他边界：stdout 是协议通道，日志与启动提示一律走 stderr；没有鉴权，能启动这个进程的人就能读知识库，
开了 `--allow-sql` 还能读表白名单内的业务数据，与本机 HTTP 服务的无鉴权同级；真实语料正文照旧不进公开仓库
（[docs/adr/0002](docs/adr/0002-real-corpus-reports-out-of-public-repo.md)）。

协议实现是手写的最小 stdio JSON-RPC（只实现 `initialize` / `ping` / `tools/list` / `tools/call`），
因为官方 `mcp` SDK 要求 Python 3.10+ 而本仓库钉在 3.9，选型与代价见
[docs/adr/0004](docs/adr/0004-mcp-stdio-without-sdk.md)。

## 只读公网演示（Render）

完整问答依赖本机 Ollama（`bge-m3` 嵌入 + `qwen3.8:27b-mlx` 生成）与 `bge-reranker`，
云上既没有 Ollama，也没有能装下 27B 与 reranker 的小实例。因此公网演示走**只读**路线：
把 v6 入库链路的真实产物导出成快照，只用一个小服务暴露语料与检索，问答接口明确拒绝。

当前实例：<https://private-rag-readonly.onrender.com>（打开即是演示页，就绪状态 `/ready`，服务信息 `/info`）。

### 生成快照

快照由真实加载器产出，因此扫描件 OCR 文本、表格文档、页码与表格名与本地一致：

```bash
python3 scripts/init_demo_db.py                    # 生成 data/sql/demo.db
python3 scripts/build_demo_snapshot.py             # 导出 data/demo_snapshot.json
```

构建器带白名单校验：file 源只允许 `data/docs`，sql 源只允许 `data/sql/demo.db`，
出现 web 源直接拒绝构建，避免把个人文档打进公网部署。

### 本地跑只读服务

```bash
python3 scripts/serve_readonly.py --port 8799
```

不加载模型、不连 Qdrant，因此启动快、内存占用低；依赖清单单独放在
`requirements-readonly.txt`（只有 fastapi/uvicorn/pydantic/jieba/rank-bm25）。

### 部署

服务用 Render CLI 创建，Python 运行时 + 免费实例：

```bash
render services create --name private-rag-readonly --type web_service \
  --repo https://github.com/theo-w/private-rag --branch v6.1-readonly-render \
  --runtime python --plan free \
  --build-command "pip install -r requirements-readonly.txt" \
  --start-command "python scripts/serve_readonly.py" \
  --health-check-path /health
```

`scripts/serve_readonly.py` 监听 `0.0.0.0` 并读取 Render 注入的 `PORT`；
开 auto-deploy 后推送到部署分支即重新部署。

该服务的部署分支现已改为 `main`（上面 `--branch` 是创建时的取值）：

```bash
render services update srv-dbtfv3jbc2fs73f3cg40 --branch main
```

改分支不会自动重新部署，需要手动触发一次或等下一次推送到 main。

### 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/`、`/demo` | 演示页。根路径与 `/demo` 是同一页面，分享根链接即可直接看 |
| GET | `/info` | 服务信息 JSON（版本、模式、模型开关、可用路径） |
| GET | `/health` | 存活检查，200 |
| GET | `/ready` | 快照就绪状态，含文档数、扫描件数、表格数；模型三项显式为 `false` |
| GET | `/api/v1/documents` | 分页列出快照文档（标题/来源/页码/表格名/扫描件标记） |
| GET | `/api/v1/document?doc_id=` | 单文档全文。`doc_id` 含 `:` 与 `#`，只能走查询参数 |
| POST | `/api/v1/search` | BM25 关键词检索，返回与线上同字段的 `citations`（含 `page` / `table`） |
| POST | `/api/v1/chat`、`/chat/stream` | 恒返回 409 `generation_disabled` |

默认无鉴权。需要加锁时设 `RAG_DEMO_TOKEN`（或启动加 `--token`），此后 `/api/v1/*`
要求请求头 `X-Demo-Token` 与之相等，缺失或不匹配返回 401；`/health` 与 `/ready` 不受限。

### 只读部署不能代表完整效果

- **没有语义召回**：检索只有 BM25 关键词那一路，向量召回、RRF 融合、`bge-reranker`
  精排都不在其中，命中顺序与本地 `full` 策略不同。
- **没有父子块与查询改写**：`meta` 里 `used_parent=false`、`query_rewrite=off` 是真实状态。
- **索引粒度是页 / 表 / 行**，不是 v5 的子块；引用片段因此可能是整页文本的开头。
- **不能问答**：任何回答都需要模型，这个部署里没有模型。

要看完整链路仍在本地跑 `scripts/serve.py` 配合 Ollama。

## 多源配置

复制示例并按需修改：

```bash
cp config/sources.example.yaml config/sources.yaml
python3 scripts/ingest.py --config config/sources.yaml
```

`sources.yaml` 支持三类来源：

```yaml
sources:
  - type: file
    path: data/docs
    tags: [local]
    include: ["*.md", "*.pdf"]          # 可选
  - type: web
    urls: [https://example.com/a]
    timeout: 15
    tags: [web]
  - type: sql
    dsn: ${DEMO_DATABASE_URL:-sqlite:///data/sql/demo.db}
    query: SELECT id, name, description FROM products ORDER BY id
    id_column: id
    title_column: name
    content_columns: [name, description]
    updated_at_column: updated_at
    label: demo.products
    max_rows: 5000
    tags: [business]
    allow_runtime_query: true       # 可选：允许 v7 agentic 问答运行时只读查询本库（默认 false）
    runtime_tables: [products]      # 可选：可查表白名单；留空则由 query 的 FROM 子句推导
```

- `${VAR}` / `${VAR:-default}` 会在加载配置时替换，数据库密码不要写进仓库。
- 相对路径（文件路径、SQLite DSN）统一按项目根目录解析，脚本可在任意目录执行。
- 不传 `--config` 时默认只扫描 `data/docs`；`--path` 仍可指定单个文件或目录。
- `allow_runtime_query` 只管 v7 的运行时查询，与入库用的固定 `query` 是两件事：入库仍按 `query` 抽行建索引，运行时查询则由模型自己写 SQL，且只允许单条 `SELECT`、只查白名单表、自动限制行数。

### 数据库接入

- SQLite：无需额外依赖，`sqlite:///data/sql/demo.db` 直接可用。
- MySQL：`pip install pymysql`，DSN 形如 `mysql+pymysql://user:${MYSQL_PASSWORD}@host/db`。
- PostgreSQL：`pip install psycopg2-binary`，DSN 形如 `postgresql+psycopg2://user:${PG_PASSWORD}@host/db`。
- 查询请使用只读账号与只读 SQL；`max_rows` 防止误拉全表。日志与元数据中的 DSN 只保留驱动、主机和库名。

### 网页接入

- 使用 `requests` 抓取，`trafilatura` 抽取正文、标题与发布日期；单条 URL 失败只记录错误，不阻塞其他来源。
- 需要可访问的 URL；内网页面请确保运行环境有相应网络权限。

## 增量与更新语义

- **幂等**：`doc_id + content_hash` 未变化时跳过，不重复调用 embedding。
- **稳定 ID**：`uuid5(doc_id + content_hash + chunk_index)`，重复写入是覆盖而不是新增。
- **更新**：内容变化时先删除该 `doc_id` 的旧向量，再写入新版本；PDF 按页、SQL 按行、网页按 URL 独立更新。
- **prune**：`--prune` 删除本次没有再次出现的文件/页面/行。若某个 source 在加载阶段有错误（如网页暂时失败），跳过该 source 的 prune，避免误删。
- **词法索引**：每次入库结束重建 `storage/lexical/bm25.pkl`；问答时缺失会自动降级为纯向量检索并提示。

```bash
python3 scripts/ingest.py --config config/sources.yaml --prune     # 增量 + 清理消失来源
python3 scripts/ingest.py --recreate                               # 清空集合全量重建
```

## 引用输出

回答要求 LLM 在相关句子后标注 `[n]`；CLI 同时打印结构化引用（来源类型、标题、URI、页码/表名、片段、重排分数）：

```
📝 回答：
橙子富含维生素 C，适合日常补充维 C。[1]

📚 引用：
[1] 橙子（sql）
    demo.products#1｜表 products
    name: 橙子 category: 水果 description: 橙子富含维生素 C……
```

## 测试

单元测试不依赖 Ollama 与真实数据库：

```bash
python3 -m pytest tests -q
```

CI（`.github/workflows/ci.yml`）在每次 push 与 PR 上跑两个作业：

- `tests`：只装 `requirements-ci.txt`（**不含** `torch` / `transformers` / `sentence-transformers` /
  `FlagEmbedding` / `faiss-cpu`——单测不加载任何模型，精排在 `retrieval/rerank.py` 里是函数内懒加载；
  但 `pdfplumber` 与 `rapidocr-onnxruntime` 必须装，`test_ocr.py` 会断言这两个后端可导入），跑全量单测，
  再用 `evaluate.py --validate-only` 校验 v4 / v5 / v7 三份评估集的证据逐字校验与结构——标注漂移在打模型之前就会被拦住。
  `rapidocr-onnxruntime` 带的是非 headless 的 opencv，所以作业会先试 `import cv2`，缺 libGL 才补装 `libgl1`。
- `readonly`：只装 `requirements-readonly.txt`（Render 上实际安装的那份），验证只读演示的依赖清单真的够用，
  并断言导入 `private_rag.api.readonly` 后 `sys.modules` 里没有 `torch` / `FlagEmbedding` / `qdrant_client`。
  其中 `test_readonly_index_ranks_like_lexical_index` 被排除：它要 `import LexicalIndex`，
  而 `retrieval/lexical.py` 模块级导入 `qdrant_client`；该条在 `tests` 作业里照常执行，覆盖没有丢。

覆盖：配置解析与环境变量、SQL/网页连接器、jieba 分词与 BM25、RRF 融合、父子块切分与父块扩展、查询改写解析与回退、OCR 与表格提取、PDF 加载器（文本/扫描件/表格/降级）、入库幂等/更新/prune、引用构建与拒答、会话 TTL/容量/历史裁剪、共享引擎 readiness/降级/流式事件、API 同步/流式/错误格式/多会话隔离与并发、演示页路由、v4/v5 数据集校验/确定性指标/Ragas 映射/报告渲染。API 测试注入 fake engine，不加载 Ollama 与真实 Qdrant。v7 agentic 部分另覆盖只读 SQL 护栏（写操作、多语句、越权表必须被拒，CTE 与子查询不能误拒）、工具层证据与引用统一编号、检索-行动循环（意图路由、步数与字符预算、无效工具名、模型不可用降级、决策 JSON 的输出预算与解析回退）、轨迹落盘与 `--list`/单条回放 CLI、`/chat/agent` 与 `/traces` 接口；v7.2 另覆盖库内数据目录写进工具描述、采样词驱动关键词回退路由、证据充分性解析与拒答短路、缺口透传进最终提示词、路由提示词不含评估集实体；决策器与工具都是注入的替身，同样不依赖 Ollama 与 Qdrant。v8 的 MCP 部分另覆盖协议面（版本协商、通知不回应、
漏 `id` 的请求仍回应、坏行按协议错误回应不退出、`resources/*` 返回空列表）、三个工具的入参校验与只读声明、
编号证据与引用载荷与截断预算、`sql_query` 的写操作与越权表必须被拒（包的是 v7 那颗真护栏工具，指向临时
SQLite）、存储被占用时翻译成可操作的提示、`serve_stdio` 逐行按序回应与中文不转义，
以及入口 CLI（`--list-tools` 不打开知识库、`--allow-sql` 未授权时只暴露两个工具并在 stderr 说清原因、
打不开库时退出码 3 与 stderr 提示、打桩引擎跑通 stdio）；`build_tool_registry` 的装配测试保证 agentic 与
MCP 共用同一份授权判定。引擎与 `build_engine` 都是注入的替身，不打模型、不打开真实 Qdrant。

冒烟测试需要已入库并启动 Ollama：

```bash
python3 tests/smoke_test.py            # 知识库 + 混合检索
python3 tests/smoke_test.py --e2e      # 额外验证完整问答与拒答（较慢）
```

## 目录结构

```
private_rag/
├── config.py             统一常量与路径（项目根目录解析）
├── models.py             SourceDocument / Citation / IngestStats
├── sources.py            sources.yaml 校验、环境变量插值、source_key
├── store.py              Qdrant client、集合、按 doc_id 查询/删除/prune
├── ingestion/
│   ├── files.py          txt / md / pdf（pdfplumber + OCR + 表格）
│   ├── ocr.py            图片 OCR（rapidocr）与表格提取
│   ├── web.py            URL 抓取 + 正文抽取
│   ├── sql.py            SQLAlchemy 查询 -> 行级文档
│   ├── chunking.py       父块(2000/0)内切子块(700/100)，生成稳定父块 ID
│   └── pipeline.py       父子块切分、幂等写入、更新替换、prune、BM25 重建
├── retrieval/
│   ├── lexical.py        jieba + rank_bm25，落盘/加载/过滤（保存父块字段）
│   ├── hybrid.py         dense + BM25 + RRF，多查询融合与父块字段解析
│   ├── parent.py         父块去重、预算控制与子块回退
│   └── rerank.py         bge-reranker 精排
├── api/
│   ├── app.py            FastAPI 工厂、lifespan、请求 ID 中间件
│   ├── routes.py         health/ready/sessions/chat/stream 路由
│   ├── schemas.py        Pydantic 请求/响应模型
│   ├── errors.py         统一错误响应与异常处理器
│   └── static/demo.html  单文件 Web 演示页（/demo）
├── agent/
│   ├── planner.py        决策器接口与 LLM 实现（可注入替身，测试不打模型）
│   ├── prompts.py        路由 / 步进 / 最终回答提示词与关键词回退
│   ├── tools.py          kb_search 与只读 sql_query 工具、证据统一编号
│   ├── sql_guard.py      运行时 SQL 只读护栏（单条 SELECT、表白名单、强制 LIMIT）
│   ├── runtime.py        检索-行动循环；`build_tool_registry` 按 sources 配置装配可用工具（agentic 与 MCP 共用）
│   └── trace.py          决策轨迹落盘（JSONL）与回放渲染
├── mcp/
│   ├── server.py         手写最小 stdio JSON-RPC（initialize / ping / tools/list / tools/call）
│   ├── tools.py          kb_search / ask / sql_query 三个只读工具、入参校验与失败说明
│   └── evidence.py       证据与引用的文本渲染、JSON 载荷与 output schema
├── qa.py                 共享问答引擎（CLI/API 同一条链路，含检索 trace）
├── query_rewrite.py      多查询 / HyDE 改写与失败回退
├── sessions.py           内存多轮会话、TTL/容量/历史裁剪
├── generation.py         编号上下文、历史、[n] 引用与流式生成
├── evaluation.py         v4/v5/v7 数据集校验、策略与 agentic 对比、Ragas 与报告渲染
└── eval_env.py           评估库路径隔离（导入配置前生效）
config/settings.py        兼容入口（re-export private_rag.config）
config/sources.example.yaml / sources.demo.yaml / sources.eval.yaml / sources.eval.agent.yaml（agentic 运行时只读 SQL，不入库）
scripts/ingest.py         多源入库 CLI
scripts/chat_cli.py       混合检索问答 CLI
scripts/chat_agent.py     agentic 问答 CLI（意图路由 + 工具调用 + 决策轨迹）
scripts/replay_trace.py   决策轨迹回放 CLI
scripts/serve.py          HTTP 服务入口（单 worker）
scripts/serve_mcp.py      MCP server 入口（stdio，供 Claude Desktop / Claude Code 调用；`--allow-sql` 才暴露业务库查询）
scripts/init_demo_db.py   生成演示 SQLite 库
scripts/prepare_eval_kb.py 构建评估专用知识库
scripts/evaluate.py       策略评估与报告 CLI
eval/project_rag_qa.jsonl 40 条固定标注问答（v4）
eval/v5_paraphrase_qa.jsonl 18 条口语化/复杂问题（v5）
eval/v7_agent_qa.jsonl    29 条按工具分类的 agentic 问答（v7，含 3 条带对话历史的指代样本）
eval/reports/             v4/v5/v7 Markdown / JSON 报告
tests/                    pytest 单测 + smoke_test.py
docs/ROADMAP.md           v1–v8 路线与验收标准
data/docs/                本地文档
storage/                  向量库与 BM25 索引（已 gitignore，可由 ingest 重建）
storage/traces/           agentic 决策轨迹 agent.jsonl（已 gitignore，每请求追加一行）
requirements-eval.txt     可选评估依赖（Ragas）
legacy/                   v1 FAISS 单脚本版，仅作演进参考
```

## 设计说明

- **为什么混合检索**：向量擅长语义相似，BM25 擅长专有名词、型号、精确词命中；RRF 只看排名不看分数尺度，融合稳定，且两路各自可独立降级。
- **为什么 rerank**：召回阶段追求覆盖（top20），cross-encoder 再按问题-片段相关性精排，减少无关上下文对生成的影响。
- **为什么引用到页码/行**：文件按页、SQL 按行、网页按 URL 生成稳定 `doc_id`，既能增量更新，也能让引用可回溯。
- **为什么凭证不落元数据**：SQL `source_uri` 只使用 `label#pk`，DSN 只保留驱动/主机/库名；配置支持环境变量插值。
- **为什么 API 单 worker + 同步路由**：Qdrant 本地磁盘模式只允许一个进程；同步路由由 FastAPI 放入线程池，避免 CPU 密集的检索/精排/生成阻塞事件循环。扩展到多 worker 需要 Qdrant server 模式与外部会话存储。
- **可对比的实验开关**：`--dense-only`、`--no-rerank`、`--source-type`、`--tag` 可用于记录“改造前/后”的检索与回答差异；v4 用 Ragas 把差异量化。
- **为什么父块存在每个子块里**：避免额外 docstore 与父子一致性维护；代价是索引体积放大 2–3 倍，对本地规模可接受。父块超预算时回退子块，保证精排命中的片段一定进入上下文。

## v1 与当前版本的关系

`legacy/rag_llm_faiss.py` 保留了最初的 FAISS 单文件版本，用于复盘“从 FAISS 到 Qdrant + rerank + 引用”的演进。部分 macOS 环境可能因 libomp 冲突退出；主流程不受影响。路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)。
