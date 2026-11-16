# 本机开发存储（PostgreSQL + Milvus）

`compose.dev.yml` 在本机用 Docker 起四个容器：PostgreSQL（关系库）、Milvus standalone（向量库），以及 Milvus 依赖的 etcd（元数据）和 MinIO（段文件）。前后端仍在宿主机运行（`make backend`、`make frontend`）。方案与阶段进度见 [数据库与向量库接入开发文档](../docs/plans/2026-11-15-数据库与向量库接入开发文档.md)。

当前状态（2026-11-17）：后端连接 PostgreSQL（迁移到 `0002`，有五张记忆表和只读接口 `GET /api/v1/memory`）和 Milvus（`make memory-init` 建两个 v1 集合与别名），就绪检查 `GET /api/v1/health/storage` 报两者的真实状态。记忆还没有被聊天使用（P5 起）。Postgres 或 Milvus 停了后端照常启动，聊天和盘口不受影响，就绪检查显示 `degraded`。

## 一览

| 项目 | 值 |
| --- | --- |
| Compose 项目名 | `doghunt-dev`（文件顶层 `name`；卷名前缀 `doghunt-dev_`） |
| 镜像 | `postgres:17-alpine`、`quay.io/coreos/etcd:v3.5.18`、`milvusdb/minio:RELEASE.2024-12-18T13-15-44Z`、`milvusdb/milvus:v2.5.27`（选择理由写在编排文件开头；后端 SDK 用 `pymilvus` 2.5.18） |
| 宿主机端口 | Postgres `127.0.0.1:5432`，Milvus gRPC `127.0.0.1:19530`，Milvus 健康检查 `127.0.0.1:9091`；etcd、MinIO 不发布 |
| 数据卷 | `doghunt-dev_pg_data`、`doghunt-dev_etcd_data`、`doghunt-dev_minio_data`、`doghunt-dev_milvus_data` |
| 密码 | `DOGHUNT_DB_PASSWORD`，只写在被 Git 忽略的 `backend/.env` |
| 资源 | Docker Desktop 建议给 6 GB 以上内存；首次启动约 1 分钟才 healthy |

## 首次准备

1. 安装并启动 Docker Desktop（Apple Silicon 直接用 arm64 镜像，不要加 `platform: linux/amd64`）。
2. 没有 `backend/.env` 时从示例复制：`cp backend/.env.example backend/.env`。
3. 在 `backend/.env` 里给 `DOGHUNT_DB_PASSWORD` 填一个本机随机密码，例如用下面的命令生成后手动粘贴：

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
   ```

   密码第一次 `up` 时写进 Postgres 数据卷，之后再改 `.env` **不会**改库里的密码；要改需进库执行 `ALTER USER`，或在确认不要数据后重置（见下文）。

## 起停与查看

以下命令都在**项目根目录**执行。Makefile 目标和原始命令等价，任选一种。

| 操作 | Makefile | 原始命令 |
| --- | --- | --- |
| 启动 | `make storage-up` | `docker compose --env-file backend/.env -f deploy/compose.dev.yml up -d` |
| 查看状态 | `make storage-ps` | `docker compose --env-file backend/.env -f deploy/compose.dev.yml ps` |
| 检查 | `make storage-check` | `(cd backend && uv run --locked python ../scripts/check_storage.py)`（括号让命令结束后仍停在项目根目录） |
| 跟随日志 | `make storage-logs` | `docker compose --env-file backend/.env -f deploy/compose.dev.yml logs -f --tail=100` |
| 只看 Milvus 日志 | — | `docker compose --env-file backend/.env -f deploy/compose.dev.yml logs --tail=200 milvus` |
| 停止（保留数据） | `make storage-down` | `docker compose --env-file backend/.env -f deploy/compose.dev.yml down` |
| 迁移到最新版本 | `make db-migrate` | `(cd backend && uv run --locked alembic upgrade head)` |
| 查看迁移版本 | `make db-current` | `(cd backend && uv run --locked alembic current)` |
| 初始化 Milvus 集合、索引、别名 | `make memory-init` | `(cd backend && uv run --locked python -m app.services.memory init)` |
| 写入固定父块目录 | `make memory-seed` | `(cd backend && uv run --locked python -m app.services.memory seed --parents)` |
| 只读列出记忆 | — | `(cd backend && uv run --locked python -m app.services.memory list)`（加 `--all` 包含已忘记的） |
| 回退一个版本 | — | `(cd backend && uv run --locked alembic downgrade -1)`（有数据时先按下文备份） |
| 后端看存储状态 | — | `curl -fsS http://127.0.0.1:8000/api/v1/health/storage`（先 `make backend`） |

