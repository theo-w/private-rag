# MCP 工具面加回 `sql_query`，但要过三重门

取代 [ADR 0005](0005-mcp-tools-read-only-no-sql.md) 里「不暴露运行时 SQL」这一条：MCP 工具面从两个
工具变成三个——`kb_search`、`ask`、`sql_query`（业务库只读查询）。入库/写库仍然不暴露。

`sql_query` 包的是 agentic 那颗运行时查询工具，装配也共用 `private_rag/agent/runtime.py` 的
`build_tool_registry`：授权判定、表白名单、有界采样、缺配置降级这几套语义只有一处实现，两个入口
（`/chat/agent` 与 MCP）不会各写一套然后漂移。护栏照旧——`sql_guard` 的单条 `SELECT`、表白名单、
强制 `LIMIT`，返回的是护栏改写后真正执行的那条 SQL。

ADR 0005 的顾虑没有被推翻，只是加了一道门来兜它：外部客户端读业务库这件事，不该由「装了这份代码」
就默认成立，而要由人在启动命令行上显式开。三重门，缺一门 `sql_query` 就不出现在 `tools/list`：

1. `scripts/serve_mcp.py --allow-sql`：默认关。谁来启动进程，谁决定这次会话能不能读业务库。
2. `sources.yaml` 里对应 sql 源带 `allow_runtime_query: true`：与 agentic 完全同一个授权位。
3. `sql_guard` 的只读护栏：单条 `SELECT`、表白名单、强制行数上限。

## Considered Options

- 保持两个工具（ADR 0005 原案）：外部客户端问不出「库里现在有多少件」这类实时数值，只能问已入库的
  行级文档；而本机 agentic 已经能查同一批表，两边能力不对称，客户端侧的跨侧推理做不了。
- `sql_query` 与知识库工具一样默认暴露：省一个开关，但把「本机进程 + 配置授权」这个前提彻底丢掉，
  等于任何装了这份代码的人启动服务就能读业务库。
- `--allow-sql` 显式开 + 复用既有授权与护栏（选定）：默认面与 ADR 0005 一致，要扩面得有人明确表态；
  扩面以后用的仍是那套已经被 agentic 验证过的护栏与授权位。

## Consequences

- 默认启动的 MCP 面仍是 `kb_search` + `ask`；加了 `--allow-sql` 但配置没授权，也只会有两个工具，
  并且启动时会在 stderr 说清为什么没有 `sql_query`（缺配置文件 / 没有 `allow_runtime_query: true` /
  业务库连不上），不静默降级。
- MCP 面与 agentic 面重新等价：外部客户端问得出的实时数值，`/chat/agent` 同样问得出，共用同一份授权。
  代价是业务库的信息面随 `--allow-sql` 一起开放出去——表结构与查询结果本身就是信息，只读不代表无害。
- 仍然没有鉴权：开了 `--allow-sql` 之后，能启动这个进程的人就能读白名单内的业务表，与本机 HTTP 服务
  的无鉴权同级。要对外多人开放，先解决鉴权，再谈这个开关。
- `--list-tools` 不装配 `sql_query`：那颗工具的定义要从 `sources.yaml` 与业务库取表结构，自检模式不
  打开它们，所以那里只列知识库两个工具。
- 与 PR #22（同样暴露 `sql_query`）的口径差异只剩引用载荷：那边每条引用附块级 `locator`，这边沿用
  既有 `Citation` 字段。这一条已在 v8.14 补齐——MCP 的证据块与引用都带块级定位，但按 `docs/adr/0009`
  的做法挂在 `ToolBlock` 而不是 `Citation`，HTTP 与评估报告的引用形状没动。
