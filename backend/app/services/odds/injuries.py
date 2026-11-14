"""伤病快照：更新盘口后在后台从 DunkAPI 取一次全联盟伤病列表（每次 2 credits），聊天时只给相关比赛的两队。

DunkAPI 只给当前状态，没有报告时间和短评。本模块拿上一份快照比对，记下每名球员当前状态最早出现在哪份快照
（listed_since），以及上一份还没列出时的采集时间（listed_after），用来判断是不是该队最后一场之后的新伤病。
聊天只读本地文件，不访问外部服务。
"""

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.core import applog
from app.integrations import dunkapi
from app.integrations.odds import PROJECT_ROOT
from app.schemas.odds import OddsEvent
from app.services.odds.schedule import EASTERN, TEAM_BY_ABBR

logger = logging.getLogger(__name__)
INJURY_ROOT = PROJECT_ROOT / "data" / "injuries"
PROVIDER = "DunkAPI"
# 每次 2 credits；按每天最多约 6 次估算，和球队效率合计不超过每月预算。
MIN_REFRESH_HOURS = 4
# 超过这个时长的伤病列表标为 stale，提醒模型出场状态可能已变。
STALE_HOURS = 12
# DunkAPI 缩写（ESPN 写法）→ BALLDONTLIE 缩写；其余 24 队两边一致。
DUNK_ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def team_name(abbr: object) -> str | None:
    code = str(abbr or "").strip().upper()
    return TEAM_BY_ABBR.get(DUNK_ABBR.get(code, code))


def normalize_injuries(raw_players: list[dict]) -> dict[str, list[dict]]:
    """按球队分组，只留下能对上 30 队的球员和判断出场需要的字段。全都对不上时视为格式变化。"""
    teams: dict[str, list[dict]] = {}
    for raw in raw_players:
        if not isinstance(raw, dict):
            continue
        team, player = team_name(raw.get("team")), _text(raw.get("name"))
        if team is None or player is None:
            continue
        teams.setdefault(team, []).append({
            "player_id": _text(str(raw["player_id"])) if raw.get("player_id") is not None else None,
            "player": player, "position": _text(raw.get("position")), "status": _text(raw.get("status")),
            "injury": _text(raw.get("type")), "detail": _text(raw.get("detail")), "return_date": _text(raw.get("return_date")),
        })
    if raw_players and not teams:
        raise ValueError("伤病列表里没有可识别的球队")
    for players in teams.values():
        players.sort(key=lambda p: (p["status"] != "Out", p["player"]))
    return teams


def _key(team: str, player: dict) -> tuple:
    return team, player.get("player_id") or player["player"], player.get("status")


def carry_listing(teams: dict[str, list[dict]], previous: dict | None, fetched: str) -> None:
    """同一球员、同一状态沿用上一份的时间；新出现或状态变化时，listed_after 记上一份的采集时间。
    上一份不是 DunkAPI（或没有上一份）时 listed_after 为 None，表示无法知道何时开始列出。"""
    seen: dict[tuple, dict] = {}
    if previous and previous.get("provider") == PROVIDER:
        for team, players in previous["teams"].items():
            for player in players:
                seen[_key(team, player)] = player
    after = previous["fetched_at"] if previous and previous.get("provider") == PROVIDER else None
    for team, players in teams.items():
        for player in players:
            old = seen.get(_key(team, player))
            if old and old.get("listed_since"):
                player["listed_since"], player["listed_after"] = old["listed_since"], old.get("listed_after")
            else:
                player["listed_since"], player["listed_after"] = fetched, after


