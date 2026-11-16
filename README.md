# private-rag

完全本地离线的中文 RAG：支持**文件 / 网页 / 数据库**多源知识库，使用**向量 + BM25 混合召回、RRF 融合、bge-reranker 精排**，回答带**引用溯源**。v5 增加 **Parent-Document 父子块上下文**与**查询改写（多查询 / HyDE）**：子块负责召回，父块负责给模型完整上下文。Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储。

## 架构

```
data/docs (txt/md/pdf) ─┐
网页 URL ───────────────┼─→ connectors → SourceDocument → 父块(2000) → 子块(700/100) ─→ Qdrant（子块向量 + 父块原文）
SQL 数据库 ─────────────┘                                                          └─→ storage/lexical/bm25.pkl

问题 ─→ 查询改写（off / multi / HyDE，可选）
     ─→ 每个查询各跑 dense top20 + BM25 top20 → RRF 融合 top10（跨查询再融合）
     ─→ bge-reranker 用原问题精排 top3 → 父块去重/扩展（预算 6000 字符）→ Ollama 生成 [n] 引用
                                   ↑
                     CLI（chat_cli.py）/ HTTP API（serve.py，同步 + SSE 流式）/ Web 演示页（/demo）
```

- 入库与问答彻底分离：`ingest.py` 是唯一写入入口，`chat_cli.py` 与 `serve.py` 只读。
- CLI 与 HTTP API 共用 `private_rag/qa.py` 的 `RagEngine`，检索、精排、生成与引用链路完全一致。
- 每个子块都带 `doc_id / source_type / source_uri / title / page / table / tags / content_hash`，另带父块 `parent_id / parent_index` 与父块原文，可直接生成引用；旧库没有父块字段时自动回退子块。
- 父块默认开启、查询改写默认关闭：日常问答默认不额外调用改写 LLM；需要时可通过 API 或演示页单独打开（CLI 使用默认值）。
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

## v4 自动评估（Ragas）

v4 用固定评估集把“检索策略有没有用”量化。评估知识库只使用项目自身文档（`README.md`、`docs/ROADMAP.md`、`docs/beginner-guide.md`），与 `data/docs` 演示库完全隔离：

| 评估资源 | 路径 |
|---|---|
| Qdrant 集合 | `rag_eval_kb` |
| Qdrant 存储 | `storage/eval/qdrant_storage` |
| BM25 索引 | `storage/eval/lexical/bm25.pkl` |
| Ragas 判分缓存 | `storage/eval_cache` |

这些路径通过 `RAG_COLLECTION_NAME`、`RAG_STORAGE_DIR`、`RAG_LEXICAL_INDEX_PATH` 环境变量覆盖，由 `private_rag/eval_env.py` 在导入配置前设置，因此**不会与正在运行的演示服务争抢 Qdrant 本地锁**。

评估集固定 40 条：36 条可回答 + 4 条应拒答，覆盖架构、入库、检索、API、边界与拒答 6 类；标注与字段说明见 [eval/README.md](eval/README.md)。

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
- 父子块切分对 OCR 与表格透明：正文与表格各自走相同的父子块流程。

### 依赖安装

OCR 功能为**可选依赖**，不安装时 PDF 仍按现有方式处理（文本提取），不阻塞服务启动：

```bash
# 安装 OCR 依赖（可选）
pip install pdfplumber rapidocr-onnxruntime
```

- `pdfplumber 0.11.7`：PDF 渲染 + 表格提取。
- `rapidocr-onnxruntime 1.4.4`：中英 OCR，ONNX 模型内置（约 100MB），无需联网下载。

### 降级行为

| 场景 | 行为 |
|---|---|
| 未安装 `pdfplumber` | 回退到 `PyPDFLoader`（文本提取，无 OCR/表格） |
| 未安装 `rapidocr-onnxruntime` | 扫描件页面跳过 OCR，文本为空则记录 warning |
| OCR 推理失败 | 该页跳过，记录 warning，不影响其他文件 |

降级行为在入库日志中输出 warning，不阻塞其他文件。

### 效果

