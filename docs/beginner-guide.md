# private-rag 项目流程导读（小白版）

> 面向第一次接触 RAG 的读者：先讲清楚“这东西在干嘛、数据怎么流动”，再给出按真实实现删减后的示意代码。完整实现请以文中链接的源码为准。

## 一句话总结

这个项目像一位“只回答你资料里内容的私人图书管理员”：先把你的文件、网页、数据库整理成一张张小卡片，给每张卡片编好“语义地址”和“关键词目录”；你提问时，它先用两套目录找出候选卡片，再精读筛出最相关的 3 张，最后让本地大模型只照着这 3 张卡片回答，并告诉你答案来自哪一页、哪个网址或哪条记录。

这就是 RAG（检索增强生成），可以理解成“开卷考试”：模型不许凭记忆答题，必须先翻你的资料。

## 整体流程

整个项目分成两个阶段，**建库**和**问答**是分开的。

### 阶段一：建库（离线，跑一次）

```text
config/sources.yaml
   ├─ 本地文件（txt / md / pdf）
   ├─ 网页 URL
   └─ SQL 查询
          ↓ 三种来源都变成统一的“文档”
   正文 + 身份信息（标题、来源、标签、页码/表名）
          ↓ 剪成小卡片
   每张约 700 字符，相邻重叠 100 字符
          ↓ 两种索引，各干各的活
   ┌──────────────────────┬──────────────────────┐
   │ bge-m3 把文字变成向量 │ jieba 分词 + BM25     │
   │ 存进 Qdrant          │ 存成关键词索引文件     │
   │ 负责“意思相近”       │ 负责“关键词命中”       │
   └──────────────────────┴──────────────────────┘
```

为什么要剪成小卡片？因为一整本书塞不进模型，而且检索时只需要最相关的几段。重叠 100 字符是为了避免一句话刚好被切在两张卡片的边界上。

为什么要两种索引？因为一种会“漏”：

- 向量检索懂同义表达，比如“冷藏”能匹配“放冰箱”，但有时会漏掉专有名词、编号；
- BM25 抓关键词很准，但不懂“番茄”和“西红柿”是一回事；
- 两个一起用，互相补位。

### 阶段二：问答（在线，每次提问）

```text
用户问题 + 这个会话最近几轮对话
        ↓
   ┌────┴─────┐
向量检索 20 条   关键词检索 20 条
   └────┬─────┘
     RRF 合并 → 留 10 条
        ↓
  reranker 逐条精读 → 只留 3 条
        ↓
  拼成带 [1][2][3] 编号的提示词
        ↓
  本地 Ollama 模型回答
        ↓
  答案 + 引用列表
        ↓
  写回会话历史 → 返回 JSON 或边生成边推送（SSE）
```

这里有三个关键角色：

- **RRF 合并**：像两个排行榜按名次折算积分。某张卡片在向量榜排第 1，加分；在关键词榜也排第 1，再加一次分。两边都靠前的卡片总分最高。
- **reranker（重排模型）**：像面试官逐份精读简历。它把“问题 + 每张卡片”放在一起逐条比较，选出真正最相关的 3 条。
- **大模型**：像只拿到这 3 张卡片的答题学生。提示词明确要求“只能根据卡片回答，没有就拒答”，所以资料里没有的内容不会被编出来。

## 数据流向

用一条线看完整条数据链路：

```text
文件 / 网页 / 数据库
  → 统一文档（正文 + 元数据：标题、来源、页码、标签……）
  → 文本小卡片
  → Qdrant（向量 + 卡片原文 + 元数据）
  → 用户提问
  → 向量召回 + 关键词召回
  → RRF 合并
  → reranker 精排
  → 带编号的上下文 + 最近对话历史
  → 本地大模型
  → 答案 + 引用
  → 会话内存（仅用于下一轮理解指代）
  → HTTP JSON 或 SSE 流式返回
```

值得记住的一点：**引用不是模型编的**。每张卡片入库时就带着 `source_type / title / source_uri / page / table` 这些身份信息；返回答案时，系统直接把这些元数据整理成引用列表，模型只负责在答案里标 `[1]`、`[2]`。

## 关键函数

