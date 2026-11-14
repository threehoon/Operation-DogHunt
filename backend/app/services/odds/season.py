"""整季比分与实力分：更新盘口后在后台从 BALLDONTLIE 免费档增量更新本赛季常规赛比分，聊天时按对手强弱修正净胜分。

实力分（rating）的算法与 SRS 相同：主队净胜分 ≈ 主队实力分 − 客队实力分 + 主场优势，用整季比分迭代求解，联盟平均为 0。
只用比分，不知道伤病、轮休和垃圾时间；季初样本小，波动大。聊天只读本地文件，不访问外部服务。
"""

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.integrations import balldontlie
from app.integrations.odds import PROJECT_ROOT
from app.schemas.odds import OddsEvent
from app.services.odds.schedule import EASTERN, normalize_games, us_date

logger = logging.getLogger(__name__)
SEASON_ROOT = PROJECT_ROOT / "data" / "season"
# 距上次保存不到这么久就不请求，避免频繁更新盘口时反复翻页。
MIN_REFRESH_HOURS = 6
# 增量更新时往回重取几天，接收比分修正和补录。
OVERLAP_DAYS = 3
MIN_TEAM_GAMES = 5
RECENT_GAMES = 5


def season_start_year(day: date) -> int:
    """NBA 赛季 10 月开打、6 月结束；8 月起算新赛季。BALLDONTLIE 用开始年份：2026 = 2026-27。"""
    return day.year if day.month >= 8 else day.year - 1


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def regular_games(raw_games: list[dict]) -> list[dict]:
    """只留常规赛；季后赛单独成样本，不能和常规赛平均。"""
    return normalize_games([g for g in raw_games if isinstance(g, dict) and not g.get("postseason")])