- 扫描 PDF 入库后，OCR 文本进入向量与 BM25 索引，问答时可被召回。
- 表格作为独立文档入库，引用中显示 `表 {表名}_table_{N}` 与页码。
- 文本型 PDF 继续正常工作，不退回 v5 功能。

### 已知限制

- **结构化表格只对文本型 PDF 有效**：`extract_tables` 依赖页面里的文字对象与绘制的边框线。扫描件页面是一整张位图，两者都不存在，因此扫描件只能拿到整页 OCR 文本，提不出单元格（版面表格识别属于后续版本范围）。
- **表格需要有边框线**：只是靠文字对齐排版的表格，`extract_tables` 默认策略识别不出来（实测返回 0 张）；这类内容仍会以整页正文的形式被检索到。
- **OCR 精度**：清晰印刷体效果好，手写体、低分辨率、倾斜页面识别率下降；200 dpi 渲染可缓解低分辨率这一类。

## 只读公网演示（Render）

完整问答依赖本机 Ollama（`bge-m3` 嵌入 + `qwen3.8:27b-mlx` 生成）与 `bge-reranker`，
云上既没有 Ollama，也没有能装下 27B 与 reranker 的小实例。因此公网演示走**只读**路线：
把 v6 入库链路的真实产物导出成快照，只用一个小服务暴露语料与检索，问答接口明确拒绝。

当前实例：<https://private-rag-readonly.onrender.com>（打开即是演示页，就绪状态 `/ready`，服务信息 `/info`）。

### 生成快照

快照由真实加载器产出，因此扫描件 OCR 文本、表格文档、页码与表格名与本地一致：

```bash
python scripts/init_demo_db.py                    # 生成 data/sql/demo.db
python scripts/build_demo_snapshot.py             # 导出 data/demo_snapshot.json
```

构建器带白名单校验：file 源只允许 `data/docs`，sql 源只允许 `data/sql/demo.db`，
出现 web 源直接拒绝构建，避免把个人文档打进公网部署。

### 本地跑只读服务

```bash
python scripts/serve_readonly.py --port 8799
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
开 auto-deploy 后推送到该分支即重新部署。

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

覆盖：配置解析与环境变量、SQL/网页连接器、jieba 分词与 BM25、RRF 融合、父子块切分与父块扩展、查询改写解析与回退、OCR 与表格提取、PDF 加载器（文本/扫描件/表格/降级）、入库幂等/更新/prune、引用构建与拒答、会话 TTL/容量/历史裁剪、共享引擎 readiness/降级/流式事件、API 同步/流式/错误格式/多会话隔离与并发、演示页路由、v4/v5 数据集校验/确定性指标/Ragas 映射/报告渲染。API 测试注入 fake engine，不加载 Ollama 与真实 Qdrant。

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
├── qa.py                 共享问答引擎（CLI/API 同一条链路，含检索 trace）
├── query_rewrite.py      多查询 / HyDE 改写与失败回退
├── sessions.py           内存多轮会话、TTL/容量/历史裁剪
├── generation.py         编号上下文、历史、[n] 引用与流式生成
├── evaluation.py         v4/v5 数据集校验、策略对比、Ragas 与报告渲染
└── eval_env.py           评估库路径隔离（导入配置前生效）
config/settings.py        兼容入口（re-export private_rag.config）
config/sources.example.yaml / sources.demo.yaml / sources.eval.yaml
scripts/ingest.py         多源入库 CLI
scripts/chat_cli.py       混合检索问答 CLI
scripts/serve.py          HTTP 服务入口（单 worker）
scripts/init_demo_db.py   生成演示 SQLite 库
scripts/prepare_eval_kb.py 构建评估专用知识库
scripts/evaluate.py       策略评估与报告 CLI
eval/project_rag_qa.jsonl 40 条固定标注问答（v4）
eval/v5_paraphrase_qa.jsonl 18 条口语化/复杂问题（v5）
eval/reports/             v4/v5 Markdown / JSON 报告
tests/                    pytest 单测 + smoke_test.py
docs/ROADMAP.md           v1–v7 路线与验收标准
data/docs/                本地文档
storage/                  向量库与 BM25 索引（已 gitignore，可由 ingest 重建）
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