def load_injuries(root: Path | None = None) -> dict | None:
    """读最新一份；损坏时返回 None，不回退到更旧的文件。"""
    paths = sorted((root or INJURY_ROOT).glob("*/injuries.json"))
    if not paths:
        return None
    try:
        record = json.loads(paths[-1].read_text(encoding="utf-8"))
        _time(record["fetched_at"])
        if not isinstance(record["teams"], dict) or not all(isinstance(v, list) for v in record["teams"].values()):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def refresh_injuries(events: list[OddsEvent], root: Path | None = None, now: datetime | None = None) -> str:
    """取数失败保留旧文件，不影响盘口更新；返回一句结果给日志。"""
    if not events:
        return "没有已开盘比赛，跳过伤病"
    now = now or datetime.now(timezone.utc)
    try:
        key = dunkapi.load_key()
        if key is None:
            return f"未配置 {dunkapi.KEY_NAME}，跳过伤病"
        previous = load_injuries(root)
        if previous and previous.get("provider") == PROVIDER:
            age = now - _time(previous["fetched_at"])
            if age < timedelta(hours=MIN_REFRESH_HOURS):
                return f"伤病 {age.seconds // 60} 分钟前已更新，本次跳过（节省 credits）"
        data = dunkapi.call("get_injuries", {"league": "nba"}, key)
        if not isinstance(data.get("injuries"), list):
            raise ValueError("injuries 不是列表")
        teams = normalize_injuries(data["injuries"])
        fetched = now
        carry_listing(teams, previous, _stamp(fetched))
        folder = (root or INJURY_ROOT) / (fetched.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=False)
        record = {"provider": PROVIDER, "fetched_at": _stamp(fetched), "report_date": _text(data.get("date")), "teams": teams}
        with (folder / "injuries.json").open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=1)
        return f"伤病已保存 {len(teams)} 队 {sum(map(len, teams.values()))} 人（报告日 {record['report_date']}）"
    except dunkapi.DunkError as error:
        return f"伤病未更新：{error}"
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        applog.event(logger, "task.error", "伤病未更新：数据格式异常或保存失败", level=logging.WARNING, error=exc)
        return "伤病未更新：数据格式异常或保存失败"


def _eastern_day(value: str | None) -> date | None:
    return _time(value).astimezone(EASTERN).date() if value else None


def new_since(player: dict, last_game: str | None) -> bool | None:
    """True：上一份快照还没列出、且那份是在最后一场的东部日期之后采集的；False：最后一场之前就已列出；其他无法判断。"""
    if not last_game:
        return None
    game = date.fromisoformat(last_game)
    after, since = _eastern_day(player.get("listed_after")), _eastern_day(player.get("listed_since"))
    if after and after > game:
        return True
    if since and since < game:
        return False
    return None


def _team(record: dict, team: str, last_game: str | None) -> dict:
    if team not in record["teams"]:
        return {"team": team, "listed": False, "last_game": last_game, "players": []}
    players = [{"player": p["player"], "position": p.get("position"), "status": p.get("status"), "injury": p.get("injury"),
                "detail": p.get("detail"), "return_date": p.get("return_date"), "listed_since": p.get("listed_since"),
                "new_since_last_game": new_since(p, last_game)} for p in record["teams"][team]]
    return {"team": team, "listed": True, "last_game": last_game, "players": players}


def injury_context(record: dict | None, events: list[OddsEvent], now: datetime | None = None, form: dict | None = None) -> dict:
    """form 是 team_form 的结果，用来取每队最后一场的日期。"""
    if record is None:
        return {"status": "unavailable", "reason": "没有保存的伤病列表；需要配置 DunkAPI Key 后再更新一次盘口"}
    stale = (now or datetime.now(timezone.utc)) - _time(record["fetched_at"]) > timedelta(hours=STALE_HOURS)
    form_events = (form or {}).get("events") or {}
    result: dict = {
        "status": "available", "provider": record.get("provider"), "fetched_at": record["fetched_at"],
        "report_date": record.get("report_date"), "stale": stale,
        "note": ("DunkAPI 伤病报告，没有报告时间和短评；listed 为 false 表示未列出该队伤病，不等于确认全员可出场；"
                 "new_since_last_game 由服务端按本地快照判断，null 表示无法判断"),
        "events": {},
    }
    for event in events:
        sides = form_events.get(event.id) or {}
        result["events"][event.id] = {
            "home": _team(record, event.home_team, (sides.get("home") or {}).get("last_game")),
            "away": _team(record, event.away_team, (sides.get("away") or {}).get("last_game")),
        }
    return result


def log_refresh(events: list[OddsEvent]) -> None:
    applog.run_step(logger, "伤病", refresh_injuries, events)
