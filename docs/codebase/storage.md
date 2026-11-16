# 本机开发存储｜模块子块

[返回父块](../../CODEBASE.md) · 上级：[后端子块](backend.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | storage |
| 核对日期 | 2026-11-16（Asia/Shanghai） |
| 核对范围 | P1：`deploy/compose.dev.yml`、`deploy/README.md`、`scripts/check_storage.py`、`Makefile` 的 `storage-*` 目标、`backend/.env.example`、`.gitignore` 的 `*.dump`。P2（2026-11-16，基准 `90b131c` 加未提交改动）：`backend/app/db/`、`core/config.py` 的 `db_*`、`main.py` 的启动检查、`routes/health.py` 的 `/health/storage`、`alembic.ini`、`migrations/`、`Makefile` 的 `db-*`、`tests/_isolation.py`、`test_db.py`、`test_memory_integration.py`；`backend/.env` 只核对了有非空 `DOGHUNT_DB_PASSWORD`，没有读其他值 |
| 实现状态 | 部分实现：开发编排、使用说明、检查脚本（[开发文档](../plans/2026-11-15-数据库与向量库接入开发文档.md) P1）；后端连接 PostgreSQL、Alembic 空基线迁移、存储就绪接口（P2）。还没有任何表（P3），Milvus 集合与嵌入未实现（P4、P5） |
| 验证程度 | 本轮运行验证：P1 本机起停容器逐条核对；P2 离线测试 208 项（4 项集成默认跳过）与真实 Postgres 集成测试 4 项通过，日常库迁移往返与停库启动实测（见“验证记录”） |

## 负责什么

在本机用 Docker 起四个容器，给以后的长期记忆和预测记录用：PostgreSQL（关系库）、Milvus standalone（向量库），以及 Milvus 依赖的 etcd 和 MinIO。前后端仍在宿主机运行。P2 起后端连接 PostgreSQL，但还没有表，只用于启动检查、`/api/v1/health/storage` 和迁移；Milvus 还不连接。聊天、盘口和日志不依赖这些容器，Postgres 停了后端照常启动。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [compose.dev.yml](../../deploy/compose.dev.yml) | 顶层 `name: doghunt-dev`；服务 `postgres`、`etcd`、`minio`、`milvus`；卷 `pg_data`、`etcd_data`、`minio_data`、`milvus_data` | 镜像 `postgres:17-alpine`、`quay.io/coreos/etcd:v3.5.18`、`milvusdb/minio:RELEASE.2024-12-18T13-15-44Z`、`milvusdb/milvus:v2.5.27`；Postgres 与 Milvus 端口只绑 `127.0.0.1`，etcd、MinIO 不发布；四个服务都有健康检查，Milvus 等 etcd、MinIO healthy 才启动 |
| [deploy/README.md](../../deploy/README.md) | — | 起停、日志、检查、端口冲突、备份、恢复、重置（带警告）、排错 |
| [check_storage.py](../../scripts/check_storage.py) | `main`、`check_containers`、`check_postgres`、`check_milvus`、`Checker.redact` | 只用标准库。检查容器 running/healthy 和项目名、端口只绑本机、容器内 `pg_isready`、用 `.env` 密码经 TCP 登录 `SELECT 1`、宿主机端口可连、Milvus `/healthz`；退出码 0 通过、1 有失败、2 配置或 Docker 不可用；打印前把密码替换成 `***` |
| [Makefile](../../Makefile) | `STORAGE_COMPOSE`、`storage-up`、`storage-down`、`storage-ps`、`storage-logs`、`storage-check` | `docker compose --env-file backend/.env -f deploy/compose.dev.yml …`；`storage-check` 用 `cd backend && uv run --locked python ../scripts/check_storage.py` |
| [backend/.env.example](../../backend/.env.example) | `DOGHUNT_DB_PASSWORD`、`DOGHUNT_DB_PORT`、`DOGHUNT_MILVUS_PORT`、`DOGHUNT_MILVUS_HEALTH_PORT`；P2 加 `DOGHUNT_DB_HOST/USER/NAME/POOL_SIZE/POOL_TIMEOUT_SECONDS/STATEMENT_TIMEOUT_MS/ECHO/STARTUP_CHECK`、`DOGHUNT_DATABASE_URL` | 密码必填；三个端口变量用于端口冲突，默认 5432、19530、9091；其余是后端连接参数，注释掉即默认 |
| [config.py](../../backend/app/core/config.py) | `Settings.db_*`、`database_url_override`（环境变量 `DOGHUNT_DATABASE_URL`）、`database_url`、`db_startup_check` | `database_url` 返回 `SecretStr`：有覆盖用覆盖，否则用 `quote(…, safe="")` 拼 `postgresql+psycopg://user:pw@host:port/name`（不用 `quote_plus`：SQLAlchemy 用 `unquote` 解析，`+` 不会还原成空格） |
| [db/session.py](../../backend/app/db/session.py) | `get_engine`、`get_sessionmaker`（都 `lru_cache`，第一次用才建）、`session_scope`、`dispose_engine`、`safe_url`、`register_database_secrets`、`check_database`、`DatabaseStatus` | Postgres 连接池 `pool_size`（默认 5）+ `max_overflow=5`、`pool_timeout`、`pool_pre_ping`，`connect_timeout=2`，`statement_timeout` 按配置（默认 2 秒）；SQLite（仅测试）每个连接 `PRAGMA foreign_keys=ON`。`check_database` 执行 `SELECT 1` 并读 `alembic_version`，失败返回脱敏后的类名加首行，不抛异常 |
| [db/base.py](../../backend/app/db/base.py) / [db/types.py](../../backend/app/db/types.py) | `Base`、`NAMING_CONVENTION`；`UTCDateTime` | 约束命名固定；时间列写入必须带时区并转 UTC，读出补 UTC（SQLite 存不带时区的 UTC） |
| [main.py](../../backend/app/main.py) | `_startup_database_check`、`lifespan` | 登记数据库密码；`db_startup_check` 为真时在线程里 `check_database`，最多等 1 秒，成功记 info、失败或超时记 warning（事件 `db.startup`），不阻止启动；退出时 `dispose_engine` |
| [routes/health.py](../../backend/app/api/routes/health.py) / [schemas/health.py](../../backend/app/schemas/health.py) | `GET /api/v1/health/storage`、`StorageHealthResponse` | 同步路由；库通时 `status=ok`，断开时仍 HTTP 200、`status=degraded`、`database.status=down` 加原因；`url` 不带密码；`milvus`、`embedding` 固定 `not_implemented`，不计入整体状态 |
| [alembic.ini](../../backend/alembic.ini)、[migrations/env.py](../../backend/migrations/env.py)、[0001_baseline.py](../../backend/migrations/versions/0001_baseline.py) | `run_migrations_online/offline`；版本 `0001` | 连接串从 `get_settings()` 读，不写在 ini；导入 `app.models` 和 `Base.metadata` 供 autogenerate；SQLite 时 `render_as_batch`。`0001` 是空基线，第一版记忆表在 P3 的下一个版本 |
| [Makefile](../../Makefile) | `db-migrate`、`db-current` | `alembic upgrade head`、`alembic current`（在 `backend/` 下） |
| [tests/_isolation.py](../../backend/tests/_isolation.py) | `FORCED_ENV`、`TEST_DATABASE_URL` | 每个测试文件第一行导入：日志目录、临时 SQLite、关启动检查、假嵌入/向量库、记忆开关全关；已缓存的配置和 engine 会被清掉 |
| [test_db.py](../../backend/tests/test_db.py)、[test_memory_integration.py](../../backend/tests/test_memory_integration.py) | 27 项离线；4 项集成（`DOGHUNT_TEST_INTEGRATION=1`） | 集成测试用 `backend/.env` 的连接参数，新建再删除 `doghunt_test` 库，不动日常库数据 |

## 输入、输出和依赖

- 配置：Compose 用 `--env-file backend/.env` 读密码和端口变量；没填密码时 `up` 直接报错。后端 `Settings` 从同一个文件读 `DOGHUNT_DB_*`（P2），两个 Milvus 端口变量后端不用。检查脚本和后端都是环境变量优先于 `backend/.env`；改了要重启后端。
- 依赖：Docker Desktop（本机实测服务端 29.9.3、Compose 5.7.0、分到 7.75 GiB 内存）。检查脚本还依赖 `uv`（Makefile 方式）或直接用宿主机 Python 运行（实测 3.12 与 3.14）。
- 数据：只在四个 Docker 卷里，`down` 保留，`down -v` 删除。Postgres 密码在第一次建库时写入卷，之后改 `.env` 不会改库里的密码。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 开发编排、起停、检查 | 已实现，本机运行验证 | 上表 |
| 备份与恢复 Postgres | 已写命令并实测一次 | [deploy/README.md](../../deploy/README.md) |
| 后端连接 Postgres、迁移、存储健康接口 | 已实现（P2）；没有表 | `db/session.py`、`migrations/`、`/health/storage` |
| 记忆表与只读接口 | 未实现（P3） | `models/` 仍只有文档字符串 |
| Milvus 集合、`pymilvus`、从 Postgres 重建向量 | 未实现（P4、P6） | 同上 |
| 交付编排 `compose.package.yml` | 未实现（P10） | — |

## 验证记录

| 日期 | 方式或命令 | 实际结果与边界 |
| --- | --- | --- |
| 2026-11-16 | `docker compose pull` 等价逐个拉取；`docker image inspect` | 四个镜像都是 `linux/arm64`；原定 `minio/minio:RELEASE.2023-03-20T20-16-18Z` 在 Docker Hub 和 quay.io 都拉不到，换成 Milvus 官方新编排里的 `milvusdb/minio` 镜像，镜像内有 `curl` |
| 2026-11-16 | `make storage-up` → `make storage-ps` / `make storage-check` | 四个容器 healthy；首次约 70 秒、重启约 40 秒到 Milvus healthy；检查 11 项 OK、退出码 0；`docker compose ls` 为 `doghunt-dev`，卷带 `doghunt-dev_` 前缀 |
| 2026-11-16 | 写入测试表和测试集合 → `make storage-down` → `make storage-up` | Postgres 测试行和 Milvus 测试集合的 2 条向量都还在；验证后已删除，库和 Milvus 回到空状态 |
| 2026-11-16 | 停 Postgres、停 Milvus、错误密码各跑一次检查 | 分别报容器没运行 / 连接被拒 / `password authentication failed`，退出码 1；输出不含密码 |
| 2026-11-16 | 按 README 原文执行备份、删行、恢复；改用 55432/29530/29091 端口再改回 | 恢复后测试行回来；改端口后检查通过 |
| 2026-11-16 | `docker stats --no-stream` | 空载约 390 MiB（刚启动），写入并重启后约 560 MiB |
| 2026-11-16 | Milvus 升到 v2.5.27、etcd 升到 v3.5.18，原卷上 `up -d` 原地升级 | 40 秒到 healthy，`make storage-check` 通过；建集合、写入、搜索命中，重启后数据在，测试集合已删除；空载约 450 MiB |
| 2026-11-16 | P2：`cd backend && uv sync --locked`；`uv run --locked python -m unittest discover -s tests` | 依赖 sqlalchemy 2.0.57、psycopg 3.3.9、alembic 1.20.1；`Ran 208 tests / OK (skipped=4)`（新增 `test_db.py` 27 项；4 项是默认跳过的集成测试） |
| 2026-11-16 | P2 门禁：`make storage-up` 后 `DOGHUNT_TEST_INTEGRATION=1 … -p test_memory_integration.py -v` | 4 项通过：空库 `doghunt_test` 上 upgrade head → downgrade -1 → upgrade head，`alembic current` 为 `0001 (head)`；会话时区设成 Asia/Shanghai 时 `UTCDateTime` 读回 UTC、列类型 `timestamp with time zone`；`session_scope` 回滚；`statement_timeout` 为 `2s`；`/health/storage` 为 ok、`revision=0001`、不含密码。跑完测试库已删除 |
| 2026-11-16 | P2：日常库 `doghunt`（0 张表）上 `make db-migrate` → `alembic downgrade -1` → `make db-migrate` | 每步成功，最后 `0001 (head)`；库里只有 `alembic_version` 一张表 |
| 2026-11-16 | P2：`uvicorn` 起在 8010，先库在，再 `docker compose stop postgres` 后重起 | 库在：0.6 秒就绪，`/health/storage` ok；停库：0.3 秒就绪，`/health` 200、`/odds` 200、`/health/storage` 200 + `degraded`（`Connection refused`），启动日志一条 warning；两份日志和 `data/logs` 里检索不到密码；随后 `start postgres`，healthy |
| 2026-11-16 | `git status`、在已跟踪和未跟踪文件中检索密码 | `backend/.env` 被忽略，检索不到密码 |
| 2026-11-16 | `cd backend && uv run --locked python -m unittest discover -s tests` | `Ran 177 tests / OK`；离线测试不依赖这些容器 |

## 限制、未决事项与下一步

- Milvus 重启后约 1 分钟内，建表、删集合可能报 `node not match`（etcd 里旧节点登记未过期），重试即可；P4 初始化命令要做有限重试。
- MinIO 镜像不是 Milvus 2.5 官方编排原配（原配的 `minio/minio` 拉不到），而是同一官方仓库较新编排里的镜像；已验证 v2.5.27 能在其中建集合、写入、搜索，P4 集成测试再确认。
- Milvus 已定为 v2.5.27（含 CVE-2026-26190 修复），P4 的 `pymilvus` 用 2.5.18。
- `/health/storage` 的 `milvus`、`embedding` 现在固定是 `not_implemented`，P4、P5 接入后再改成真实状态。
- 启动检查超过 1 秒时 `lifespan` 不再等，但那次查询还在线程里跑完；建连由 `connect_timeout=2` 限制（推算，没有单独实测数据库卡住的情况）。
- 迁移只走 Alembic；P2 的 `0001` 是空基线，开发文档第 6 节原计划的 `0001_memory_tables` 改成 P3 的下一个版本。
- 日志脱敏只登记 8 位及以上的值（`applog.register_secret`），数据库密码短于 8 位时不会被打码；本机密码 32 位。
- 独立复核找出的 4 条问题（非数字端口导致启动失败、并发建多个连接池、进程内迁移改日志配置、读版本吞错）已修并补用例。
- 下一步是 P3：记忆表模型、迁移、仓储与只读接口。

## 历史

- 2026-11-16：新建本子块；P1 完成开发编排、使用说明、检查脚本与 Makefile 目标（提交 `9e6f7af`）。同日 Milvus 从 v2.5.4 升到 v2.5.27，etcd 从 v3.5.16 升到 v3.5.18（提交 `90b131c`）。
- 2026-11-16：P2 关系库与迁移：后端连接 PostgreSQL、Alembic 空基线、`/health/storage`、测试隔离。
