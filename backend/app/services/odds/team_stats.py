"""球队效率快照：更新盘口后在后台从 DunkAPI 取一次全联盟本季常规赛效率（每次 5 credits，一次返回 30 队），聊天时给相关两队并推算总分。

DunkAPI 的回合数公式：FGA − OREB + TOV + 0.44 × FTA，取双方平均；效率是每 100 回合得失分。只有赛季累计值，没有近期或截至某天的数据。
聊天只读本地文件，不访问外部服务。
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import applog
from app.integrations import dunkapi
from app.integrations.odds import PROJECT_ROOT
from app.schemas.odds import OddsEvent
from app.services.odds.schedule import EASTERN, TEAM_BY_ABBR

logger = logging.getLogger(__name__)
TEAM_STATS_ROOT = PROJECT_ROOT / "data" / "team_stats"
# 每月预算按每天约一次估算（30 × 5 = 150 credits）。
MIN_REFRESH_HOURS = 20
STALE_HOURS = 36
MIN_TEAM_GAMES = 5
TEAMS = set(TEAM_BY_ABBR.values())
# DunkAPI 写法 → The Odds API 全名；其余 29 队两边一致。
DUNK_NAMES = {"LA Clippers": "Los Angeles Clippers"}
NUMBERS = ("games", "wins", "losses", "pts", "opp_pts", "pace", "off_rating", "def_rating", "net_rating", "ast", "reb", "tov")
# 这些字段 DunkAPI 给的是 0 到 1 的比例，保存时换成百分数。
RATIOS = ("efg_pct", "tov_pct", "oreb_pct", "ft_rate", "three_rate", "opp_efg_pct")


def season_end_year(now: datetime) -> int:
    """DunkAPI 用结束年份：2027 = 2026-27。8 月起算新赛季。"""
    day = now.astimezone(EASTERN).date()
    return day.year + 1 if day.month >= 8 else day.year


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def normalize_teams(raw_teams: list[dict]) -> dict[str, dict]:
    """只留下能对上 30 队的球队；缺字段记为 None，不补 0。全都对不上时视为格式变化。"""
    teams: dict[str, dict] = {}
    for raw in raw_teams:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("display_name", ""))
        team = DUNK_NAMES.get(name, name)
        if team not in TEAMS:
            continue
        row: dict = {field: _number(raw.get(field)) for field in NUMBERS}
        for field in RATIOS:
            value = _number(raw.get(field))
            row[field] = round(value * 100, 1) if value is not None and value <= 1 else value
        for field in ("games", "wins", "losses"):
            row[field] = int(row[field]) if row[field] is not None else None
        teams[team] = row
    if raw_teams and not teams:
        raise ValueError("球队效率里没有可识别的球队")
    return teams


def load_team_stats(root: Path | None = None) -> dict | None:
    """读最新一份；损坏时返回 None，不回退到更旧的文件。"""
    paths = sorted((root or TEAM_STATS_ROOT).glob("*/team_stats.json"))
    if not paths:
        return None
    try:
        record = json.loads(paths[-1].read_text(encoding="utf-8"))
        datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
        if not isinstance(record["teams"], dict):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def refresh_team_stats(root: Path | None = None, now: datetime | None = None) -> str:
    """取数失败保留旧文件；返回一句结果给日志。"""
    now = now or datetime.now(timezone.utc)
    try:
        key = dunkapi.load_key()
        if key is None:
            return f"未配置 {dunkapi.KEY_NAME}，跳过球队效率"
        season = season_end_year(now)
        old = load_team_stats(root)
        if old and old.get("season") == season:
            age = now - datetime.fromisoformat(old["fetched_at"].replace("Z", "+00:00"))
            if age < timedelta(hours=MIN_REFRESH_HOURS):
                return f"球队效率 {age.seconds // 3600} 小时前已更新，本次跳过（节省 credits）"
        data = dunkapi.call("get_team_stats", {"season": season, "season_type": "regular"}, key)
        if not isinstance(data.get("teams"), list):
            raise ValueError("teams 不是列表")
        teams = normalize_teams(data["teams"])
        fetched = now
        folder = (root or TEAM_STATS_ROOT) / (fetched.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=False)
        record = {"provider": "DunkAPI", "fetched_at": fetched.isoformat(timespec="seconds").replace("+00:00", "Z"),
                  "season": season, "season_type": "regular", "method": data.get("method"), "teams": teams}
        with (folder / "team_stats.json").open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=1)
        return f"球队效率已保存 {len(teams)} 队（{season - 1}-{str(season)[-2:]} 常规赛）"
    except dunkapi.DunkError as error:
        return f"球队效率未更新：{error}"
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        applog.event(logger, "task.error", "球队效率未更新：数据格式异常或保存失败", level=logging.WARNING, error=exc)
        return "球队效率未更新：数据格式异常或保存失败"


def projected_total(home: dict, away: dict) -> dict | None:
    """预计回合数 = 两队 pace 平均；每队预计得分 = 回合数 × (本队进攻效率 + 对手防守效率) / 2 / 100。"""
    needed = ("pace", "off_rating", "def_rating")
    if any(team.get(field) is None for team in (home, away) for field in needed):
        return None
    possessions = (home["pace"] + away["pace"]) / 2
    home_points = possessions * (home["off_rating"] + away["def_rating"]) / 200
    away_points = possessions * (away["off_rating"] + home["def_rating"]) / 200
    return {"possessions": round(possessions, 1), "home_points": round(home_points, 1),
            "away_points": round(away_points, 1), "total": round(home_points + away_points, 1)}


def efficiency_context(record: dict | None, events: list[OddsEvent], now: datetime | None = None) -> dict:
    if record is None:
        return {"status": "unavailable", "reason": "没有保存的球队效率；需要配置 DunkAPI Key 后再更新一次盘口"}
    fetched = datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
    season = record.get("season")
    result: dict = {
        "status": "available", "provider": record.get("provider"), "fetched_at": record["fetched_at"],
        "stale": (now or datetime.now(timezone.utc)) - fetched > timedelta(hours=STALE_HOURS),
        "season": f"{season - 1}-{str(season)[-2:]}" if isinstance(season, int) else None,
        "note": "赛季累计值，不是近期状态；百分比字段已换成百分数；projection 是服务端按 pace 与攻防效率推算的估算",
        "events": {},
    }
    for event in events:
        home, away = record["teams"].get(event.home_team), record["teams"].get(event.away_team)
        entry: dict = {"home": {"team": event.home_team, **(home or {"games": 0})},
                       "away": {"team": event.away_team, **(away or {"games": 0})}}
        if home and away and min(home.get("games") or 0, away.get("games") or 0) >= MIN_TEAM_GAMES:
            entry["projection"] = projected_total(home, away)
        result["events"][event.id] = entry
    return result


def log_refresh() -> None:
    applog.run_step(logger, "球队效率", refresh_team_stats)