- `up -d` 后 Milvus 要等 etcd、MinIO healthy 才启动，自身健康检查留了 90 秒启动期。看到 `starting` 先等，不要反复重启。
- `make storage-check` 检查：四个容器 running 且 healthy；Postgres、Milvus 的端口只绑 `127.0.0.1`，etcd、MinIO 没发布；容器内 `pg_isready`；用 `backend/.env` 的密码经 TCP 登录执行 `SELECT 1`；宿主机能连 Postgres 端口；Milvus `/healthz` 返回 200、gRPC 端口能连。直接运行脚本时，全部通过退出码 0，有检查失败 1，没有 `.env`、密码为空或 Docker 没运行时 2；经 `make storage-check` 运行时，任何失败 make 都返回 2，要区分原因看输出或直接运行脚本。输出不含密码。
- 变量读取和 Compose 一致：终端里设置的同名环境变量（哪怕为空）优先于 `backend/.env`；`.env` 里允许 `export` 前缀和未加引号值后的 ` # 注释`。
- `down` 只删容器和网络，数据卷保留，下次 `up -d` 数据还在。
- 迁移和后端读同一份 `backend/.env` 的 `DOGHUNT_DB_*`（连接串在程序里拼，不写进 `alembic.ini`）。新克隆或重置后按顺序执行：`make storage-up` → `make db-migrate` → `make memory-seed` → `make memory-init`。

## 记忆库初始化（`memory-seed`、`memory-init`）

两条命令都可以重复执行，结果不变。

- `make memory-seed`：把固定的五个父块（`output_pref`、`method_pref`、`team_focus`、`event_research`、`review_notes`）写进 Postgres。已有的不重复插入，输出形如 `父块目录：新建 0，更新标题 0，未变 5`。要先 `make db-migrate`；数据库连不上或没迁移时退出码 1。
- `seed --samples` 另写 4 条开发样例子块（来源标“开发样例”），只用于开发和测试，`DOGHUNT_ENVIRONMENT=production` 时拒绝。目前没有删除命令，写进日常库前想清楚。
- `make memory-init`：在 Milvus 里建 `doghunt_memory_child_v1`、`doghunt_memory_parent_v1` 两个集合、索引，load 后建别名 `doghunt_memory_child`、`doghunt_memory_parent`。已存在的部分只检查不改。退出码：

  | 退出码 | 含义 | 处理 |
  | --- | --- | --- |
  | 0 | 成功（第二次执行会显示“已存在”“未变”） | — |
  | 1 | Milvus 连不上或操作失败 | `make storage-check`；刚重启 Milvus 时会自动对 `node not match` 重试 6 次、每次等 10 秒，仍失败就过一会儿再执行 |
  | 3 | 集合维度或字段与配置不一致，提示 `collection dim=X, config dim=Y` | 不会自动重建。确认 `DOGHUNT_EMBEDDING_DIM`；真要换维度，改 `DOGHUNT_MILVUS_COLLECTION_VERSION` 建新版本集合 |
  | 4 | 别名已指向别的集合，没有改动 | 确认要切换时运行 `(cd backend && uv run --locked python -m app.services.memory init --force)`；旧集合保留 |

  经 `make` 运行时，任何非 0 退出码 make 都返回 2，要区分原因看输出或直接运行命令。
- 后端启动时会查一次 Milvus（最多等 1 秒），没执行 `memory-init` 时 `/health/storage` 的 `milvus.status` 是 `not_initialized`。

## 端口冲突

`up` 报 `port is already allocated` 或 `address already in use` 时：

1. 查谁占了端口：`lsof -nP -iTCP:5432 -sTCP:LISTEN`（换成 19530 或 9091 查 Milvus）。
2. 能停就停掉占用的程序（例如本机 Homebrew 装的 Postgres：`brew services stop postgresql@17`，按实际版本）。
3. 不能停就在 `backend/.env` 里改宿主机端口，再重新 `make storage-up`：

   ```bash
   DOGHUNT_DB_PORT=55432
   DOGHUNT_MILVUS_PORT=29530
   DOGHUNT_MILVUS_HEALTH_PORT=29091
   ```

   编排和 `make storage-check` 都读这三个变量；容器内部端口不变。后端读同一个 `DOGHUNT_DB_PORT`；Milvus 端口后端不读，改了 `DOGHUNT_MILVUS_PORT` 要同时把 `DOGHUNT_MILVUS_URI` 改成 `http://127.0.0.1:29530`；改完重启后端。