| 函数 / 类 | 接收什么 | 做什么 | 返回什么 |
|---|---|---|---|
| `run_ingest`（[pipeline.py](../private_rag/ingestion/pipeline.py)） | 数据源配置 | 逐个来源读取、切块、算向量、写 Qdrant，最后重建 BM25 | 入库统计（新增/更新/跳过/失败） |
| `load_file_documents` / `load_web_documents` / `load_sql_documents` | 一个文件、一批 URL、一条 SQL 查询 | 把三种不同来源都转成统一文档；PDF 按页、SQL 按行 | 文档列表 + 错误列表 |
| `HybridRetriever.retrieve`（[hybrid.py](../private_rag/retrieval/hybrid.py)） | 用户问题、来源/标签过滤 | 同时跑向量检索和 BM25，再用 RRF 合并 | 融合后的候选卡片 |
| `Reranker.rerank`（[rerank.py](../private_rag/retrieval/rerank.py)） | 问题 + 候选卡片 | 逐条精读打分，按相关性排序 | 最相关的 N 条（默认 3） |
| `build_prompt` / `generate_answer` / `stream_answer`（[generation.py](../private_rag/generation.py)） | 问题、卡片、历史 | 拼出严格提示词，调用本地 LLM；没有候选时直接拒答 | 答案文本 + 引用列表（流式则逐段文字） |
| `RagEngine.answer` / `stream`（[qa.py](../private_rag/qa.py)） | 问题、检索选项、历史 | 串起“检索 → 精排 → 生成”，CLI 和 API 共用 | 同步结果或流式事件 |
| `Session.history_for_prompt` / `add_exchange`（[sessions.py](../private_rag/sessions.py)） | 会话里已有的问答 | 取最近几轮、按字符预算裁剪；或把本轮问答追加进去 | 可直接放进提示词的历史 / 无 |
| `chat` / `chat_stream`（[routes.py](../private_rag/api/routes.py)） | HTTP 请求 | 检查知识库、找会话、加会话锁、调用引擎、写回历史 | JSON 响应 / SSE 事件流 |

## 简易代码

下面这些是**按真实逻辑重写的示意版**，省略了错误处理、分页、类型标注和锁。真实代码在链接的文件里。

### 1. 全景：十二条看懂核心

```python
# ---------- 建库（离线，跑一次） ----------
docs = load_all_sources("config/sources.yaml")   # 文件/网页/数据库 → 统一文档
chunks = split(docs, size=700, overlap=100)      # 长文剪成小卡片
qdrant.save(chunks, embed(chunks))               # bge-m3：文字 → 语义坐标
bm25 = build_bm25(chunks)                        # jieba 分词 → 关键词索引

# ---------- 问答（在线，每次提问） ----------
history = session.history_for_prompt()           # 最近几轮，只帮助理解“它”指谁
dense = qdrant.search(embed(question), top=20)   # 按“意思”找 20 条
lexical = bm25.search(question, top=20)          # 按“关键词”找 20 条
candidates = rrf(dense, lexical)[:10]            # 两个榜单合并，留 10 条
top3 = reranker.rerank(question, candidates, top=3)  # 逐条精读，只留 3 条
answer = llm.invoke(build_prompt(question, top3, history))  # 只准看这 3 条作答
return answer, citations(top3)                   # 答案 + 引用
```

### 2. 入库：怎么做到重复跑也不重复存

```python
# 简化自 private_rag/ingestion/pipeline.py
def ingest_one(document):
    # 同一个 doc_id 已入库，且内容哈希没变 → 直接跳过
    if find_doc_hash(document.doc_id) == document.content_hash:
        return "skipped"

    chunks = split_text(document.text, size=700, overlap=100)

    # 先算新向量，再删旧数据：就算中途失败，旧知识也还在
    vectors = embed(chunks)              # bge-m3：每段文字 → 一串数字
    delete_old_vectors(document.doc_id)

    # 卡片原文、向量、元数据一起写进 Qdrant
    save_chunks(
        ids=deterministic_ids(document.doc_id, document.content_hash),
        texts=chunks,
        vectors=vectors,
        metadata={
            "doc_id": document.doc_id,
            "source_type": document.source_type,   # file / web / sql
            "source_uri": document.source_uri,     # 文件路径 / URL / 库名#主键
            "title": document.title,
            "tags": document.tags,
            "page": document.page,                 # PDF 页码，可选
            "table": document.table,               # SQL 表名，可选
        },
    )

# 全部入库结束后，从 Qdrant 读出所有卡片，重建一份 BM25 索引
rebuild_bm25_from_all_chunks()
```

三种来源在入库前都会被“翻译”成同一种文档：

- 文件：txt/md 整个文件一份；PDF 一页一份，保留页码。
- 网页：抓 HTML，用 trafilatura 抽正文和标题/日期。
- 数据库：一条 SQL 查询，每一行变成一份文档；连接串只保留主机和库名，用户名密码不会写进日志或元数据。

### 3. 检索：两路召回 + RRF 合并

```python
# 简化自 private_rag/retrieval/hybrid.py
def retrieve(question, source_type=None, tags=None):
    # 路线 A：按语义找
    dense = qdrant.search(
        embed(question),
        top=20,
        filter=build_filter(source_type, tags),
    )

    # 路线 B：按关键词找；BM25 不存在时自动退化成纯向量
    lexical = bm25.search(question, top=20) if bm25 else []

    # RRF：某条卡片在某个榜单排第 rank，就加 1 / (60 + rank)
    merged = {}
    for rank, card in enumerate(dense, start=1):
        merged[card.id] = merged.get(card.id, card)
        merged[card.id].score += 1 / (60 + rank)

    for rank, card in enumerate(lexical, start=1):
        merged[card.id] = merged.get(card.id, card)
        merged[card.id].score += 1 / (60 + rank)

    return sorted(merged.values(), key=lambda c: c.score, reverse=True)[:10]
```

直觉：名次越靠前，加分越多；两路都出现，就加两次分。所以“语义像”和“关键词对得上”的卡片会浮到最上面。

