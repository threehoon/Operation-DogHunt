"""BALLDONTLIE 免费档赛程与比分（/v1/games）。Key 只放项目根目录 .env.local，不进入日志和接口响应。

免费档限 5 次/分钟：本模块所有请求共用一个限速器，超过时等待而不是报错；不自动重试。
近期赛程翻页最多 MAX_PAGES 次（近 30 天约 220 场，每页 100 场），整季比分最多 SEASON_MAX_PAGES 次。
"""

import time
from collections import deque
from threading import Lock

import httpx

from app.integrations import local_keys

ENDPOINT = "https://api.balldontlie.io/v1/games"
KEY_NAME = "BALLDONTLIE_API_KEY"
MAX_PAGES = 4
SEASON_MAX_PAGES = 15  # 常规赛 1230 场，每页 100 场
TIMEOUT_SECONDS = 10
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 61  # 比官方 60 秒多留 1 秒余量
_recent: deque[float] = deque()
_rate_lock = Lock()
_sleep = time.sleep


class ScheduleError(Exception):
    pass


def load_key() -> str | None:
    """没配置返回 None（赛程是可选资料）；配置了但格式不对才报错。"""
    try:
        return local_keys.read_key(KEY_NAME)
    except ValueError as error:
        raise ScheduleError(str(error)) from None


def _wait_turn() -> None:
    """保证任意 RATE_WINDOW_SECONDS 秒内最多 RATE_LIMIT 次请求；赛程和整季比分在同一个后台线程里先后运行，共用这个计数。"""
    with _rate_lock:
        while True:
            now = time.monotonic()
            while _recent and now - _recent[0] >= RATE_WINDOW_SECONDS:
                _recent.popleft()
            if len(_recent) < RATE_LIMIT:
                _recent.append(now)
                return
            _sleep(RATE_WINDOW_SECONDS - (now - _recent[0]) + 0.1)


def _pages(params: list[tuple[str, str | int]], key: str, max_pages: int, label: str) -> list[dict]:
    games: list[dict] = []
    cursor = None
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _ in range(max_pages):
            query = params + [("per_page", 100)] + ([("cursor", cursor)] if cursor is not None else [])
            _wait_turn()
            try:
                response = client.get(ENDPOINT, params=query, headers={"Authorization": key})
            except httpx.HTTPError:
                # 不输出异常原文，避免请求头信息进入日志。
                raise ScheduleError(f"{label}接口连接失败或超时，未自动重试。") from None
            if response.status_code != 200:
                messages = {401: "Key 无效或账号无权访问比赛接口。", 429: "超过免费档 5 次/分钟限制，未自动重试。"}
                raise ScheduleError(f"HTTP {response.status_code}：" + messages.get(response.status_code, f"{label}接口返回错误。"))
            try:
                payload = response.json()
            except ValueError:
                raise ScheduleError(f"{label}接口没有返回有效 JSON。") from None
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise ScheduleError(f"{label}接口返回格式不符合预期。")
            games.extend(data)
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                return games
    raise ScheduleError(f"{label}超过 {max_pages} 页，本次不保存，避免数据不完整。")


def fetch_games(key: str, dates: list[str]) -> list[dict]:
    """取指定日期（美国东部日期，YYYY-MM-DD）的全部比赛。按日期而不是起止范围取，盘口里有远期比赛时也不会拉整段赛程。"""
    return _pages([("dates[]", day) for day in dates], key, MAX_PAGES, "赛程")


def fetch_season_games(key: str, season: int, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """取一个赛季（BALLDONTLIE 按开始年份：2026 = 2026-27）的比赛，可用东部日期限定范围，用于增量更新整季比分。"""
    params: list[tuple[str, str | int]] = [("seasons[]", season)]
    if start_date:
        params.append(("start_date", start_date))
    if end_date:
        params.append(("end_date", end_date))
    return _pages(params, key, SEASON_MAX_PAGES, "整季比分")
