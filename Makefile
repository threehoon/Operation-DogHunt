.PHONY: help setup frontend backend build storage-up storage-down storage-ps storage-logs storage-check db-migrate db-current

# 本机开发存储：密码从 backend/.env 读，项目名 doghunt-dev；用法见 deploy/README.md
STORAGE_COMPOSE = docker compose --env-file backend/.env -f deploy/compose.dev.yml

help:
	@echo "make setup          安装前后端依赖（首次运行）"
	@echo "make backend        启动 FastAPI：127.0.0.1:8000"
	@echo "make frontend       启动 Vue：127.0.0.1:5173"
	@echo "make build          检查 TypeScript 并构建前端"
	@echo "make storage-up     启动本机 PostgreSQL + Milvus（Docker）"
	@echo "make storage-down   停止存储容器，保留数据卷"
	@echo "make storage-ps     查看存储容器状态"
	@echo "make storage-logs   跟随存储容器日志（Ctrl+C 退出）"
	@echo "make storage-check  检查容器健康、端口只绑本机、数据库登录与 Milvus 健康"
	@echo "make db-migrate     数据库迁移到最新版本（alembic upgrade head）"
	@echo "make db-current     查看数据库当前迁移版本"

setup:
	npm --prefix frontend ci
	cd backend && uv sync --locked

frontend:
	npm --prefix frontend run dev

backend:
	cd backend && uv run --locked uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port 8000

build:
	npm --prefix frontend run build

storage-up:
	$(STORAGE_COMPOSE) up -d

storage-down:
	$(STORAGE_COMPOSE) down

storage-ps:
	$(STORAGE_COMPOSE) ps

storage-logs:
	$(STORAGE_COMPOSE) logs -f --tail=100

storage-check:
	cd backend && uv run --locked python ../scripts/check_storage.py

db-migrate:
	cd backend && uv run --locked alembic upgrade head

db-current:
	cd backend && uv run --locked alembic current