### 4. 生成：让模型只读这 3 张卡片

```python
# 简化自 private_rag/generation.py
REFUSAL = "【资料中没有相关信息】"

def answer(question, chunks, history):
    # 一条候选都没有 → 直接拒答，不浪费一次大模型调用
    if not chunks:
        return REFUSAL, []

    # 卡片按 [1][2][3] 编号，并带上来源，方便模型标注依据
    context = "\n\n".join(
        f"[{i}] (来源: {c.source_type} | {c.title} | {c.source_uri})\n{c.text}"
        for i, c in enumerate(chunks, start=1)
    )

    # 历史只用来理解“它”“这个”指什么，明确不能当事实依据
    history_text = render_history(history)
    prompt = f"""
你是知识库问答助手，严格只根据下面带编号的上下文回答。
回答时用 [编号] 标注依据；没有答案就回复{REFUSAL}，不要使用你自己的知识。
{history_text}
上下文：
{context}

用户问题：
{question}
"""
    text = llm.invoke(prompt)
    return text, build_citations(chunks)   # 引用直接来自卡片的元数据


def stream_answer(question, chunks, history):
    """同样是上面那套提示词，只是改成一段一段往外吐字。"""
    if not chunks:
        yield REFUSAL
        return
    for piece in llm.stream(build_prompt(question, chunks, history)):
        yield piece.content
```

### 5. API 与会话：同一会话排队，不同会话并行

```python
# 简化自 private_rag/api/routes.py
@router.post("/chat")
def chat(request):
    require_ready()                       # 知识库不存在/为空 → 503

    session = resolve_session(request.session_id)
    # 没传 session_id → 自动新建；传了未知/过期 id → 404

    with session.lock:                    # 同一会话串行，防止历史打架
        history = session.history_for_prompt()
        result = engine.answer(request.question, history=history)
        session.add_exchange(               # 只有成功生成才写入历史
            request.question,
            result.answer,
            result.citations,
        )

    return {
        "session_id": session.id,
        "answer": result.answer,
        "citations": result.citations,
        "meta": {...},
        "request_id": request.state.request_id,
    }
```

流式接口用的是同一套逻辑，只是把结果边生成边发出去：

```python
def event_stream():
    yield sse("session", {"session_id": session.id})   # 先告诉客户端用哪个会话

    with session.lock:
        for event in engine.stream(question, history=history):
            if event.kind == "citations":
                yield sse("citations", {...})          # 先把引用发出去
            elif event.kind == "token":
                yield sse("token", {"text": event.text})  # 一个字一段
            elif event.kind == "done":
                session.add_exchange(...)              # 完整生成后才落历史
                yield sse("done", {"answer": ..., "citations": ...})
```

所以在浏览器或前端里，可以先把“引用”显示出来，再看着答案像打字机一样逐字出现。中途断开则不会留下半轮问答。

## 关键变量

| 变量 | 默认值 | 用大白话说 |
|---|---:|---|
| `CHUNK_SIZE` | 700 | 一张卡片最多约 700 个字符 |
| `CHUNK_OVERLAP` | 100 | 相邻卡片重叠 100 字符，避免一句话被切断 |
| `DENSE_TOP_K` | 20 | 语义检索先捞 20 条候选 |
| `LEXICAL_TOP_K` | 20 | 关键词检索也捞 20 条 |
| `FUSION_TOP_K` | 10 | 两路合并后留 10 条给下一关 |
| `TOP_K_RERANK` | 3 | 精读后最终只给大模型 3 条 |
| `RRF_K` | 60 | 名次折算积分的平滑系数，越大名次差距越平缓 |
| `SESSION_TTL_SECONDS` | 1800 | 会话闲置 30 分钟就自动清理 |
| `MAX_HISTORY_TURNS` | 6 | 每次最多带最近 6 轮对话给模型 |
| `MAX_HISTORY_CHARS` | 3000 | 历史总长度预算，超了就从旧到新裁掉 |

## 最需要看懂的五处

1. **建库和问答是两条时间线**：建库慢，负责把资料整理好；问答快，只做查找和生成。两者通过 Qdrant 和 BM25 索引交接。
2. **两份索引各管一种“相关性”**：向量管“意思像不像”，BM25 管“词对不对得上”，RRF 负责把两个排行榜合成一个。
3. **reranker 是质量闸门**：它把候选从 10 条压到 3 条，大模型看到的资料越干净，越不容易胡编。
4. **引用来自元数据，不是模型自由发挥**：每张卡片入库时就带着来源、页码、表名，返回时直接组装成引用。
5. **会话只是“理解指代的记事本”**：历史帮助模型知道“它”指橙子，但提示词明确规定历史不能当事实依据；会话在内存里，服务重启就清空。

当前版本还有两个使用边界：服务默认只监听本机、没有鉴权，不要直接暴露公网；Qdrant 用本地磁盘模式，所以 `serve.py` 固定单进程单 worker，需要横向扩展时要换 Qdrant server 模式并把会话搬到 Redis 之类的外部存储。
