# MCP 暴露手写最小 stdio server，不引官方 mcp SDK

要把知识库检索与问答暴露给 Claude Desktop / Claude Code 这类外部 MCP 客户端，标准做法是装官方 `mcp` SDK。但本仓库钉在 Python 3.9（CI matrix 只有 3.9，本机系统 Python 就是 3.9），而 `mcp` 从第一个发布版本 0.9.1 到最新的 2.3.0，`requires_python` 一律是 `>=3.10`，`pip install mcp` 在 3.9 上直接被解析器拒绝。我们决定自己写最小 stdio server：每行一个 JSON 对象的 JSON-RPC 2.0，只实现客户端真正会调的 `initialize` / `ping` / `tools/list` / `tools/call`，外加对 `notifications/*` 的忽略与 `resources/*`、`prompts/*` 的空列表应答，零新增依赖。

## Considered Options

- 装官方 SDK：协议面最全（进度通知、`listChanged`、采样、elicitation 都免费），但要求整个仓库升到 3.10，或者为 MCP 单独维护第二个环境与依赖清单——两者都比这个功能本身大。
- 手写最小实现（选定）：零依赖、与现有 `requirements-ci.txt` 一致，CI 不用改；代价是协议要自己跟。
- HTTP/SSE 传输：与已有 `scripts/serve.py` 抢同一份本地 Qdrant 磁盘存储，等于把单进程限制变成两个服务协调，收益不抵复杂度。

## Consequences

- 只支持 stdio。进度通知已按阶段手写补上（v8.15，见 `docs/adr/0010`）：客户端带 `progressToken` 时，长调用在响应之前先几条「现在在做什么」。分段的工具结果仍做不到——协议里没有这种东西，SDK 也帮不上。
- 协议版本要手工跟进：`SUPPORTED_PROTOCOL_VERSIONS` 里只有三个已知日期，客户端带更新的日期时回落到我方声明的版本，由客户端决定能不能继续。协议新增必填字段时这里要改代码，不会自动跟上。
- 没有鉴权：能启动这个进程的人就能读知识库，与本机 HTTP 服务的无鉴权同级。
- 将来仓库升到 3.10+ 时，可以把 `private_rag/mcp/server.py` 换成 SDK 的 `Server` + `stdio_server`，工具层（`private_rag/mcp/tools.py`）与证据编号（`private_rag/mcp/evidence.py`）不用动——协议面与业务面是分开的，这层替换是纯替换。
- 只实现四个方法意味着「看起来能用」依赖客户端的宽容度：测试里钉死了坏行不退出、漏 `id` 也回应、`resources/*` 返回空列表这些容错行为，改动时别把它们当多余代码删掉。
