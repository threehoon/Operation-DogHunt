# 项目协作补充

## 代码现状说明书

- 接手开发前先读根目录 [CODEBASE.md](CODEBASE.md)，再按本次任务阅读相关模块子块。继续遵守用户的全局协作规则和交接要求。
- 完成影响代码行为、接口、模块职责、依赖或运行方式的改动后，使用 [$codebase-map](.agents/skills/codebase-map/SKILL.md) 核对受影响源码，更新相关子块，再同步父块摘要。交付时说明更新范围和实际验证结果。
- 只修改普通文案、交接或代码地图自身时，不递归触发维护。无关模块不跟着刷新核对日期。
- 这条规则在 AI 执行开发任务时生效。用户在编辑器手动修改的代码，在下次相关开发任务或手动调用 Skill 时核对同步。
- Skill 与模板是通用文件；项目当前状况只写在 `CODEBASE.md` 和 `docs/codebase/` 的实例中。

## 聊天提示词维护

- 修改 `backend/app/prompts/chat/` 下的提示词后，使用 [$chat-prompt-maintenance](.agents/skills/chat-prompt-maintenance/SKILL.md) 记录改版并更新锁文件，再按上一节同步代码地图。只改 CHANGELOG、issues、eval_cases、README 等记录文件时不递归触发。
- 用户在编辑器手动修改提示词后，离线测试会因锁文件不一致而失败；在下次相关开发任务或手动调用 Skill 时补齐记录。

## API 地址文档维护

- `docs/api/README.md` 是项目 API 地址统一清单。修改或新增外部 API 域名、Base URL、Endpoint、版本、工具名、后端路由、端口或代理目标后，使用 [$api-address-maintenance](.agents/skills/api-address-maintenance/SKILL.md) 同步清单，并核对相关调用方、配置示例与 README。只改普通文案或历史交接时不递归触发。
- 真实模型中转等未公开地址仅记录在被 Git 忽略的 `docs/api/local-addresses.local.md`；共享文档记录配置变量与拼接方式，禁止写入 Key、Token、认证头值或带 Key 的完整 URL。
- `docs/api/local-addresses.local.md` 同时保留其他外部 API 与内部接口的完整地址，让用户打开一份文档即可查全；相关地址变化时与共享清单一起同步，不能只维护中转地址。
- 此规则在 AI 执行相关任务时生效，不提供后台监听。用户手动修改后，在下次相关开发或显式调用 Skill 时核对同步；供应商外部变更需有通知或核验依据。

## 文件夹管理

- 新建文件、新增版本文件，或发现目录平铺过多、版本系列与记录混放时，使用 [$folder-organization](.agents/skills/folder-organization/SKILL.md) 决定放置位置或提出分组方案。项目目录约定以 [docs/项目目录说明.md](docs/项目目录说明.md) 和代码地图为准。
- 移动或改名文件前先列出前后结构和需要同步的引用，等用户确认；完成后按上面的规则同步代码地图，涉及聊天提示词目录时同时走 `$chat-prompt-maintenance`。
- 只新建单个文件且放在已有约定位置时，不需要整体扫描。
