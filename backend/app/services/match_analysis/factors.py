"""逐项因素（chat_method 第 3 条）：每个函数读一场比赛的一类资料，返回因素行，缺资料时往 missing 里记一项。

输入是 services/odds 各 *_context 函数给出的单场资料（按比赛 id 取出的那一段），不重新取数。
"""

from statistics import mean

from app.services.match_analysis.card import (OVER, RANK_GAP, RANK_NEWS, RANK_PRICE, RANK_REST, SPREAD, TOTAL, UNDER,
                                              Factor)

SPREAD_GAP = 3.0  # 推算的主队净胜分和市场相差达到这么多分，才算选边理由
TOTAL_GAP = 4.0  # 推算总分和共识总分相差达到这么多分，才算选边理由
MIN_GAMES = 5  # 任一队少于这么多场时，实力和节奏只作参照
HOME_EDGE = 2.0  # 只有近期战绩时，主场优势按约 2 分估计
MOVE_GAP = 1.0  # 共识线变化至少这么多分，算盘口明显移动


def _games(side: dict | None) -> int:
    return side.get("games") or 0 if isinstance(side, dict) else 0


def strength(home: str, away: str, market_margin: float | None, form: dict | None, season: dict | None,
             fetched: dict, missing: list[str]) -> Factor | None:
    """实力差距：整季实力分优先，没有时用近期战绩 + 主场优势。market_margin 是共识让分点取相反数。"""
    form_margin = None
    if form and _games(form.get("home")) and _games(form.get("away")) and form.get("home_minus_away_avg_margin") is not None:
        form_margin = round(form["home_minus_away_avg_margin"] + HOME_EDGE, 1)
    if season and season.get("model_home_margin") is not None:
        ours, enough = season["model_home_margin"], True  # 服务端只在两队都满 5 场时给 model_home_margin
        games = min(_games(season.get("home")), _games(season.get("away")))
        source, at = f"BALLDONTLIE 整季比分推算实力分（{games}+ 场）", fetched.get("season")
        rated = [season.get(k, {}).get("rating") for k in ("home", "away")]
        if None not in rated and season.get("home_edge") is not None:
            source += f"：主队实力分 {rated[0]:g}，客队 {rated[1]:g}，主场优势 {season['home_edge']:g}"
    elif form_margin is not None:
        ours = form_margin
        games = min(_games(form["home"]), _games(form["away"]))
        enough = games >= MIN_GAMES
        source, at = f"BALLDONTLIE 近期比分（两队至少 {games} 场），主场优势按 2 分", fetched.get("schedule")
    else:
        missing.append("实力与战绩（让分）")
        return None
    row = Factor(SPREAD, "strength", "实力差距", "A", source, ours=ours, market_value=market_margin, fetched_at=at)
    if market_margin is None:
        row.note = "没有共识让分，无法比较"
        return row
    row.diff = round(ours - market_margin, 1)
    side = home if row.diff > 0 else away if row.diff < 0 else None
    if abs(row.diff) >= SPREAD_GAP:
        row.points_to = side
        row.is_reason, row.rank = enough, RANK_GAP if enough else None
        row.note = "达到 3 分门槛" if enough else "达到门槛，但任一队不足 5 场，只作参照"
    else:
        row.note = "相差不到 3 分，与市场一致"
    if season and season.get("model_home_margin") is not None and form_margin is not None:
        if (form_margin - market_margin) * row.diff < 0:
            row.note += f"；近期战绩推算主队净胜 {form_margin:g}，方向和整季相反，以整季为准"
    return row


