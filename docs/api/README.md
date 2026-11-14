# API 地址清单

API 可以理解成程序之间的“服务窗口”：地址决定去哪里，请求方法和参数决定办什么事。本目录集中记录 Operation DogHunt 实际使用的接口，避免改了代码却漏改说明。

核对日期：2026-11-15（Asia/Shanghai）。依据：当前工作区源码（含未提交改动），Git HEAD `ed9c1cf`。本次只核对实现和配置位置，未调用供应商，不代表接口当前联网可用。

## 外部服务

Base URL 是服务的基础地址，Endpoint 是最终请求的具体地址。表中的 `{变量}` 表示需要从配置读取或拼接，不能原样复制访问。

| 服务 / 用途 | 方法与地址 | 地址由哪里决定 | 使用状态 |
| --- | --- | --- | --- |
| 模型中转：聊天与功能块模型适配层 | `POST {DOGHUNT_MODEL_BASE_URL 去除末尾 /}/responses` | [Settings.model_base_url](../../backend/app/core/config.py)，由环境变量或 `backend/.env` 的 `DOGHUNT_MODEL_BASE_URL` 配置；[llm.stream_model](../../backend/app/integrations/llm.py) 拼接 | 聊天在用；功能块框架可调用，但尚无业务入口 |
| The Odds API：NBA 赛前让分、大小分 | `GET https://api.the-odds-api.com/v4/sports/basketball_nba/odds/` | [fetch_nba_odds.py](../../scripts/fetch_nba_odds.py) 的 `ENDPOINT`；后端通过 [integrations/odds.py](../../backend/app/integrations/odds.py) 复用 | 网页手动刷新和独立脚本在用 |
| BALLDONTLIE：近期赛程、比分与整季比分 | `GET https://api.balldontlie.io/v1/games` | [balldontlie.py](../../backend/app/integrations/balldontlie.py) 的 `ENDPOINT` | 更新盘口后的后台补充数据；未配置 Key 则跳过 |
| DunkAPI：伤病 | `POST https://mcp.dunkapi.com/v1/get_injuries` | [dunkapi.py](../../backend/app/integrations/dunkapi.py) 的 `BASE_URL` + 工具名；[injuries.py](../../backend/app/services/odds/injuries.py) 调用 | 更新盘口后的后台补充数据；未配置 Key 则跳过 |
| DunkAPI：球队节奏、攻防效率等 | `POST https://mcp.dunkapi.com/v1/get_team_stats` | 同一 `BASE_URL`；[team_stats.py](../../backend/app/services/odds/team_stats.py) 调用 | 更新盘口后的后台补充数据；未配置 Key 则跳过 |
| DunkAPI：排名 | `POST https://mcp.dunkapi.com/v1/get_standings` | 同一 `BASE_URL`；`CREDITS` 允许名单 | 仅适配层允许调用，当前没有业务调用入口 |

想在一份文档里查看模型真实地址和其他 API 的完整地址，请打开本机完整清单 [local-addresses.local.md](local-addresses.local.md)；该文件同时收录中转、The Odds API、BALLDONTLIE、DunkAPI 与内部接口，被 Git 忽略，其他人克隆仓库后不会自带。共享文档里的 `https://relay.example` 是脱敏占位符，不能用于连接。项目不读取 Codex 或其他应用的模型配置。

## 参数、认证和触发方式

认证信息像“通行证”，只记录放在哪里，不把真实 Key 写进文档或请求示例。