## 备份 Postgres

Milvus 不需要备份，能从 Postgres 重建（重建命令在 P6 实现）。备份文件含记忆正文，放在项目外，不要提交（`.gitignore` 已忽略 `*.dump`）。

```bash
mkdir -p ~/doghunt-backups
docker compose --env-file backend/.env -f deploy/compose.dev.yml \
  exec -T postgres pg_dump -U doghunt -Fc doghunt > ~/doghunt-backups/doghunt-$(date +%Y-%m-%d).dump
```

升级镜像、跑迁移、重建向量之前都先备份。

## 恢复 Postgres

先停后端（`make backend` 那个终端按 Ctrl+C），再把备份喂给容器里的 `pg_restore`。`--clean --if-exists` 会先删掉备份里有的表再重建，库里现有的同名数据会被覆盖：

```bash
docker compose --env-file backend/.env -f deploy/compose.dev.yml \
  exec -T postgres pg_restore --clean --if-exists -U doghunt -d doghunt < ~/doghunt-backups/doghunt-2026-11-16.dump
```

把文件名换成实际的备份。P6 完成后，恢复完还要从 Postgres 重建向量：`cd backend && uv run --locked python -m app.services.memory rebuild`（**现在还没有这个命令**）。

## 重置（删除全部数据）

> **警告：下面的命令会永久删除 Postgres、Milvus、etcd、MinIO 四个数据卷，包括所有记忆和预测记录，不能撤销。** 只在明确不要这些数据时执行，执行前先按上文备份。

1. 确认操作的是开发编排：`docker compose ls` 里的项目名是 `doghunt-dev`，`docker volume ls --filter name=doghunt-dev_` 列出的正是要删的卷。交付编排的项目名是 `doghunt`，不要混用 `-f` 文件。
2. 执行：

   ```bash
   docker compose --env-file backend/.env -f deploy/compose.dev.yml down -v
   ```

3. 再 `make storage-up` 会用 `backend/.env` 当前的密码重新建库。

升级镜像不要用 `down -v`：先备份，再改 tag、`up -d`、跑迁移、重建向量，最后核对。

## 排错

| 现象 | 先看 | 处理 |
| --- | --- | --- |
| `up` 报 `set DOGHUNT_DB_PASSWORD in backend/.env` | `backend/.env` 是否有非空的 `DOGHUNT_DB_PASSWORD` | 按“首次准备”填写 |
| `failed to connect to the docker API` | Docker Desktop 是否在运行 | 启动 Docker Desktop 后重试 |
| Milvus 一直 `starting` 或反复重启 | `docker compose ... logs --tail=200 milvus`；Docker Desktop 内存设置 | 内存给到 6 GB 以上；确认 etcd、MinIO 都是 healthy |
| Milvus 刚重启后建表、删集合报 `node not match[expectedNodeID=…][actualNodeID=…]` | 是否刚 `stop`/`up` 过 Milvus | etcd 里旧节点的登记还没过期，等约 1 分钟再试；2026-11-16 实测重试后成功。`make memory-init` 会自动重试约 1 分钟 |
| `/health/storage` 里 `milvus.status` 是 `not_initialized` | 是否执行过 `make memory-init` | 执行它；别名建好后接口返回 `ok` |
| `storage-check` 报 Postgres 登录失败 | 是否在第一次 `up` 之后改过 `DOGHUNT_DB_PASSWORD` | 改回原密码，或进库 `ALTER USER doghunt PASSWORD '...'`，或备份后重置 |
| `storage-check` 报端口没绑在 127.0.0.1 | `compose.dev.yml` 的 `ports` 是否被改成 `0.0.0.0` 或省略了地址 | 改回 `127.0.0.1:`；Milvus 本机没有认证，Postgres 只有一个开发密码，都不能暴露到局域网 |
| 拉取镜像失败 | `docker pull <镜像>` 的报错 | 换成 Milvus 官方 standalone 编排里同主版本、带 arm64 的 tag，并写回开发文档第 8.1 节 |
