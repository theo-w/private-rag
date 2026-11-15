# private-rag 项目流程导读（小白版）

> 面向第一次接触 RAG 的读者：先讲清楚“这东西在干嘛、数据怎么流动”，再给出按真实实现删减后的示意代码。完整实现请以文中链接的源码为准。

## 一句话总结

这个项目像一位“只回答你资料里内容的私人图书管理员”：先把你的文件、网页、数据库整理成两层卡片——小卡片用来快速查找，大卡片保留完整段落；你提问时，它先用两套目录找出候选小卡片，精读筛出最相关的 3 张，再把它们所属的大卡片整段交给本地大模型，只照着这些资料回答，并告诉你答案来自哪一页、哪个网址或哪条记录。

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
          ↓ 先切成约 2000 字符的“父块”（大卡片）
          ↓ 再在父块内切成约 700 字符、相邻重叠 100 的子块（小卡片）
          ↓ 两种索引都只索引子块
   ┌──────────────────────┬──────────────────────┐
   │ bge-m3 把文字变成向量 │ jieba 分词 + BM25     │
   │ 存进 Qdrant          │ 存成关键词索引文件     │
   │ 负责“意思相近”       │ 负责“关键词命中”       │
   └──────────────────────┴──────────────────────┘
```

为什么要剪成小卡片？因为一整本书塞不进模型，而且检索时只需要最相关的几段。重叠 100 字符是为了避免一句话刚好被切在两张卡片的边界上。

具体默认值是：子块每张约 700 字符，相邻重叠 100 字符；父块约 2000 字符，父块之间不重叠。

为什么不直接把小卡片送给模型？小卡片适合“找得准”，但前后文可能被切断。于是每张小卡片都记住自己属于哪张大卡片；命中后系统把整张大卡片送进模型，答案就不容易丢前因后果。这就是 v5 的 Parent-Document RAG（父子块）。

为什么要两种索引？因为一种会“漏”：

- 向量检索懂同义表达，比如“冷藏”能匹配“放冰箱”，但有时会漏掉专有名词、编号；
- BM25 抓关键词很准，但不懂“番茄”和“西红柿”是一回事；
- 两个一起用，互相补位。

### 阶段二：问答（在线，每次提问）

```text
用户问题 + 这个会话最近几轮对话
        ↓
（可选）查询改写：换个说法 / 写一段假想答案
        ↓
   ┌────┴─────┐
向量检索 20 条   关键词检索 20 条
   └────┬─────┘
     RRF 合并 → 留 10 条
        ↓
  reranker 用原问题逐条精读 → 只留 3 条子块
        ↓
  按父块去重、扩展（总预算 6000 字符）
        ↓
  拼成带 [1][2][3] 编号的提示词
        ↓
  本地 Ollama 模型回答
        ↓
  答案 + 引用列表
        ↓
  写回会话历史 → 返回 JSON 或边生成边推送（SSE）
```

这里有五个关键角色：

- **查询改写（可选）**：像同一句话换几种说法再搜一次。`multi` 让模型生成最多两个同义查询；`hyde` 让模型先写一段“假想答案”，拿它去检索。默认关闭；改写失败就退回原问题。
- **RRF 合并**：像两个排行榜按名次折算积分。某张卡片在向量榜排第 1，加分；在关键词榜也排第 1，再加一次分。两边都靠前的卡片总分最高；多查询时，各查询的榜单也会再合并一次。
- **reranker（重排模型）**：像面试官逐份精读简历。它把“原问题 + 每张子块”放在一起逐条比较，选出真正最相关的 3 张。注意这里始终用原问题，不用改写后的查询，避免检索跑偏。
- **父块扩展**：像查到目录条目后，把整页原文翻出来。系统按父块去重，在 6000 字符预算内送整段父块；放不下就退回命中的子块，保证精排选中的内容一定进上下文。
- **大模型**：像只拿到这几段父块原文的答题学生。提示词明确要求“只能根据资料回答，没有就拒答”，所以资料里没有的内容不会被编出来。

## 数据流向

用一条线看完整条数据链路：

```text
文件 / 网页 / 数据库
  → 统一文档（正文 + 元数据：标题、来源、页码、标签……）
  → 父子两级切分：父块约 2000 字符，子块约 700 字符 / 重叠 100
  → Qdrant（子块向量 + 子块原文 + 父块原文 + 元数据）
  → 用户提问
  →（可选）查询改写：多查询 / HyDE
  → 每个查询：向量召回 + 关键词召回
  → RRF 合并（查询内 + 跨查询）
  → reranker 用原问题精排
  → 父块去重 + 6000 字符预算
  → 带编号的父块上下文 + 最近对话历史
  → 本地大模型
  → 答案 + 引用
  → 会话内存（仅用于下一轮理解指代）
  → HTTP JSON 或 SSE 流式返回
