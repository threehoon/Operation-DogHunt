"""分析卡的数据结构：每场比赛一张，服务端按 chat_method 的确定规则算好，模型只负责解释。

全部字段都能直接转成 JSON（dataclasses.asdict），写进交给模型的资料、SSE 的 analysis 事件和聊天存档。
"""

from dataclasses import asdict, dataclass, field

CARD_VERSION = "match_analysis_v1"

SPREAD, TOTAL = "spread", "total"
OVER, UNDER = "Over", "Under"

# 选边理由的优先顺序（chat_method 第 4 条）；数字越小越靠前。
RANK_NEWS = 1  # 新伤病、盘口明显移动（B 级资料由模型按 D1 处理，服务端没有）
RANK_GAP = 2  # 战绩推算和共识的差达到门槛
RANK_PRICE = 3  # 所选平台的线比共识有利
RANK_REST = 4  # 休息差
RANK_FALLBACK = 6  # 两边差距很小，只是微弱倾向


@dataclass
class Factor:
    """一行因素。points_to 是这项指向的一边（球队全名，或 Over / Under），None 表示不指向任何一边。"""

    market: str  # spread / total
    key: str  # strength、pace、rest、injury、movement、price
    label: str  # 中文名，例如“实力差距”
    grade: str  # 资料等级：A
    source: str  # 具体来源，例如“BALLDONTLIE 整季比分推算”
    ours: float | None = None  # 我方数值：推算的主队净胜分或总分
    market_value: float | None = None  # 市场数值：共识的主队净胜分或总分
    diff: float | None = None  # ours − market_value
    points_to: str | None = None
    is_reason: bool = False  # 算不算选边理由（达到门槛、样本够、方向明确）
    rank: int | None = None  # 是理由时的优先级
    note: str = ""
    fetched_at: str | None = None


@dataclass
class Lean:
    """一个玩法的倾向。line 是写给用户的那条线：选场时是所选平台，否则是对这一边最有利的平台。"""

    market: str
    side: str  # 球队全名，或 Over / Under
    point: float | None  # 这一边的让分点（球队视角）或总分线
    book: str | None
    confidence: str  # 高 / 中 / 低
    confidence_reason: str
    basis: list[str] = field(default_factory=list)  # 用作理由的因素 key，按优先顺序
    weak: bool = False  # True 表示没有理由，只是微弱倾向（第 4 条最后一档）
    adjust: float = 0.0  # 预计中点相对共识向倾向一侧移动的分数，0 到 3
    expected: list[float] = field(default_factory=list)  # 让分：主队净胜分范围；大小分：总分范围；宽度不超过 6
    expected_text: str = ""


@dataclass
class Card:
    event_id: str
    home: str
    away: str
    commence_time: str
    book: str | None  # 选场时的平台；未选场为 None
    consensus_home_point: float | None
    consensus_total: float | None
    factors: list[Factor] = field(default_factory=list)
    spread: Lean | None = None
    total: Lean | None = None
    missing: list[str] = field(default_factory=list)  # 缺失资料，每项写影响哪个玩法
    sources: dict[str, str | None] = field(default_factory=dict)  # 各项资料的采集时间
    version: str = CARD_VERSION

    def to_dict(self) -> dict:
        return asdict(self)