def pace(market_total: float | None, form: dict | None, efficiency: dict | None, fetched: dict,
         missing: list[str]) -> Factor | None:
    """节奏与得分环境：球队效率推算总分优先，没有时用两队近期场均总分的平均。"""
    projection = efficiency.get("projection") if efficiency else None
    if projection and projection.get("total") is not None:
        ours, enough = projection["total"], True  # 服务端只在两队都满 5 场时给 projection
        source, at = "DunkAPI 本季 pace 与攻防效率推算", fetched.get("team_stats")
    elif form and _games(form.get("home")) and _games(form.get("away")) \
            and form["home"].get("avg_game_total") is not None and form["away"].get("avg_game_total") is not None:
        ours = round(mean([form["home"]["avg_game_total"], form["away"]["avg_game_total"]]), 1)
        games = min(_games(form["home"]), _games(form["away"]))
        enough = games >= MIN_GAMES
        source, at = f"BALLDONTLIE 近期比分的场均总分（两队至少 {games} 场）", fetched.get("schedule")
    else:
        missing.append("节奏与得分（大小分）")
        return None
    row = Factor(TOTAL, "pace", "节奏与得分环境", "A", source, ours=ours, market_value=market_total, fetched_at=at)
    if market_total is None:
        row.note = "没有共识总分，无法比较"
        return row
    row.diff = round(ours - market_total, 1)
    side = OVER if row.diff > 0 else UNDER if row.diff < 0 else None
    if abs(row.diff) >= TOTAL_GAP:
        row.points_to = side
        row.is_reason, row.rank = enough, RANK_GAP if enough else None
        row.note = "达到 4 分门槛" if enough else "达到门槛，但任一队不足 5 场，只作参照"
    else:
        row.note = "相差不到 4 分，与市场一致"
    return row


def _tired(side: dict) -> bool:
    return bool(side.get("back_to_back")) or (side.get("games_in_4_days") or 0) >= 3


def _rested(side: dict) -> bool:
    days = side.get("rest_days")
    days = side.get("rest_days_at_least") if days is None else days
    return not _tired(side) and (days or 0) >= 1


def _tired_text(side: dict) -> str:
    return "背靠背" if side.get("back_to_back") else "4 天 3 赛"


def _rest_text(side: dict) -> str:
    days = side.get("rest_days")
    return f"休 {days} 天" if days is not None else f"至少休 {side.get('rest_days_at_least')} 天"


def rest(rest_event: dict | None, fetched: dict, missing: list[str]) -> Factor | None:
    """休息差：一方背靠背或 4 天 3 赛，另一方至少休 1 天。只用于让分。"""
    if not rest_event or rest_event.get("status") != "available":
        missing.append("赛程与体能（让分）")
        return None
    home, away = rest_event.get("home") or {}, rest_event.get("away") or {}
    row = Factor(SPREAD, "rest", "赛程与体能", "A", "BALLDONTLIE 赛程", fetched_at=fetched.get("schedule"))
    if _tired(home) and _rested(away):
        row.points_to, row.note = away.get("team"), f"{home.get('team')} {_tired_text(home)}，{away.get('team')} {_rest_text(away)}"
    elif _tired(away) and _rested(home):
        row.points_to, row.note = home.get("team"), f"{away.get('team')} {_tired_text(away)}，{home.get('team')} {_rest_text(home)}"
    else:
        row.note = "没有休息差"
    unmatched = rest_event.get("listed_in_schedule") is False
    if row.points_to and not unmatched:
        row.is_reason, row.rank = True, RANK_REST
    if unmatched:
        row.note += "；赛程里没找到这场对阵，休息数据可能对不上" + ("，只作参照" if row.points_to else "")
    return row


def _names(players: list[dict]) -> str:
    return "、".join(p.get("player") or "?" for p in players)