```

值得记住的一点：**引用不是模型编的**。每张卡片入库时就带着 `source_type / title / source_uri / page / table` 这些身份信息；返回答案时，系统直接把这些元数据整理成引用列表，模型只负责在答案里标 `[1]`、`[2]`。

## 关键函数

| 函数 / 类 | 接收什么 | 做什么 | 返回什么 |
|---|---|---|---|
| `run_ingest`（[pipeline.py](../private_rag/ingestion/pipeline.py)） | 数据源配置 | 逐个来源读取、父子两级切块、算子块向量、写 Qdrant，最后重建 BM25 | 入库统计（新增/更新/跳过/失败） |
| `split_parent_child`（[chunking.py](../private_rag/ingestion/chunking.py)） | 一篇文档的正文 | 先按 2000 字符切父块，再在父块内按 700/100 切子块；给每个父块生成稳定 ID | 子块列表，每块带父块原文与序号 |
| `load_file_documents` / `load_web_documents` / `load_sql_documents` | 一个文件、一批 URL、一条 SQL 查询 | 把三种不同来源都转成统一文档；PDF 按页、SQL 按行 | 文档列表 + 错误列表 |
| `HybridRetriever.retrieve`（[hybrid.py](../private_rag/retrieval/hybrid.py)） | 用户问题、来源/标签过滤 | 同时跑向量检索和 BM25，再用 RRF 合并；多查询结果可再融合 | 融合后的候选子块 |
| `Reranker.rerank`（[rerank.py](../private_rag/retrieval/rerank.py)） | 原问题 + 候选子块 | 逐条精读打分，按相关性排序 | 最相关的 N 条（默认 3） |
| `expand_parents`（[parent.py](../private_rag/retrieval/parent.py)） | 精排后的子块、字符预算 | 按父块去重；放得下就换整段父块，放不下退回子块并截断 | 实际送入 LLM 的上下文片段 |
| `rewrite_queries`（[query_rewrite.py](../private_rag/query_rewrite.py)） | 原问题、改写模式 | 调本地 LLM 生成同义查询或 HyDE 文本；任何失败都退回原问题 | 用于检索的查询列表 |
| `build_prompt` / `generate_answer` / `stream_answer`（[generation.py](../private_rag/generation.py)） | 问题、父块、历史 | 拼出严格提示词，调用本地 LLM；没有候选时直接拒答 | 答案文本 + 引用列表（流式则逐段文字） |
| `RagEngine.retrieve_with_trace` / `answer` / `stream`（[qa.py](../private_rag/qa.py)） | 问题、检索选项、历史 | 串起“改写 → 多查询检索 → 原问题精排 → 父块扩展 → 生成”，CLI 和 API 共用 | 检索 trace、同步结果或流式事件 |
| `Session.history_for_prompt` / `add_exchange`（[sessions.py](../private_rag/sessions.py)） | 会话里已有的问答 | 取最近几轮、按字符预算裁剪；或把本轮问答追加进去 | 可直接放进提示词的历史 / 无 |
| `chat` / `chat_stream`（[routes.py](../private_rag/api/routes.py)） | HTTP 请求 | 检查知识库、找会话、加会话锁、调用引擎、写回历史 | JSON 响应 / SSE 事件流 |

## 简易代码

下面这些是**按真实逻辑重写的示意版**，省略了错误处理、分页、类型标注和锁。真实代码在链接的文件里。

### 1. 全景：一条链路看懂核心

```python
# ---------- 建库（离线，跑一次） ----------
docs = load_all_sources("config/sources.yaml")   # 文件/网页/数据库 → 统一文档
parents, chunks = split_parent_child(docs)       # 先父块，再子块；子块记住父块原文
qdrant.save(chunks, embed(chunks))               # bge-m3：只给子块算语义坐标
bm25 = build_bm25(chunks)                        # jieba 分词 → 只给子块建关键词索引

