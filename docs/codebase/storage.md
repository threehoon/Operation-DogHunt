# 本机开发存储｜模块子块

[返回父块](../../CODEBASE.md) · 上级：[后端子块](backend.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | storage |
| 核对日期 | 2026-11-17（Asia/Shanghai） |
| 核对范围 | P1：`deploy/compose.dev.yml`、`deploy/README.md`、`scripts/check_storage.py`、`Makefile` 的 `storage-*` 目标、`backend/.env.example`、`.gitignore` 的 `*.dump`。P2（2026-11-16，基准 `90b131c` 加未提交改动）：`backend/app/db/`、`core/config.py` 的 `db_*`、`main.py` 的启动检查、`routes/health.py` 的 `/health/storage`、`alembic.ini`、`migrations/`、`Makefile` 的 `db-*`、`tests/_isolation.py`、`test_db.py`、`test_memory_integration.py`；`backend/.env` 只核对了有非空 `DOGHUNT_DB_PASSWORD`，没有读其他值。P3、P4（2026-11-17，基准 `2f6eb9a` 加未提交改动）：`models/memory.py`、`migrations/versions/0002_memory_tables.py`、`repositories/memory.py`、`schemas/memory.py`、`api/routes/memory.py`、`api/router.py`、`services/memory/parents.py`、`__main__.py`、`vector_init.py`、`integrations/vectorstore.py`、`config.py` 的 Milvus 字段、`main.py` 的 Milvus 启动检查、`routes/health.py` 与 `schemas/health.py`、`tests/_isolation.py`、`Makefile` 的 `memory-*`、`backend/.env.example`、`pyproject.toml` 与 `uv.lock` 的 pymilvus 及传递依赖；另外只读查了日常库和 Milvus 的当前状态 |
| 实现状态 | 部分实现：开发编排、使用说明、检查脚本（[开发文档](../plans/2026-11-15-数据库与向量库接入开发文档.md) P1）；后端连接 PostgreSQL、Alembic 迁移、存储就绪接口（P2）；五张记忆表、仓储、只读记忆接口、固定父块种子（P3）；Milvus 封装与假实现、集合初始化、启动检查、`/health/storage` 真实 Milvus 状态（P4）。嵌入、召回、写入、维护与重建未实现（P5、P6） |
| 验证程度 | 本轮运行验证：2026-11-17 离线 302 项（10 项集成默认跳过）、真实 Postgres 集成 6 项、真实 Milvus 集成 4 项、`storage-check` 通过（lead 整合后于 2026-11-17 00:31–00:32 在同一份源码上重跑，结果相同：离线 302 项 OK(skipped=10)、Postgres 集成 6 项、Milvus 集成 4 项、storage-check 11 项全部通过；lead 另在 8011 端口实测：Milvus 在时 /health/storage ok，stop milvus 后两次请求 0.009 秒、0.004 秒返回 degraded + milvus=down，/health 200，start 后约 6 秒恢复 ok，日志与响应里检索不到数据库密码）；8010 端口实测为实现方报告；P3、P4 独立复核问题都已修复并复查（见“验证记录”） |

## 负责什么

在本机用 Docker 起四个容器，给以后的长期记忆和预测记录用：PostgreSQL（关系库）、Milvus standalone（向量库），以及 Milvus 依赖的 etcd 和 MinIO。前后端仍在宿主机运行。P2 起后端连接 PostgreSQL；P3 起有五张记忆表和只读接口 `/api/v1/memory`；P4 起后端连接 Milvus（启动检查、`/api/v1/health/storage`、`make memory-init`）。记忆还没有被聊天使用（P5）。聊天、盘口和日志不依赖这些容器，Postgres 或 Milvus 停了后端照常启动。

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
| [routes/health.py](../../backend/app/api/routes/health.py) / [schemas/health.py](../../backend/app/schemas/health.py) | `GET /api/v1/health/storage`、`StorageHealthResponse` | 同步路由；断开时仍 HTTP 200。`database`：`status`、去密码 `url`、`latency_ms`、`revision`、`error`。`milvus`（P4，`MilvusHealth`）：`status` 为 `ok` / `down` / `not_initialized` / `fake`，`provider`、去账号密码的 `uri`、`latency_ms`、`collections`（别名 → 集合名）、`dim`、`error`、`detail`。整体 `degraded` 当且仅当 `database=down` 或 `milvus` 为 `down` / `not_initialized`；`fake` 和 `embedding=not_implemented` 不计入 |
| [alembic.ini](../../backend/alembic.ini)、[migrations/env.py](../../backend/migrations/env.py)、[0001_baseline.py](../../backend/migrations/versions/0001_baseline.py) | `run_migrations_online/offline`；版本 `0001` | 连接串从 `get_settings()` 读，不写在 ini；导入 `app.models` 和 `Base.metadata` 供 autogenerate；SQLite 时 `render_as_batch`。`0001` 是空基线 |
| [0002_memory_tables.py](../../backend/migrations/versions/0002_memory_tables.py) | 版本 `0002`（`down_revision=0001`） | 手写，建五张记忆表；取值写死在迁移里不从模型导入；约束名用 `op.f()` 写成最终名；`downgrade` 按外键倒序删表 |
| [models/memory.py](../../backend/app/models/memory.py) | `MemoryParent`、`MemoryChild`、`MemoryUsage`、`MemoryEvent`、`MemoryExtraction`；取值元组 `BLOCK_KINDS`、`BLOCK_STATUSES`、`EVENT_ACTIONS` 等 | 开发文档第 7.3 节五张表。CHECK：子块 `kind`、`status`、`embed_state`、`scope_kind`、`source_kind`、标题 ≤100、正文 ≤300、`forgotten_at IS NULL OR status='disabled'`；父块 `embed_state`；事件 `action`；抽取 `outcome`。部分唯一索引 `uq_memory_children_live_content`（只覆盖 active/candidate）加 5 个普通索引，`ix_memory_usage_trace_id`、`ix_memory_events_created_at`。外键 CASCADE / 自引用 SET NULL，不建 ORM relationship。`block_ids` 在 Postgres 是 JSONB |
| [repositories/memory.py](../../backend/app/repositories/memory.py) | `list_parents`、`seed_parents`、`list_children`、`get_child`、`find_by_content_hash`、`insert_child`、`update_child`、`disable_child`、`forget_child`、`restore_child`、`restore_status`、`add_event`、`list_events`、`DATABASE_UNAVAILABLE_ERRORS` | 只 flush 不 commit，事务由调用方 `session_scope` 管；列表默认不含已忘记，`updated_at` 倒序加 id，`limit` 1–500，事件 1–200；改过滤相关字段时 `embed_state` 回 `pending`；停用不写事件，忘记写 `forget`，恢复写 `restore` 并按 `superseded_by` / `expires_at` 推断状态 |
| [schemas/memory.py](../../backend/app/schemas/memory.py) / [routes/memory.py](../../backend/app/api/routes/memory.py) | `MemoryTreeResponse`、`MemoryChildOut`；`GET /api/v1/memory`、`GET /api/v1/memory/children/{child_id}` | camelCase 输出，对齐前端 `types/memory.ts`；时间输出带偏移的 UTC ISO 8601。同步路由；连接类错误和连接串无效 → 503（不带连接串），没找到 → 404，其他错误 500 |
| [parents.py](../../backend/app/services/memory/parents.py) | `PARENT_CATALOG`、`PARENT_KEYS`、`get_parent_spec`、`is_known_parent_key` | 固定五个父块：`output_pref`、`method_pref`、`team_focus`、`event_research`、`review_notes` |
| [services/memory/__main__.py](../../backend/app/services/memory/__main__.py) | `seed --parents`、`seed --samples`、`list [--all]`、`init [--force]` | 先登记数据库密码。`seed` 不带参数退出码 2；数据库不可用或写入冲突 1；`--samples` 在 `environment=production` 时拒绝（1）。`init` 懒加载 `vector_init`，`seed`/`list` 不导入 pymilvus |
| [vectorstore.py](../../backend/app/integrations/vectorstore.py) | `VectorStore`、`FakeVectorStore`、`MilvusVectorStore`、`get_vector_store`、`close_vector_store`、`new_client`、`build_child_filter`、`build_schema`、`safe_uri`、`short_error`、`VectorStoreError` | 唯一导入 pymilvus 的地方（懒导入，导入期间把 `dotenv.load_dotenv` 换成空函数，不让它读 `backend/.env`）；每个客户端独立连接名；普通调用 float 超时加 `retry_times=0` 只发一次，upsert/load 用 int 超时 `max(1, ceil(t))`（`single_attempt`、`rpc_timeout`）；建连接无锁快路径、等锁最多 timeout、连不上后 5 秒冷却直接报 down；按 id 删除自己拼 `id in ["…"]`（`build_id_filter`，id 白名单 `[A-Za-z0-9_.:-]{1,36}`，每批 100）；非法 URI 整条登记为秘密、`uri` 显示“（地址格式无效）”；只用别名访问集合。字段按开发文档第 8.2 节，长期有效写 `4102444800`。检索 COSINE、`ef=max(64, limit)`、Bounded；过滤值只允许 `[A-Za-z0-9_.:-]{1,64}`；`event_id=None` 不过滤、给 id 取该场或全局、空串只取全局，`parent_ids=[]` 返回空。`check()` / `ensure_loaded()`（只在未 load 时 load 一次）供健康检查和启动用 |
| [vector_init.py](../../backend/app/services/memory/vector_init.py) | `run_init`、`EXIT_OK/FAILED/MISMATCH/ALIAS_CONFLICT` | 建集合 `<别名>_<版本>` → 核对维度和字段 → 补索引（HNSW M=16/efConstruction=200/COSINE；子块 `parent_id`、`event_id` INVERTED，不支持时退回 AUTOINDEX）→ load → 别名（指向别处时不改，`--force` 才切）。退出码 0/1/3/4；`node not match` 重试 6 次、间隔 10 秒；超时至少 30 秒并取整为 int（`vs.rpc_timeout`） |
| [config.py](../../backend/app/core/config.py) | `milvus_uri`、`milvus_token`、`milvus_db_name`、`milvus_child_alias`、`milvus_parent_alias`、`milvus_timeout_seconds`、`milvus_collection_version`、`milvus_startup_check`、`vectorstore_provider`、`embedding_dim` | 默认 `http://127.0.0.1:19530`、无 token、`default`、两个别名、2 秒、`v1`、启动检查开、`milvus`、1024；token 是 `SecretStr` |
| [main.py](../../backend/app/main.py) | `_startup_milvus_check`、`STARTUP_MILVUS_CHECK_SECONDS` | 登记 Milvus token 和 URI 密码；`provider=milvus` 且开关打开时在线程里 `ensure_loaded`，最多等 1 秒，记 `milvus.startup`（成功 info，失败或超时 warning），不阻止启动；退出时 `close_vector_store` |
| [Makefile](../../Makefile) | `db-migrate`、`db-current`、`memory-init`、`memory-seed` | `alembic upgrade head`、`alembic current`、`python -m app.services.memory init`、`… seed --parents`（都在 `backend/` 下） |
| [tests/_isolation.py](../../backend/tests/_isolation.py) | `FORCED_ENV`、`TEST_DATABASE_URL` | 每个测试文件第一行导入：日志目录、临时 SQLite、关连库和连 Milvus 的启动检查（`DOGHUNT_MILVUS_STARTUP_CHECK=false`）、假嵌入/向量库、记忆开关全关；已缓存的配置和 engine 会被清掉 |
| [test_db.py](../../backend/tests/test_db.py)、[test_memory_store.py](../../backend/tests/test_memory_store.py)、[test_memory_api.py](../../backend/tests/test_memory_api.py)、[test_vectorstore.py](../../backend/tests/test_vectorstore.py) | 离线 27、29、9、50 项 | 数据库连接；记忆表约束、仓储、种子、迁移往返与结构比对；只读接口；假向量库、过滤表达式、模拟客户端下的 Milvus 封装与 `run_init`、健康与启动检查、pymilvus 导入边界 |
| [test_memory_integration.py](../../backend/tests/test_memory_integration.py)、[test_vector_integration.py](../../backend/tests/test_vector_integration.py) | 集成 6 项、4 项（`DOGHUNT_TEST_INTEGRATION=1`） | Postgres：新建再删除 `doghunt_test` 库，不动日常库。Milvus：用别名 `doghunt_test_memory_*`、版本 `itest`/`itest2`，不碰正式别名，跑完删除并检查无残留；连不上时整类跳过 |

## 输入、输出和依赖

- 配置：Compose 用 `--env-file backend/.env` 读密码和端口变量；没填密码时 `up` 直接报错。后端 `Settings` 从同一个文件读 `DOGHUNT_DB_*`（P2）和 `DOGHUNT_MILVUS_*`、`DOGHUNT_VECTORSTORE_PROVIDER`、`DOGHUNT_EMBEDDING_DIM`（P4）；两个 Milvus 端口变量后端不用，改了 `DOGHUNT_MILVUS_PORT` 要同步改 `DOGHUNT_MILVUS_URI`。检查脚本和后端都是环境变量优先于 `backend/.env`；改了要重启后端。
- 依赖：Docker Desktop（本机实测服务端 29.9.3、Compose 5.7.0、分到 7.75 GiB 内存）。检查脚本还依赖 `uv`（Makefile 方式）或直接用宿主机 Python 运行（实测 3.12 与 3.14）。后端 P4 加了 `pymilvus` 2.5.18，连带 grpcio、milvus-lite、pandas、pyarrow、faiss-cpu、numpy、protobuf 等传递依赖，本机 `.venv` 里 pyarrow 约 126 MB、pandas 约 53 MB、grpc 约 41 MB（版本见[开发文档](../plans/2026-11-15-数据库与向量库接入开发文档.md)第 8.1 节）。
- 数据：只在四个 Docker 卷里，`down` 保留，`down -v` 删除。Postgres 密码在第一次建库时写入卷，之后改 `.env` 不会改库里的密码。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 开发编排、起停、检查 | 已实现，本机运行验证 | 上表 |
| 备份与恢复 Postgres | 已写命令并实测一次 | [deploy/README.md](../../deploy/README.md) |
| 后端连接 Postgres、迁移、存储健康接口 | 已实现（P2） | `db/session.py`、`migrations/`、`/health/storage` |
| 记忆表、仓储、只读接口、固定父块种子 | 已实现（P3）；编辑、停用、忘记、恢复只有仓储函数，没有接口和命令（P6.4） | `models/memory.py`、`0002`、`repositories/memory.py`、`routes/memory.py`、`__main__.py` |
| Milvus 封装、集合与别名初始化、启动与健康检查 | 已实现（P4）；集合是空的，还没有写向量的流程 | `vectorstore.py`、`vector_init.py`、`main.py`、`/health/storage` |
| 嵌入、召回、聊天注入 | 未实现（P5） | 聊天不调用记忆或向量库 |
| 从 Postgres 重建向量、对账、过期维护 | 未实现（P6） | 没有 `rebuild` 命令 |
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
| 2026-11-17 | `coding_check`：`backend-offline-tests`（`cd backend && uv run --locked python -m unittest discover -s tests`） | P4 复核修复后（实现方运行的 `coding_check`）：`Ran 302 tests / OK (skipped=10)`，其中 `test_vectorstore` 50 项；10 项是默认跳过的集成测试。修复前为 293 项；P3 完成时中间结果 257 项、`skipped=6`（实现方报告） |
| 2026-11-17 | `coding_check`：`backend-db-integration`（`DOGHUNT_TEST_INTEGRATION=1 … -p test_memory_integration.py -v`） | 本轮运行：6 项 OK。新增 `test_5`：Postgres 上 JSONB、timestamptz、bigint，部分唯一索引与 SQLite 同序列一致，三条外键规则，CHECK（标题超长先被 `varchar(100)` 拦下），`forgotten_at` CHECK；`test_6`：`seed --parents` 命令跑两次（第二次“未变 5”）、`GET /api/v1/memory` 与详情、404、不含密码。`test_1` 迁移往返到 `0002` 后结构比对 `([], {})` |
| 2026-11-17 | `coding_check`：`backend-vector-integration`（`… -p test_vector_integration.py -v`）、`storage-check` | 本轮运行：4 项 OK、`storage-check` 11 项 OK。初始化两次幂等，索引 HNSW / INVERTED / INVERTED；插入、过期过滤、按比赛与父块过滤、删除后搜不到、对账 id 集合；别名冲突退出码 4，`--force` 切换并保留旧集合；维度 16 对 1024 退出码 3 |
| 2026-11-16/17 | 日常库与日常 Milvus（实现方报告） | 日常库 `0001 → 0002`，`seed --parents` 两次为新建 5 / 未变 5；复核修改迁移后 `downgrade` 到 `0001` 再 `upgrade`、重新 seed，父块 UUID 变了。`make memory-init` 两次退出码 0（第二次全部已存在）；`DOGHUNT_EMBEDDING_DIM=768` 退出码 3，提示“维度不一致：collection dim=1024, config dim=768” |
| 2026-11-17 | 只读核对当前状态（docs 角色） | `alembic current` 为 `0002 (head)`；5 个父块、0 个子块、0 条事件；`MilvusVectorStore().check()` 为 `ok`，两个正式别名指向 `*_v1`，两个集合都是 0 行；四个容器 healthy |
| 2026-11-16/17 | 8010 端口起后端（实现方报告） | Milvus 在：`/health/storage` 中 `milvus=ok`、`collections` 为两个 `*_v1`、`dim=1024`，`database` ok、`revision=0002`；`GET /api/v1/memory` 200（parents 5 / children 0 / log 0），`/children/nope` 404，不含密码。`docker compose stop milvus` 后启动：一条 warning“Milvus 1 秒内没有响应，先不用语义召回启动”，`/health`、`/odds` 200，`/health/storage` 200 + `degraded`、`milvus=down`（复核修复前的代码）；`start milvus` 后约 8 秒 `check_storage` 退出码 0 |
| 2026-11-17 | 复核修复后 8010 复测（实现方报告） | 客户端已缓存后 `stop milvus`：`/health/storage` 连续 3 次 200 + `degraded`、`milvus=down`，每次约 0.005 秒（连接被拒立即返回）；`start milvus` 后 5 秒内 `check_storage` 退出码 0，同一进程缓存的客户端自动恢复，下一次 `ok`。`init` 再跑退出码 0、全部未变；`DOGHUNT_EMBEDDING_DIM=768` 退出码 3 |
| 2026-11-16 | `git status`、在已跟踪和未跟踪文件中检索密码 | `backend/.env` 被忽略，检索不到密码 |
| 2026-11-16 | `cd backend && uv run --locked python -m unittest discover -s tests` | `Ran 177 tests / OK`；离线测试不依赖这些容器 |

## 限制、未决事项与下一步

- Milvus 重启后约 1 分钟内，建表、删集合可能报 `node not match`（etcd 里旧节点登记未过期）；`vector_init` 对它重试 6 次、间隔 10 秒，其他错误不重试。
- MinIO 镜像不是 Milvus 2.5 官方编排原配（原配的 `minio/minio` 拉不到），而是同一官方仓库较新编排里的镜像；P4 的 Milvus 集成测试 4 项在这套编排上通过（2026-11-17）。
- Milvus 已定为 v2.5.27（含 CVE-2026-26190 修复），后端 `pymilvus` 锁定 2.5.18（`>=2.5.18,<2.6`）；两者要一起升级。
- `/health/storage` 的 `milvus` 已是真实状态（P4）；`embedding` 仍固定 `not_implemented`，P5 再改。Milvus 被停止（连接被拒）时这个接口立即返回，实测约 0.005 秒；Milvus 不响应时受 `DOGHUNT_MILVUS_TIMEOUT_SECONDS`（默认 2 秒）限制；连不上后 5 秒内直接报 `down`。
- pymilvus 有自己的日志 handler（`propagate=False`），不经过 `applog` 脱敏：连接失败时打印连接名 `doghunt-<uuid>`，RPC 失败时往 stderr 打含 `127.0.0.1:19530` 的 ERROR。目前没有 token；启用 `DOGHUNT_MILVUS_TOKEN` 后要再核对。
- 停用目前不写维护事件：`memory_events.action` 的 CHECK 没有 `disable` 值，留给 P6.4 决定；停用、忘记后的父块摘要重算在 P6。
- 本机日常 Milvus 已有两个空的正式 v1 集合和别名；日常库迁移 `0002` 后重建过一次，父块 UUID 和最初 seed 时不同（还没有子块引用它们，P5 写向量前不影响）。
- 阶段门禁“Milvus 能从 Postgres 重建”还没做，要等 P6 的 `rebuild`。
- P4 独立复核（reviewer-p4）首轮 1 个高（float 超时让 pymilvus 重试 75 次，断开后一次调用卡约 210 秒）、2 个中等（删除表达式可注入；建连全程持锁使 timeout 失效）、2 个低（导入时还原环境变量抹掉其他线程改动；非法 URI 使启动失败）问题，都已修复，复查全部 resolved，没有新的高、中问题。
- P4 复核遗留 4 个低问题（未修，P5 要注意）：`get_load_state` 遇 `Loading` 时 pymilvus 内部的 `get_loading_progress` 不带 `retry_times`；Milvus 恢复后 gRPC 默认重连退避可能让状态一段时间仍是 `down`（推断，未实测）；大集合在超时内 load 不完会先报 load 失败、Milvus 后台继续加载；pymilvus 自带日志器在 RPC 失败时往 stderr 打含 `127.0.0.1:19530` 的 ERROR，不经 `applog` 脱敏（目前没有 token）。
- 启动检查超过 1 秒时 `lifespan` 不再等，但那次查询还在线程里跑完；建连由 `connect_timeout=2` 限制（推算，没有单独实测数据库卡住的情况）。
- 迁移只走 Alembic；`0001` 是空基线，记忆表在 `0002`。
- 日志脱敏只登记 8 位及以上的值（`applog.register_secret`），数据库密码短于 8 位时不会被打码；本机密码 32 位。
- 独立复核找出的 4 条问题（非数字端口导致启动失败、并发建多个连接池、进程内迁移改日志配置、读版本吞错）已修并补用例。
- P3 独立复核（reviewer-p3）首轮 2 个中等、3 个低问题已修，复查全部 resolved（见开发文档第 18 节 v0.7）。
- 下一步是 P5：嵌入与召回。

## 历史

- 2026-11-16：新建本子块；P1 完成开发编排、使用说明、检查脚本与 Makefile 目标（提交 `9e6f7af`）。同日 Milvus 从 v2.5.4 升到 v2.5.27，etcd 从 v3.5.16 升到 v3.5.18（提交 `90b131c`）。
- 2026-11-16：P2 关系库与迁移：后端连接 PostgreSQL、Alembic 空基线、`/health/storage`、测试隔离。
- 2026-11-17：P3 记忆表（迁移 `0002`、仓储、只读接口、父块种子）与 P4 Milvus 接入（`pymilvus` 2.5.18、向量库封装、`make memory-init`、启动检查、`/health/storage` 真实状态）；P3、P4 复核问题已修；未提交。