| 服务 | 关键参数 | 认证与配置位置 | 何时请求 |
| --- | --- | --- | --- |
| 模型中转 | JSON：`model`、`reasoning`、`instructions`、`input`、`stream=true`、`store=false`、`max_output_tokens` | `Authorization: Bearer …`；`DOGHUNT_MODEL_API_KEY`、可选 `DOGHUNT_MODEL_EXTRA_HEADERS`，环境变量优先，其次 `backend/.env`；[示例](../../backend/.env.example) | 发起聊天时；同一个 `/responses` 提供流式和收齐后返回两种调用方式 |
| The Odds API | Query：`regions=us`、`markets=spreads,totals`、`oddsFormat=decimal`、`dateFormat=iso`、`commenceTimeFrom` | Query 中的 `apiKey`；`THE_ODDS_API_KEY`，环境变量优先，其次根目录 `.env.local`；[示例](../../.env.example) | 点击“更新盘口”或手动执行脚本；不要复制带 Key 的完整 URL |
| BALLDONTLIE | 近期：`dates[]`；整季：`seasons[]`、可选 `start_date` / `end_date`；分页：`per_page=100`、`cursor` | `Authorization: …`，没有 `Bearer` 前缀；`BALLDONTLIE_API_KEY`，环境变量优先，其次根目录 `.env.local` | 盘口成功保存后后台更新；整季比分约 6 小时内不重复；两类取数共用限速器 |
| DunkAPI | 伤病 JSON：`{"league":"nba"}`；效率 JSON：`{"season":赛季年份,"season_type":"regular"}` | `x-api-key: …`；`DUNKAPI_API_KEY`，环境变量优先，其次根目录 `.env.local` | 盘口成功保存后后台更新；伤病约 4 小时、效率约 20 小时内不重复 |

上表间隔来自项目缓存规则；DunkAPI 本地账本上限为每月 450 credits，允许名单记录伤病 2、球队效率 5、排名 2 credits。这些是当前代码的预算约束，不代表已重新核实供应商最新套餐。维护文档无需刷新真实数据或消耗模型额度。

## 网页访问本项目后端

浏览器先访问本项目后端，再由后端访问外部服务；浏览器不持有供应商 Key。

开发时，浏览器在 `http://127.0.0.1:5173` 请求 `/api/...`，Vite（启动前端页面的工具）将它转发给后端。默认目标是 `http://127.0.0.1:8000`，可用 `frontend/.env.local` 的 `BACKEND_PROXY_TARGET` 覆盖，见 [vite.config.ts](../../frontend/vite.config.ts) 和 [示例配置](../../frontend/.env.example)。本地 `preview` 使用 `4173` 端口和相同转发规则；正式部署的代理地址尚未在这里配置。

下表写出默认后端直连完整地址；经开发网页访问时，把 `8000` 换成 `5173`，路径保持不变。

| 方法 | 默认完整地址 | 用途 / 是否请求外部服务 | 源码 |
| --- | --- | --- | --- |
| `GET` | `http://127.0.0.1:8000/api/v1/health` | 检查后端能否响应；不检查供应商连通 | [health.py](../../backend/app/api/routes/health.py) |
| `GET` | `http://127.0.0.1:8000/api/v1/model` | 检查模型配置是否齐全；不调用模型 | [chat.py](../../backend/app/api/routes/chat.py) |
| `POST` | `http://127.0.0.1:8000/api/v1/chat` | 等完整回答后返回；调用模型，可能计费 | 同上 |
| `POST` | `http://127.0.0.1:8000/api/v1/chat/stream` | 逐段返回回答；调用模型，可能计费 | 同上 |
| `GET` | `http://127.0.0.1:8000/api/v1/odds` | 只读本地盘口快照；不刷新供应商 | [odds.py](../../backend/app/api/routes/odds.py) |
| `POST` | `http://127.0.0.1:8000/api/v1/odds/refresh` | 请求新盘口并后台补充赛程、伤病、球队效率和整季比分；可能消耗额度 | 同上、[board.py](../../backend/app/services/odds/board.py) |
| `GET` | `http://127.0.0.1:8000/api/v1/logs/traces` | 调用日志列表（Query：`days` 1–14、`kind`、`problems_only`、`q`、`cursor`、`limit`）；只读本机日志文件，只接受本机请求，这里的请求不记日志 | [logs.py](../../backend/app/api/routes/logs.py) |
| `GET` | `http://127.0.0.1:8000/api/v1/logs/traces/{trace_id}` | 一次调用的全部日志、触发的后台任务和聊天存档；编号格式 12 位十六进制，后台任务带 `task-` 前缀 | 同上 |
| `GET` | `http://127.0.0.1:8000/api/docs` | 自动生成的接口说明页面 | [main.py](../../backend/app/main.py) |
| `GET` | `http://127.0.0.1:8000/api/openapi.json` | OpenAPI：供程序读取的接口格式说明 | 同上 |

