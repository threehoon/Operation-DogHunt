# 功能块框架｜模块子块

[返回父块](../../CODEBASE.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | blocks |
| 核对日期 | 2026-11-15（Asia/Shanghai） |
| 核对范围 | `services/analysis`（registry、checks、rules、assemble、runner、report）、`prompts/blocks`、`prompts/shared`、`tests/test_blocks.py`；这些文件最后一次代码提交为 73afa11（2026-11-13），之后未变 |
| 实现状态 | 部分实现：框架能读取、自检、拼装、检查并纠正一次；只有草稿实力块（样板 strength_v2）；没有接口、脚本或任务调用它，结果不落盘 |
| 验证程度 | 本轮运行验证：test_blocks 41 项离线测试通过（全部用模拟模型）；从未用真实模型跑过 |

## 负责什么

“功能块”是把单场预测拆成小任务的框架：每个块只看自己的几项指标，让模型输出固定格式的 JSON，再由程序分四层检查（L1 文件自检、L2 输入、L3 格式、L4 业务规则）。设计见 [功能块提示词与校验开发文档](../design/功能块提示词与校验开发文档.md)，思路见 [功能块设计思路](../design/功能块设计思路.md)。

它与聊天里的分析卡（`services/match_analysis`，见 [聊天与提示词](chat.md)）是两套东西，互不调用。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [registry.py](../../backend/app/services/analysis/registry.py) | `load_blocks`、`load_block`、`load_shared`、`BlockDefinitionError`、`BlockDefinition.markets`；`SHARED_FILES`、`REQUIRED_SECTIONS` | 读取块定义并做 L1 自检 |
| [checks.py](../../backend/app/services/analysis/checks.py) | `prepare_input`、`check_reply`、`parse_reply`、`PacketError`、`GAME_FIELDS` | L2 输入检查，L3 格式检查，再调 L4 |
| [rules.py](../../backend/app/services/analysis/rules.py) | `MARKETS`、`market_errors`、`RULES`、`BlockRules`、`common_warnings`、`insufficient_output` | L4 业务规则和“数据不足”输出 |
| [assemble.py](../../backend/app/services/analysis/assemble.py) | `assemble`、`correction_message` | 拼装指令和数据包，生成纠正消息 |
| [runner.py](../../backend/app/services/analysis/runner.py) | `run_block`、`BlockRun`、`Attempt` | 跑一个块：调用、检查、最多纠正一次 |
| [report.py](../../backend/app/services/analysis/report.py) | `CheckReport`、`Issue`、`status_of` | 检查结果和状态 |
| [prompts/blocks/](../../backend/app/prompts/blocks/strength_v2.md) | `strength_v1`、`strength_v2`（各有 `.md` 和 `.schema.json`） | 实力块草稿 |
| [prompts/shared/](../../backend/app/prompts/shared/glossary.md) | `glossary.md`、`rules.md` | 所有块共用的术语和规则 |
| [prompts/README.md](../../backend/app/prompts/README.md) | — | 提示词目录说明 |
| [test_blocks.py](../../backend/tests/test_blocks.py) | 41 项 | 离线测试 |

## 输入、输出和依赖

- 输入：块定义文件，加一场比赛的数据包。比赛信息只收 `game_id`、`home`、`away`、`tipoff_utc` 四个字段，再加本块指标。
- 输出：`BlockRun`，含每次尝试和 `CheckReport`（状态为 ok、warning、invalid 或 skipped）。只返回给调用方，不保存。
- 依赖：jsonschema（Draft 2020-12）；默认调用 [backend.md](backend.md) 的 `request_model`。

## 四层检查

每个块由 `blocks/{块}_v{版本}.md`（给模型看）和同名 `.schema.json`（输入指标和输出 JSON Schema）组成，共用 `shared/rules.md` 和 `shared/glossary.md`。

- **L1 文件自检**（`load_block` / `load_blocks`）：
  - 文件成对出现；文件名、schema 的 version、schema 的 id 一致；
  - 五个二级标题齐全：角色、输入说明、分析方法、禁止事项、输出要求；
  - schema 合法，`required` 覆盖全部字段，`additionalProperties: false`；
  - 只用约定字段，每种玩法的字段齐全（分数基准和修正二选一）；指标名规范且写进了 `.md`；
  - 已在 `RULES` 登记业务规则；
  - 程序生成的“数据不足”输出（`insufficient_output`）必须符合 schema。

  任何一个块不过，就合并报告所有问题。
- **L2 输入**（`prepare_input`）：只收本块指标和四个比赛字段，混入盘口或其他指标就报错；不是有限数字的指标算缺失，不补值。缺核心指标时不调用模型，状态为 skipped。
- **L3 格式**（`parse_reply`、`check_reply`）：只容忍最外层代码块，拒收 NaN，jsonschema 一次收集全部错误。L3 不过就不跑 L4。
- **L4 业务规则**：先跑所有块通用的 `market_errors`，再跑本块规则。
  - 错误：方向和分数符号矛盾（基准总分不查符号）；中性却不是 0。
  - 警告：缺失项漏报或误报；理由里的数字找不到来源；实力块季初（少于 10 场或场次缺失）把握大于 50（阈值暂定）。
  - `margin_pts - elo_margin` 只记录，不判。

## 拼装与运行

- `assemble`：
  - 指令放在 `instructions`，数据包放在用户消息的 `<game_data>` 里；
  - `<` 会转义，防止伪造结束标签；
  - 记录指令、数据和 shared 文件的 SHA-256。
- `run_block`：
  - 有错误就纠正一次，仍有错误为 invalid；只有警告时不纠正；
  - `ModelError` 不重试；默认用 `request_model`；
  - 只有 ok 和 warning 算可用（`usable`）。
- 分数口径和把握刻度写在 [glossary.md](../../backend/app/prompts/shared/glossary.md)：
  - `*_pts` 是完整基准，`*_adj_pts` 是修正，汇总时相加；
  - 把握分为 0、低、中、高四档；
  - 改 shared 文件会改变所有块的提示词哈希。
- 当前块：[strength_v2](../../backend/app/prompts/blocks/strength_v2.md) 是样板，只输出让分，`reasons` 排在最前，必须有 reasons、missing、spread_lean、spread_confidence、margin_pts。旧版 strength_v1 保留，仍能通过自检。指标名暂定，等指标程序做好后对齐。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 块定义读取与自检 | 已实现 | `registry.py` |
| 输入、格式、业务规则检查 | 已实现 | `checks.py`、`rules.py` |
| 拼装与一次纠正 | 已实现，只在测试中用模拟模型跑过 | `assemble.py`、`runner.py` |
| 调用入口 | 未实现：没有接口、脚本或任务调用 `run_block` | 全仓搜索只有定义和测试 |
| 结果保存 | 未实现：CSV 和原始 JSON 记录等批量脚本时再做 | [小批量测试方案](../plans/功能块小批量测试方案.md) |
| 指标程序 | 未实现：块的输入指标还没有程序生成 | — |
| 其他块 | 只有实力块草稿 | `prompts/blocks/` |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-15 | `cd backend && uv run --locked python -m unittest discover -s tests` | 本轮运行：172 项通过，其中 test_blocks 41 项：L1 坏文件（临时目录）、L2、L3、L4（含修正分数与总分符号、季初警告）、拼装隔离与哈希、标签注入、纠正流程与跳过；全部离线 |
| 2026-11-13 | 同类命令 | 历史记录：框架上线时 50 项通过，聊天提示词迁移后 57 项，合入分数口径（PR #4）后 60 项；全部用模拟模型 |

## 限制、未决事项与下一步

- 提示词是草稿，指标名暂定；季初把握阈值 50 暂定。
- 从未用真实模型跑过，模型能否稳定按 schema 输出未知。
- 下一步按 [小批量测试方案](../plans/功能块小批量测试方案.md) 写批量脚本和结果保存（建议，未执行）。

## 历史

- 2026-11-13：功能块框架上线（50 项测试）；聊天提示词迁移后 57 项；PR #4 统一分数口径后 60 项。
- 2026-11-13：Windows 本机缺 tzdata，聊天的 RouteTests 8 项未跑（见 PR #3）；功能块测试不受影响。
