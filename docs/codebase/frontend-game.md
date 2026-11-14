# 3D 启动页与沙盘篮球比赛｜模块子块

[返回父块](../../CODEBASE.md) · 同级：[前端网站](frontend.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | frontend-game |
| 核对日期 | 2026-11-15（Asia/Shanghai），只抽查了入口与关键常数；其余行为说明沿用 2026-11-11（启动页、比赛第一二轮）与 2026-11-14（比赛第三轮）的记录 |
| 核对范围 | 2026-11-15 读过：`router/index.ts` 守卫、`stores/startupGate.ts`、`startup/modules.ts`、`startup/cameraRig.ts` 的 `orbitLimits`、`startup/stage.ts` 的 `prepareGame` / `startGame`、`StartupView.vue` 的预热计时、`game/sim/constants.ts`（`RULES`、`SHOT`、`PERFECT`、`EDGE`、`STEP`）、`game/index.ts` 步进、`actions.ts` 的 `releaseJumper`。未逐行重读：`startup/scene/`、`render/`、`materials/`、`textures/`，`game/view/` 全部，`game/sim/` 的防守、碰撞与 AI 细节 |
| 实现状态 | 部分实现：启动页（实时 3D 指挥室、模块菜单、点选站点、开场运镜、进入流程、降级提示）可用；3v3 街机篮球可完整打完一场，键盘或手柄操作，不支持触屏；全部由代码生成，未经美术或项目负责人评审 |
| 验证程度 | 历史验证：2026-11-11 启动页与比赛第二轮、2026-11-14 比赛第三轮（构建、离线模拟、无头浏览器）。2026-11-15 只跑了整站构建，没有重测 3D 与比赛；没有真人试玩，没有手机实机 |
| Git 基准 | 2026-11-15 核对时 HEAD `03881cc`，`frontend/` 无未提交改动。启动页与比赛原型在 `e6df9c8` 提交，第三轮在 `54aceb3` 提交 |

## 负责什么

启动页 `/welcome` 是进入系统前的 3D 指挥室：一间无人的指挥室，环形落地窗外是雨夜城市，中央是全息篮球战术桌。用户从这里进入六个业务模块，或点“开始比赛”玩一场原创的 3v3 街机篮球（沙盘篮球比赛）。两者都用 `three`（WebGL 渲染库）实时绘制，没有外部模型、图片或音频文件；屏幕与全息内容只有抽象图形，不含比分、盘口或胜率。

方案依据 [3D 动漫启动页设计文档](../design/3D动漫启动页设计文档.md) 的方案 B。与文档的差异是使用者明确要求、未经项目负责人确认的：画面改为写实的电影化风格（文档 3.1 建议卡通渲染），待机动效多于文档 6.1（雨、车流、闪电、战术演示），新增模块菜单、点选设备、可选声音和篮球比赛。设计文档第 10 节四项待确认事项仍是临时选择：水平完整环绕；`/` 留给聊天、另设 `/welcome`；同一标签页进入后跳过；模型全部代码生成。

内页外壳、聊天和其他页面见 [前端网站](frontend.md)。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [router/index.ts](../../frontend/src/router/index.ts) / [startupGate.ts](../../frontend/src/stores/startupGate.ts) | `beforeEach` / `hasEntered` / `markEntered` / `unmarkEntered` / `rememberTarget` / `enterTarget` | 每个标签页第一次打开任何地址先转到 `/welcome` 并记住原地址；放行记录存 `sessionStorage`（键 `doghunt:startup-entered`），存储不可用时退回内存；跳转失败时撤回放行 |
| [StartupView.vue](../../frontend/src/views/StartupView.vue) | `openScene` / `enter` / `dock` / `preview` / `warmGame` | 固定的标题、进入按钮、“开始比赛”、模块菜单、站点卡片、悬浮标签和声音开关；场景加载、失败、进入三种状态；`/welcome` 标了 `meta.fullscreen`，不套内页外壳 |
| [StartupMenu.vue](../../frontend/src/components/startup/StartupMenu.vue) / [StationCard.vue](../../frontend/src/components/startup/StationCard.vue) | `preview` / `enter` 事件 | 模块菜单（悬停预览、点击直达）；镜头停靠后的站点说明卡片 |
| [startup/modules.ts](../../frontend/src/startup/modules.ts) | `STARTUP_MODULES` / `moduleOf` | 六个模块的序号、名称、地址、在指挥室的位置；`planned: true` 的记录、统计、记忆标“预览” |
| [startup/stage.ts](../../frontend/src/startup/stage.ts) | `createStage` / `prepareGame` / `startGame` | 渲染器、主循环、自由环绕与停靠切换、指针交互、开场运镜、后台暂停、画质降档、资源释放；按需加载并预热比赛 |
| [cameraRig.ts](../../frontend/src/startup/cameraRig.ts) / [picker.ts](../../frontend/src/startup/picker.ts) | `orbitLimits` / `CameraTween` / `StationPicker` | 镜头边界与横竖屏机位、绕桌弧线过渡；判断指针对准哪个站点 |
| [startup/render/](../../frontend/src/startup/render/pipeline.ts) | `Pipeline` | 每帧顺序：室外画面 → 地面倒影 → 主画面 → 环境光遮蔽 → 泛光 → 调色；环境反射捕获 |
| [startup/materials/](../../frontend/src/startup/materials/kit.ts) / [textures/](../../frontend/src/startup/textures/forge.ts) | `createMaterialKit` / `TextureForge` | PBR 材质库、雨窗玻璃、石材地面、屏幕着色器；在显卡上生成表面细节贴图 |
| [startup/scene/](../../frontend/src/startup/scene/index.ts) | `buildWorld` / `STATIONS` | 代码搭建的室内外场景；尺寸集中在 `layout.ts` 的 `room`，站点在 `stations.ts`，全息战术在 `hologram/` |
| [quality.ts](../../frontend/src/startup/quality.ts) / [audio.ts](../../frontend/src/startup/audio.ts) | `QUALITY` / `QualityGovernor` / `createSoundscape` | 三档画质与按帧耗时降档；Web Audio 合成的环境声和界面音效 |
| [game/index.ts](../../frontend/src/game/index.ts) / [input.ts](../../frontend/src/game/input.ts) / [sound.ts](../../frontend/src/game/sound.ts) | `Game.create` / `begin` / `end` / `GameInput` / `createGameSound` | 比赛控制器（`STEP` = 1/120 秒固定步进，一帧最多 12 步）；键盘与手柄；合成比赛音效 |
| [game/sim/](../../frontend/src/game/sim/match.ts) | `createMatch` / `stepMatch` | 不依赖渲染库的模拟（13 个文件）：`ball.ts` 球与篮筐物理，`athlete.ts` 跑动与滑步，`contact.ts` 身体对抗，`shooting.ts` 命中率与完美窗口，`actions.ts` 动作，`possession.ts` / `match.ts` 球权与规则，`ai.ts` 进攻决策，`defense.ts` 防守，`footwork.ts` 共用走位，`constants.ts` 参数与 `EDGE` |
| [game/view/](../../frontend/src/game/view/gameView.ts) | `GameView.build` / `warmUp` / `Director` | 比赛画面总装、球场、篮架、环境反射、转播与特写镜头；分段搭建并预热 |
| [game/view/athlete/](../../frontend/src/game/view/athlete/animator.ts) / [arena/](../../frontend/src/game/view/arena/index.ts) | `createAthleteModel` / `animateAthlete` / `createArena` | 球员骨架、零件、步态、逆向运动学与动作姿势；球馆看台、观众、场边、记分屏与灯光 |
| [GameHud.vue](../../frontend/src/components/game/GameHud.vue) / [game/hud.ts](../../frontend/src/game/hud.ts) | `setMeter` / `HudState` | 记分牌、进攻计时、提示、出手条、操作说明、暂停与终场菜单；`hud.ts` 不引用渲染库 |

## 输入、输出和依赖

- 输入：路由守卫转来的原地址；指针拖动、滚轮、点击、键盘（Tab、Enter、Esc）；比赛中的键盘或手柄。
- 输出：进入系统后以替换方式前往原地址（默认 `/`）；比赛只在本页显示，不保存成绩，不调用后端。
- 依赖：`three` 与浏览器 WebGL、Web Audio。启动页停留期间不请求后端接口（2026-11-11 实测请求数为 0）。比赛代码是按需加载的独立包，复用启动页的渲染器、后期管线和材质库。

## 已实现与缺口

### 启动页

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 进入流程 | 首次打开先到 `/welcome`；加载中“进入系统”立即可用，连点只跳一次；同一标签页刷新不再出现 | `router/index.ts`、`startupGate.ts`、`StartupView.enter` |
| 场景 | 室内：战术桌、吊顶环形灯与投影机、左侧弧形工作台（九块动态屏）、右侧休息区、背墙项目标识、机柜、门和控制面板。室外：三种风格楼群窗灯、车流、球馆与探照灯、云层、闪电；玻璃上有雨珠、水汽和室内倒影。材质为 PBR，地面实时倒影，主光投影；后期含环境光遮蔽、泛光、色调曲线、色差、暗角、颗粒 | `startup/scene/`、`render/`、`materials/` |
| 全息战术桌 | 十个棋子循环演示挡拆、突破分球、弱侧反跑三套示意战术 | `scene/hologram/plays.ts` |
| 站点互动 | 六个站点：战术桌 = NBA 助手，一二三号工位 = 比赛与盘口、预测记录、效果统计，机柜 = 长期记忆，墙面面板 = 设置。悬停出标签和地面光环，点击停靠并出卡片；拖动、Esc 或“返回”回到自由环绕；菜单悬停预览、点击直达 | `stations.ts`、`picker.ts`、`StartupMenu.vue`、`StationCard.vue` |
| 镜头 | 水平可转一整圈，竖直 52°–88°，距离 5–8 米，不能平移；约 3.6 秒开场运镜，期间按钮可用 | `cameraRig.ts` 的 `orbitLimits` |
| 降级 | WebGL 不可用或上下文丢失时提示“当前无法显示 3D 场景，仍可进入系统”并可重试，菜单仍能直达；每次重试换新画布 | `StartupView.openScene` |
| 性能与无障碍 | 三档画质，触屏从中档起步，帧率低时依次降分辨率、倒影、遮蔽；切到后台暂停，离开释放显卡资源；“减少动态效果”只画静止一帧；Tab 顺序以“进入系统”为首，随后六个菜单项 | `quality.ts`、`stage.ts` |
| 声音 | 雨声、低鸣、雷声、提示音为合成音，默认关闭，点“声音”才创建音频环境，离开页面释放 | `audio.ts` |

### 沙盘篮球比赛

原创街机篮球，玩法借鉴通用做法（蓄力出手条、上篮、扣篮、抢断、盖帽），不用任何现有游戏的名称、界面或素材。球员是没有五官的机械运动员，队名“猎犬”“赤狐”虚构，不涉及真人或真实球队。属设计文档范围之外的新增内容，未经项目负责人确认。

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 入口与衔接 | “进入系统”下方的“开始比赛 PLAY”，触屏不显示。场景就绪约 4.2 秒后、或鼠标移到 / 键盘聚焦 PLAY 时在后台预热（分段搭建、每段让出一帧，最后预编译着色器并离屏画一遍）。点击后镜头俯冲战术桌、过曝成白再显出球场，落到转播机位；没搭好时镜头停在桌上方等待。退出后保留画面，再进不用重搭 | `StartupView.warmGame`、`stage.prepareGame` / `startGame`、`GameView.warmUp` |
| 规则 | 3v3 全场，玩家控制青色“猎犬”。两节各 3 分钟，24 秒进攻时限（进攻篮板后 14 秒），2 分与 3 分，出界，进球后底线发球，下半场换边，压哨有效，球下落时不能盖帽，平分加时 1 分钟。哨响时还没出手的跳投作废。不设犯规、罚球、走步、回场、8 秒 | `constants.ts` 的 `RULES`、`match.ts`、`actions.ts` 的 `releaseJumper` |
| 操作 | WASD / 方向键移动，Shift 加速，J / 空格投篮（按住蓄力、松开出手）；按距离、朝向和路线是否被身体挡住自动选扣篮、上篮或跳投。防守时 J 封盖，K 传球（防守时换人），L 变向换手（防守时抢断），Esc 暂停，M 静音。防守不按加速时面向持球人滑步、后撤（横移 ×0.84、后退 ×0.7）。手柄：左摇杆、X 投、A 传、B 抢断、右扳机加速、Start 暂停 | `input.ts`、`constants.ts` 的 `STRAFE`、`actions.ts` |
| 难度 | 电脑对手略高一档：`EDGE` = 速度 ×1.04、投篮 +0.08、抢断 +0.15、封盖 +0.1、出手时机离散 ×0.85；双方同一套规则；玩家操作的球员加减速快，电脑球员有起步与急停惯性 | `constants.ts` 的 `EDGE` |
| 防守（第三轮） | 盯人站在持球人与篮筐之间约 1.1 米，按 0.2 秒后位置堵路线，退到离筐 1.6 米站住；被突破时离补位点最近的一人协防到筐前约 1.8 米；上篮扣篮时身边和筐下的人都可能起跳。进到 1.35 米内会考虑掏球，护球差更敢伸手（背身 ×0.45、正面冲 ×1.6、全速再 ×1.3、换手瞬间 ×0.5），掏空失去重心。电脑持球时有人贴身不全速运球、被堵先找空位队友、传不出就急停跳投 | `defense.ts`、`ai.ts`（2026-11-14 记录，2026-11-15 未重读） |
| 出手（第三轮） | 出手条在头顶，四档时机：完美、不错、偏早、偏晚。**完美出手必进**，正对筐心、不掷骰子、不会被封盖；窗口在起跳时定下，离筐越远、身前防守越近、跑动起跳越窄，投得准的人略宽。后卫空位约 4.8 米内 5 个物理步（约 42 毫秒），到约 9.5 米 3 步，更远没有；不足 3 步直接取消，两侧刻度变灰。其余命中率 = 距离基础 × 时机 × 防守干扰 × 跑动折扣 | `shooting.ts`、`constants.ts` 的 `SHOT` / `PERFECT`、`game/index.ts` 出手条换算 |
| 碰撞（第三轮） | 球员是竖直圆柱，按“分量”推开：站稳的防守人最稳，运球、掏球、失去重心的人易被顶开，腾空最轻；持球人撞上站稳的防守人推不动。上篮扣篮腾空后仍被地面上的身体挡住，扣篮被顶停会变成抛投。球与地板、篮板、篮圈逐步求解，高速时拆两小步防穿透 | `contact.ts`、`ball.ts` |
| 画面 | 硬表面机械运动员，三种体型；动作全部现场计算（落脚点步态、双骨逆向运动学，运球、跳投、上篮、扣篮挂筐、抢断、封盖、防守姿态）；球馆有阶梯看台、约九成上座的观众、环形屏、技术台、替补席、四面记分屏、照明灯 | `game/view/athlete/`、`arena/` |
| 进球反馈与音效 | 得分提示、篮网甩动、屏幕闪队色、观众欢呼；篮筐仰拍特写并慢放约半秒，扣篮、三分、完美出手后切庆祝特写；“减少动态效果”下不切特写、不慢放、不过曝。拍球、鞋声、哨声、砸筐、刷网、蜂鸣器、观众声为合成音，进比赛默认开、可静音，指挥室环境声暂停 | `Director`、`sound.ts` |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-15 | `npm --prefix frontend run build`（类型检查 + vite build），整站一次 | 通过，`295 modules transformed`；启动页 3D 包 `stage` 705.50 kB（gzip 186.89 kB），比赛包 `game` 94.97 kB（gzip 35.01 kB）。本轮没有改 3D 和比赛代码，没有在浏览器里重测 |
| 2026-11-14 | 比赛第三轮：`npm run build`；临时离线模拟脚本直接调用 `createMatch` / `stepMatch`（未进仓库）；无头 Edge 进入真实比赛 | 构建通过（275 modules，比赛包约 95 kB）。电脑互打 200 场全部正常终场，数值高的一方 145 胜 55 负。脚本“拿球直冲篮下”：改前 40 场全胜、每回合 1.51 分，改后 100 场 14 胜 86 负、0.65 分。脚本“空位投、路开就突、被堵就传”：时机误差 30 毫秒时 46 胜 54 负，50 毫秒时 25 胜 75 负。各组约 3700 次完美出手全部命中；穷举 173,918 次完美出手全部进筐，最远 9.61 米。浏览器里绿区随位置出现或消失（前场 3 步，占条 3.38%），无脚本报错；一次被开发服务器热更新断线刷新 |
| 2026-11-11 | 比赛第二轮：`npm run build`；电脑互打 60 场；检视页逐帧拼图；无头 Edge 实时打完一场 | 构建通过（168 modules）；60 场全部终场；两分跳投约 29%–38%，三分 21%–27%；站立到庆祝各动作逐帧核对；实时约 430 秒打完一场并能“再来一局”；1600×900 下 238 帧/秒（无头上限 240）。一次无头音频设备报错，一次疑似热更新导致的整页刷新，未进一步证实 |
| 2026-11-11 | 启动页：`npm run build`；无头 Edge（Windows 11，RTX 4060 Laptop） | 构建通过（131 modules，3D 包约 700 kB）。`/` 转 `/welcome`；直接访问 `/matches` 进入后回到 `/matches`，加载中连点只跳一次；菜单、Tab + Enter、点选站点、Esc 收起通过；进入后画布移除、无残留绘制；停留期间接口请求 0；390×844 无横向溢出；“减少动态效果”下无绘制循环；屏蔽 WebGL 后显示降级提示；闪电与声音环境建立已确认；1440×900、2560×1440 达 240 帧/秒上限 |

## 限制、未决事项与下一步

- 没有真人试玩第三轮，难度是否合适只能由使用者判断；身体分量、掏球倍率、完美窗口、协防距离、`EDGE` 都是按模拟结果定的，使用者没有给过数字。60 帧显示器上 3 步窗口约 1 帧，手感未确认。
- “完美出手封不到”是为保证必进做的取舍，电脑同样适用；电脑三分命中率因此比第二轮高（30%–37%）。
- 未在手机实机、Safari、Firefox、核显或低性能设备上运行；帧率只代表一块独立显卡，自动降档没有被实际触发过，后台暂停未实测。无头浏览器听不到声音，音效听感未确认；手柄没用真实手柄测过。
- 电脑 AI 只有一个难度；不支持触屏比赛。页面空闲时会自动预加载比赛场景，对只想进入系统的人有额外的显存与后台计算开销。
- 画面只做过截图自检，没有经过美术或项目负责人评审；上篮撞人、扣篮被顶停后的抛投动作没有逐帧看过。

## 历史

- 2026-11-11：先做过卡通渲染的几何体原型，同日按使用者要求整体重做为写实风格，卡通版已不存在。
- 2026-11-11：比赛第二轮按试玩意见重做衔接、难度、球员模型与动作、球馆；修复“三不沾落地没人捡、压哨时这一节不结束”的卡死。修过一个时序问题：预热时不连阴影一起画，之后球场受光表面全黑（`GameView.warmUp` 有说明）。
- 2026-11-12：内页重做时，记录、统计、记忆三项的“待开发”标记改为“预览”，设置不再标记。
- 2026-11-14：比赛第三轮。起因是使用者反映“拿球后几乎可以无脑跑到内圈扣篮，没有阻拦，也没有碰撞阻挡，人机也很少抢断”，并要求“投篮难度随距离增加，完美出手可以更难，但不要出现完美出手还不进”。改动：碰撞由“两人各让一半”改为按分量推开（原来等于推着防守人走），腾空的人参与碰撞（原来扣篮会穿人）；防守新增 `defense.ts`；完美出手由“固定 7 步、只是加成”改为“必进、窗口随距离收窄”；修复哨响后空中的人仍把球投出、换边后飞向另一篮筐的问题（`releaseJumper`）。
- 2026-11-14：界面样式统一时只改了启动页界面层（标题正体、按钮去发光改左侧亮线、菜单悬停不位移、进入时淡出到内页底色 `#080c10`），3D 场景与 `GameHud.vue` 未动。
