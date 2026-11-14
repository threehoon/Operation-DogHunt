"""从用户的话里找出问的是哪些球队；未选场时决定交给模型哪几场比赛。

认得三种说法：球队叫法（中文按包含、英文按整词）、大写缩写（LAL、DEN），以及球员名。
球员名只认两处：最新伤病名单里的球员（英文名），和下面手工维护的常用球星表（D4，不另接球员名单接口）。
"""

import re
import unicodedata
from dataclasses import dataclass, field

from app.schemas.chat import ChatMessage
from app.services.odds.schedule import TEAM_BY_ABBR

# 球队全名（与 The Odds API 一致）→ 用户常用叫法。英文按整词匹配，中文按包含匹配。
TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "Atlanta Hawks": ("老鹰", "hawks"),
    "Boston Celtics": ("凯尔特人", "凯尔特", "绿军", "绿凯", "celtics"),
    "Brooklyn Nets": ("篮网", "nets"),
    "Charlotte Hornets": ("黄蜂", "hornets"),
    "Chicago Bulls": ("公牛", "bulls"),
    "Cleveland Cavaliers": ("骑士", "cavaliers", "cavs"),
    "Dallas Mavericks": ("独行侠", "小牛", "mavericks", "mavs"),
    "Denver Nuggets": ("掘金", "nuggets"),
    "Detroit Pistons": ("活塞", "pistons"),
    "Golden State Warriors": ("勇士", "warriors"),
    "Houston Rockets": ("火箭", "rockets"),
    "Indiana Pacers": ("步行者", "溜马", "pacers"),
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

# 也是英文常用词的叫法：全小写时不算（"the heat is on"），写成 Heat、HEAT 才算。
COMMON_WORD_ALIASES = frozenset({"heat", "magic", "thunder", "suns", "kings", "jazz", "nets", "bucks", "spurs"})

# 大写缩写，按整词、区分大小写匹配；小写的 was、min、den 是普通英文词，不算。
# BALLDONTLIE 的 30 个缩写，加上 ESPN、DunkAPI 和常见写法。
TEAM_ABBR: dict[str, str] = {**TEAM_BY_ABBR, "GS": "Golden State Warriors", "NY": "New York Knicks", "SA": "San Antonio Spurs",
                             "UTAH": "Utah Jazz", "WSH": "Washington Wizards",
                             "PHO": "Phoenix Suns", "BRK": "Brooklyn Nets", "NOLA": "New Orleans Pelicans"}

# 含球队叫法但说的不是球队的词，匹配前先去掉。
NOT_TEAM_PHRASES = ("魔术师", "热火朝天", "太阳能", "太阳穴", "雷霆万钧", "火箭筒")

# 常用球星表：叫法 → (英文名, 球队)。手工维护，球队按 2025-26 赛季阵容整理（2026-06）；
# 新赛季转会要人工核对。伤病名单里有同一英文名时，以伤病名单的球队为准。
# 休赛期去向不确定的球员没有收录，问到时退回整张表并说明没认出。
STAR_PLAYERS: dict[str, tuple[str, str]] = {
    "约基奇": ("Nikola Jokic", "Denver Nuggets"),
    "约老师": ("Nikola Jokic", "Denver Nuggets"),
    "贾马尔穆雷": ("Jamal Murray", "Denver Nuggets"),
    "东契奇": ("Luka Doncic", "Los Angeles Lakers"),
    "东77": ("Luka Doncic", "Los Angeles Lakers"),
    "库里": ("Stephen Curry", "Golden State Warriors"),
    "巴特勒": ("Jimmy Butler", "Golden State Warriors"),
    "杜兰特": ("Kevin Durant", "Houston Rockets"),
    "申京": ("Alperen Sengun", "Houston Rockets"),
    "塔图姆": ("Jayson Tatum", "Boston Celtics"),
    "杰伦布朗": ("Jaylen Brown", "Boston Celtics"),
    "亚历山大": ("Shai Gilgeous-Alexander", "Oklahoma City Thunder"),
    "SGA": ("Shai Gilgeous-Alexander", "Oklahoma City Thunder"),
    "霍姆格伦": ("Chet Holmgren", "Oklahoma City Thunder"),
    "恩比德": ("Joel Embiid", "Philadelphia 76ers"),
    "马克西": ("Tyrese Maxey", "Philadelphia 76ers"),
    "布伦森": ("Jalen Brunson", "New York Knicks"),
    "唐斯": ("Karl-Anthony Towns", "New York Knicks"),
    "爱德华兹": ("Anthony Edwards", "Minnesota Timberwolves"),
    "华子": ("Anthony Edwards", "Minnesota Timberwolves"),
    "戈贝尔": ("Rudy Gobert", "Minnesota Timberwolves"),
    "文班亚马": ("Victor Wembanyama", "San Antonio Spurs"),
    "文班": ("Victor Wembanyama", "San Antonio Spurs"),
    "福克斯": ("De'Aaron Fox", "San Antonio Spurs"),
    "多诺万米切尔": ("Donovan Mitchell", "Cleveland Cavaliers"),
    "莫布里": ("Evan Mobley", "Cleveland Cavaliers"),
    "伦纳德": ("Kawhi Leonard", "Los Angeles Clippers"),
    "莱昂纳德": ("Kawhi Leonard", "Los Angeles Clippers"),
    "小卡": ("Kawhi Leonard", "Los Angeles Clippers"),
    "布克": ("Devin Booker", "Phoenix Suns"),
    "哈利伯顿": ("Tyrese Haliburton", "Indiana Pacers"),
    "班凯罗": ("Paolo Banchero", "Orlando Magic"),
    "锡安": ("Zion Williamson", "New Orleans Pelicans"),
    "康宁汉姆": ("Cade Cunningham", "Detroit Pistons"),
    "坎宁安": ("Cade Cunningham", "Detroit Pistons"),
    "斯科蒂巴恩斯": ("Scottie Barnes", "Toronto Raptors"),
    "三球": ("LaMelo Ball", "Charlotte Hornets"),
    "萨博尼斯": ("Domantas Sabonis", "Sacramento Kings"),
}

