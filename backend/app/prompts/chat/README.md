# 聊天助手提示词

首页聊天框模型的静态提示词。设计依据见 [聊天预测提示词设计文档](../../../../docs/design/聊天预测提示词设计文档.md)，读取与拼装代码在 `app/services/chat/prompt/`。

## 当前版本

以 `versions.lock.json` 的 `active` 为准，下表同步列出：

| 顺序 | 模块 | 当前版本 | 内容 |
| --- | --- | --- | --- |
| 1 | role | `chat_role_v1` | 角色与能力边界 |
| 2 | sources | `chat_sources_v5` | A/B/C 资料等级（A 级分盘口、赛程与战绩、整季实力分、DunkAPI 球队效率与伤病，写出来源） |
| 3 | odds | `chat_odds_v8` | 盘口与报价规则、按球队或球员聚焦、分析卡字段、未选场只留倾向用到的平台报价、整表资料最多 15 场、市场信号、休息天数、近期战绩、伤病列表、整季实力分、球队效率 |
| 4 | method | `chat_method_v8` | 分析方法（照服务端分析卡写结论并解释；规则摘要；只有用户提供新消息时才能推翻分析卡并写明原因） |
| 5 | output | `chat_output_v6` | 结论置顶、按分析卡列分玩法因素表与预计推导、把握说明与分档（改结论时用）、追问 |

版本文件放在 `modules/{模块}/`，例如 `modules/method/chat_method_v8.md`。拼装：五个文件按上表顺序用空行连接，再由 `service.py` 追加当前北京时间、快照说明和 `<saved_odds_snapshot>`。时间和快照不进静态哈希。后端进程只读一次文件，改完要重启后端。

## 其他文件

| 文件 | 用途 |
| --- | --- |
| `versions.lock.json` | 当前版本、各文件哈希与字数、静态提示词哈希；按版本名登记，由命令生成，不手写 |
| `modules/{模块}/` | 各模块的全部版本文件，旧版本保留 |
| `records/CHANGELOG.md` | 改版记录 |
| `records/issues.md` | 使用中发现的问题 |
| `eval/eval_cases.md` | 回归问题集 |
| `eval/snapshots/` | 回归用的固定盘口快照（S-）和赛程比分、伤病资料（D-），用法见 `eval_cases.md` |

## 修改方法

1. 直接改文件也可以，改完调用 `$chat-prompt-maintenance`，它会补版本号、改版记录和锁文件。
2. 已登记的版本内容不能再改，哪怕只是错别字；改动一律存为新版本，旧文件保留。
3. 漏了记录时，离线测试 `test_chat.py` 会失败并提示运行 Skill。

```sh
cd backend
.venv/bin/python -m app.services.chat.prompt check
```
