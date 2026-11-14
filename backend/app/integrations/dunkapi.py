"""DunkAPI 免费档（每月 500 credits）。每个工具是 POST https://mcp.dunkapi.com/v1/{tool}，JSON 正文，Key 放 x-api-key 请求头。

额度在请求前扣除，所以本模块先在本地账本登记本月已用 credits，超过 MONTHLY_BUDGET 就不发请求；
账本只能防止本项目超支，不知道同一账号在别处的用量。不自动重试。
条款摘要（dunkapi.com/docs/timing-and-rights）：允许保存、派生计算和在自己的网站展示；禁止把原始数据转成数据集、数据流或接口转发；
免费档与付费档规则相同，不要求署名。完整条款优先于摘要。
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

from app.integrations import local_keys
from app.integrations.odds import PROJECT_ROOT

BASE_URL = "https://mcp.dunkapi.com/v1/"
KEY_NAME = "DUNKAPI_API_KEY"
TIMEOUT_SECONDS = 20
# 官方 openapi.json 的 x-credits；未列出的工具不允许调用，避免意外扣费。
CREDITS = {"get_team_stats": 5, "get_injuries": 2, "get_standings": 2}
# 留 50 给手动核对，免费档每月 500。
MONTHLY_BUDGET = 450
LEDGER_PATH = PROJECT_ROOT / "data" / "dunkapi" / "usage.json"
_ledger_lock = Lock()


class DunkError(Exception):
    pass


def load_key() -> str | None:
    try:
        return local_keys.read_key(KEY_NAME)
    except ValueError as error:
        raise DunkError(str(error)) from None


def _month(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m")


def read_ledger(path: Path | None = None, now: datetime | None = None) -> dict:
    """本月账本；文件损坏时按预算用满处理，宁可不请求也不超支。"""
    path = path or LEDGER_PATH
    month = _month(now or datetime.now(timezone.utc))
    if not path.exists():
        return {"month": month, "credits": 0, "calls": []}
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(ledger.get("credits"), int) or not isinstance(ledger.get("calls"), list):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        return {"month": month, "credits": MONTHLY_BUDGET, "calls": [], "corrupted": True}
    if ledger.get("month") != month:
        return {"month": month, "credits": 0, "calls": []}
    return ledger


def _reserve(tool: str, path: Path | None, now: datetime) -> None:
    path = path or LEDGER_PATH
    with _ledger_lock:
        ledger = read_ledger(path, now)
        if ledger.get("corrupted"):
            raise DunkError(f"额度账本 {path.name} 无法读取，未发请求；核对后删除或修复该文件。")
        cost = CREDITS[tool]
        if ledger["credits"] + cost > MONTHLY_BUDGET:
            raise DunkError(f"本月已登记 {ledger['credits']} credits，再调用 {tool}（{cost}）会超过项目上限 {MONTHLY_BUDGET}，未发请求。")
        ledger["credits"] += cost
        ledger["calls"].append({"at": now.isoformat(timespec="seconds").replace("+00:00", "Z"), "tool": tool, "credits": cost})
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


def call(tool: str, body: dict, key: str, ledger: Path | None = None) -> dict:
    """返回响应里的 data 对象。先登记额度再请求；请求失败不退回登记（服务器 500 时官方会退款，本地账本偏保守）。"""
    if tool not in CREDITS:
        raise DunkError(f"{tool} 未列入允许调用的工具。")
    _reserve(tool, ledger, datetime.now(timezone.utc))
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.post(BASE_URL + tool, json=body, headers={"x-api-key": key})
    except httpx.HTTPError:
        # 不输出异常原文，避免请求头信息进入日志。
        raise DunkError(f"{tool} 连接失败或超时，未自动重试。") from None
    if response.status_code != 200:
        messages = {400: "参数不被接受。", 401: "Key 无效或缺失。", 402: "账号 credits 不足。", 429: "请求太频繁，未自动重试。"}
        raise DunkError(f"{tool} HTTP {response.status_code}：" + messages.get(response.status_code, "接口返回错误。"))
    try:
        payload = response.json()
    except ValueError:
        raise DunkError(f"{tool} 没有返回有效 JSON。") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise DunkError(f"{tool} 返回格式不符合预期。")
    return data
