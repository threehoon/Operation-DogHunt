# 聊天与提示词｜模块子块

[返回父块](../../CODEBASE.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | chat |
| 核对日期 | 2026-11-15（Asia/Shanghai） |
| 核对范围 | `services/chat`（service、context、prompt 包）、`prompts/chat`（modules、records、eval、锁文件）、`services/match_analysis`、`/chat` 与 `/chat/stream` 路由、相关测试；前端只核对了分析卡的读取入口 |
| 实现状态 | 已实现：聊天每次只读已保存数据，服务端先算分析卡，模型解释分析卡；一次只处理一个聊天请求；没有长期记忆 |
| 验证程度 | 本轮运行验证：172 项离线测试和提示词自检通过；真实模型回归为 2026-11-15 历史记录，本轮未调用模型 |

## 负责什么

聊天把用户问题、已保存的盘口快照、赛程、伤病、整季实力和球队效率整理成一份给模型看的“背景资料”，再连同固定提示词发给模型。2026-11-15 起，后端在调用模型前先为每场比赛算一张“分析卡”：列出各项依据、给出让分和大小分倾向、信心和预计分差范围；模型只负责解释分析卡，除非用户提供新的 B 级信息（规则 D1），不推翻卡上的倾向。

边界：
- 本模块不取外部数据，只读取 [外部数据与后台取数](data-sources.md) 保存的文件。
- 与 `services/analysis`（[功能块框架](blocks.md)）无关，两者互不调用。
- 模型请求本身在 [backend.md](backend.md) 的 `integrations/llm.py`。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [routes/chat.py](../../backend/app/api/routes/chat.py) | `POST /chat`、`POST /chat/stream` | 普通回复和流式回复；`_rejected` 记 `chat.rejected` |
| [service.py](../../backend/app/services/chat/service.py) | `_inflight`、`busy`、`_build`、`chat`、`prepare_stream`、`_finish` | 并发限制、组装资料、调用模型、发事件、写存档 |
| [context/payload.py](../../backend/app/services/chat/context/payload.py) | `build_payload`、`odds_context`、`_trim`、`DETAIL_EVENT_LIMIT` | 选比赛、裁剪报价、拼各来源段落、调用分析卡 |
| [context/teams.py](../../backend/app/services/chat/context/teams.py) | `find_focus`、`Focus`、`TEAM_ALIASES`、`TEAM_ABBR`、`STAR_PLAYERS` | 从问题里认球队、缩写和球员，决定看哪几场 |
| [prompt/\_\_init\_\_.py](../../backend/app/services/chat/prompt/__init__.py) | `static_prompt`、`check`、`build`、`update_lock` | 读取并核对提示词模块，维护锁文件 |
| [prompt/\_\_main\_\_.py](../../backend/app/services/chat/prompt/__main__.py) | `python -m app.services.chat.prompt check \| lock` | 命令行自检和更新锁文件 |
| [prompts/chat/](../../backend/app/prompts/chat/README.md) | `modules/`、`records/`、`eval/`、[versions.lock.json](../../backend/app/prompts/chat/versions.lock.json) | 提示词正文、改版记录、测试用例和锁文件 |
| [match_analysis/\_\_init\_\_.py](../../backend/app/services/match_analysis/__init__.py) | `analyze`、`analyze_event` | 每场比赛算一张分析卡 |
| [card.py](../../backend/app/services/match_analysis/card.py) | `Card`、`Factor`、`Lean`、`RANK_*`、`CARD_VERSION` | 分析卡结构和依据排序 |
| [factors.py](../../backend/app/services/match_analysis/factors.py) | `strength`、`pace`、`rest`、`injury`、`movement`、`price` | 各项依据和阈值 |
| [lean.py](../../backend/app/services/match_analysis/lean.py) | `pick_side`、`confidence`、`expected_range`、`build_lean` | 选边、信心、预计范围 |
| [cache.py](../../backend/app/services/match_analysis/cache.py) | `MAX_CARDS`、`clear` | 进程内分析卡缓存 |
| [test_chat.py](../../backend/tests/test_chat.py) / [test_match_analysis.py](../../backend/tests/test_match_analysis.py) | 41 项 / 40 项 | 离线测试 |

## 输入、输出和依赖

- 输入：`ChatRequest`（字段限制见 [backend.md](backend.md)）；可选 `selection`（`event_id`、`bookmaker_key`），由比赛页选场带入。
- 读取的文件：最新盘口快照和历史快照、赛程、伤病、整季比分、球队效率，全部通过 `asyncio.to_thread` 读取；盘口状态不是 available 时只读盘口。
- 输出：`/chat` 返回 `ChatResponse(reply, model, usage)`；`/chat/stream` 返回 SSE 事件流（见下节）；每次请求写一份聊天存档（见 [调用日志](logs.md)）。
- 依赖：[backend.md](backend.md) 的 `request_model` / `stream_model`；[外部数据与后台取数](data-sources.md) 的读取函数和 `*_context`；前端读取方在 [frontend.md](frontend.md)。

## 请求流程与事件顺序

1. 路由先查忙碌：已有聊天在处理 → 429；模型未配置 → 503。
2. `_build` 读取资料、算分析卡、拼提示词。选场失效（409）或提示词无效（503）在开始流式回复前就返回 HTTP 错误。
3. 提示词正文 = 静态提示词 + “当前北京时间：…” + 外部数据说明 + `<saved_odds_snapshot>…</saved_odds_snapshot>`（资料以紧凑 JSON 写入）。北京时间固定按 UTC+8 计算，因为 Windows 没有 tzdata。
4. 流式事件，在 `async with _inflight` 内依次发出：
   - `analysis`：有分析卡时最先发，内容为 `{cards: [...]}`；
   - `delta`：每段文字一个，`{text}`；
   - `done`：`ChatResponse` 全部字段。
   - 出错时发 `error`，内容为 `{detail, status}`。ModelError 用原状态码；其他异常记 `chat.crash`（只记类型），状态 500。进入事件流后再次发现忙碌也发 `error`，状态 429。
5. `finally` 调 `_finish`：记 `chat.end`（outcome 为 done、error 或 disconnected，默认 disconnected；reply_chars），并保存存档。
6. `chat.context` 日志字段：ms、prompt_versions、prompt_sha256 前 12 位、prompt_locked、context_chars、messages、question_chars、selection、sources（盘口状态、抓取时间、是否过期、比赛数、历史文件数，以及各来源抓取时间）和 analysis（cards、analysis_ms、cache_hits、cache_misses）。

## 选哪几场比赛

- 有选场：只看这一场。比赛不在当前快照 → 409“所选比赛已不在…”；平台不在 → 409。信号按所选平台算；共识用这场所有平台，报价只留所选平台。
- 没选场：`find_focus` 从最后一条用户消息认球队（`SELECTION_MARK = "\n\n本次选场："` 之后的文字不算）。
  - 认出球队：只看相关比赛，`focus` 写 teams、matched_events、total_events，认出球员时加 players。
  - 认不出、问“全部/整张/其他/哪几场”等（reason "wide"）、或只说“今晚/明天”等时间词（reason "time"）：看整张表，取开赛最早的 15 场（`DETAIL_EVENT_LIMIT`）。超出时写 `details_limit = {covered, total}`；reason 为 none 或 time 时写 `focus_note`。
  - 没选场时，每场报价只保留分析卡让分和大小分倾向所用的平台（`_trim`）。
- 球队识别规则：
  - 30 队中英文叫法（含凯尔特、绿凯、溜马），英文按整词匹配；heat、magic、suns、kings 等常用词全小写时不算。
  - 匹配前先去掉“魔术师、热火朝天、太阳能、太阳穴、雷霆万钧、火箭筒”。
  - 大写整词缩写：30 队，加 GS、NY、SA、UTAH、WSH、PHO、BRK、NOLA，不收 NO。
  - 39 个常用球星叫法（按 2025-26 阵容手工整理）；伤病名单里有同名球员时以名单球队为准。伤病名单中唯一、4 个字母以上且不在 `COMMON_SURNAMES` 里的英文姓也能单独认出。
- 资料字段：status、provider、fetched_at、stale、message、selection、focus、focus_note、events、details_limit、analysis、market_signals，以及 schedule_rest、team_form、injuries、season_strength、team_efficiency。原始段落仍然保留，便于模型对照分析卡。
- 代码注释估算：每场明细约 4.7 千字，加 8 个平台报价约 8 千字；15 场约 12 万字。

## 分析卡怎么算

`analyze(board, events, book, signals, sections, history, stats)` 对每场比赛调 `analyze_event`，返回 `{event_id: 卡片字典}`。`signals` 为空时自己算 `market_signals`。卡片版本 `match_analysis_v1`。

- 依据行，按顺序为：实力、伤病、休息（只算让分）、让分盘口移动、让分报价、节奏、总分盘口移动、总分报价。
- 依据排序：新伤病和盘口移动 1，实力或节奏差 2，报价 3，休息 4，兜底 6。
- 主要阈值（`factors.py`）：让分差 3、总分差 4、双方至少 5 场、主场优势 2 分、盘口移动 1 分。
  - 实力：优先用整季 `model_home_margin`（来源写明实力分和 home_edge）；没有时用近期战绩分差 + 2。双方都满 5 场才能当理由。战绩和整季方向相反时加备注。
  - 节奏：优先用球队效率的预计总分，否则用双方场均总分的平均值。
  - 休息：背靠背或 4 天 3 场算疲劳；至少休 1 天且不疲劳算休息充足。本场不在赛程表里时只作参考，不当理由。
  - 伤病：一方有 `new_since_last_game` 为 True 的缺阵球员时，指向对手。False 表示已计入，null 表示未知；双方都有新伤病时抵消。待定（Day-To-Day）单独列出。数据超过 12 小时或本场不在名单里时加备注。伤病只有在来源可用且未过期时才算“已知”。
  - 盘口移动：共识变化至少 1 分；让分看主队让分是否变得更负。
  - 报价：只在选了平台时算，用 `better_for`。
- 缺数据时在 `missing` 写明，如“实力与战绩（让分）”“伤病情况未知（让分、大小分）”“让分报价”。
- 选边（`pick_side`）：按排序从前往后比；同一级两边都有理由时，取条数多的一边，相同就看下一级；都没有时按参考行或差值方向，最后默认主队或小分，并标为微弱倾向。
- 信心（`confidence`）：
  - 高：至少两类中等理由同向，其中有盘口移动、实力或节奏之一，没有反向理由，且伤病已知。
  - 只有伤病和休息时最多为中，因为盘口通常已计入这两类信息。
  - 只有报价、中等理由方向相反或微弱倾向时为低。
- 预计范围：同向的实力或节奏差按一半调整（0.5–3 分），否则按信心调整（低 0.5、中 1、高 2）。范围宽 6 分（±3）。中点必须在所选盘口线有利的一侧，否则挪到线外半分。
- 缓存：键包括卡片版本、比赛 id、平台、盘口和历史快照的抓取时间、各来源抓取时间、伤病是否过期，以及比赛 JSON 的 sha1 前 16 位。最多 64 张，LRU，只在进程内，重启后清空。
- 设计取舍：排序沿用提示词 v7 的优先级（新伤病和盘口移动排在实力前）。所以 E-012 中实力差 9.6 分指向湖人，卡片仍选掘金、信心低。要改这一点，需要同时改 `card.py` 的排序和提示词。

## 提示词与锁文件

- 模块顺序固定为 role、sources、odds、method、output。文件放在 `modules/{模块}/chat_{模块}_v{n}.md`。
- 当前启用：chat_role_v1、chat_sources_v5、chat_odds_v8、chat_method_v8、chat_output_v6；静态提示词 8491 字；锁文件登记 28 个版本文件。
- `check` 会检查：
  - 启用顺序、文件名格式、根目录没有散放文件、所在文件夹与模块一致、首行为 `# 文件名`；
  - 没有未登记文件、旧版本没有被删除、哈希一致、CHANGELOG 有对应 `### 版本名` 条目，以及 static_sha256。
- `update_lock` 不允许改已登记版本的哈希。锁缺失或提示词无效时返回 503，不调用模型；未锁定只记警告。`static_prompt` 有 lru_cache，改提示词后要重启服务。
- 改版流程见 [$chat-prompt-maintenance](../../.agents/skills/chat-prompt-maintenance/SKILL.md)，记录在 [CHANGELOG](../../backend/app/prompts/chat/records/CHANGELOG.md) 和 [issues](../../backend/app/prompts/chat/records/issues.md)（I-001 至 I-008）。
- 测试用例在 [eval_cases.md](../../backend/app/prompts/chat/eval/eval_cases.md)（E-001 至 E-013；核心为 001、002、003、006、007、008、009、010、012）。快照 `S-001.json` 是合成盘口，`D-001.json` 是 DunkAPI 格式的合成赛程和伤病。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 普通与流式聊天 | 已实现；一次只处理一个请求，忙碌返回 429 | `service.py` 的 `_inflight` |
| 服务端分析卡 | 已实现；前端在回复上方显示 | `match_analysis`、[frontend.md](frontend.md) |
| 选场与球队识别 | 已实现；球星表需每赛季手工核对 | `teams.py` 的 `STAR_PLAYERS` |
| 提示词版本锁 | 已实现 | `prompt/__init__.py` |
| 主场优势单独展示 | 未做；E-012 首跑时实力行缺实力分，已补来源，但 home_edge 仍未单列 | 2026-11-15 CHANGELOG |
| 输入 token 下降 | 未达成：原始段落仍保留，便于对照 | 执行方案 3.6 |
| 长期记忆 | 未实现，`services/memory` 只有占位 | [backend.md](backend.md) |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-15 | `cd backend && uv run --locked python -m unittest discover -s tests` | 本轮运行：172 项全部通过，其中 test_chat 41 项（流式首个事件为 analysis），test_match_analysis 40 项 |
| 2026-11-15 | `uv run --locked python -m app.services.chat.prompt check` | 本轮运行：通过 |
| 2026-11-15 | 真实模型核心回归（medium，经 `service.prepare_stream`） | 历史记录：E-001/002/003/006/007/008/009/010/012 通过；E-006 改问“我觉得应该选湖人”；E-011 未跑；low 下 E-010 和“分析全部”内容一致 |
| 2026-11-15 | 速度对比（执行方案 3.7，medium） | 历史记录：选场 E-010 首字 8.3s→6.2s、总用时 31.6s→28.5s、输入 6519→7949；low 为 2.6s/22.7s。整张表 11.6/28.8/7826→3.4/17.5/10559。网页真实 5 场（trace `3b96855dead9`）8.6s/24.3s，输入 20327、输出 1013。思考强度保持 medium |

相关记录：[执行方案](../plans/2026-11-15-聊天分析层与目录整理执行方案.md)、[交接](../handoff/2026-11-15-聊天分析卡与文档整理.md)、[设计文档](../design/聊天预测提示词设计文档.md)。

## 限制、未决事项与下一步

- 分析卡缓存和提示词缓存都只在进程内；改提示词后要重启。
- 球星表按 2025-26 阵容整理，转会后需要人工更新。
- 分析卡排序把新伤病和盘口移动放在实力前，实力差很大时也可能选另一边（见 E-012）；是否调整待定。
- E-011 尚未用真实模型跑过。
- 建议（未执行）：把 home_edge 单独写进实力行；评估是否去掉与分析卡重复的原始段落以降低输入 token。

## 历史

- 2026-11-11：聊天开始读取保存的盘口（19 项测试，[交接](../handoff/2026-11-11-独立比赛页与AI盘口联动.md)）。
- 2026-11-13：提示词迁到 `prompts/chat` 并加锁，4 个用例真实回归（[交接](../handoff/2026-11-13-聊天提示词核心回归试跑.md)）；method_v2 修 I-001。
- 2026-11-13：提速并要求必须给倾向（I-002）：改用 high、`/chat/stream`、focus 和盘口信号（[交接](../handoff/2026-11-13-聊天提速与必给倾向.md)）；output_v3 修 I-004，加 E-009（[交接](../handoff/2026-11-13-预测模板output_v3与回归试跑.md)）。
- 2026-11-14：I-008 修正选场范围，加 details_limit 和 odds_v7。
- 2026-11-15 阶段 1：prompt 拆成包、提示词按模块分文件夹，static_sha256 `c5b5be57…` 不变，127 项测试。
- 2026-11-15 阶段 2：球队识别认缩写、简称和球员名，去掉误匹配，时间词改为看整张表，132 项测试。
- 2026-11-15 阶段 3：服务端分析卡，odds_v8 / method_v8 / output_v6（静态提示词 7959→8491 字），172 项测试。踩过的坑：
  - 本场不在赛程表时，休息差曾被当作理由（热火@太阳）；
  - 微弱倾向的文字曾有误导；
  - E-012 用固定时钟读真实文件，触发了“时间异常”提示。
- 旧文档曾写“忙碌返回 409”“13 场约 36KB”，与当前代码不符：现在忙碌为 429，15 场约 12 万字。
