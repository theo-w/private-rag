# Google Drive 语料用本机 CLI 预下载到本地目录，而不是在入库链路接 Google SDK

要把 Drive 里的真实文档喂进知识库，最省事的做法是在连接器里直接调 Drive API：一个 `source_type: gdrive`，入库时现场拉正文。这与「全本地离线」冲突——检索链路一旦认了这种 source，就得带着凭据联网，仓库也要多出 google-api-python-client 与 OAuth 流程两大依赖（仓库钉 Python 3.9、尽量少依赖）。我们决定把联网限制在一步：用本机 CLI（rclone 或 gdrive）先把 Drive 文件下载到本地目录，下载目录本身就是一个普通 `type: file` 源，入库与检索链路一行都不改。

## Considered Options

- 入库时直接调 Drive API（新 `source_type: gdrive`）：少一个步骤，但联网进了检索链路，且 `source_type` 在 API schema 与 CLI 里是 `Literal["file","web","sql"]`，扩它要同时改请求校验、引用渲染与过滤参数。
- Google SDK + 预下载：联网仍然只在同步一步，但为一个下载动作引入整套 SDK 与 OAuth 依赖。
- 本机 CLI 预下载（选定）：Python 侧零新增依赖，凭据留在 CLI 自己的配置文件里（rclone 的 remote 在 `~/.config/rclone/rclone.conf`），不进命令行也不进日志；代价是要文档化 rclone 授权步骤，并处理两个 CLI 的参数差异。

## Consequences

下载目录默认 `data/gdrive`，已 gitignore，并由脚本用 `git check-ignore` 强制确认——仓库内的目录若没被忽略，同步直接报错，真实语料正文不可能跟着提交（docs/adr/0002）。`build_demo_snapshot.py` 的白名单只允许 `data/docs`，`data/gdrive` 会被判越界拒绝，正好挡住把真实语料打进公网只读演示。

Drive 里的 Google 原生文档（Docs/Sheets/Slides）没有文件实体，下载时必须导出；入库链路只认 txt/md/pdf，所以同步默认把导出格式钉成 pdf，docx/xlsx 那类 rclone 默认格式反而读不了。同步后会清点目录，把不支持的后缀列出来，目录里一个可入库文件都没有时明确失败，而不是让 pipeline 报「没有找到可入库文件」。

来源区分靠 `tags: [gdrive]`，`source_type` 仍是 `file`，`source_uri` 沿用既有路径约定（仓库内目录写成相对仓库根的路径）：过滤、引用、增量幂等与 prune 全部沿用 file 源的语义，代价是引用上看不出「这条来自 Drive」，只能看 tag。
