"""检查本机开发存储（deploy/compose.dev.yml）是否可用。

只用标准库，不改任何数据。检查项：
1. 四个容器的运行与健康状态（docker compose ps）；
2. 宿主机发布的端口是否都只绑 127.0.0.1，etcd、MinIO 是否没有发布；
3. PostgreSQL：容器内 pg_isready；用 backend/.env 的密码走 TCP 登录执行 SELECT 1；宿主机端口能连上；
4. Milvus：宿主机 /healthz 返回 200，gRPC 端口能连上。

全部通过时退出码 0，有失败时 1，配置或 Docker 本身不可用时 2。
输出不包含密码；子进程的报错在打印前会把密码替换成 ***。
用法（项目根目录）：make storage-check
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "backend" / ".env"
COMPOSE_FILE = ROOT / "deploy" / "compose.dev.yml"
PROJECT_NAME = "doghunt-dev"
SERVICES = ("postgres", "etcd", "minio", "milvus")
# 只有这两个服务应该把端口发布到宿主机
PUBLISHED_SERVICES = {"postgres", "milvus"}
DB_USER = "doghunt"
DB_NAME = "doghunt"
TIMEOUT_SECONDS = 5


def read_env_file(path: Path) -> dict[str, str]:
    """按 Compose 的 .env 规则读取 KEY=VALUE：忽略注释和空行，允许 export 前缀；
    引号里的值原样保留，未加引号的值在“空白 + #”处截掉行尾注释。"""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] in "'\"" and value[0] in value[1:]:
            value = value[1:value.index(value[0], 1)]
        else:
            for marker in (" #", "\t#"):
                if marker in value:
                    value = value[:value.index(marker)].rstrip()
        values[key.strip()] = value
    return values


def setting(env: dict[str, str], key: str, default: str) -> str:
    """与 Compose 插值一致：环境变量只要设置了（哪怕为空）就优先于 backend/.env；
    最终为空时用默认值（对应 ${VAR:-默认}；密码为空由调用方按 ${VAR:?} 报错）。"""
    value = os.environ[key] if key in os.environ else env.get(key, "")
    return value or default


class Checker:
    def __init__(self, password: str) -> None:
        self.password = password
        self.failures = 0

    def redact(self, text: str) -> str:
        return text.replace(self.password, "***") if self.password else text

    def report(self, ok: bool, name: str, detail: str) -> None:
        if not ok:
            self.failures += 1
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {name}: {self.redact(detail)}")

    def compose(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        cmd = ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(COMPOSE_FILE), *args]
        env = {**os.environ, **(extra_env or {})}
        return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def first_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "（无输出）"


def tcp_open(port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT_SECONDS):
            return True, f"127.0.0.1:{port} 可连接"
    except OSError as exc:
        return False, f"127.0.0.1:{port} 连不上（{exc.strerror or exc}）"


def check_containers(checker: Checker) -> None:
    result = checker.compose("ps", "--all", "--format", "json")
    if result.returncode != 0:
        checker.report(False, "容器状态", first_line(result.stderr or result.stdout))
        return
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    by_service = {row.get("Service"): row for row in rows}
    projects = {row.get("Project") for row in rows}
    if rows and projects != {PROJECT_NAME}:
        checker.report(False, "项目名", f"期望 {PROJECT_NAME}，实际 {sorted(p or '?' for p in projects)}")

    for service in SERVICES:
        row = by_service.get(service)
        if row is None:
            checker.report(False, f"容器 {service}", "没有创建；先运行 make storage-up")
            continue
        state, health = row.get("State", "?"), row.get("Health") or ""
        detail = f"{state} / {health or '无健康状态'}" if state == "running" else f"{state}（没有运行）"
        checker.report(state == "running" and health == "healthy", f"容器 {service}", detail)

        publishers = [p for p in row.get("Publishers") or [] if p.get("PublishedPort")]
        if service not in PUBLISHED_SERVICES:
            if publishers:
                ports = ", ".join(f"{p.get('URL')}:{p['PublishedPort']}" for p in publishers)
                checker.report(False, f"端口 {service}", f"不应发布到宿主机，实际发布了 {ports}")
            continue
        if not publishers:
            if state == "running":
                checker.report(False, f"端口 {service}", "没有发布端口")
            continue
        exposed = [p for p in publishers if p.get("URL") != "127.0.0.1"]
        ports = ", ".join(f"{p.get('URL')}:{p['PublishedPort']}->{p.get('TargetPort')}" for p in publishers)
        if exposed:
            checker.report(False, f"端口 {service}", f"有端口没绑在 127.0.0.1：{ports}")
        else:
            checker.report(True, f"端口 {service}", f"只绑本机 {ports}")


