# 后端测试

`test_chat.py` 使用 Python 自带 unittest、httpx 的 MockTransport 与 ASGITransport，在进程内验证中转请求和网站接口。外部请求由本地样例代替，不读取真实模型配置、不消耗额度。

```sh
cd backend
.venv/bin/python -m unittest discover -s tests -v
```

覆盖 Responses 流与 JSON 兼容返回、额外认证头、401/403/404/429/503 错误、无自动重试、超时、半截回答不交付、配置缺失、输入边界、历史对话传递、服务端提示与单进程并发限制。真实中转成功记录另见交接，不把离线通过当成外部服务可用。

`test_chat.py` 的 `ChatPromptTests` 覆盖聊天提示词：仓库里的提示词文件都已登记到锁文件并有改版记录（失败时运行 `$chat-prompt-maintenance`），以及在临时目录造出的未记录修改、未登记新版本、旧版本被删、版本标识错误、缺锁文件；另有提示词无法加载时返回 503 的接口用例。`EvalSnapshotTests` 检查 `eval_cases.md` 引用的固定快照存在、在快照约定的时刻不过期、选场能找到。

`test_blocks.py` 覆盖功能块框架：定义文件自检（在临时目录造坏文件，含玩法字段与分数口径）、输入检查、结构与业务检查（含通用符号检查和季初把握警告）、拼装隔离与哈希、纠正一次的流程；模型调用全部用 `AsyncMock` 代替。

还覆盖聊天每次读取保存快照、不可用状态、选场及单平台隔离、所选比赛或平台失效时不调用模型、拒收前端伪造报价；`test_odds.py` 覆盖盘口整理与刷新边界，以及赛程与比分（`ScheduleTests`）、近期战绩统计（`TeamFormTests`）、ESPN 伤病整理、保存与读取（`InjuryTests`）和后台补充取数（`start_extras`）；外部接口都用 `httpx.MockTransport` 或打桩代替。`EvalSnapshotTests` 另核对 `D-001.json` 的数字和 `eval_cases.md` 描述一致。后续存储接入后，再覆盖记忆有效期与任务恢复。

**隔离**：每个测试文件第一行 `import _isolation`，在导入任何 `app` 模块前把日志目录、数据库（临时 SQLite）、嵌入与向量库（假实现）、记忆开关（全关）和启动连库检查（关）用环境变量固定下来；`backend/.env` 里写了真实库也不会被连上。

`test_db.py` 覆盖数据库接入（P2）：连接串拼接与特殊字符密码、去密码显示与脱敏、隔离本身、`UTCDateTime`、`session_scope`、SQLite 外键、Alembic 升降级、`/health/storage` 正常与断库、启动连库检查不阻塞。`test_memory_integration.py` 是真实存储的集成测试，默认跳过；先 `make storage-up`，再：

```sh
cd backend
DOGHUNT_TEST_INTEGRATION=1 uv run --locked python -m unittest discover -s tests -p "test_memory_integration.py" -v
```

它用 `backend/.env` 的连接参数新建 `doghunt_test` 库（`DOGHUNT_TEST_DB_NAME` 可改），跑完删除，不动日常库数据。
