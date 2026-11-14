# 文档总目录

`docs/` 按用途分文件夹，根目录只放这份目录。新文档先找对应的文件夹；放不进现有分类时，按 [$folder-organization](../.agents/skills/folder-organization/SKILL.md) 定位置。

| 文件夹 | 放什么 | 现有文档 |
| --- | --- | --- |
| [requirements/](requirements/) | 产品需求 | [NBA 预测网站需求文档](requirements/NBA预测网站需求文档.md) |
| [design/](design/) | 页面与提示词、功能块的设计 | [3D 动漫启动页设计文档](design/3D动漫启动页设计文档.md)、[聊天预测提示词设计文档](design/聊天预测提示词设计文档.md)、[功能块设计思路](design/功能块设计思路.md)、[功能块提示词与校验开发文档](design/功能块提示词与校验开发文档.md) |
| [plans/](plans/) | 执行方案、测试方案，带任务状态 | [聊天分析层与目录整理执行方案](plans/2026-11-15-聊天分析层与目录整理执行方案.md)、[调用日志与查看页执行方案](plans/2026-11-15-调用日志与查看页执行方案.md)、[免费详细数据接入执行计划](plans/免费详细数据接入执行计划.md)、[功能块小批量测试方案](plans/功能块小批量测试方案.md) |
| [guides/](guides/) | 使用说明和目录约定 | [API 取数使用说明](guides/API取数使用说明.md)、[项目目录说明](guides/项目目录说明.md) |
| [api/](api/README.md) | 全部 API 地址清单（真实中转地址只在被 Git 忽略的本地文件里） | [API 地址清单](api/README.md) |
| [codebase/](../CODEBASE.md) | 代码地图子块，入口是根目录的 `CODEBASE.md` | [代码现状说明书](../CODEBASE.md) |
| [handoff/](handoff/INDEX.md) | 每次开发的交接记录 | [交接索引](handoff/INDEX.md) |
| [assets/](assets/) | 文档用到的图片 | — |

聊天提示词的版本、改版记录和回归用例不在这里，放在 [backend/app/prompts/chat/](../backend/app/prompts/chat/README.md)。
