# private-rag

完全本地离线的中文 RAG：支持**文件 / 网页 / 数据库**多源知识库，使用**向量 + BM25 混合召回、RRF 融合、bge-reranker 精排**，回答带**引用溯源**。Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储。

## 架构

```
data/docs (txt/md/pdf) ─┐
网页 URL ───────────────┼─→ connectors → SourceDocument → 切分 ─→ Qdrant（向量）
SQL 数据库 ─────────────┘                                      └─→ storage/lexical/bm25.pkl

问题 ─→ RagEngine ─→ dense top20 ─┐
                     BM25 top20 ───┴─→ RRF 融合 top10 ─→ bge-reranker top3 ─→ Ollama 生成 [n] 引用
                                   ↑
                     CLI（chat_cli.py）/ HTTP API（serve.py，同步 + SSE 流式）
```

- 入库与问答彻底分离：`ingest.py` 是唯一写入入口，`chat_cli.py` 与 `serve.py` 只读。
- CLI 与 HTTP API 共用 `private_rag/qa.py` 的 `RagEngine`，检索、精排、生成与引用链路完全一致。
- 每个 chunk 都带 `doc_id / source_type / source_uri / title / page / table / tags / content_hash`，可直接生成引用。
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
python scripts/init_demo_db.py

# 2. 按演示配置全量入库（文件 data/docs + 数据库 products 表）
python scripts/ingest.py --config config/sources.demo.yaml --recreate

# 3. 按来源过滤提问，回答会带编号引用
python scripts/chat_cli.py "橙子有什么营养？" --source-type sql
python scripts/chat_cli.py "香蕉要放冰箱吗？" --source-type file
```

常用参数：

```bash
python scripts/chat_cli.py "维生素 C 相关的记录有哪些？" --tag business
python scripts/chat_cli.py "苹果适合冷藏吗？" --dense-only        # 只走向量，用于对比
python scripts/chat_cli.py "苹果适合冷藏吗？" --no-rerank         # 跳过精排，便于观察召回
```

交互模式（`exit` 退出）：

```bash
python scripts/chat_cli.py --source-type sql
```

## HTTP API（v3）

启动服务（另开终端）：

```bash
python scripts/serve.py                  # 默认 127.0.0.1:8000
python scripts/serve.py --port 8766 --no-rerank
python scripts/serve.py --reload         # 开发模式，代码变更自动重启
```

- Swagger 文档：<http://127.0.0.1:8000/docs>
- `GET /health`：存活检查；`GET /ready`：检查 Qdrant 集合、BM25 与 reranker，未就绪返回 503。
- 默认只监听本机、无鉴权，请勿直接暴露公网。
- `--no-rerank` 等价于 `RAG_DISABLE_RERANK=1`；readiness 会显示“重排模型未加载”，问答仍按融合结果降级运行。

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
- `top_k`：1–10，默认 3，表示最终送入 LLM 的片段数。
- `source_type`：`file` / `web` / `sql`；`tags`：最多 10 个，命中任意一个即可。
- `use_lexical` / `use_rerank`：是否启用 BM25 与 reranker，默认均为 `true`。

同步响应包含 `session_id / answer / citations / meta / request_id`；`meta` 中有 `retrieved / used_lexical / used_rerank / history_turns / elapsed_ms`。

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

## 多源配置

复制示例并按需修改：

```bash
cp config/sources.example.yaml config/sources.yaml
python scripts/ingest.py --config config/sources.yaml
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
```

- `${VAR}` / `${VAR:-default}` 会在加载配置时替换，数据库密码不要写进仓库。
- 相对路径（文件路径、SQLite DSN）统一按项目根目录解析，脚本可在任意目录执行。
- 不传 `--config` 时默认只扫描 `data/docs`；`--path` 仍可指定单个文件或目录。

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
python scripts/ingest.py --config config/sources.yaml --prune     # 增量 + 清理消失来源
python scripts/ingest.py --recreate                               # 清空集合全量重建
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
python -m pytest tests -q
```

