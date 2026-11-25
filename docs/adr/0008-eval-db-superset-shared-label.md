# 评估专用 SQL 库做成演示库的超集并沿用 `demo.products` 标签

v8.7 要把 agentic 评估集从 29 条做到 40 条，其中 24 条的得分侧依赖运行时 SQL。演示库只有 4 行（四种水果），
给每条样本一段互不相同的证据就必须加行；直接往演示库加行会让 v7 那几条依赖「这四种水果」的参考答案失效，
已经落盘的几轮读数也对不上。我们决定新建一份评估专用 SQLite（`scripts/init_eval_db.py` → `data/sql/demo_eval.db`），
做成演示库的**超集**：前 4 行与 `scripts/init_demo_db.py` 逐字段相等（单测钉住这条），再加 8 行新货与
`storage / supplier / stock` 三列；`scripts/init_demo_db.py` 与 `data/sql/demo.db` 一字不改。

引用标签沿用 `demo.products`，不给新库单独起标签。原因是一条实现上的硬约束：`ToolRegistry.register` 以工具名为
字典键（`private_rag/agent/tools.py:283`），而 `SqlQueryTool.name` 是类常量——同一份 sources 配置里挂两个 sql 源会
互相覆盖，实际只有一个 `sql_query` 工具。如果新集用 `demo_eval.products`，v7 + 新集合并一次跑时 agentic 组只能查到
一侧的库，另一半样本的 `source_hit` 恒为 0，而合并跑正是这一项的主要用途。

## Considered Options

- 直接给演示库加行：省一个文件，但会让 v7 的参考答案失效、历史读数不可复现，演示页也会多出与演示脚本不一致的行。
- 新库 + 独立标签 `demo_eval.products`：两份互不干扰，看起来更干净，但一个进程里只能有一个 `sql_query` 工具，
  合并跑的 agentic 组必然废掉一半样本；只用固定链路时又用不上 SQL，等于两头都测不了。
- 给 `ToolRegistry` 改成按来源分名（`sql_query:<label>`）：能让两个 sql 源共存，但要动工具命名、提示词里的工具目录、
  引用渲染与既有单测，且真实场景里同时挂两个 SQL 源的需求还没出现。这一项不为此扩张。
- 新库做成超集 + 共用标签（选定）：代价是合并跑里 v7 那 29 条查的也是 12 行的库，需要逐条盯依赖「这四种水果」
  语义的 `d03` / `s01`。

## Consequences

新增 8 行的 `category` 一律不含「水果」二字，也不写含「水果」的词，这样 `category LIKE '%水果%'` 这类写法在合并跑里
仍然只回到演示库那 4 行。评估库前 4 行与演示库逐字段相等由单测钉住，漂移会直接失败。

共用标签意味着合并跑与单跑新集用的是同一份引用口径（`expected_source: demo.products`、`both_sides_hit` 的侧名），
v7 与新集的逐条判定可比；但工具目录（`_data_catalog` 的列名与有界样例值）会因为 12 行而变长并受长度上限截断，
所以新集基线与 v7 各轮**不做跨轮均值比较**——要比较就在新集上做同进程交替的 A/B。

评估 SQL 从此有两条路径：`config/sources.eval.agent.yaml` 继续指向演示库（复现 v7 各轮用它），
新增的 `config/sources.eval.agent.v8.yaml` 指向评估库（跑新集与合并跑用它）。缺库时 `evaluate.py`
会把两个生成脚本都列出来，避免把人引到错的脚本上。