# ---------- 问答（在线，每次提问） ----------
history = session.history_for_prompt()           # 最近几轮，只帮助理解“它”指谁
queries = rewrite(question, mode="off")          # 默认只有原问题；可选多查询 / HyDE
results = [retrieve(q, top=20) for q in queries] # 每个查询各跑向量 + 关键词
candidates = fuse_queries(results)[:10]          # 查询内 RRF + 跨查询 RRF，留 10 条
top3 = reranker.rerank(question, candidates, top=3)  # 始终用原问题精读，只留 3 条子块
contexts = expand_parents(top3, max_chars=6000)  # 按父块去重，尽量送整段父块
answer = llm.invoke(build_prompt(question, contexts, history))  # 只准看这些资料作答
return answer, citations(top3)                   # 答案 + 引用
```

### 2. 入库：怎么做到重复跑也不重复存

```python
# 简化自 private_rag/ingestion/pipeline.py
def ingest_one(document):
    # 同一个 doc_id 已入库，且内容哈希没变 → 直接跳过
    if find_doc_hash(document.doc_id) == document.content_hash:
        return "skipped"

    # 先切父块，再在父块内切子块；子块自带父块原文和序号
    chunks = split_parent_child(document.text, parent_size=2000, child_size=700, overlap=100)

    # 先算新向量，再删旧数据：就算中途失败，旧知识也还在
    vectors = embed([chunk.text for chunk in chunks])
    delete_old_vectors(document.doc_id)

    # 子块原文、向量、父块原文、元数据一起写进 Qdrant
    # 每个子块的 payload 里还带一份 parent={id,index,count,text}，也就是父块全文
    save_chunks(
        ids=deterministic_ids(document.doc_id, document.content_hash),
        texts=[chunk.text for chunk in chunks],
        vectors=vectors,
        metadata={
            "doc_id": document.doc_id,
            "source_type": document.source_type,   # file / web / sql
            "source_uri": document.source_uri,     # 文件路径 / URL / 库名#主键
            "title": document.title,
            "tags": document.tags,
            "page": document.page,                 # PDF 页码，可选
            "table": document.table,               # SQL 表名，可选
            # 每个子块还会写入自己的 parent_id / parent_index
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

### 5. v5：小块负责找，大段负责读

Parent-Document RAG 的核心就一句话：**检索用子块，送模型用父块**。下面把建库和问答各拆成两段。

```python
# 建库：先父块，再子块；子块记住自己的父块
parents = split_by_size(document.text, size=2000, overlap=0)
children = [
    child
    for parent in parents
    for child in split_by_size(parent.text, size=700, overlap=100)
]
# 向量与 BM25 只给 children 建索引；每个 child 额外保存 parent.id / parent.text

# 问答：精排留下 3 个子块后，扩展成父块
seen = set()
contexts, used = [], 0
for child in top3:
    if child.parent_id in seen:          # 同一个父块只送一次
        continue
    seen.add(child.parent_id)
    parent_text = child.parent_text
    if used + len(parent_text) <= 6000:  # 预算够 → 送整段父块
        contexts.append(parent_text)
        used += len(parent_text)
    else:                                # 预算不够 → 退回命中的子块
        remaining = 6000 - used
        if remaining > 0:
            contexts.append(child.text[:remaining])
        break
```

查询改写则是“多找几条路，最后仍按原问题选”：

```python
queries = rewrite(question, mode)        # off → 只有原问题；multi/hyde → 多 1~2 条
results = [retrieve(q) for q in queries] # 每条查询各跑 dense + BM25
candidates = fuse_queries(results)[:10]  # 查询内 RRF + 跨查询 RRF
top3 = reranker.rerank(question, candidates, top=3)  # 用原问题，不用改写文本
```

- `multi` 生成的是“换个说法”的检索查询；`hyde` 生成的是一段可能出现在资料里的假想文字。两者都只参与检索，不会进入最终提示词。
- 改写调用失败、JSON 解析失败或没有可用变体时，自动退回原问题；默认 `off`，所以日常问答不会多一次模型调用。
- 父块文本会随每个子块存一份，索引体积约放大 2–3 倍；这是用空间换“无需额外文档仓库、父子不会不一致”。

### 6. API 与会话：同一会话排队，不同会话并行

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
| `CHUNK_SIZE` | 700 | 子块最多约 700 个字符，用来检索 |
| `CHUNK_OVERLAP` | 100 | 相邻子块重叠 100 字符，避免一句话被切断 |
| `PARENT_CHUNK_SIZE` | 2000 | 父块最多约 2000 字符，命中后整段送模型 |
| `CONTEXT_MAX_CHARS` | 6000 | 所有父块加起来的字符预算，超了回退子块 |
| `MAX_VARIANTS` | 2 | 多查询最多生成 2 个改写查询 |
| `MAX_VARIANT_CHARS` | 300 | 每个改写查询最长 300 字符 |
| `MAX_HYDE_CHARS` | 400 | HyDE 假想文档最长 400 字符 |
| `DENSE_TOP_K` | 20 | 语义检索先捞 20 条候选 |
| `LEXICAL_TOP_K` | 20 | 关键词检索也捞 20 条 |
| `FUSION_TOP_K` | 10 | 两路合并后留 10 条给下一关 |
| `TOP_K_RERANK` | 3 | 精读后最终只给大模型 3 条 |
| `RRF_K` | 60 | 名次折算积分的平滑系数，越大名次差距越平缓 |
| `SESSION_TTL_SECONDS` | 1800 | 会话闲置 30 分钟就自动清理 |
| `MAX_HISTORY_TURNS` | 6 | 每次最多带最近 6 轮对话给模型 |
| `MAX_HISTORY_CHARS` | 3000 | 历史总长度预算，超了就从旧到新裁掉 |

## 最需要看懂的六处

1. **建库和问答是两条时间线**：建库慢，负责把资料整理好；问答快，只做查找和生成。两者通过 Qdrant 和 BM25 索引交接。
2. **两份索引各管一种“相关性”**：向量管“意思像不像”，BM25 管“词对不对得上”，RRF 负责把两个排行榜合成一个。
3. **reranker 是质量闸门**：它把候选从 10 条压到 3 条，大模型看到的资料越干净，越不容易胡编。
4. **引用来自元数据，不是模型自由发挥**：每张卡片入库时就带着来源、页码、表名，返回时直接组装成引用。
5. **会话只是“理解指代的记事本”**：历史帮助模型知道“它”指橙子，但提示词明确规定历史不能当事实依据；会话在内存里，服务重启就清空。
6. **v5 是两级阅读**：子块负责找得准，父块负责读得全；查询改写只是多找几条路，最后仍按原问题精排，失败就退回原问题。

当前版本还有两个使用边界：服务默认只监听本机、没有鉴权，不要直接暴露公网；Qdrant 用本地磁盘模式，所以 `serve.py` 固定单进程单 worker，需要横向扩展时要换 Qdrant server 模式并把会话搬到 Redis 之类的外部存储。
