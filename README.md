# private-rag

完全本地离线的中文 RAG：支持**文件 / 网页 / 数据库**多源知识库，使用**向量 + BM25 混合召回、RRF 融合、bge-reranker 精排**，回答带**引用溯源**。Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储。

## 架构

```
data/docs (txt/md/pdf) ─┐
网页 URL ───────────────┼─→ connectors → SourceDocument → 切分 ─→ Qdrant（向量）
SQL 数据库 ─────────────┘                                      └─→ storage/lexical/bm25.pkl

问题 ─→ dense top20 ─┐
       BM25 top20 ───┴─→ RRF 融合 top10 ─→ bge-reranker top3 ─→ Ollama 生成 [n] 引用
```

- 入库与问答彻底分离：`ingest.py` 是唯一写入入口，`chat_cli.py` 只读。
- 每个 chunk 都带 `doc_id / source_type / source_uri / title / page / table / tags / content_hash`，可直接生成引用。
- 阶段路线与验收标准见 [docs/ROADMAP.md](docs/ROADMAP.md)。

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

覆盖：配置解析与环境变量、SQL/网页连接器、jieba 分词与 BM25、RRF 融合、入库幂等/更新/prune、引用构建与拒答。

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
└── generation.py         编号上下文、[n] 引用 prompt 与回答
config/settings.py        兼容入口（re-export private_rag.config）
config/sources.example.yaml / sources.demo.yaml
scripts/ingest.py         多源入库 CLI
scripts/chat_cli.py       混合检索问答 CLI
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
- **可对比的实验开关**：`--dense-only`、`--no-rerank`、`--source-type`、`--tag` 可用于记录“改造前/后”的检索与回答差异；v4 会用 Ragas 把差异量化。

## v1 与当前版本的关系

`legacy/rag_llm_faiss.py` 保留了最初的 FAISS 单文件版本，用于复盘“从 FAISS 到 Qdrant + rerank + 引用”的演进。部分 macOS 环境可能因 libomp 冲突退出；主流程不受影响。路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)。