def check_postgres(checker: Checker, port: int) -> None:
    ready = checker.compose("exec", "-T", "postgres", "pg_isready", "-U", DB_USER, "-d", DB_NAME)
    checker.report(ready.returncode == 0, "Postgres pg_isready", first_line(ready.stdout or ready.stderr))

    # 连容器自己的服务名（不是回环地址），走 pg_hba 的密码规则，确认 backend/.env 的密码有效。
    # 密码经 PGPASSWORD 环境变量传入，不出现在命令行参数里。
    login = checker.compose(
        "exec", "-T", "-e", "PGPASSWORD", "postgres",
        "psql", "-h", "postgres", "-U", DB_USER, "-d", DB_NAME, "-tAc", "SELECT 1",
        extra_env={"PGPASSWORD": checker.password},
    )
    ok = login.returncode == 0 and login.stdout.strip() == "1"
    detail = "密码登录并执行 SELECT 1 成功" if ok else first_line(login.stderr or login.stdout)
    checker.report(ok, "Postgres 登录", detail)

    ok, detail = tcp_open(port)
    checker.report(ok, "Postgres 宿主机端口", detail)


def check_milvus(checker: Checker, port: int, health_port: int) -> None:
    url = f"http://127.0.0.1:{health_port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(200).decode("utf-8", "replace").strip()
            checker.report(response.status == 200, "Milvus /healthz", f"HTTP {response.status} {body}")
    except urllib.error.HTTPError as exc:
        checker.report(False, "Milvus /healthz", f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        checker.report(False, "Milvus /healthz", f"{url} 连不上（{reason}）")

    ok, detail = tcp_open(port)
    checker.report(ok, "Milvus gRPC 端口", detail)


def main() -> int:
    if not ENV_FILE.is_file():
        print(f"[FAIL] 配置: 没有 {ENV_FILE.relative_to(ROOT)}；从 backend/.env.example 复制并填 DOGHUNT_DB_PASSWORD")
        return 2
    env = read_env_file(ENV_FILE)
    password = setting(env, "DOGHUNT_DB_PASSWORD", "")
    if not password:
        print("[FAIL] 配置: DOGHUNT_DB_PASSWORD 为空；在 backend/.env 填一个本机随机密码，并确认终端里没有设置同名空变量")
        return 2
    try:
        db_port = int(setting(env, "DOGHUNT_DB_PORT", "5432"))
        milvus_port = int(setting(env, "DOGHUNT_MILVUS_PORT", "19530"))
        health_port = int(setting(env, "DOGHUNT_MILVUS_HEALTH_PORT", "9091"))
    except ValueError as exc:
        print(f"[FAIL] 配置: 端口不是整数（{exc}）")
        return 2

    checker = Checker(password)
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print("[FAIL] Docker: 找不到 docker 命令")
        return 2
    if info.returncode != 0:
        print("[FAIL] Docker: 守护进程没有运行；先启动 Docker Desktop")
        return 2

    print(f"检查 {PROJECT_NAME}（{COMPOSE_FILE.relative_to(ROOT)}），Docker {info.stdout.strip()}")
    check_containers(checker)
    check_postgres(checker, db_port)
    check_milvus(checker, milvus_port, health_port)

    if checker.failures:
        print(f"结果：{checker.failures} 项失败。排查见 deploy/README.md 的“排错”一节")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
