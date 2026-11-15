"""比赛分析层：把 chat_method 第 3 到 6 条的确定规则放进代码，每场比赛算一张分析卡（D2）。

模型只负责解释分析卡、回答追问；用户给出新消息（B 级资料）时才能推翻倾向，并写明原因（D1）。
现有的功能块框架在 services/analysis/，和这里无关。
"""

from app.schemas.odds import OddsBoard, OddsEvent
from app.services.match_analysis import cache, factors
from app.services.match_analysis.card import OVER, SPREAD, TOTAL, UNDER, Card
from app.services.match_analysis.lean import build_lean
from app.services.odds.signals import market_signals


def _section(context: dict | None, event_id: str) -> dict | None:
    if not isinstance(context, dict) or context.get("status") != "available":
        return None
    return (context.get("events") or {}).get(event_id)


def _fetched(context: dict | None) -> str | None:
    return context.get("fetched_at") if isinstance(context, dict) and context.get("status") == "available" else None


def _line_picker(market: str, signal: dict, event: OddsEvent, book: str | None):
    part = signal.get(market) or {}

    def line(side: str) -> tuple[float | None, str | None]:
        if book:
            point = (part.get("selected") or {}).get("home_point" if market == SPREAD else "point")
            if market == SPREAD and side == event.away_team and point is not None:
                point = -point
            return point, book
        key = {event.home_team: "best_for_home", event.away_team: "best_for_away", OVER: "best_for_over",
               UNDER: "best_for_under"}[side]
        best = part.get(key) or {}
        books = best.get("books") or [None]
        return best.get("point"), books[0]

    return line


def analyze_event(event: OddsEvent, book: str | None, signal: dict, sections: dict[str, dict | None]) -> Card:
    """event 带全部平台（用来算共识）；book 是选场时的平台。sections 是 payload 里各项资料的整段。"""
    home, away = event.home_team, event.away_team
    spread_part, total_part = signal.get(SPREAD) or {}, signal.get(TOTAL) or {}
    home_point, total = spread_part.get("consensus_home_point"), total_part.get("consensus")
    market_margin = -home_point if home_point is not None else None
    # 共识有报价不代表所选平台也有；缺失时不能用其他平台的线代替。
    spread_quoted = home_point is not None and (not book or bool(spread_part.get("selected")))
    total_quoted = total is not None and (not book or bool(total_part.get("selected")))
    fetched = {name: _fetched(sections.get(key)) for name, key in
               (("schedule", "schedule_rest"), ("injuries", "injuries"), ("season", "season_strength"), ("team_stats", "team_efficiency"))}
    missing: list[str] = []
    if not spread_quoted:
        missing.append(f"{book} 让分报价" if book else "让分报价")
    if not total_quoted:
        missing.append(f"{book} 总分报价" if book else "总分报价")
    form = _section(sections.get("team_form"), event.id)
    rows = [
        factors.strength(home, away, market_margin, form, _section(sections.get("season_strength"), event.id), fetched, missing),
        factors.injury(home, away, sections.get("injuries"), _section(sections.get("injuries"), event.id), fetched, missing),
        factors.rest(_section(sections.get("schedule_rest"), event.id), fetched, missing),
        factors.movement(SPREAD, signal, home, away),
        factors.price(SPREAD, signal, book),
        factors.pace(total, form, _section(sections.get("team_efficiency"), event.id), fetched, missing),
        factors.movement(TOTAL, signal, home, away),
        factors.price(TOTAL, signal, book),
    ]
    rows = [r for r in rows if r is not None]
    injuries = sections.get("injuries") or {}
    known = injuries.get("status") == "available" and not injuries.get("stale")
    card = Card(event.id, home, away, event.commence_time, book, home_point, total, rows, missing=missing,
                sources={"odds": signal.get("movement", {}).get("latest_fetched_at"), **fetched})
    card.spread = build_lean(SPREAD, [r for r in rows if r.market == SPREAD], home, away, market_margin if spread_quoted else None,
                             _line_picker(SPREAD, signal, event, book), known)
    card.total = build_lean(TOTAL, [r for r in rows if r.market == TOTAL], home, away, total if total_quoted else None,
                            _line_picker(TOTAL, signal, event, book), known)
    return card


def analyze(board: OddsBoard, events: list[OddsEvent], book: str | None, signals: dict | None,
            sections: dict[str, dict | None], history: list | None = None, stats: dict | None = None) -> dict[str, dict]:
    """events 用带全部平台的比赛；signals 为 None 时（没有历史快照）在这里按当前快照算一次。
    stats 非空时写入 cache_hits、cache_misses，供 chat.context 日志使用。"""
    fetched = {key: _fetched(sections.get(key)) for key in ("schedule_rest", "injuries", "season_strength", "team_efficiency")}
    stale = bool((sections.get("injuries") or {}).get("stale"))
    history_at = tuple(at for at, _ in history or [])
    cards, hits, todo = {}, 0, []
    for event in events:
        key = cache.card_key(event, book, board.fetched_at, history_at, fetched, stale)
        if (card := cache.get(key)) is not None:
            cards[event.id], hits = card, hits + 1
        else:
            todo.append((event, key))
    if todo:
        if signals is None:
            signals = market_signals(board, [event for event, _ in todo], book, history or [])
        for event, key in todo:
            cards[event.id] = analyze_event(event, book, signals.get(event.id) or {}, sections).to_dict()
            cache.put(key, cards[event.id])
    if stats is not None:
        stats.update(cache_hits=hits, cache_misses=len(todo))
    return {event.id: cards[event.id] for event in events}
