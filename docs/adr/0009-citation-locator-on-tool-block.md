# 引用定位器挂 `ToolBlock` 而不是 `Citation`，只进 MCP 载荷

v8 的 MCP 工具面合入 main 以后（PR #24），引用只能说「出自这个文件、第几页」，客户端想做「跳到原文那一处」
或者把命中的那句话高亮，缺一组坐标。已关闭的 PR #22 做过一版，但那个版本把定位器挂在当时的
`result_payload` 上，main 已经把 MCP 载荷拆成 `evidence_payload` / `citation_payload` / `evidence_citations`，
没法直接取用。我们决定在 main 上重做，并且把定位器挂在 `ToolBlock.locator`（`private_rag/agent/tools.py`），
由 MCP 载荷层把它同时写进证据块与引用条目，**不动 `Citation`**。

定位器本身在 `private_rag/generation.py` 的 `chunk_locator()`：`chunk_id`（入库时按
「`doc_id` + 内容哈希 + 块序号」算出的 `point_id`）、`doc_id` / `chunk_index` / `chunk_count`、
`parent_id`、`page` / `table`、`snippet_offset`（命中片段在**实际返回的那段文本**里的起始位置）。
取不到的键直接不出现，不用 `null` 占位——片段被字符上限截掉时 `find` 找不到，就整个省略这个键，
不臆造一个数字。SQL 侧没有「块」，定位就是「哪条查询取了哪些行」：`sql_locator()` 给
`source_uri` / `table` / `sql`（护栏改写后执行的那条）/ `row_count` / `truncated`。

## Considered Options

- **挂 `Citation`**（PR #22 的做法，字段更靠外、客户端只读一处）：`Citation` 是 HTTP `/chat`、`/chat/agent`
  与评估报告共用的那一份，加字段会让三处都多出键。评估报告的引用是按字段白名单序列化的，多一个字段
  就改一次报告形状，历史轮次不能逐字比对——而这套评估的全部可比性都建立在「历史报告可逐字比」上。
  更麻烦的是 `CitationOut` 这类响应模型会把它静默丢掉，HTTP 侧表现为「结构里声明了、实际没有」。
- **只挂 `ToolBlock`，MCP 载荷层负责写出去**（选定）：agentic 链路天然看不见它——`render_evidence` 只读
  `label` / `text` / `citation`，轨迹与评估序列化都不读这个字段，所以模型看到的东西与改动前逐字相同。
  代价是同一份定位信息在证据块与引用条目里各写一遍（客户端两处都要能读到，这个重复是值得的）。
- **HTTP 也带上**：对外更好用，但正是上面那条不可比代价，用户已拍板不做。真要加，得先决定评估报告
  的口径怎么兼容，那是另一项工作。
- **`ask` 也补定位**：`ask` 的引用来自生成阶段（`QaResult.citations`），手上没有检索块，补不出
  `chunk_id` 与偏移。硬补要么让引用撒谎，要么把生成链路改造成携带块——都不在这次范围里。所以它的
  outputSchema 也不声明 `locator`，否则工具定义在承诺一个给不出的东西。

## Consequences

- 开关 `RAG_DISABLE_CITATION_LOCATOR=1` 退回的必须是**逐字**的今天的形状，包括 `tools/list` 里那份工具定义：
  schema 由调用时的构造函数按开关拼（`search_output_schema()` / `sql_output_schema()` / `ask_output_schema()`），
  不是模块级常量。验证方式是同一命令在开关两种状态下各跑一次，关闭那份与 `origin/main` 的输出 `diff` 为空。
- `chunk_id` 会随内容改变而改变。这是正确行为（旧 id 指向的已经不是那块内容），但它不是跨版本的固定引用坐标，
  客户端不能把它当「永久链接」存下来。
- 文档级的字符偏移仍然没有：入库时没记，定位器里也不臆造。ROADMAP 那条「引用定位器 v2（入库时记文档级
  字符偏移）」继续开着。
- agentic 链路与评估的读数不受影响，但**这条要靠测**：locator 在工具层构造，万一它改变了证据文本或编号，
  所有历史读数就不可比。所以有一轮同进程 `off → on → off` 的护栏（见 ROADMAP 的 v8.14 详细验收），
  判读按既有噪声底口径——这一处量的是「有没有扰动」，不是收益。
