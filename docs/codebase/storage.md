# 本机开发存储｜模块子块

[返回父块](../../CODEBASE.md) · 上级：[后端子块](backend.md)

| 项目 | 当前记录 |
| --- | --- |
| 模块标识 | storage |
| 核对日期 | 2026-11-16（Asia/Shanghai） |
| 核对范围 | `deploy/compose.dev.yml`、`deploy/README.md`、`scripts/check_storage.py`、`Makefile` 的 `storage-*` 目标、`backend/.env.example` 新增变量、`.gitignore` 的 `*.dump`；Git 基准 `f528ea5` 加本轮未提交改动；`backend/.env` 只核对了是否有非空 `DOGHUNT_DB_PASSWORD`，没有读其他值 |
| 实现状态 | 部分实现：开发编排、使用说明和检查脚本已实现（[开发文档](../plans/2026-11-15-数据库与向量库接入开发文档.md) P1）；后端连接、迁移、表、Milvus 集合、存储健康接口都未实现（P2–P4） |
| 验证程度 | 本轮运行验证：本机 arm64 实际起停容器，P1 完成标准逐条核对（见下文“验证记录”） |

## 负责什么

在本机用 Docker 起四个容器，给以后的长期记忆和预测记录用：PostgreSQL（关系库）、Milvus standalone（向量库），以及 Milvus 依赖的 etcd 和 MinIO。前后端仍在宿主机运行。**后端代码目前不读取也不连接它们**，聊天、盘口和日志与这些容器无关，容器停了也不影响现有功能。

## 到哪里找实现

| 源码或配置 | 入口或关键符号 | 用途 |
| --- | --- | --- |
| [compose.dev.yml](../../deploy/compose.dev.yml) | 顶层 `name: doghunt-dev`；服务 `postgres`、`etcd`、`minio`、`milvus`；卷 `pg_data`、`etcd_data`、`minio_data`、`milvus_data` | 镜像 `postgres:17-alpine`、`quay.io/coreos/etcd:v3.5.16`、`milvusdb/minio:RELEASE.2024-12-18T13-15-44Z`、`milvusdb/milvus:v2.5.4`；Postgres 与 Milvus 端口只绑 `127.0.0.1`，etcd、MinIO 不发布；四个服务都有健康检查，Milvus 等 etcd、MinIO healthy 才启动 |
| [deploy/README.md](../../deploy/README.md) | — | 起停、日志、检查、端口冲突、备份、恢复、重置（带警告）、排错 |
| [check_storage.py](../../scripts/check_storage.py) | `main`、`check_containers`、`check_postgres`、`check_milvus`、`Checker.redact` | 只用标准库。检查容器 running/healthy 和项目名、端口只绑本机、容器内 `pg_isready`、用 `.env` 密码经 TCP 登录 `SELECT 1`、宿主机端口可连、Milvus `/healthz`；退出码 0 通过、1 有失败、2 配置或 Docker 不可用；打印前把密码替换成 `***` |
| [Makefile](../../Makefile) | `STORAGE_COMPOSE`、`storage-up`、`storage-down`、`storage-ps`、`storage-logs`、`storage-check` | `docker compose --env-file backend/.env -f deploy/compose.dev.yml …`；`storage-check` 用 `cd backend && uv run --locked python ../scripts/check_storage.py` |
| [backend/.env.example](../../backend/.env.example) | `DOGHUNT_DB_PASSWORD`、`DOGHUNT_DB_PORT`、`DOGHUNT_MILVUS_PORT`、`DOGHUNT_MILVUS_HEALTH_PORT` | 密码必填；三个端口变量用于端口冲突，默认 5432、19530、9091 |

## 输入、输出和依赖

- 配置：Compose 用 `--env-file backend/.env` 读密码和端口变量；没填密码时 `up` 直接报错。`Settings` 设了 `extra="ignore"`，后端读到这些新变量不会报错，但目前也不使用。检查脚本里环境变量优先于 `backend/.env`。
- 依赖：Docker Desktop（本机实测服务端 29.9.3、Compose 5.7.0、分到 7.75 GiB 内存）。检查脚本还依赖 `uv`（Makefile 方式）或直接用宿主机 Python 运行（实测 3.12 与 3.14）。
- 数据：只在四个 Docker 卷里，`down` 保留，`down -v` 删除。Postgres 密码在第一次建库时写入卷，之后改 `.env` 不会改库里的密码。

## 已实现与缺口

| 能力 | 当前情况 | 依据 |
| --- | --- | --- |
| 开发编排、起停、检查 | 已实现，本机运行验证 | 上表 |
| 备份与恢复 Postgres | 已写命令并实测一次 | [deploy/README.md](../../deploy/README.md) |
| 后端连接 Postgres、迁移、存储健康接口 | 未实现（P2） | `backend/app/db/` 仍只有文档字符串 |
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
| 2026-11-16 | `git status`、在已跟踪和未跟踪文件中检索密码 | `backend/.env` 被忽略，检索不到密码 |
| 2026-11-16 | `cd backend && uv run --locked python -m unittest discover -s tests` | `Ran 177 tests / OK`；离线测试不依赖这些容器 |

## 限制、未决事项与下一步

- Milvus 重启后约 1 分钟内，建表、删集合可能报 `node not match`（etcd 里旧节点登记未过期），重试即可；P4 初始化命令要做有限重试。
- MinIO 镜像不是 Milvus v2.5.4 标签原配，而是同一官方仓库较新编排的组合；P1 只验证了 Milvus 能在其中建桶、写段文件，P4 集成测试再确认。
- Milvus 2.5 分支已到 v2.5.27，本轮按方案用 v2.5.4，是否升级留到 P4 前决定。
- 下一步是 P2：后端加 SQLAlchemy、psycopg、Alembic，配置里接 `DOGHUNT_DB_*`，加 `/health/storage`。

## 历史

- 2026-11-16：新建本子块；P1 完成开发编排、使用说明、检查脚本与 Makefile 目标。
