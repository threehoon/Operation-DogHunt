"""分析卡缓存：同一场比赛、同一平台，各数据源采集时间都没变时直接用上次算好的卡。

键里放比赛 id、所选平台、盘口快照与历史快照的采集时间、赛程 / 伤病 / 整季 / 效率的采集时间、伤病是否过期、
分析卡版本，再加这场比赛报价的哈希（测试和手工快照可能共用同一个采集时间）。只存在进程内存里，重启后端即清空。
"""

import hashlib
from collections import OrderedDict

from app.schemas.odds import OddsEvent
from app.services.match_analysis.card import CARD_VERSION

MAX_CARDS = 64  # 一天最多 15 场，留足选场、换平台和历史快照变化的余量

_cards: OrderedDict[tuple, dict] = OrderedDict()


def card_key(event: OddsEvent, book: str | None, odds_at: str | None, history_at: tuple, fetched: dict,
             injuries_stale: bool) -> tuple:
    digest = hashlib.sha1(event.model_dump_json().encode("utf-8")).hexdigest()[:16]
    sources = tuple(sorted(fetched.items()))
    return CARD_VERSION, event.id, book, odds_at, history_at, sources, injuries_stale, digest


def get(key: tuple) -> dict | None:
    card = _cards.get(key)
    if card is not None:
        _cards.move_to_end(key)
    return card


def put(key: tuple, card: dict) -> None:
    _cards[key] = card
    _cards.move_to_end(key)
    while len(_cards) > MAX_CARDS:
        _cards.popitem(last=False)


def clear() -> None:
    _cards.clear()