覆盖：配置解析与环境变量、SQL/网页连接器、jieba 分词与 BM25、RRF 融合、入库幂等/更新/prune、引用构建与拒答、会话 TTL/容量/历史裁剪、共享引擎 readiness/降级/流式事件、API 同步/流式/错误格式/多会话隔离与并发。API 测试注入 fake engine，不加载 Ollama 与真实 Qdrant。

冒烟测试需要已入库并启动 Ollama：

```bash
python tests/smoke_test.py            # 知识库 + 混合检索
python tests/smoke_test.py --e2e      # 额外验证完整问答与拒答（较慢）
```

## 目录结构

```
private_rag/
├── config.py             统一常量与路径（项目根目录解析）
├── models.py             SourceDocument / Citation / IngestStats
├── sources.py            sources.yaml 校验、环境变量插值、source_key
├── store.py              Qdrant client、集合、按 doc_id 查询/删除/prune
├── ingestion/
│   ├── files.py          txt / md / pdf（按页）
│   ├── web.py            URL 抓取 + 正文抽取
│   ├── sql.py            SQLAlchemy 查询 -> 行级文档
│   └── pipeline.py       切分、幂等写入、更新替换、prune、BM25 重建
├── retrieval/
│   ├── lexical.py        jieba + rank_bm25，落盘/加载/过滤
│   ├── hybrid.py         dense + BM25 + RRF
│   └── rerank.py         bge-reranker 精排
├── api/
│   ├── app.py            FastAPI 工厂、lifespan、请求 ID 中间件
│   ├── routes.py         health/ready/sessions/chat/stream 路由
│   ├── schemas.py        Pydantic 请求/响应模型
│   └── errors.py         统一错误响应与异常处理器
├── qa.py                 共享问答引擎（CLI/API 同一条链路）
├── sessions.py           内存多轮会话、TTL/容量/历史裁剪
└── generation.py         编号上下文、历史、[n] 引用与流式生成
config/settings.py        兼容入口（re-export private_rag.config）
config/sources.example.yaml / sources.demo.yaml
scripts/ingest.py         多源入库 CLI
scripts/chat_cli.py       混合检索问答 CLI
scripts/serve.py          HTTP 服务入口（单 worker）
scripts/init_demo_db.py   生成演示 SQLite 库
tests/                    pytest 单测 + smoke_test.py
docs/ROADMAP.md           v1–v7 路线与验收标准
data/docs/                本地文档
storage/                  向量库与 BM25 索引（已 gitignore，可由 ingest 重建）
legacy/                   v1 FAISS 单脚本版，仅作演进参考
```

## 设计说明

- **为什么混合检索**：向量擅长语义相似，BM25 擅长专有名词、型号、精确词命中；RRF 只看排名不看分数尺度，融合稳定，且两路各自可独立降级。
- **为什么 rerank**：召回阶段追求覆盖（top20），cross-encoder 再按问题-片段相关性精排，减少无关上下文对生成的影响。
- **为什么引用到页码/行**：文件按页、SQL 按行、网页按 URL 生成稳定 `doc_id`，既能增量更新，也能让引用可回溯。
- **为什么凭证不落元数据**：SQL `source_uri` 只使用 `label#pk`，DSN 只保留驱动/主机/库名；配置支持环境变量插值。
- **为什么 API 单 worker + 同步路由**：Qdrant 本地磁盘模式只允许一个进程；同步路由由 FastAPI 放入线程池，避免 CPU 密集的检索/精排/生成阻塞事件循环。扩展到多 worker 需要 Qdrant server 模式与外部会话存储。
- **可对比的实验开关**：`--dense-only`、`--no-rerank`、`--source-type`、`--tag` 可用于记录“改造前/后”的检索与回答差异；v4 会用 Ragas 把差异量化。

## v1 与当前版本的关系

`legacy/rag_llm_faiss.py` 保留了最初的 FAISS 单文件版本，用于复盘“从 FAISS 到 Qdrant + rerank + 引用”的演进。部分 macOS 环境可能因 libomp 冲突退出；主流程不受影响。路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)。