# 伤病名单里的英文姓，只有唯一且不是常用词时才单独认（"jokic" 认，"green"、"brown" 不认）。
COMMON_SURNAMES = frozenset({"allen", "ball", "banks", "barnes", "bridges", "brown", "craig", "davis", "green", "grant",
                             "harris", "holiday", "house", "hunter", "james", "johnson", "jones", "king", "love", "miller",
                             "porter", "price", "rose", "smith", "strong", "thomas", "walker", "wells", "white", "williams",
                             "young"})

# 前端选场时在用户消息末尾附加的标记，与 frontend/src/components/chat/types.ts 的 SELECTION_MARK 一致。
SELECTION_MARK = "\n\n本次选场："
# 问到全部、其他或多场比赛，表示要看整张表，不再沿用上文球队；即使同一句里提到了球队（“除了猛龙，别的比赛呢”）。
WIDE_SCOPE = re.compile(r"全部|整张|(所有|其他|其它|别的|其余|剩下的?|另外的?|每一?|哪几?|哪一?|哪些|什么|几)的?(比赛|场)")
# 时间范围词（D5）：没提球队时表示问当天整张表（“分析一下今晚”）；提了球队时以球队为准（“约基奇今天打吗”）。
TIME_SCOPE = re.compile(r"今晚|今天|今日|明天|明晚|昨天|tonight|today|tomorrow", re.I)


@dataclass
class Focus:
    """teams 为空表示给整张表；reason 说明为什么：teams 认出了球队，wide 问多场，time 问某天，none 没认出。"""

    teams: set[str] = field(default_factory=set)
    players: dict[str, str] = field(default_factory=dict)  # 认出的球员叫法 → 球队
    reason: str = "none"


def _plain(text: str) -> str:
    """去掉重音：Jokić → Jokic。"""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _fold(text: str) -> str:
    """去掉重音和中文名里的间隔号、空格，用来匹配中文叫法（“斯科蒂·巴恩斯”）。"""
    return re.sub(r"[·•・\s]+", "", _plain(text))


def _word(alias: str, text: str, flags: int = 0) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags) is not None


def mentioned_teams(text: str) -> set[str]:
    for phrase in NOT_TEAM_PHRASES:
        text = text.replace(phrase, " ")
    lowered = text.lower()
    found = set()
    for team, aliases in TEAM_ALIASES.items():
        for alias in aliases:
            if not alias.isascii():
                hit = alias in text
            elif alias in COMMON_WORD_ALIASES:
                hit = any(m.group(0) != alias for m in re.finditer(rf"(?<![A-Za-z0-9]){alias}(?![A-Za-z0-9])", text, re.I))
            else:
                hit = _word(alias, lowered)
            if hit:
                found.add(team)
                break
    found.update(team for abbr, team in TEAM_ABBR.items() if _word(abbr, text))
    return found


def injury_players(injuries: dict | None) -> dict[str, str]:
    """伤病快照里的球员英文名 → 球队；injuries 是 load_injuries 的结果。"""
    if not injuries or not isinstance(injuries.get("teams"), dict):
        return {}
    return {_plain(p["player"]).strip(): team for team, players in injuries["teams"].items()
            for p in players if isinstance(p, dict) and isinstance(p.get("player"), str)}


def mentioned_players(text: str, injuries: dict | None = None) -> dict[str, str]:
    """返回认出的球员叫法 → 球队。中文叫法查球星表，英文名查伤病名单和球星表的英文名。"""
    listed = injury_players(injuries)
    known = {**{name: team for name, team in STAR_PLAYERS.values()}, **listed}
    folded = _fold(text)
    found: dict[str, str] = {}
    for alias, (name, team) in STAR_PLAYERS.items():
        hit = alias in folded if not alias.isascii() else _word(alias, text)
        if hit:
            found[alias] = listed.get(name, team)
    plain = _plain(text)
    surnames: dict[str, list[str]] = {}
    for name in known:
        surnames.setdefault(name.split()[-1].lower(), []).append(name)
    for name, team in known.items():
        surname = name.split()[-1].lower()
        unique = len(surnames[surname]) == 1 and len(surname) >= 4 and surname not in COMMON_SURNAMES
        if _word(name, plain, re.I) or (unique and _word(surname, plain, re.I)):
            found[name] = team
    return found


def find_focus(messages: list[ChatMessage], injuries: dict | None = None) -> Focus:
    """从最近一条提到球队或球员的用户消息里取球队，让“那大小分呢”这类追问沿用上文的比赛。

    只在未选场时调用：之前消息里的选场标签已被用户取消，不再算作提到球队。
    问多场（WIDE_SCOPE）就停止沿用；没提球队、只带今晚、今天这类时间词也停止沿用（D5）。
    """
    for message in reversed(messages):
        if message.role != "user":
            continue
        text = message.content.split(SELECTION_MARK, 1)[0]
        if WIDE_SCOPE.search(text):
            return Focus(reason="wide")
        players = mentioned_players(text, injuries)
        if teams := mentioned_teams(text) | set(players.values()):
            return Focus(teams, players, "teams")
        if TIME_SCOPE.search(text):
            return Focus(reason="time")
    return Focus()


def focus_teams(messages: list[ChatMessage], injuries: dict | None = None) -> set[str]:
    return find_focus(messages, injuries).teams
