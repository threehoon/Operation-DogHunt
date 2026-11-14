"""拼装交给模型的资料：只读网站同一份盘口快照，不调用外部取数接口，不接受浏览器提供的报价。"""

import json
from time import perf_counter

from app.integrations.llm import ModelError
from app.schemas.chat import ChatMessage, OddsSelection
from app.schemas.odds import OddsBoard, OddsEvent
from app.services.chat.context.teams import Focus, find_focus
from app.services.match_analysis import analyze
from app.services.odds.injuries import injury_context
from app.services.odds.schedule import rest_context
from app.services.odds.season import strength_context
from app.services.odds.signals import Lines, market_signals
from app.services.odds.team_form import form_context
from app.services.odds.team_stats import efficiency_context

# 市场信号、赛程、战绩、伤病、实力分和效率最多附带开赛最早的这么多场（NBA 一天最多 15 场），
# 让“分析全部”也有资料；快照跨好几天时只覆盖最早的这些场，用 details_limit 说明，避免输入过长。
# 每场这些资料约 4.7 千字，加上 8 家平台报价约 8 千字；15 场约 12 万字。
DETAIL_EVENT_LIMIT = 15
# 未选场、也没认出球队或球员时附带的说明（含“张三今天打吗”这类带时间词的句子）；问全部、问多场时不附带。
UNRECOGNIZED_NOTE = ("没从用户最近的提问里认出球队或球员（球员只认伤病名单和常用球星表），下面是整张表；"
                     "如果用户问的是某位球员，先说明没认出，请用户补充球队。")


def _trim(event: OddsEvent, card: dict | None) -> OddsEvent:
    """未选场时每场只留分析卡倾向用到的平台报价；其他平台的线已汇总在 market_signals。没有分析卡的比赛保持原样。"""
    if not card:
        return event
    keep = {lean["book"] for lean in (card.get("spread"), card.get("total")) if lean and lean.get("book")}
    books = [book for book in event.bookmakers if book.key in keep]
    return event.model_copy(update={"bookmakers": books}) if books else event


def odds_context(board: OddsBoard, selection: OddsSelection | None, messages: list[ChatMessage] | None = None,
                 history: list[tuple[str, Lines]] | None = None, schedule: dict | None = None,
                 injuries: dict | None = None, season: dict | None = None, team_stats: dict | None = None) -> str:
    """schedule、injuries、season、team_stats 是对应 load_* 的结果，None 表示没有可用资料。"""
    return json.dumps(build_payload(board, selection, messages, history, schedule, injuries, season, team_stats),
                      ensure_ascii=False, separators=(",", ":"))


def build_payload(board: OddsBoard, selection: OddsSelection | None, messages: list[ChatMessage] | None = None,
                  history: list[tuple[str, Lines]] | None = None, schedule: dict | None = None,
                  injuries: dict | None = None, season: dict | None = None, team_stats: dict | None = None,
                  stats: dict | None = None) -> dict:
    """和 odds_context 相同，返回未转成 JSON 的字典，供聊天服务另取 analysis 推给网页和存档。
    stats 非空时写入 analysis_ms、cache_hits、cache_misses。"""
    events = board.events
    focus = None
    found = find_focus(messages, injuries) if not selection and messages else Focus(reason="selected")
    if teams := found.teams:
        # 未选场但问到具体球队时只给相关比赛，减少输入；找不到时给空列表并说明，不回退到整张表。
        matched = [event for event in events if {event.home_team, event.away_team} & teams]
        focus = {"teams": sorted(teams), "matched_events": len(matched), "total_events": len(events)}
        if found.players:
            focus["players"] = found.players
        events = matched
    if selection:
        event = next((event for event in events if event.id == selection.event_id), None)
        if event is None:
            raise ModelError("所选比赛已不在最新保存的数据中，请到比赛与盘口重新选场，或取消选场后再问。", 409)
        book = next((book for book in event.bookmakers if book.key == selection.bookmaker_key), None)
        if book is None:
            raise ModelError("所选平台在最新保存的数据中已无报价，请到比赛与盘口重新选择平台。", 409)
        signals = market_signals(board, [event], selection.bookmaker_key, history) if history is not None else None
        full = [event]  # 分析卡要用全部平台算共识
        events = [event.model_copy(update={"bookmakers": [book]})]
        detailed = events
        limit = None
    else:
        detailed = sorted(events, key=lambda event: event.commence_time)[:DETAIL_EVENT_LIMIT]
        limit = {"covered": len(detailed), "total": len(events)} if len(detailed) < len(events) else None
        signals = market_signals(board, detailed, None, history) if history is not None and detailed else None
        full = detailed
    few = bool(detailed)
    form = form_context(schedule, detailed) if few else None
    sections = {
        "schedule_rest": rest_context(schedule, detailed) if few else None,
        "team_form": form,
        "injuries": injury_context(injuries, detailed, form=form) if few else None,
        "season_strength": strength_context(season, detailed) if few else None,
        "team_efficiency": efficiency_context(team_stats, detailed) if few else None,
    }
    started = perf_counter()
    analysis = analyze(board, full, selection.bookmaker_key if selection else None, signals, sections, history, stats) if few else None
    if stats is not None:
        stats["analysis_ms"] = round((perf_counter() - started) * 1000, 1)
    if not selection and analysis:
        events = [_trim(event, analysis.get(event.id)) for event in events]

    payload = {
        "status": board.status,
        "provider": board.provider,
        "fetched_at": board.fetched_at,
        "stale": board.stale,
        "message": board.message,
        "selection": selection.model_dump() if selection else None,
        "focus": focus,
        "focus_note": UNRECOGNIZED_NOTE if found.reason in ("none", "time") and messages else None,
        "events": [event.model_dump() for event in events],
        "details_limit": limit,
        "analysis": analysis,
        "market_signals": signals,
        **sections,
    }
    return payload
