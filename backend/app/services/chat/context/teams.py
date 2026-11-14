"""从用户的话里找出问的是哪些球队；未选场时决定交给模型哪几场比赛。"""

import re

from app.schemas.chat import ChatMessage

# 球队全名（与 The Odds API 一致）→ 用户常用叫法。英文按整词匹配，中文按包含匹配。
TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "Atlanta Hawks": ("老鹰", "hawks"),
    "Boston Celtics": ("凯尔特人", "绿军", "celtics"),
    "Brooklyn Nets": ("篮网", "nets"),
    "Charlotte Hornets": ("黄蜂", "hornets"),
    "Chicago Bulls": ("公牛", "bulls"),
    "Cleveland Cavaliers": ("骑士", "cavaliers", "cavs"),
    "Dallas Mavericks": ("独行侠", "小牛", "mavericks", "mavs"),
    "Denver Nuggets": ("掘金", "nuggets"),
    "Detroit Pistons": ("活塞", "pistons"),
    "Golden State Warriors": ("勇士", "warriors"),
    "Houston Rockets": ("火箭", "rockets"),
    "Indiana Pacers": ("步行者", "pacers"),
    "Los Angeles Clippers": ("快船", "clippers"),
    "Los Angeles Lakers": ("湖人", "lakers"),
    "Memphis Grizzlies": ("灰熊", "grizzlies"),
    "Miami Heat": ("热火", "heat"),
    "Milwaukee Bucks": ("雄鹿", "bucks"),
    "Minnesota Timberwolves": ("森林狼", "timberwolves", "wolves"),
    "New Orleans Pelicans": ("鹈鹕", "pelicans"),
    "New York Knicks": ("尼克斯", "knicks"),
    "Oklahoma City Thunder": ("雷霆", "thunder"),
    "Orlando Magic": ("魔术", "magic"),
    "Philadelphia 76ers": ("76人", "七六人", "76ers", "sixers"),
    "Phoenix Suns": ("太阳", "suns"),
    "Portland Trail Blazers": ("开拓者", "blazers"),
    "Sacramento Kings": ("国王", "kings"),
    "San Antonio Spurs": ("马刺", "spurs"),
    "Toronto Raptors": ("猛龙", "raptors"),
    "Utah Jazz": ("爵士", "jazz"),
    "Washington Wizards": ("奇才", "wizards"),
}


def mentioned_teams(text: str) -> set[str]:
    lowered = text.lower()
    found = set()
    for team, aliases in TEAM_ALIASES.items():
        for alias in aliases:
            hit = alias in text if not alias.isascii() else re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered)
            if hit:
                found.add(team)
                break
    return found


# 前端选场时在用户消息末尾附加的标记，与 frontend/src/components/chat/types.ts 的 SELECTION_MARK 一致。
SELECTION_MARK = "\n\n本次选场："
# 问到全部、其他或多场比赛，表示要看整张表，不再沿用上文球队。
WIDE_SCOPE = re.compile(r"全部|整张|(所有|其他|其它|别的|其余|剩下的?|另外的?|每一?|哪几?|哪一?|哪些|什么|几)的?(比赛|场)")


def focus_teams(messages: list[ChatMessage]) -> set[str]:
    """从最近一条提到球队的用户消息里取球队，让“那大小分呢”这类追问沿用上文的比赛。

    只在未选场时调用：之前消息里的选场标签已被用户取消，不再算作提到球队；
    遇到问全部或其他比赛的消息就停止沿用，返回空集合表示给整张表。
    """
    for message in reversed(messages):
        if message.role != "user":
            continue
        text = message.content.split(SELECTION_MARK, 1)[0]
        if WIDE_SCOPE.search(text):
            return set()
        if teams := mentioned_teams(text):
            return teams
    return set()