def injury(home: str, away: str, injuries: dict | None, injury_event: dict | None, fetched: dict,
           missing: list[str]) -> Factor | None:
    """伤病：new_since_last_game 为 true 的 Out 是新伤病，指向对手；false 已在战绩里；null 无法判断。只用于让分。"""
    if not injuries or injuries.get("status") != "available" or not injury_event:
        missing.append("伤病情况未知（让分、大小分）")
        return None
    row = Factor(SPREAD, "injury", "伤病与阵容", "A", "DunkAPI 伤病列表（可能滞后于球队官方报告）", fetched_at=fetched.get("injuries"))
    new: dict[str, list[dict]] = {}
    notes = []
    for key, team in (("home", home), ("away", away)):
        side = injury_event.get(key) or {}
        players = side.get("players") or []
        if side.get("listed") is False:
            notes.append(f"{team} DunkAPI 未列出伤病，不等于确认全员可出场")
            continue
        fresh = [p for p in players if p.get("status") == "Out" and p.get("new_since_last_game") is True]
        old = [p for p in players if p.get("status") == "Out" and p.get("new_since_last_game") is False]
        unknown = [p for p in players if p.get("status") == "Out" and p.get("new_since_last_game") is None]
        doubtful = [p for p in players if p.get("status") == "Day-To-Day"]
        if fresh:
            new[team] = fresh
            notes.append(f"{team} 新伤病缺阵：{_names(fresh)}")
        if old:
            notes.append(f"{team} 已在战绩里的缺阵：{_names(old)}")
        if unknown:
            notes.append(f"{team} 无法确定是否新伤病：{_names(unknown)}")
        if doubtful:
            notes.append(f"{team} 出场待定：{_names(doubtful)}")
    if len(new) == 1:
        hurt = next(iter(new))
        row.points_to = away if hurt == home else home
        row.is_reason, row.rank = True, RANK_NEWS
        notes.append("服务端不知道球员在队中的作用，影响大小要按球员判断，作用不明时写不确定")
    elif len(new) == 2:
        notes.append("两队都有新伤病，方向互相抵消")
    if injuries.get("stale"):
        notes.append("伤病列表采集已超过 12 小时，出场状态可能已变")
    row.note = "；".join(notes) or "两队没有列出缺阵或待定球员"
    return row


def movement(market: str, signal: dict | None, home: str, away: str) -> Factor | None:
    """盘口明显移动：同一场共识线从最早快照到现在至少变了 1 分，指向线移动的方向。"""
    moved = (signal or {}).get("movement") or {}
    pair = moved.get("consensus_home_point" if market == SPREAD else "consensus_total")
    row = Factor(market, "movement", "盘口移动", "A", f"本地 {moved.get('snapshots', 0)} 份盘口快照的共识线",
                 fetched_at=moved.get("latest_fetched_at"))
    if not pair or None in pair:
        row.note = "只有一份快照，没有历史可比" if moved.get("snapshots", 0) <= 1 else "缺少可比的共识线"
        return row
    first, now = pair
    row.ours, row.market_value, row.diff = now, first, round(now - first, 2)
    if abs(row.diff) >= MOVE_GAP:
        if market == SPREAD:
            row.points_to = home if row.diff < 0 else away  # 主队让分点变得更负，表示市场更看好主队
        else:
            row.points_to = OVER if row.diff > 0 else UNDER
        row.is_reason, row.rank = True, RANK_NEWS
        row.note = f"共识线 {first:g} → {now:g}"
    else:
        row.note = f"共识线 {first:g} → {now:g}，变化不到 1 分"
    return row


def price(market: str, signal: dict | None, book: str | None) -> Factor | None:
    """所选平台的线比共识更有利的一边，差半分也算。未选场时没有这一项。"""
    if not book:
        return None
    part = (signal or {}).get(SPREAD if market == SPREAD else TOTAL) or {}
    selected = part.get("selected")
    if not selected:
        return None
    row = Factor(market, "price", "所选平台报价差", "A", f"{book} 与各平台中位数比较",
                 ours=selected.get("home_point" if market == SPREAD else "point"),
                 market_value=part.get("consensus_home_point" if market == SPREAD else "consensus"),
                 diff=selected.get("diff_vs_consensus"))
    if selected.get("better_for"):
        row.points_to = selected["better_for"]
        row.is_reason, row.rank = True, RANK_PRICE
        row.note = f"比共识多给 {abs(row.diff):g} 分"
    else:
        row.note = "和共识一样"
    return row
