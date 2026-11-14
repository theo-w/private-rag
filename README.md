# private-rag

完全本地离线的中文 RAG 问答 Demo：Ollama 负责 embedding 与生成，Qdrant 本地磁盘模式负责向量存储，bge-reranker 负责精排。

## 架构

```
data/docs/*.txt|*.md|*.pdf
        │  scripts/ingest.py（切分 + 向量化 + 幂等写入）
        ▼
storage/qdrant_storage（本地 Qdrant 集合 rag_knowledge_base）
        │  scripts/chat_cli.py（只读检索）
        ▼
向量召回 top10 → bge-reranker-v2-m3 重排 top3 → Ollama LLM 生成
```

职责分离：`ingest.py` 是唯一的写入入口，`chat_cli.py` 只读，不再每次启动重建向量库。

## 环境要求

- Python 3.9+
- [Ollama](https://ollama.com/) 已启动，且已拉取模型：

```bash
ollama pull bge-m3
ollama pull qwen3.8:27b-mlx
```

- 首次运行会自动从 HuggingFace 下载 `BAAI/bge-reranker-v2-m3` 重排模型。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 若希望复用本机已安装的依赖（避免重装 torch 等大包），可改用：
> `python3 -m venv --system-site-packages .venv`，然后直接激活使用。

## 使用

入库（默认扫描 `data/docs`，重复执行会跳过未变化的文件）：

```bash
python scripts/ingest.py
```

新增/更新文档后重复执行即可；文件内容变化时会先删除该文件的旧向量再写入新向量。需要全量重建时：

```bash
python scripts/ingest.py --recreate
```

指定单个文件或目录：

```bash
python scripts/ingest.py --path data/docs/test.txt
```

问答（交互模式，`exit` 退出）：

```bash
python scripts/chat_cli.py
```

单次提问：

```bash
python scripts/chat_cli.py "苹果适合放冰箱吗？"
```

## 冒烟测试

只验证检索链路（快，不调用大模型）：

```bash
python tests/smoke_test.py
```

同时验证完整问答与拒答行为（会加载重排模型并调用 LLM，较慢）：

```bash
python tests/smoke_test.py --e2e
```

## 目录结构

```
config/settings.py     统一配置（路径、切分参数、模型名、检索参数）
scripts/ingest.py      文档入库
scripts/chat_cli.py    检索问答
tests/smoke_test.py    冒烟测试
data/docs/             知识库文档
storage/               本地向量库（已 gitignore，可由 ingest 重建）
legacy/                历史版本（FAISS 单脚本版，仅作参考；部分 macOS 环境会因 libomp 冲突退出）
```

## 设计说明

- **幂等入库**：以文件 sha256 判断是否变化，chunk 使用确定性 UUID，重复执行不会产生重复向量。
- **更新语义**：同一路径的文件内容变化时，先按 `metadata.source` 删除旧向量，再写入新版本。
- **路径统一**：`settings.py` 中的路径都基于项目根目录，脚本可在任意目录运行。
- **引用来源**：向量 payload 中记录相对项目根的 `source` 与 `file_name`，答问时直接展示。
