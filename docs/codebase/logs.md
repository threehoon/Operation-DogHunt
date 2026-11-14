# 调用日志｜模块子块

[返回父块](../../CODEBASE.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | logs |
| 核对日期 | 2026-11-15（Asia/Shanghai） |
| 核对范围 | 后端 `core/applog.py`、`core/trace_middleware.py`、`services/logs/reader.py`、`routes/logs.py`、`tests/test_logs.py`；前端 `/logs` 页面只核对了入口、刷新常量和文件位置，界面细节沿用 2026-11-15 前端子块原记录 |
| 实现状态 | 已实现：每个请求和后台任务都有调用编号，写结构化日志；聊天另存完整存档；本机可在 `/logs` 页面查看 |
| 验证程度 | 本轮运行验证：后端离线测试通过（test_logs 18 项）；前端构建与浏览器检查为 2026-11-15 历史记录，本轮未重跑 |

## 负责什么

“调用编号”（trace id）是每次请求或后台任务的 12 位十六进制编号。本模块负责：
- 生成调用编号，并把它带进日志和响应头；
- 把日志按天写成 JSONL 文件，写入前先对 Key、Token 等敏感内容脱敏；
- 为每次聊天保存一份完整存档；
- 提供只允许本机访问的查询接口，以及前端 `/logs` 查看页。

出错时，前端的红色提示后附“编号 xxx，查看日志”，点开即可看到这次请求的每一步。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [applog.py](../../backend/app/core/applog.py) | `trace`、`new_trace_id`、`event`、`run_step`、`redact`、`register_secret`、`save_archive` | 编号上下文、事件记录、脱敏、聊天存档 |
| 同一文件 | `DailyJsonHandler`、`configure_console`、`configure_files`、`shutdown_files` | 控制台和按天文件输出 |
| [trace_middleware.py](../../backend/app/core/trace_middleware.py) | `TraceMiddleware` | 每个 HTTP 请求生成编号，加 `x-trace-id` 响应头，记 `http.request` |
| [reader.py](../../backend/app/services/logs/reader.py) | `load_entries`、`summarize`、`list_traces`、`get_trace`、`read_archive` | 读取日志并按编号归组 |
| [routes/logs.py](../../backend/app/api/routes/logs.py) | `GET /logs/traces`、`GET /logs/traces/{trace_id}`、`local_only` | 查询接口，只允许本机 |
| [_isolation.py](../../backend/tests/_isolation.py) / [test_logs.py](../../backend/tests/test_logs.py) | 临时日志目录 / 18 项 | 测试不写真实日志目录 |
| [client.ts](../../frontend/src/api/client.ts) | `TraceError`、`traceOf`、`traceHeader` | 前端请求失败时带上编号 |
| [logs.ts](../../frontend/src/api/logs.ts) / [types/logs.ts](../../frontend/src/types/logs.ts) | `listTraces`、`getTrace` | 调用查询接口并检查数据形状 |
| [LogsView.vue](../../frontend/src/views/LogsView.vue) | `/logs?trace=编号` | 日志查看页 |
| [components/logs/](../../frontend/src/components/logs/labels.ts) | `TraceList`、`TraceTimeline`、`TraceChatArchive`、`TraceLink`、`labels.ts` | 列表、时间线、聊天存档、编号链接、中文步骤名 |

## 输入、输出和依赖

- 输入：后端各模块调用 `applog.event` 写的事件；环境变量 `DOGHUNT_LOG_DIR`、`DOGHUNT_TRACE_DIR` 可改存放位置。
- 输出：
  - `data/logs/app-YYYY-MM-DD.jsonl`，保留 14 天；
  - `data/traces/` 下的聊天存档，按修改时间保留最新 200 份。

  两者都已被 Git 忽略。
- 依赖：聊天存档内容来自 [聊天与提示词](chat.md)；后台任务来自 [外部数据与后台取数](data-sources.md)；应用启动时的配置见 [backend.md](backend.md)；前端外壳见 [frontend.md](frontend.md)。

## 后端怎么记

- 编号：HTTP 请求为 12 位十六进制；后台任务带 `task-` 前缀，并记父编号。格式校验为 `^(task-)?[0-9a-f]{12}$`。编号存在 contextvars 里，`current_trace()` 读取。
- 中间件：纯 ASGI 实现，跳过 `/api/v1/logs` 本身。在 `app.http` 下记 `http.request`，字段为 method、path、status、ms，outcome 为 done、error 或 disconnected。
- 事件名：
  - http.request；
  - chat.context、chat.end、chat.rejected、chat.crash；
  - llm.start、llm.first_token、llm.done、llm.http、llm.error；
  - task.start、task.step、task.end、task.error；
  - ext.call、odds.saved、odds.error、archive.error。
- 出错信息（`describe_error`）：
  - 记类型；
  - 出错位置只取 `backend/app` 和 `scripts` 下的最后 6 帧，以及原因链；
  - 只有项目自己的错误（ModelError、DunkError、ScheduleError、FetchError、OddsRefreshError、ChatPromptError）记原文，其他异常不记原文，避免带出敏感内容。
- 脱敏：
  - 按规则替换 Bearer、x-api-key、authorization、api_key、access_token、secret、password 和 `?key=` 的值；
  - 再替换启动时登记的已知密钥（至少 8 个字符），包括模型 Key、附加请求头的值和三个数据源 Key。
- 后台步骤：`run_step` 记 `task.step` 的开始和结果。结果含“未更新”算失败，含“跳过”算跳过；异常不外抛。
- 聊天存档：`save_archive` 写入开始和结束时间、outcome、error、模型和思考强度、资料摘要、请求、`analysis_cards`、完整提示词、回答（中断时为已收到部分）、reply_complete、usage。写入前脱敏，失败记 `archive.error`。
- 时间按北京时间（UTC+8）显示。

## 查询接口

- 只允许 127.0.0.1、::1、localhost 访问，否则返回 403。
- `GET /api/v1/logs/traces`：参数 days 1–14（默认 1）、kind（chat、odds_refresh、task、api）、problems_only、q（不超过 100 字）、cursor（从 0 起）、limit 1–200（默认 50）。
- `GET /api/v1/logs/traces/{trace_id}`：返回 summary、entries、children（后台子任务）、archive；找不到时返回 404“…日志只保留 14 天”。
- 读取在线程里进行。单个日志文件超过 20MB 时只读最后一段，丢掉被截断的首行，坏行跳过。
- 每条记录的状态为 running、error、warning 或 ok，附一句标题。

## 前端查看页

- 入口：
  - 侧栏底部 `rail-logs`；
  - Ctrl/⌘+K 命令 `logs`；
  - 路由标题“调用日志”；
  - 报错提示后的 `TraceLink`（“编号 xxx，查看日志”）。`ChatView`、`MatchBoard`、`stores/system.ts` 的 errorTrace 会显示这个链接。
- 页面功能：
  - 筛选：今天、3 天、14 天，类型，只看有问题的，按编号、接口或说明搜索；
  - 手动刷新；每 5 秒自动刷新（`AUTO_MS`），默认关，标签页在后台时不刷；
  - 分页加载；
  - 详情：复制编号、复制 JSON、父任务和子任务互相跳转。
- 时间线显示距开始的毫秒数、中文步骤名、警告和错误颜色；有错误的步骤默认展开。`labels.ts` 里 chat.rejected 显示为“聊天被拒”，chat.crash 显示为“聊天意外中断”。
- 聊天存档显示对话、回答、读到的资料和完整系统说明。分析卡用 `analysisCards` 解析，再交给 `AnalysisCards.vue` 显示；另有“原始数据”开关。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 请求与后台任务编号 | 已实现 | `trace_middleware.py`、`applog.trace` |
| 按天日志与清理 | 已实现，保留 14 天 | `DailyJsonHandler` |
| 聊天存档 | 已实现，保留 200 份，含分析卡 | `save_archive` |
| 本机查询与查看页 | 已实现 | `routes/logs.py`、`LogsView.vue` |
| 多进程写同一文件 | 没有文件锁 | `applog.py` |
| 前端自身报错上报 | 未实现 | — |
| 状态条识别 `/logs` | 未处理：`/logs` 不在 `STARTUP_MODULES` 里，状态条显示“未知位置/页面不存在” | `StatusStrip.vue` |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-15 | `cd backend && uv run --locked python -m unittest discover -s tests` | 本轮运行：172 项通过，其中 test_logs 18 项（含存档的 `analysis_cards` 和 `chat.context` 的 analysis_ms） |
| 2026-11-15 | 日志功能上线时的真实聊天 | 历史记录：当时 126 项测试通过；trace `5692be868234` 组装资料 35ms、首字 8.3s、总用时 10.3s，输入 19564、输出 287 token，存档 67KB；检查日志和存档，未发现密钥 |
| 2026-11-15 | 前端 `vue-tsc` 与 `vite build`，预览端口 5174 | 历史记录：292 个模块构建通过，页面读到 16 条记录；本轮未重跑 |

相关记录：[执行方案](../plans/2026-11-15-调用日志与查看页执行方案.md)、[交接](../handoff/2026-11-15-调用日志与查看页.md)。

## 限制、未决事项与下一步

- 多个进程同时写同一天的日志时没有文件锁。
- 聊天存档保存完整问答和提示词，虽已脱敏，仍属本机敏感记录。
- 前端自己的报错不上报后端。
- 手机宽度下侧栏底部整体隐藏，只能输入地址进入日志页。
- `/logs` 页面上状态条显示“未知位置/页面不存在”，因为日志页没加进 `STARTUP_MODULES`（当时为避免改动 3D 站点类型）。
- 建议（未执行）：给状态条单独补一条 `/logs` 的显示规则。

## 历史

- 2026-11-15：调用日志与查看页上线，包括调用编号、结构化日志、聊天存档和 `/logs` 页面（提交 42ffc82）。
- 2026-11-15：聊天加分析卡后，存档增加 `analysis_cards`，`chat.context` 增加 analysis 统计；日志页可以显示分析卡。
