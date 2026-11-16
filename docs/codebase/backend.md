# 后端服务｜模块子块

[返回父块](../../CODEBASE.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | backend |
| 核对日期 | 2026-11-16（Asia/Shanghai；P2 只核对了应用骨架、配置、健康检查与数据库相关部分） |
| 核对范围 | 应用骨架（`main.py`、`core/config.py`、`api/router.py`）、健康检查和模型状态路由、`schemas/`、`integrations/llm.py`、预留目录、`pyproject.toml`、`.env.example`、`Makefile`；聊天、数据源、功能块、日志各自见下级子块；本机 `backend/.env` 的实际值未核对 |
| 实现状态 | 部分实现：FastAPI 应用、健康检查（含存储就绪检查）、模型连接、聊天、盘口和后台取数、调用日志已实现；数据库连接与迁移框架已实现但还没有表；正式预测保存、记忆和通用任务框架只有预留目录 |
| 验证程度 | 2026-11-16 本轮运行验证：208 项离线测试通过（4 项集成默认跳过），真实 Postgres 集成测试 4 项通过；真实模型和真实外部 API 只有历史记录，本轮未调用 |

## 负责什么

FastAPI 是提供后端接口的 Python 框架。后端提供：
- 连通检查、存储就绪检查和模型状态；
- 经中转站调用模型的聊天；
- 盘口快照读取和手动更新，以及更新后在后台补取赛程、伤病、实力和效率；
- 调用日志查询。

所有接口都挂在 `/api/v1` 下，接口说明在 `/api/docs`。聊天只读网站已保存的数据，不让模型自己联网。本块只写应用骨架、配置和模型连接，其余内容拆到下级子块：

| 子块 | 内容 |
| --- | --- |
| [聊天与提示词](chat.md) | `/chat`、`/chat/stream`、资料组装、服务端分析卡、提示词版本与锁文件 |
| [外部数据与后台取数](data-sources.md) | `/odds` 路由、盘口表、BALLDONTLIE 赛程和整季比分、DunkAPI 伤病和球队效率、盘口信号 |
| [功能块框架](blocks.md) | `services/analysis` 四层检查框架和实力块草稿（暂无调用入口） |
| [调用日志](logs.md) | 调用编号、结构化日志、聊天存档、`/logs` 接口和前端查看页 |
| [盘口取数工具](odds.md) | `scripts/fetch_nba_odds.py` 和 The Odds API 取数 |
| [本机开发存储](storage.md) | `deploy/compose.dev.yml` 的 PostgreSQL + Milvus 容器、`make storage-*`、`scripts/check_storage.py`；后端的数据库连接（`db/`）、Alembic 迁移、`/health/storage` |

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [main.py](../../backend/app/main.py) | `create_app`、`lifespan`、`_register_known_secrets`、`_startup_database_check`、`app` | 创建应用，挂中间件和路由；启动时登记密钥（含数据库密码）、打开日志文件、做一次最多 1 秒的连库检查（见 [storage](storage.md)）；退出时释放连接池 |
| [config.py](../../backend/app/core/config.py) | `Settings`、`get_settings`、`model_configured`、`database_url` | 读取基础配置、模型配置和数据库配置（`db_*` 字段见 [storage](storage.md)） |
| [router.py](../../backend/app/api/router.py) | `api_router` | 注册 health、odds、chat、logs 四组路由 |
| [routes/health.py](../../backend/app/api/routes/health.py) / [schemas/health.py](../../backend/app/schemas/health.py) | `GET /health`、`HealthResponse`；`GET /health/storage`、`StorageHealthResponse` | `/health` 返回 status "ok"、app、environment、version（`__version__ = "0.1.0"`），不查存储；`/health/storage` 见 [storage](storage.md) |
| [routes/chat.py](../../backend/app/api/routes/chat.py) | `GET /model`、`ModelStatus` | 返回是否已配置、模型名和超时秒数，不请求中转站；聊天路由见 [chat.md](chat.md) |
| [schemas/chat.py](../../backend/app/schemas/chat.py) | `ChatMessage`、`OddsSelection`、`ChatRequest`、`ChatResponse` | 聊天请求和回复的字段限制 |
| [schemas/odds.py](../../backend/app/schemas/odds.py) | `OddsBoard`、`OddsEvent`、`OddsBookmaker` | 来源、采集时间、新鲜度、比赛、平台、玩法、盘口和十进制赔率；不返回 Key 或路径 |
| [llm.py](../../backend/app/integrations/llm.py) | `stream_model`、`request_model`、`TextDelta`、`ModelError` | 调用中转站的 Responses 流式接口 |
| [pyproject.toml](../../backend/pyproject.toml) | `requires-python`、`dependencies` | Python 3.12 起；依赖 alembic（>=1.14,<2）、fastapi、httpx、jsonschema（>=4.23,<5）、psycopg[binary]（>=3.2,<4）、pydantic-settings、sqlalchemy（>=2.0.36,<2.1）、uvicorn；`[tool.uv] package = false`；具体版本见 uv.lock（2026-11-16 锁定 alembic 1.20.1、psycopg 3.3.9、sqlalchemy 2.0.57） |
| [backend/.env.example](../../backend/.env.example) | `DOGHUNT_*` | 模型和基础配置示例；开发存储的密码、端口与数据库连接变量（见 [storage](storage.md)） |
| [Makefile](../../Makefile) | `make setup`、`make backend`；`storage-*`、`db-migrate`、`db-current` 见 [storage](storage.md) | 安装依赖；在 127.0.0.1:8000 启动并自动重载 |
| [models](../../backend/app/models/__init__.py)、[repositories](../../backend/app/repositories/__init__.py)、[tasks](../../backend/app/tasks/__init__.py)、[memory](../../backend/app/services/memory/__init__.py) | 只有文档字符串 | 预留目录 |

## 输入、输出和依赖

- 启动：`create_app` 先配置控制台日志，再创建 FastAPI（文档 `/api/docs`，接口格式 `/api/openapi.json`）。随后挂上 `TraceMiddleware`（见 [调用日志](logs.md)），并以 `/api/v1` 注册路由。
- `lifespan` 启动时：
  - 登记需要脱敏的已知密钥：模型 Key、附加请求头的值，以及用 `local_keys.read_key` 读到的 BALLDONTLIE、DunkAPI、The Odds API 三个 Key；
  - 打开按天日志文件。

  退出时关闭日志文件。
- 配置（`Settings`）：前缀 `DOGHUNT_`，读 `backend/.env`，忽略多余变量。

  | 字段 | 默认或限制 |
  | --- | --- |
  | app_name | "Operation DogHunt" |
  | environment | development / test / production |
  | model_base_url、model_name | 无默认；`model_configured` 要求地址、Key、模型名都有 |
  | model_api_key | SecretStr |
  | model_reasoning_effort | none / minimal / low / medium / high / xhigh，默认 high |
  | model_extra_headers | SecretStr，JSON 文本，默认 "{}" |
  | model_timeout_seconds | 默认 120，范围 5–300 |
  | model_max_output_tokens | 默认 4096，范围 128–16384；思考 token 也计入 |
  | db_host、db_port、db_user、db_password、db_name 等 | 数据库连接，见 [storage](storage.md)；`DOGHUNT_DATABASE_URL` 可整体覆盖 |

  `get_settings` 有 lru_cache，改配置后要重启后端。
- 模型配置在 `backend/.env`；三个数据源 Key 在根目录 `.env.local`（示例见根目录 [.env.example](../../.env.example)），两者互不读取。浏览器不持有任何 Key。
- 聊天请求限制（`schemas/chat.py`）：
  - 消息 1–41 条，user 和 assistant 交替，以 user 开头和结尾；单条 1–12000 字，合计不超过 60000 字；
  - `odds_selection` 的 `event_id`、`bookmaker_key` 各 1–200 字，不允许多余字段（多余时返回 422）。
- 依赖：[前端](frontend.md) 通过 Vite 代理调用这些接口；地址清单见 [API 清单](../api/README.md)。

## 模型连接（`integrations/llm.py`）

- 请求：
  - `POST {model_base_url}/responses`，Bearer 认证，加上附加请求头，`Accept: text/event-stream`；
  - 请求体含 model、`reasoning.effort`、instructions、input、`stream: true`、`store: false`、max_output_tokens。
- 地址校验：只接受 https 或 http，不能带查询参数、片段或用户名。
- 不跟随重定向，连接超时 10 秒，总超时用 `asyncio.timeout` 控制。
- 不自动重试，也不自动降低思考强度。
- 体积上限：中转站返回普通 JSON 时最多 1MB，流式最多 2MB。
- 结果：`stream_model` 边收边产出 `TextDelta`，最后产出完整结果；`request_model` 收齐后返回。只有 `response.completed` 算完成；`error`、`response.failed`、`response.incomplete` 都转成 `ModelError`。
- 错误对照（`ModelError`，默认 502）：

  | 情况 | 返回 |
  | --- | --- |
  | 401 / 403 | 提示检查 Key |
  | 429 | 429，提示额度不足或请求太频繁 |
  | 404 | 提示检查地址是否带 `/v1` |
  | 400 | 提示检查模型名和思考强度 |
  | 超时 | 504 |
  | `httpx.InvalidURL`（如代理变量 `NO_PROXY` 含 `::1/128`） | 503，提示检查代理环境变量并重启 |
  | 其他连接错误 | 连接失败提示 |

  解析错误只给安全提示，不转发中转站原文。
- 日志：`llm.start`、`llm.first_token`、`llm.done`（首字秒数、总秒数、输入、输出和思考 token）、`llm.http`（上游状态码）、`llm.error`。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 应用、健康检查、模型状态 | 已实现；`/health` 不查外部服务或存储，`/health/storage` 查 Postgres | `create_app`、`health`、`storage_health`、`model_status` |
| 模型连接 | 已实现，流式和完整返回两种 | `llm.py` |
| 聊天、盘口与后台取数、调用日志 | 已实现，见下级子块 | [chat](chat.md)、[data-sources](data-sources.md)、[logs](logs.md) |
| 功能块框架 | 部分实现，无调用入口 | [blocks](blocks.md) |
| 数据库和持久化 | 部分实现：后端连 PostgreSQL、Alembic 空基线、存储就绪检查（[storage](storage.md)）；还没有表，Milvus 未连接 | `db/`、`migrations/`；`models`、`repositories` 仍只有文档字符串 |
| 通用任务、超时恢复、定期维护 | 未实现；只有盘口更新后的后台线程，没有持久任务和失败恢复 | `tasks`、[data-sources](data-sources.md) |
| 记忆和正式预测保存 | 未实现；对话不保存，刷新页面不能恢复 | `services/memory` |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-16 | `cd backend && uv run --locked python -m unittest discover -s tests`；集成测试见 [storage](storage.md) | 本轮运行：`Ran 208 tests / OK (skipped=4)`（新增 `test_db` 27 项，4 项集成默认跳过）；所有测试文件第一行导入 `_isolation`，不连真实库 |
| 2026-11-15 | `cd backend && uv run --locked python -m unittest discover -s tests` | 历史：`Ran 172 tests / OK`（blocks 41、chat 41、data_sources 12、logs 18、match_analysis 40、odds 20）；全部离线，不调真实模型或外部 API |
| 2026-11-15 | `uv run --locked python -m app.services.chat.prompt check` | 本轮运行：通过 |
| 2026-11-14 | 聊天流断开修复：临时撤回修复跑新增 3 项，并用 `curl` 请求 8000 和 5173 的 `/chat/stream` | 历史记录：撤回后 3 项报错，恢复后通过；带 `::1/128` 的后端 0.1 秒内返回 503 error 事件。在干净环境下真实调用 3 次（medium，选场掘金 vs 鹈鹕 FanDuel）：首字 8.2–12.0 秒，总用时 26.5–31.3 秒，输入 5106 token |
| 2026-11-11 | 真实中转与网页 | 历史记录：`gpt-6.1-sol` 返回成功，输入 246、输出 8 token；项目专用 Key 验证为输入 14358、输出 9 token（[交接](../handoff/2026-11-11-项目专用模型Key验证.md)） |
| 2026-11-11 | 项目骨架 HTTP 检查 | 历史记录：后端直连和前端转发的健康检查、`/api/docs` 均为 HTTP 200（[交接](../handoff/2026-11-11-Vue与FastAPI项目骨架.md)） |

启动命令为根目录 `make backend`，启动后可请求 `http://127.0.0.1:8000/api/v1/health`。首次运行先执行 `make setup`。

## 限制、未决事项与下一步

- `/health` 只说明应用能响应，不检查外部服务或存储；存储状态看 `/health/storage`。
- 并发限制只在单个进程内有效；盘口更新的锁和冷却也是。公网多进程部署前，需要另行设计认证和共享限流。
- 超时后中转站可能仍已计费。代码默认 120 秒、思考强度 high。旧文档记录本机 `.env` 自 2026-11-14 起为 240 秒、medium，本轮未核对。
- 运行环境里 `NO_PROXY` 含 `::1/128` 时，聊天会立即返回 503，要在去掉该项的终端里重启后端。在项目启动入口固定 NO_PROXY 的方案（见 [网络修正验证](../handoff/2026-11-14-网络修正验证与免费数据缺口.md)）尚未实施。
- [routes/README.md](../../backend/app/api/routes/README.md) 只写了 health 和 chat，[tests/README.md](../../backend/tests/README.md) 仍提到 ESPN，都已过时（本次未改）。
- 数据库接入按 [数据库与向量库接入开发文档](../plans/2026-11-15-数据库与向量库接入开发文档.md) 进行，P1、P2 已完成，下一步 P3 记忆表；生产部署、记忆和正式预测保存未实现。

## 历史

- 2026-11-11：Vue 与 FastAPI 骨架；接入中转模型（10 项测试）；清空复制来的 Key，改用项目专用配置，不从 Codex 或其他应用读取凭证。`get_settings` 缓存旧配置，曾需重载才生效。
- 2026-11-11：加 `/odds` 和 `/odds/refresh`，聊天开始读取保存的盘口（19 项测试）。
- 2026-11-13：加入 `reasoning.effort`，当时默认 xhigh，后改为 high（[交接](../handoff/2026-11-13-后端模型极高思考强度.md)）；加 `/chat/stream`。
- 2026-11-14：`httpx.InvalidURL` 不属于 `RequestError`，原来没捕获，导致流式回复已发响应头后直接断开，前端一直等到超时。修复后返回 503，并且流开始后的任何意外异常都转成 error 事件（[交接](../handoff/2026-11-14-聊天超时排查与断连修复.md)）。
- 2026-11-15：加调用日志（`lifespan`、`TraceMiddleware`、logs 路由）；聊天加服务端分析卡。本块拆成 chat、data-sources、blocks、logs 四个子块，原来按日期追加的增量小节合并为当前状态，历史保留在各子块的“历史”一节。
- 2026-11-16：数据库接入 P2：加 SQLAlchemy、psycopg、Alembic，`db/` 连接与会话，`/health/storage`，启动连库检查，测试隔离扩展到数据库与记忆开关。