def load_season(root: Path | None = None) -> dict | None:
    """读最新一份；损坏时返回 None，不回退到更旧的文件。"""
    paths = sorted((root or SEASON_ROOT).glob("*/season.json"))
    if not paths:
        return None
    try:
        record = json.loads(paths[-1].read_text(encoding="utf-8"))
        datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
        if not isinstance(record["season"], int) or not isinstance(record["games"], list):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def refresh_season(root: Path | None = None, now: datetime | None = None) -> str:
    """取数失败保留旧文件；返回一句结果给日志。"""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(EASTERN).date()
    season = season_start_year(today)
    try:
        key = balldontlie.load_key()
        if key is None:
            return f"未配置 {balldontlie.KEY_NAME}，跳过整季比分"
        old = load_season(root)
        if old and old["season"] == season:
            age = now - datetime.fromisoformat(old["fetched_at"].replace("Z", "+00:00"))
            if age < timedelta(hours=MIN_REFRESH_HOURS):
                return f"整季比分 {age.seconds // 60} 分钟前已更新，本次跳过"
        finals = [g["date"] for g in old["games"] if "home_score" in g] if old and old["season"] == season else []
        if finals:
            start = (date.fromisoformat(max(finals)) - timedelta(days=OVERLAP_DAYS)).isoformat()
            fresh = regular_games(balldontlie.fetch_season_games(key, season, start, today.isoformat()))
            fresh_ids = {g["id"] for g in fresh}
            games = [g for g in old["games"] if g["date"] < start and g["id"] not in fresh_ids] + fresh
            mode = f"增量（{start} 起）"
        else:
            games = regular_games(balldontlie.fetch_season_games(key, season, None, today.isoformat()))
            mode = "全季"
        games.sort(key=lambda g: (g["date"], g["home"]))
        fetched = now
        folder = (root or SEASON_ROOT) / (fetched.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=False)
        record = {"provider": "BALLDONTLIE", "fetched_at": fetched.isoformat(timespec="seconds").replace("+00:00", "Z"),
                  "season": season, "season_type": "regular", "through": today.isoformat(), "games": games}
        with (folder / "season.json").open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=1)
        scored = sum("home_score" in g for g in games)
        return f"整季比分已保存（{mode}）：{season_label(season)} 常规赛 {scored} 场有比分"
    except balldontlie.ScheduleError as error:
        return f"整季比分未更新：{error}"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return "整季比分未更新：数据格式异常或保存失败"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def ratings(games: list[dict], before: str) -> dict | None:
    """before（东部日期）之前已结束的比赛 → 主场优势与每队实力分。少于 30 场返回 None。"""
    finals = [g for g in games if "home_score" in g and g["date"] < before]
    if len(finals) < 30:
        return None
    home_edge = _mean([g["home_score"] - g["away_score"] for g in finals])
    # 每队的 (日期, 对手, 主场?, 本队净胜分)
    rows: dict[str, list[tuple[str, str, bool, int]]] = {}
    for g in finals:
        margin = g["home_score"] - g["away_score"]
        rows.setdefault(g["home"], []).append((g["date"], g["away"], True, margin))
        rows.setdefault(g["away"], []).append((g["date"], g["home"], False, -margin))
    rating = {team: 0.0 for team in rows}
    for _ in range(500):
        # 本队实力分 = 去掉主客场后的净胜分 + 对手实力分，取平均；减半步长防止来回振荡。
        raw = {team: _mean([m - (home_edge if home else -home_edge) + rating[opp] for _, opp, home, m in games_])
               for team, games_ in rows.items()}
        center = _mean(list(raw.values()))
        new = {team: (rating[team] + raw[team] - center) / 2 for team in rows}
        done = max(abs(new[t] - rating[t]) for t in rows) < 1e-6
        rating = new
        if done:
            break
    order = sorted(rating, key=rating.get, reverse=True)
    teams = {}
    for team, games_ in rows.items():
        games_.sort()
        margins = [m for *_, m in games_]
        recent = margins[-RECENT_GAMES:]
        teams[team] = {
            "games": len(games_), "wins": sum(m > 0 for m in margins), "losses": sum(m < 0 for m in margins),
            "avg_margin": round(_mean(margins), 1), "rating": round(rating[team], 1),
            "schedule_strength": round(_mean([rating[opp] for _, opp, _, _ in games_]), 1),
            "rank": order.index(team) + 1,
            f"last_{RECENT_GAMES}": {"games": len(recent), "avg_margin": round(_mean(recent), 1)},
        }
    return {"games": len(finals), "home_edge": round(home_edge, 1), "rating": rating, "teams": teams}


def strength_context(record: dict | None, events: list[OddsEvent]) -> dict:
    if record is None:
        return {"status": "unavailable", "reason": "没有保存的整季比分；需要配置 BALLDONTLIE Key 后再更新一次盘口"}
    result: dict = {
        "status": "available", "provider": record.get("provider"), "fetched_at": record.get("fetched_at"),
        "season": season_label(record["season"]),
        "note": "服务端由整季常规赛比分推算（估算），按对手强弱和主客场修正；不含伤病、轮休信息，球队 games 少于 5 场只作参照",
        "events": {},
    }
    cache: dict[str, dict | None] = {}
    for event in events:
        day = us_date(event.commence_time).isoformat()
        if day not in cache:
            cache[day] = ratings(record["games"], day)
        solved = cache[day]
        if solved is None:
            result["events"][event.id] = {"status": "too_few_games"}
            continue
        home, away = solved["teams"].get(event.home_team), solved["teams"].get(event.away_team)
        entry: dict = {"status": "available", "games_used": solved["games"], "home_edge": solved["home_edge"],
                       "home": {"team": event.home_team, **(home or {"games": 0})},
                       "away": {"team": event.away_team, **(away or {"games": 0})}}
        if home and away and min(home["games"], away["games"]) >= MIN_TEAM_GAMES:
            entry["model_home_margin"] = round(solved["rating"][event.home_team] - solved["rating"][event.away_team] + solved["home_edge"], 1)
        result["events"][event.id] = entry
    return result


def log_refresh() -> None:
    try:
        logger.info(refresh_season())
    except Exception:  # 可选步骤：任何意外都不能影响已保存的盘口。
        logger.warning("整季比分未更新：意外错误")
