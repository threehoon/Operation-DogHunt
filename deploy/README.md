# 本机开发存储（PostgreSQL + Milvus）

`compose.dev.yml` 在本机用 Docker 起四个容器：PostgreSQL（关系库）、Milvus standalone（向量库），以及 Milvus 依赖的 etcd（元数据）和 MinIO（段文件）。前后端仍在宿主机运行（`make backend`、`make frontend`）。方案与阶段进度见 [数据库与向量库接入开发文档](../docs/plans/2026-11-15-数据库与向量库接入开发文档.md)。

当前状态：P1 只有容器和检查脚本；后端还不连接数据库或 Milvus（P2、P4 才接入），聊天和盘口不受这些容器影响。

## 一览

| 项目 | 值 |
| --- | --- |
| Compose 项目名 | `doghunt-dev`（文件顶层 `name`；卷名前缀 `doghunt-dev_`） |
| 镜像 | `postgres:17-alpine`、`quay.io/coreos/etcd:v3.5.16`、`milvusdb/minio:RELEASE.2024-12-18T13-15-44Z`、`milvusdb/milvus:v2.5.4`（选择理由写在编排文件开头） |
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

- `up -d` 后 Milvus 要等 etcd、MinIO healthy 才启动，自身健康检查留了 90 秒启动期。看到 `starting` 先等，不要反复重启。
- `make storage-check` 检查：四个容器 running 且 healthy；Postgres、Milvus 的端口只绑 `127.0.0.1`，etcd、MinIO 没发布；容器内 `pg_isready`；用 `backend/.env` 的密码经 TCP 登录执行 `SELECT 1`；宿主机能连 Postgres 端口；Milvus `/healthz` 返回 200、gRPC 端口能连。直接运行脚本时，全部通过退出码 0，有检查失败 1，没有 `.env`、密码为空或 Docker 没运行时 2；经 `make storage-check` 运行时，任何失败 make 都返回 2，要区分原因看输出或直接运行脚本。输出不含密码。
- 变量读取和 Compose 一致：终端里设置的同名环境变量（哪怕为空）优先于 `backend/.env`；`.env` 里允许 `export` 前缀和未加引号值后的 ` # 注释`。
- `down` 只删容器和网络，数据卷保留，下次 `up -d` 数据还在。

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

   编排和 `make storage-check` 都读这三个变量；容器内部端口不变。后端以后接入时（P2/P4）要用同样的端口，改完重启后端。

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
| Milvus 刚重启后建表、删集合报 `node not match[expectedNodeID=…][actualNodeID=…]` | 是否刚 `stop`/`up` 过 Milvus | etcd 里旧节点的登记还没过期，等约 1 分钟再试；2026-11-16 实测重试后成功 |
| `storage-check` 报 Postgres 登录失败 | 是否在第一次 `up` 之后改过 `DOGHUNT_DB_PASSWORD` | 改回原密码，或进库 `ALTER USER doghunt PASSWORD '...'`，或备份后重置 |
| `storage-check` 报端口没绑在 127.0.0.1 | `compose.dev.yml` 的 `ports` 是否被改成 `0.0.0.0` 或省略了地址 | 改回 `127.0.0.1:`；Milvus 本机没有认证，Postgres 只有一个开发密码，都不能暴露到局域网 |
| 拉取镜像失败 | `docker pull <镜像>` 的报错 | 换成 Milvus 官方 standalone 编排里同主版本、带 arm64 的 tag，并写回开发文档第 8.1 节 |