所有经过后端的响应都带 `X-Trace-Id` 响应头（调用编号，由 [trace_middleware.py](../../backend/app/core/trace_middleware.py) 生成，`/api/v1/logs` 除外），前端报错提示据此链接到 `/logs?trace=编号` 页面。接口总前缀由 `main.create_app` 的 `/api/v1` 决定，具体路由由 [api/router.py](../../backend/app/api/router.py) 注册。前端调用位置：[client.ts](../../frontend/src/api/client.ts)、[chat.ts](../../frontend/src/api/chat.ts)、[health.ts](../../frontend/src/api/health.ts)、[odds.ts](../../frontend/src/api/odds.ts)。当前没有单独暴露的伤病、球队效率、正式预测、长期记忆或统计后端 API，不要按页面名称猜造地址。

## 地址变更时怎样同步

调用项目 Skill：`$api-address-maintenance`，入口为 [SKILL.md](../../.agents/skills/api-address-maintenance/SKILL.md)。[AGENTS.md](../../AGENTS.md) 已要求 AI 改动 API 地址时使用它。

| 变更内容 | 应同步核对的位置 |
| --- | --- |
| 模型中转根地址或 `/responses` 拼接方式 | `backend/.env` / 环境变量、`config.py`、`llm.py`、本清单、本机地址文件；按需要核对配置示例与 README |
| 体育供应商域名、版本、路径或工具名 | `scripts/fetch_nba_odds.py`、对应 `integrations/` 及 `services/odds/` 调用方、本清单；有行为变化再更新相关代码地图 |
| 后端路由、总前缀、接口说明地址 | `main.py`、`api/router.py`、`api/routes/`、`frontend/src/api/`、本清单与 README 中的使用示例 |
| 后端主机、端口或前端代理目标 | 实际启动配置、`frontend/vite.config.ts`、`frontend/.env.local`、示例配置、本清单与 README；区分默认值和本机覆盖值 |
| 新增、停用或替换供应商 | 更新“外部服务”状态及配置入口；同步检查在用调用方，历史交接保留原貌 |

Skill 是 AI 执行任务时遵循的维护流程，不是后台监听器。手动改地址后，可说“用 `$api-address-maintenance` 核对并同步 API 文档”；供应商自行改地址也不会被自动发现，需在收到通知或排查时核对。

## 已停用与未接入

- ESPN：伤病来源已被 DunkAPI 替换，`integrations/espn.py` 已从当前工作区删除，当前没有在用地址；历史文档中的 ESPN 记录不是现行配置。
- SportsDataverse、NBA CDN、API-Sports 等只在数据源探索中出现，当前没有业务接入，不纳入在用清单。
- Codex 内置搜索和其他应用的 API 不属于本项目运行依赖。

## 维护记录

| 日期 | 变化 | 依据与验证边界 |
| --- | --- | --- |
| 2026-11-15 | 新增调用日志两个只读接口与 `X-Trace-Id` 响应头 | 本机后端实测返回响应头，两个接口浏览器读取正常；没有新增或改动外部服务地址 |
| 2026-11-15 | 本机文件补齐全部外部与内部 API 地址，Skill 要求两份清单同步 | 按用户补充要求合并查看入口，模型原有配置记录保留；未调用接口 |
| 2026-11-15 | 建立统一清单、项目维护 Skill 和本机地址记录 | 核对 4 类外部服务、6 个业务接口和 2 个接口说明地址；静态核对，不调用供应商 |
