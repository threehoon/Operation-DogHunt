# 外部数据与后台取数｜模块子块

[返回父块](../../CODEBASE.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | data-sources |
| 核对日期 | 2026-11-15（Asia/Shanghai） |
| 核对范围 | `services/odds`（board、schedule、team_form、injuries、season、team_stats、signals）、`integrations`（balldontlie、dunkapi、local_keys、odds）、`routes/odds.py`、相关测试；The Odds API 取数脚本见 [odds.md](odds.md)，本次未核对 |
| 实现状态 | 已实现：网页更新盘口后在后台依次更新赛程、伤病、球队效率和整季比分；只用免费额度，按 NBA 常规赛整理 |
| 验证程度 | 本轮运行验证：离线测试通过（test_odds 20 项、test_data_sources 12 项）；真实取数为 2026-11-13 至 11-14 的历史记录，本轮未调用外部 API |

## 负责什么

本模块负责后端读取和更新外部数据，并把它们整理成聊天和分析卡能直接用的字段：
- 盘口：网页触发 The Odds API 取数并保存快照（脚本本身见 [odds.md](odds.md)），再从历史快照算盘口信号。
- 赛程与比分：BALLDONTLIE 免费版，用于休息天数和近期战绩。
- 整季实力：BALLDONTLIE 整季比分，按比分算实力分。
- 伤病和球队效率：DunkAPI 免费版。

数据都存在 `data/` 下的带时间目录里，不覆盖旧文件，并且已被 Git 忽略（`data/odds`、`schedule`、`injuries`、`season`、`team_stats`、`dunkapi`）。聊天每次只读取最新一份，见 [聊天与提示词](chat.md)。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [routes/odds.py](../../backend/app/api/routes/odds.py) | `GET /odds`、`POST /odds/refresh` | 读取盘口表；手动更新（必须是 `application/json`，否则 415） |
| [board.py](../../backend/app/services/odds/board.py) | `build_board`、`read_board`、`refresh_board`、`start_extras` | 盘口表、更新盘口、启动后台取数 |
| [integrations/odds.py](../../backend/app/integrations/odds.py) | `odds_tool`、`PROJECT_ROOT` | 把 `scripts/fetch_nba_odds.py` 当模块加载 |
| [local_keys.py](../../backend/app/integrations/local_keys.py) | `read_key` | 读 Key：环境变量优先，其次根目录 `.env.local` |
| [balldontlie.py](../../backend/app/integrations/balldontlie.py) | `fetch_games`、`fetch_season_games`、`_wait_turn`、`ScheduleError` | 赛程和比分接口，含限速 |
| [dunkapi.py](../../backend/app/integrations/dunkapi.py) | `CREDITS`、`MONTHLY_BUDGET`、`DunkError` | DunkAPI 调用和本地额度账本 |
| [schedule.py](../../backend/app/services/odds/schedule.py) | `refresh_schedule`、`load_schedule`、`rest_context` | 赛程、比分和休息天数 |
| [team_form.py](../../backend/app/services/odds/team_form.py) | `team_summary`、`form_context` | 近 30 天战绩 |
| [injuries.py](../../backend/app/services/odds/injuries.py) | `refresh_injuries`、`injury_context`、`new_since` | 伤病名单和“赛后新增” |
| [season.py](../../backend/app/services/odds/season.py) | `refresh_season`、`ratings`、`strength_context` | 整季比分和实力分 |
| [team_stats.py](../../backend/app/services/odds/team_stats.py) | `refresh_team_stats`、`projected_total`、`efficiency_context` | 球队效率和预计总分 |
| [signals.py](../../backend/app/services/odds/signals.py) | `market_signals`、`load_history` | 共识、最优报价和盘口移动 |
| [test_odds.py](../../backend/tests/test_odds.py) / [test_data_sources.py](../../backend/tests/test_data_sources.py) | 20 项 / 12 项 | 离线测试 |

## 输入、输出和依赖

- 输入：根目录 `.env.local` 或环境变量里的 `THE_ODDS_API_KEY`、`BALLDONTLIE_API_KEY`、`DUNKAPI_API_KEY`（示例见 [.env.example](../../.env.example)）。`read_key` 会去掉引号；`YOUR_API_KEY`、`YOUR_KEY` 等占位值视为未配置；值里有空白时报错。读到的 Key 会登记为需要脱敏的内容。
- 输出：`data/odds`、`data/schedule`、`data/injuries`、`data/season`、`data/team_stats` 下的快照，`data/dunkapi/usage.json` 额度账本；供聊天使用的 `*_context` 字段。
- 依赖：httpx；日志和后台任务编号见 [调用日志](logs.md)；接口地址统一记录在 [API 清单](../api/README.md)；使用说明见 [API 取数使用说明](../guides/API取数使用说明.md)。

## 盘口表与更新流程

- `build_board` 只保留 basketball_nba 的让分和大小分，并且只要结果完整的报价。
- `read_board` 只读最新一份快照，坏了也不退回旧快照。状态分为 unavailable、empty、available；超过 900 秒（`STALE_SECONDS`）标为过期。
- `refresh_board` 的返回：
  - 正在更新（锁被占用）→ 409；距上次不到 60 秒 → 429；Key 读取出错 → 503。
  - 取数失败 → 502；格式或保存出错 → 502，记 `odds.error`，旧快照保留。
  - 记 `ext.call` 时不写 URL，因为 The Odds API 的 Key 在查询参数里。成功记 `odds.saved`，然后调 `start_extras`。
- `start_extras`：用 `_extras_lock` 防止重复，上一轮没跑完就跳过。它开一个守护线程 `odds-extras`，带新的后台任务编号（父编号是本次请求）。步骤依次为：
  - `task.start`；
  - 赛程、伤病、球队效率、整季比分（各用 `run_step`）；
  - `task.end`。

  这些步骤不阻塞接口返回；前端的更新请求 25 秒超时。某一步结果含“未更新”算失败，含“跳过”算跳过；任何意外都不会让已保存的盘口变成失败。

## 各来源

**BALLDONTLIE**（`https://api.balldontlie.io/v1/games`）
- 免费版每分钟 5 次。`_wait_turn` 按 61 秒最多 5 次排队等待，各来源共用，不报错。
- 每页 100 条，按 next_cursor 翻页；赛程最多 4 页，整季最多 15 页，超出就报错且不保存。不跟随重定向，超时 10 秒。
- `Authorization` 头直接放 Key。401 和 429 有专门提示。每页记一条 `ext.call`。
- `fetch_games(key, dates)` 用 `dates[]`；`fetch_season_games` 用 `seasons[]`（赛季开始年份）加起止日期。

**DunkAPI**（`https://mcp.dunkapi.com/v1/{工具名}`，POST，`x-api-key` 头，超时 20 秒）
- 只允许三个工具：get_team_stats 5 点、get_injuries 2 点、get_standings 2 点。
- 本地账本 `data/dunkapi/usage.json`，每月（UTC）上限 450 点，在每次请求前先记账。账本损坏时拒绝请求；用 `os.replace` 原子写入。
- 失败不退点、不重试。400、401、402、429 有专门提示。`ext.call` 记点数。使用条款摘要在模块文档字符串里。

**赛程**（`schedule.py`）
- 日期按美东固定 UTC-5。需要的日期：每场比赛当天和前 5 天，加今天和前 30 天。
- `normalize_games`：双方都要能对上 30 队，跳过延期比赛；已结束的比赛（status_state 为 final，或旧格式 Final）带比分。
- 保存到 `data/schedule/<%Y%m%dT%H%M%SZ>-<8位十六进制>/schedule.json`，精确到秒，用真实当前时间。记录含 provider、fetched_at、dates、form_from、games。
- `load_schedule` 只读最新一份，损坏返回 None。
- `rest_context` 字段：
  - rest_days（0 为背靠背）、back_to_back、rest_days_at_least（前 5 天都没比赛时为 5）；
  - games_in_4_days（含本场）、previous_game、listed_in_schedule、not_covered。

**近期战绩**（`team_form.py`）
- `team_summary`：场数、胜负、场均分差、场均得失分、场均总分、主客场、近 10 场、上一场。
- `form_context`：附窗口说明和 `home_minus_away_avg_margin`。旧格式赛程没有比分时为 unavailable。

**伤病**（`injuries.py`）
- 调 get_injuries `{"league":"nba"}`；同一份数据至少隔 4 小时才更新，超过 12 小时算过期。
- 缩写换算：GS→GSW、NO→NOP、NY→NYK、SA→SAS、UTAH→UTA、WSH→WAS。
- 每名球员保留 player_id、player、position、status、injury、detail、return_date，缺阵球员排前面。一名球员都对不上球队时报错。
- `carry_listing` 沿用上一份的 listed_since 和 listed_after。
- 目录名精确到微秒（`%Y%m%dT%H%M%S%fZ`），记录带 report_date。
- `new_since`：
  - listed_after 晚于上一场比赛的美东日期 → True；
  - listed_since 早于上一场 → False；
  - 其他情况 → None。
- `injury_context` 输出 stale、note、listed（本场不在名单时为 False）、last_game、new_since_last_game。

**整季实力**（`season.py`）
- 赛季从 8 月算起；只用常规赛。同一份数据至少隔 6 小时才更新。
- 第一次取整季；之后从最后一个有比分日期的前 3 天取到今天，增量更新。目录名精确到微秒。记录含 season、season_type、through、games。
- `ratings`：已结束比赛少于 30 场时返回 None。
  - 用 SRS 式迭代（最多 500 轮，每轮走半步）；home_edge 为主队平均净胜分。
  - 每队输出 games、wins、losses、avg_margin、rating、schedule_strength、rank、last_5。
- `strength_context`：比赛太少时写 too_few_games；双方都满 5 场时给 `model_home_margin`。

**球队效率**（`team_stats.py`）
- 调 get_team_stats `{season, season_type: regular}`，赛季用结束年份。同一份数据至少隔 20 小时更新，超过 36 小时算过期。目录名精确到微秒。名称映射里单独处理了 LA Clippers。
- 数值：场数、胜负、得失分、pace、进攻、防守和净效率、助攻、篮板、失误；比率 efg_pct、tov_pct、oreb_pct、ft_rate、three_rate、opp_efg_pct 换成百分数。
- `projected_total`：回合数取双方 pace 平均；每队得分 = 回合数 × (本队进攻效率 + 对手防守效率) / 200。双方都满 5 场时给预计值。

**盘口信号**（`signals.py`）
- 最多读 60 份历史快照，按路径缓存。让分统一用主队让分。
- 每场输出：
  - 共识中位数和范围；
  - 主队、客队、大分、小分的最优报价及平台；
  - 选了平台时的 `diff_vs_consensus` 和 `better_for`；
  - 盘口移动：快照数、首末抓取时间、共识和所选平台的首末值。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 网页更新盘口 | 已实现，有 60 秒冷却和并发锁 | `board.py` 的 `refresh_board` |
| 后台补充数据 | 已实现，不阻塞接口；四步各自记录结果 | `start_extras`、`applog.run_step` |
| 赛程、休息、近期战绩 | 已实现，只收 30 队之间、未延期的比赛 | `schedule.py`、`team_form.py` |
| 伤病和赛后新增 | 已实现；第一份快照的“新增”全为 null | `injuries.py` 的 `new_since` |
| 整季实力分 | 已实现，只用比分，不含阵容变化 | `season.py` 的 `ratings` |
| 球队效率 | 已实现，是整季累计值，不反映近期变化 | `team_stats.py` |
| DunkAPI 额度 | 本地账本，不知道别处的用量 | `dunkapi.py` |
| 球员数据、首发、阵容 | 未接入 | — |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-15 | `cd backend && uv run --locked python -m unittest discover -s tests` | 本轮运行：172 项通过，其中 test_odds 20 项（盘口、战绩、伤病、信号、赛程），test_data_sources 12 项（整季实力、球队效率、DunkAPI、聊天资料）；全部离线，不调外部 API |
| 2026-11-13 | BALLDONTLIE 真实取数 | 历史记录：12 个日期 87 场，对上 13 场盘口比赛；确认按美东日期 |
| 2026-11-14 | PR #6 审核时真实取数 | 历史记录：226 场，其中 174 场有比分；修正 D-001 的固定时钟 |
| 2026-11-14 | 整季比分与 DunkAPI 球队效率 | 历史记录：183 场有比分，30 队全部对上，用 12 点额度 |
| 2026-11-14 | 伤病改用 DunkAPI | 历史记录：28 队 100 名球员，与 ESPN 结果一致；账本 14 点 |

## 限制、未决事项与下一步

- 实力分只用比分；球队效率是整季累计值；两者都不反映伤病和阵容变化。
- DunkAPI 账本只记本项目的调用；其使用条款未全部核对。
- 伤病至少隔 4 小时更新，临场变化可能晚到。
- 后台步骤的结果只写进日志，可在 [调用日志](logs.md) 页面按后台任务查看。
- `routes/README.md` 和 `tests/README.md` 仍提到 ESPN 等旧内容，与代码不符（本次未改）。

## 历史

- 2026-11-13：接入 BALLDONTLIE 免费赛程。改用 `dates[]` 而不用日期范围，是因为日期范围会把远期比赛一起拉回来（288 场、3 页）。
- 2026-11-14：加近期战绩和 ESPN 伤病（[交接](../handoff/2026-11-14-近期战绩与ESPN伤病接入.md)）；PR #6 审核（[交接](../handoff/2026-11-14-PR6审核与合并准备.md)）。
- 2026-11-14：加整季实力分和 DunkAPI 球队效率（[交接](../handoff/2026-11-14-整季实力分与DunkAPI球队效率.md)）。没用 ESPN 批量统计（Disney 条款问题，暂停），也没用 SportsDataverse。
- 2026-11-14：伤病来源换成 DunkAPI（[交接](../handoff/2026-11-14-伤病来源换成DunkAPI.md)），原因是 I-007 和 ESPN 条款未核实；`espn.py` 随后删除。
- 旧文档的目录时间格式与当前代码不符：伤病、整季、球队效率目录精确到微秒；只有赛程精确到秒且用真实当前时间。
