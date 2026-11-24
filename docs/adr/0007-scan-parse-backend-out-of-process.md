# 第三方解析器走独立虚拟环境 + subprocess，不进主依赖

v8 解析基准要把自建扫描件解析链路（RapidOCR + OpenCV 版面表格 + rapid-table SLANet）与成熟解析器
（docling、MinerU）对照。第三方解析器不能装进本仓库的解释器：本仓库与 CI 钉死 **Python 3.9**
（README「环境要求」、`ci.yml` 的矩阵），而 docling 自 2.70.0 起不要 3.9（当前钉 2.151.1 要 `>=3.10`）、
MinerU 4.2.3 要 `>=3.11`。所以第三方装进仓库根的 `.venv-parse/`（独立 3.11 解释器），主进程用
subprocess 调驱动脚本 `scripts/parse_drivers/{docling,mineru}_driver.py`，靠一份 JSON 契约通信。

后端开关是 `RAG_SCAN_PARSER=selfbuilt|docling|mineru`（默认 `selfbuilt`）。`selfbuilt` 不起任何子进程，
走的还是 v6.2 消融的 ON 侧那条链路，逐字节没变——这是新读数能与 v6.2 报告对齐的前提。

## Considered Options

- **把第三方解析器装进主依赖、升仓库到 3.10/3.11**：省一层 subprocess，但为了一个可选基准把整个
  仓库（含 CI 矩阵、README 环境要求、Render 部署的 runtime）的 Python 版本抬上去，代价与收益不对称；
  而且基准的目的是「量出自建链路处在什么位置」，不是把自建链路换掉。
- **只用第三方自己的 Python 进程跑整套入库**（把入库也搬进 3.11）：解析与入库同进程最省事，但那样
  第三方侧与自建侧跑的是两套入库代码，doc_id / 页码 / 表格序列化 / 父子块切分都可能分叉，A/B 就不可比了。
- **独立虚拟环境 + subprocess 驱动，入库仍在 3.9 主进程**（选定）：只有「解析」这一步跨解释器，
  下游装配是同一份代码，两侧读数可比。代价是多一个虚拟环境要装、多一份输出契约要维护。

## Consequences

- **一次调用解析整份 PDF**：外部解析器的模型冷启动是十几秒量级，逐页调用会把基准耗时变成冷启动次数
  乘以页数。驱动输出按页号组织，主进程在文件级装配一次后端、逐页取用（`ScanBackend.parse_page`）。
- **解析结果按 PDF 字节内容缓存**（`storage/parse_cache/`，已 gitignore，键含输出契约版本与影响结果的开关
  `variant`）：A/B 反复重跑入库不再付冷启动；换文件即换键，不会因为同路径同名而复用旧结果。MinerU 的档位
  就是这样一个开关——换档必须换键，否则旧档的缓存会冒充新档的读数。
- **MinerU 的批量解析入口是 `mineru-kit parse`，不是 `mineru parse`**：4.2.3 的 `mineru` 已经变成
  「文档库 + 本地服务」的 CLI（`mineru parse` 要先 `mineru server start`，且默认 `parse_server.local.mode=disabled`，
  没模型时报 `quality_tier_unavailable`）。`mineru-kit parse --format middle_json` 才是跑本地模型、产出逐页
  中间 JSON 的那个入口，基准要的正是这个形态。**不用 `--remote`**：它把 PDF 上传到官方 mineru.net，
  与本仓库「本机解析、语料不出机器」的前提相反，也违背基准要量本地链路的初衷。
- **档位 `RAG_MINERU_TIER`（默认 `standard`）与模型预下载**：`basic` = 流水线小模型，`standard` = 再加
  llama.cpp 的 VLM；模型要先 `mineru-kit models download --tier <档位>` 落到 `~/.mineru/models`
  （basic 档实测 863 MB），缺模型时驱动直接报错而不是悄悄降档——静默降档会把两档的读数混成一份。
- **不静默降级**：后端名非法、虚拟环境缺失、驱动失败都抛 `ScanBackendError`；某页后端没给结果则回退
  自建链路，并把这件事写进入库 `errors`。基准读数最怕「其实跑的是自建链路」混进第三方侧，宁可报错。
- **驱动脚本只用标准库 + 各自的库**，不 import `private_rag`：那边是 langchain / qdrant 等主进程依赖，
  第三方虚拟环境里没有装也不需要。表格的三种形态（docling 的 span 单元格、MinerU 的 HTML、markdown
  管道表）统一收敛成 `list[list[str]]`，合并单元格展开成占位副本，与自建侧的地格坐标可比。
- **CI 不装第三方解析器**：`requirements-parse-bench.txt` 不进 `requirements.txt`、不进 CI；新增的
  单测全部打桩子进程输出。`eval/v8_parse_bench.jsonl` 的证据校验只读文字底稿，所以 CI 里能验。
- **换机器要重装**：`.venv-parse/` 不入库，第三方依赖与模型权重都要重新下载（MinerU 数 GB 量级）。
  `RAG_PARSE_VENV_PYTHON` 可指向已有的 3.11 解释器，`RAG_PARSE_BACKEND_TIMEOUT`（默认 300 秒）兜住首跑，
  `scripts/parse_bench.py --preflight` 在正式跑读数之前先把解释器、驱动、import、模型四件事验一遍。
- **PaddleOCR-VL 没进候选**：它要 CUDA 上的 vLLM/SGLang 服务，本机 Apple Silicon 跑不了；
  报告里如实记为未测，不假装三侧齐了。
