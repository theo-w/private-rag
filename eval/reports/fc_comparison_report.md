# JSON 模式 vs 原生 function calling：决策输出通道对比

- 主模型：`qwen3.8:27b-mlx`；对照模型：`qwen2.5:7b-instruct`（仅 FC 通道）
- 样本：路由 12 条（knowledge, database, mixed, refusal）、步进 3 个合成场景；同一提示词、`num_predict=2000`、`temperature=0`
- 口径：只量「决策输出能不能稳定拿到、两模式是否判到同一边」，不判路由对错

| 通道 | n | 解析成功率 | 动作合法率 | 单条均耗时 |
|---|---|---|---|---|
| 主模型 route/json | 12 | 12/12 | 1.00 | 27.00s |
| 主模型 route/function | 12 | 12/12 | 1.00 | 18.82s |
| 主模型 step/json | 3 | 3/3 | 1.00 | 16.05s |
| 主模型 step/function | 3 | 3/3 | 1.00 | 17.99s |
| 对照 qwen2.5:7b-instruct route/function | 12 | 4/12 | 0.33 | 1.79s |

- 两模式意图一致率（两侧都解析成功的条目内）：`0.75`（可比较 12 条）
- 两模式步进动作一致率：`1.00`（可比较 3 条）
- 主模型 FC route 失败样本：无
- 主模型 FC step 失败样本：无

逐条读数见 `eval/reports/fc_comparison.json`。