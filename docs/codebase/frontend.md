# 前端网站｜模块子块

[返回父块](../../CODEBASE.md) · 同级：[3D 启动页与沙盘篮球比赛](frontend-game.md) · [调用日志](logs.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | frontend |
| 核对日期 | 2026-11-15（Asia/Shanghai） |
| 核对范围 | 2026-11-15 对照源码读过：`App.vue`、`router/`、`AppShell.vue` 与侧栏 / 命令面板、`stores/`、`api/`、`types/api.ts`、`ChatView.vue` 与 `components/chat/` 全部（含新增 `AnalysisCards.vue`）、`MatchBoard.vue` / `useMatch.ts`、`useRecordScope.ts`、`SettingsView.vue`、各组件文件头，以及旧文件的引用情况。未逐行重读：`components/stats/`、记录详情、记忆页交互和 `styles/`，沿用 2026-11-13 / 11-14 记录 |
| 实现状态 | 部分实现：真实模型聊天（逐段显示、服务端分析卡）、比赛与盘口页、运行状态与本机显示偏好可用；记录、统计、记忆只有界面和虚构演示数据；正式预测保存、赛果、复盘保存和后台配置管理未实现 |
| 验证程度 | 本轮运行验证：2026-11-15 构建通过，聊天分析卡在浏览器里连真实盘口核对过。其余页面为历史验证（2026-11-13 / 11-14，模拟接口），本轮未重测 |
| Git 基准 | HEAD `03881cc`（分析卡在此提交），`frontend/` 无未提交改动 |

## 负责什么

Vue 3 显示页面，TypeScript 检查数据和代码，Vue Router 管页面地址。前端负责：内页外壳与导航、运行状态显示、真实模型聊天及本次打开网站期间的对话、比赛与盘口页、本机显示偏好；预测记录、效果统计、长期记忆三页的界面和计算也在前端，但数据目前只有演示数据。聊天在 `/`，比赛在 `/matches`；卡片选场后只把比赛与平台标识带进聊天，报价由后端读取。

不在本块：启动页 `/welcome` 与篮球比赛见 [frontend-game](frontend-game.md)；调用日志页 `/logs` 见 [调用日志](logs.md)。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [main.ts](../../frontend/src/main.ts) / [App.vue](../../frontend/src/App.vue) | `AppShell` / `Transition` / `KeepAlive include="ChatView"` | 装入外壳和当前页面，页面间短淡入；只缓存聊天页；`meta.fullscreen` 的页面不套外壳 |
| [router/index.ts](../../frontend/src/router/index.ts) | `routes` / `beforeEach` / `afterEach` | `/` 聊天、`/welcome`、`/matches`、`/records`、`/statistics`、`/memory`、`/logs`、`/settings` 与 404；首次打开先到启动页；标签页标题为“`meta.title` · DogHunt” |
| [AppShell.vue](../../frontend/src/components/layout/AppShell.vue) / [layout/](../../frontend/src/components/layout/StationRail.vue) | `StationRail` / `StatusStrip` / `CommandPalette` / `ShellBackdrop` | 外壳挂载时只读检查后端、模型配置、盘口快照；窗口重新获得焦点且距上次超过 60 秒时再查后端；Ctrl/⌘+K 命令面板；侧栏六个工位加底部“调用日志”“返回指挥室”，窄于 1000 像素只留图标，窄于 700 像素变成底部标签栏 |
| [shell/backdrop.ts](../../frontend/src/shell/backdrop.ts) | `createBackdrop` | 原生 WebGL 落雨夜景背景（不引入 three.js），关闭或不可用时为静态渐变 |
| [stores/system.ts](../../frontend/src/stores/system.ts) | `health` / `model` / `board` / `now` / `refreshBoard` | 共用运行状态与每秒时钟；快照超过 15 分钟标“待更新”；手动更新间隔 60 秒；只有 `refreshBoard` 消耗盘口额度 |
| [preferences.ts](../../frontend/src/stores/preferences.ts) / [demo.ts](../../frontend/src/stores/demo.ts) / [records.ts](../../frontend/src/stores/records.ts) | `preferences` / `demoEnabled` / `boardIsDemo` / `predictionRecords` | 本机偏好（赔率格式、默认平台、演示、背景，存 localStorage 键 `doghunt:preferences`，逐项校验）；演示开关；记录与统计共用的数据入口 |
| [oddsSelection.ts](../../frontend/src/stores/oddsSelection.ts) | `oddsSelection` / `sampleChatRequested` | 内存里的选场（比赛 id、平台 key、标签），不存报价、不持久化 |
| [client.ts](../../frontend/src/api/client.ts) / [health.ts](../../frontend/src/api/health.ts) / [odds.ts](../../frontend/src/api/odds.ts) | `getJson` / `postStream` / `TraceError` / `traceOf` / `getOdds` | GET 5 秒超时；`postStream` 读 SSE 并返回调用编号；失败时抛带编号的 `TraceError`；盘口读取 5 秒、更新 25 秒超时 |
| [chat.ts](../../frontend/src/api/chat.ts) / [types/api.ts](../../frontend/src/types/api.ts) | `getModelStatus` / `streamChat` / `analysisCards` / `AnalysisCard` / `AnalysisLean` | 聊天流式请求、模型状态核对、分析卡格式检查 |
| [ChatView.vue](../../frontend/src/views/ChatView.vue) | `send` / `newChat` / `partial` / `liveCards` / `errorTrace` | 聊天状态与发送逻辑 |
| [components/chat/](../../frontend/src/components/chat/ChatTurn.vue) | `ChatTurn` / `AnalysisCards` / `ChatComposer` / `ChatWaiting` / `ChatWelcome` / `ChatContext` / `ChatHologram` / `markdown.ts` / `types.ts` | 一条发言、分析卡、输入区、等待卡片、起始画面、右侧资料栏、装饰战术板、安全的 Markdown 排版、`Turn` 类型与 `MAX_QUESTIONS = 20` |
| [MatchesView.vue](../../frontend/src/views/MatchesView.vue) / [components/odds/](../../frontend/src/components/odds/MatchBoard.vue) | `MatchBoard` / `NextTip` / `MatchCard` / `MatchDrawer` / `LineStrip` / `useMatch.analyze` / `lineSpread` | 比赛页：筛选分组、下一场开赛、卡片、全部报价抽屉、各平台盘口分布 |
| [RecordsView.vue](../../frontend/src/views/RecordsView.vue) / [records/](../../frontend/src/components/records/RecordDrawer.vue) / [StatisticsView.vue](../../frontend/src/views/StatisticsView.vue) / [stats/](../../frontend/src/components/stats/EquityChart.vue) | `RecordDrawer` / `recordsToCsv` / `HitRing` / `OutcomeBars` / `WeeklyBars` / `EquityChart` / `equity.ts` | 记录列表、详情与 CSV；统计指标、结算分布、周表现、累计盈亏图 |
| [useRecordScope.ts](../../frontend/src/composables/useRecordScope.ts) / [RecordFilters.vue](../../frontend/src/components/records/RecordFilters.vue) | `filters` / `records` / `query` / `reset` | 记录与统计共用筛选，经地址参数跨页携带 |
| [domain/](../../frontend/src/domain/settlement.ts) | `settle` / `summarize` / `equityCurve` | 按需求文档第 13、14 节结算赢输走盘、固定 1 单位模拟盈亏、命中率与 95% 区间、ROI、覆盖率；不依赖界面 |
| [MemoryView.vue](../../frontend/src/views/MemoryView.vue) / [components/memory/](../../frontend/src/components/memory/MemoryRack.vue) | `MemoryRack` / `MemoryInspector` | 长期记忆机柜、子块详情与操作、维护记录 |
| [SettingsView.vue](../../frontend/src/views/SettingsView.vue) | `recheck` / `bookmakers` / `restore` | 运行状态、只读后台规则、本机显示偏好 |
| [demo/](../../frontend/src/demo/league.ts) | `demoBoard` / `demoRecords` / `demoMemory` / `demoTranscript` | 虚构“沙盘联赛”：十支球队、“演示平台 A–F”、盘口快照、约四个月记录、记忆块、示例对话，固定种子生成 |
| [components/common/](../../frontend/src/components/common/SideDrawer.vue) / [styles/](../../frontend/src/styles/tokens.css) | `SideDrawer` / `DemoRibbon` / `EmptyState` / `TeamMark` / `tokens.css` / `ui.css` | 焦点不外漏的抽屉、演示提示带、空状态、球队铭牌；设计变量与共用面板、按钮、表格 |
| [vite.config.ts](../../frontend/vite.config.ts) / [package.json](../../frontend/package.json) | `proxy` / `scripts` | 开发与预览时 `/api` 转到后端（默认 `http://127.0.0.1:8000`）；`build` = `vue-tsc --noEmit` + `vite build` |

## 输入、输出和依赖

- 输入：页面地址、聊天输入与按钮、选场、筛选与偏好操作。
- 输出：运行状态、聊天回答与分析卡、盘口显示、演示数据页面、CSV 导出、本机偏好。
- 依赖：[后端](backend.md) 的 `GET /api/v1/health`、`GET /api/v1/model`、`POST /api/v1/chat/stream`、`GET /api/v1/odds`、`POST /api/v1/odds/refresh` 和日志接口；后端每个响应带 `X-Trace-Id` 调用编号。配置见 [前端配置示例](../../frontend/.env.example)；真实 Key 不进前端，也不放进会公开给浏览器的 `VITE_` 变量。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 外壳与导航 | 六个工位加 `/logs`；状态条显示快照采集时间、模型配置、后端连接（点击重查，未连接时变红、原因在悬停提示）、北京时间，用演示数据时多一个标记；命令面板可跳页、切换偏好、重查连接 | `AppShell.vue`、`StatusStrip.vue`、`CommandPalette.vue` |
| 聊天：发送 | 每次由后端读最新保存快照；选场时只用该比赛该平台，用户发言下显示选场标签（发给后端的内容附 `SELECTION_MARK` 原文）。Enter 发送、Shift+Enter 换行，输入法组字（`isComposing` 或 `keyCode 229`）不发送；发出即清空，失败时问题放回输入框；等待中不能重复提交，不自动重试；一轮最多 20 次提问 | `ChatView.send`、`ChatComposer.vue`、`types.ts` |
| 聊天：逐段显示 | `streamChat` 读 SSE：`delta` 交给 `onText`，`done` 为最终结果，`error` 抛错。生成中的回答标“生成中…”、不能复制；完整完成才进入聊天记录，中途失败丢弃半截内容；用户停在底部附近（160 像素内）才跟随滚动 | `api/chat.ts`、`ChatView.followEnd` |
| 聊天：分析卡（2026-11-15 新增） | 服务端在文字之前推 `analysis` 事件。`analysisCards()`（导出，日志页存档也用）检查 `cards` 数组及每张卡的 `event_id` / `home` / `away` 和 `spread` / `total` 的 `side`、`confidence`、两元素 `expected`，任一不对整份丢弃，文字回答照常。`ChatView` 把卡存进 `liveCards`，生成中就显示；完成时随回答写入 `Turn.analysis`，失败时和半截文字一起丢弃；只有卡、还没文字时等待卡片仍在。`ChatTurn` 把卡放在 Markdown 正文上方。`AnalysisCards.vue` 每场一行：对阵（客 @ 主，中文简称）、开赛时间与平台；让分方向带让分点、大小分方向带总分线；把握标签（高 / 中 / 低三种颜色）；预计范围换成中文（“某队赢 3–8 分”“客队赢 2 到主队赢 5 分”“总分 210–224”）；把握理由；“缺失资料：…”。窄于 700 像素时范围和理由换到第二行 | `chat.ts` 的 `analysisCards` / `streamChat(…, onText, onAnalysis)`、`ChatView.vue` 的 `liveCards`、`ChatTurn.vue`、`AnalysisCards.vue`、`types/api.ts` 的 `AnalysisCard` / `AnalysisLean` |
| 聊天：其他 | 回答按 Markdown 排版，只生成文字节点和固定标签，不执行 HTML，链接只收 http/https；回答显示模型、等待秒数、token 用量；问题和回答都有复制按钮（用户发言只复制问题原文）；起始画面有三条点一下填入的问题和最近四场选场；右侧资料栏显示快照状态、当前选场报价、本轮次数与 token，窄于 1180 像素收进“本次资料”抽屉；发送失败提示后附调用编号链接 | `markdown.ts`、`ChatTurn.vue`、`ChatWelcome.vue`、`ChatContext.vue`、`TraceLink.vue` |
| 示例对话 | 演示盘口上点“交给 AI 分析”时显示一段虚构两轮问答，标“没有调用模型”，不能接着问；转到真实选场时清掉，虚构内容不进真实请求 | `useMatch.analyze`、`ChatView.vue`、`demo/transcript.ts` |
| 比赛与盘口 | 进入页面读一次本机快照；按北京时间筛选今天、明天、近 7 天（默认）、全部；“下一场开赛”带倒计时；盘口分布条只归类计数，不求平均；全部报价抽屉；卡片默认平台先看偏好，没有时用数据源第一家；“更新盘口”手动触发，60 秒冷却，成功时显示消耗的 credits；失败保留原快照并附调用编号；没有真实比赛且演示打开时用演示盘口 | `MatchBoard.vue`、`NextTip.vue`、`LineStrip.vue`、`MatchDrawer.vue`、`useMatch.ts` |
| 预测记录 | 演示记录；共同筛选（时间 7 / 30（默认）/ 90 天 / 全部、玩法、来源（默认赛前记录）、判断来源（默认系统）、策略），最近 N 天按北京时间、全部时间含待开赛；另有结果与球队搜索；详情抽屉分盘口、判断、依据、复盘（含结算算式），演示笔记只读；CSV 严格对应可见列表并带虚构标记 | `RecordsView.vue`、`RecordDrawer.vue`、`csv.ts`、`useRecordScope.ts` |
| 效果统计 | 与记录共用数据与计算：命中率与区间、净盈亏、ROI、覆盖率（并成一条仪表带）、结算分布、周表现、策略对比；累计盈亏图按结算日累计，上面一张整体，未按玩法筛选时下面再画让分、大小分两张小图，直接标出期末、最高、最低、最大回撤，悬停或 ←/→/Home/End 逐日查看，几张图同步，窄于 560 像素时读数移到图下方 | `StatisticsView.vue`、`components/stats/`、`equity.ts` 的 `toSteps` / `extremesOf` |
| 长期记忆 | 仅界面：父块机柜、子块详情，可修改（存新版本）、停用、确认候选、忘记（可撤销）、维护记录；演示内容只在本页内存 | `MemoryView.vue`、`MemoryRack.vue`、`MemoryInspector.vue` |
| 设置 | 运行状态（含后端往返毫秒）可一键重查，只读本机后端；赔率格式、默认平台、动态背景、演示数据即时生效并存 localStorage，可恢复默认；后台规则只读 | `SettingsView.recheck` / `restore`、`preferences.ts` |
| 演示数据 | `DEFAULTS.demo = 'auto'`（用户确认保留）；设置、提示带、命令面板、各页按钮共同控制；比赛页有真实数据时优先真实；关闭后记录、统计、记忆为空 | `stores/demo.ts`、`stores/records.ts` |
| 界面样式 | 暗室取向：近墨色底，冷光强调（变量仍叫 `--cyan`）、黄铜提醒（仍叫 `--amber`），圆角 2–3 像素，读数用 `--font-num`；不用外发光、渐变字、斜切色块，眉题为“序号 + 工位名”；只留承载信息的动效；全息感留在起始画面战术板、下一场开赛投影、面板四角标记和选中亮线 | `styles/tokens.css`、`ui.css`、各组件样式块 |
| 调用日志 | 见 [调用日志](logs.md) | `LogsView.vue`、`components/logs/` |
| 未接入的旧文件 | `features/reviews/`、`features/statistics/`、`data/recordPreview.ts` 不被任何路由或页面引用；`shell/countUp.ts` 的 `useCountUp` 与 `client.ts` 的 `postJson` 已导出但没有调用方 | 2026-11-15 全文搜索引用 |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-15 | `npm --prefix frontend run build`（`vue-tsc --noEmit` + `vite build`） | 本轮重跑通过，`295 modules transformed`，HEAD `03881cc`，无未提交前端改动 |
| 2026-11-15 | 聊天分析卡：浏览器连运行中的后端与真实盘口，发送“分析全部比赛” | 发送后约 0.11 秒出现 5 张分析卡，早于模型文字；控制台无报错；`/logs` 的聊天存档页签显示同样的卡。调用了一次真实模型；格式错误的 `analysis` 数据被丢弃这一支没有在浏览器里触发过，只看了代码 |
| 2026-11-15 | 调用日志页（292 modules，浏览器连 8000 后端） | 通过，详情见调用日志子块 |
| 2026-11-14 | 累计盈亏图重做：`npm --prefix frontend run build`；无头 Edge 打开 `/statistics`（后端一律不通，用演示数据） | 构建通过（272 modules）；默认期末 +6.34，与“模拟净盈亏”一致，且等于让分 +2.93 加大小分 +3.41；悬停合计等于两部分之和；只看让分时一张图；键盘 ← 逐日移动；1440 / 1280（2 倍）/ 390 宽无横向溢出、无 pageerror。未用读屏软件，未在触屏实机试拖动 |
| 2026-11-14 | 界面样式统一：构建；无头 Edge 加模拟接口截图 | 构建通过（268 modules）；六个内页、404、启动页 1440 宽逐页看过，部分页另看 900 / 390 宽，无溢出、无 pageerror；未重测交互，未连真实后端 |
| 2026-11-13 | 聊天逐段显示：构建；浏览器连临时模拟后端（只推固定文字） | 构建通过；先等待卡片，约 1.5 秒后出现“生成中…”，文字 20 → 52 → 86 → 120 递增，完成后进入记录；模拟中途 error 时半截回答消失、问题回填；未测 Safari 与手机 |
| 2026-11-13 | Enter 发送与用户发言复制：`type-check`；浏览器拦截聊天请求 | 通过；Shift+Enter、`isComposing`、`keyCode 229`、纯空格不发请求；复制不含选场；未在真实中文输入法与 Safari 实机测 |
| 2026-11-13 | 深色三页切换：构建（268 modules）；Chromium 模拟接口 | 默认 95 条 / +6.34 单位，让分 48 条 / +2.93，CSV 48 行带虚构标记，跨页一致；结算状态、抽屉 Tab/Esc、图表方向键、偏好保留、关闭演示通过；1440 / 390 宽无溢出 |
| 2026-11-13 | PR #2 审查：构建（255 modules）；Chromium | 聊天切页保留、HTML 不执行、所选平台请求正确、示例历史不混入真实请求；未验证真实接口与手机 |
| 2026-11-11 | 骨架与首页盘口、独立比赛页：`npm run build`；浏览器 | 构建通过（52 → 66 modules）；健康检查经转发 HTTP 200；真实点击更新盘口得到 14 场、消耗 2 credits；选凯尔特人对猛龙 DraftKings 后模型回答 `-7.5 / +7.5`、总分 `216.5` 与页面一致；见 [独立比赛页交接](../handoff/2026-11-11-独立比赛页与AI盘口联动.md) |

启动与构建入口为根目录 `make frontend`、`make build`，前置依赖见 [README](../../README.md)。

## 限制、未决事项与下一步

- “后端已连接”只代表健康检查成功；“模型已配置”不是在线探测，打开页面不会调用付费模型。
- 模型读的是保存的快照，不会自动更新外部盘口；没有完整赛程、实时比分或伤病查询。分析卡由后端按固定规则计算，前端只显示，不做判断。
- 对话只在本次打开网站期间保留（切页保留，刷新丢失）；没有停止生成。
- 记录、统计、记忆只用虚构数据；正式预测保存、赛果接口、复盘保存、多人隔离未实现。模拟金额按每次 1 单位，不代表真实资金。设置只保存本机显示偏好。
- 开发预览转发不等于生产部署方案。界面未经负责人或美术评审；未在手机实机、Safari、Firefox 上全面验证；没有 Bahnschrift 时读数字体退回正文字体。
- 手机宽度下侧栏底部区域隐藏，`/logs` 只能输入地址进入。
- `TeamMark.vue` 文件头注释仍写“斜切牌子”，样式已是左侧队色的平铭牌；属注释过时，未改代码。
- 建议（未执行）：旧文件 `features/`、`data/recordPreview.ts`、`useCountUp`、`postJson` 的去留由负责人决定。

## 历史

- 2026-11-11：项目骨架、真实模型聊天接入、首页盘口（后改为独立 `/matches` 页，卡片选场带进聊天，报价由后端从磁盘读取）。项目独立模型 Key 已验证（见 [专用 Key 交接](../handoff/2026-11-11-项目专用模型Key验证.md)）。
- 2026-11-12：记录、统计、设置先后做过浅色版（个人选场复盘、复盘档案、浅色统计、设置按钮框架，交接见 `docs/handoff/2026-11-12-*`）；同日内页整体重做为延续启动页的深色控制台，PR #2 合并时三页先采用远端浅色版。删除了 `FeaturePlaceholder.vue`、`BackendStatus.vue`、`useBackendHealth.ts`，连接检查并入 `stores/system.ts`；`index.html` 加深色底防闪白。当时发送方式是 Ctrl/⌘+Enter（考虑发送要花钱）。
- 2026-11-13：用户要求三页切到深色另一版；现有历史里没有完整的另一版，用保留的组件、数据和领域计算重建入口，不宣称逐字恢复。外壳移除 `lightPage`。聊天改为逐段显示、Enter 发送、用户发言加复制按钮。
- 2026-11-14：界面样式统一（使用者要求“保留全息感，去掉过重的 AI 味和浮躁感，有运筹帷幄的感觉”，取向本地决定）。背景速度降到四成、灯火压暗，等待卡片去掉传球动画。累计盈亏图重做：原来三条线叠在一起不悬停读不到数，配色校验两条线色差只有 14.3（下限 15）；逐条画会因同日几场先后顺序画出假尖刺，所以改按结算日累计；小图不沿用整体纵轴，否则会被压成平线。
- 2026-11-15：调用日志页上线（`TraceError`、错误提示附编号链接）；聊天加服务端分析卡；本块拆出启动页与比赛、调用日志两个子块。
