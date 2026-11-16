# 项目协作补充

## UMA 与本地 Skills

- 本仓库的 Skills 保留在 `.agents/skills/<name>/SKILL.md`，以其中的 `name`、`description` 和正文为准，不复制到另一套 UMA 专属目录。`agents/openai.yaml` 保留为兼容其他客户端的界面元数据，不作为 UMA 加载成功的依据。
- 用户可以直接说“同步代码地图”“记录提示词改版”“核对 API 地址”或“整理目录”，不必输入 `$skill-name`。在 UMA 中先查当前会话的技能目录，再使用 `skill` 工具按准确名称加载；已加载的 Skill 不重复加载。目录未提供该 Skill 时，读取对应 `SKILL.md` 并明确这是手动读取，不声称已通过工具注册。
- 实现、修复、配置和文件交付任务使用 `fast-delivery` 控制范围；具体维护任务再按下文触发相应 Skill。不为普通文档修改启动完整产品流程；非平凡代码修改仍遵守 UMA 的验收、独立复核和权限要求。
- UMA 中读取文本使用 `read`，发现文件使用 `glob`，搜索内容使用 `grep`，局部修改使用 `edit`；命令通过 `bash` 指定 `workdir` 执行。Skill 内的命令以项目根目录或明确标注的工作目录为基准，资源链接以 Skill 所在目录为基准。工具不可用时按当前客户端能力处理，不编造执行结果。
- Skill 不授予额外权限。保留移动、改名、恢复用户文件前的确认；不自动调用收费模型、刷新外部数据、安装插件、改 UMA 全局配置、提交或推送 Git。
- 需要团队协作时，使用当前会话实际可用的 UMA 队友、共享任务板与消息工具；按任务分配文件范围，依赖任务按顺序交接，禁止把历史 Codex/Claude/Grok 分工当作当前已启动的队友。用户要求独立完成时不委派；与必要独立验证冲突时说明限制，不伪造复核。
- `CODEBASE.md` 和 `docs/codebase/` 仍是代码现状来源；UMA Wiki / 知识检索只补充相关证据，不替代源码核对、不另建重复代码地图。代码行为变化后按下文更新，纯 Skill 说明和协作规则变更不刷新业务模块的核对日期。

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

- 新建文件、新增版本文件，或发现目录平铺过多、版本系列与记录混放时，使用 [$folder-organization](.agents/skills/folder-organization/SKILL.md) 决定放置位置或提出分组方案。项目目录约定以 [docs/guides/项目目录说明.md](docs/guides/项目目录说明.md) 和代码地图为准。
- 移动或改名文件前先列出前后结构和需要同步的引用，等用户确认；完成后按上面的规则同步代码地图，涉及聊天提示词目录时同时走 `$chat-prompt-maintenance`。
- 只新建单个文件且放在已有约定位置时，不需要整体扫描。
