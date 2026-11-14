"""选边、把握和预计范围（chat_method 第 4 到 6 条，chat_output 的把握分档）。

- 选边：按 rank 从前往后取理由；同一级里两边都有时按条数多的一边，条数相同就看下一级；都没有时按微弱倾向选一边，把握“低”。
- 把握：高 = 至少两项独立理由同向、其中至少一项是盘口移动或战绩差、没有反向理由、伤病已知；
  中 = 至少一项中等理由（新伤病、盘口移动、战绩差、休息差）且没有反向的中等理由；其余为低。
  只靠新伤病和休息差时不超过“中”：盘口通常已计入这两类资料。报价差只能作补充，不能升档。
- 预计范围：以共识线为基准向倾向一侧调整 0 到 3 分，宽 6 分，并保证和所选的线一致。
"""

from collections.abc import Callable

from app.services.match_analysis.card import OVER, RANK_FALLBACK, SPREAD, UNDER, Factor, Lean

MEDIUM_KEYS = {"injury", "movement", "strength", "pace", "rest"}
STRONG_KEYS = {"movement", "strength", "pace"}
NEWS_AND_REST = {"injury", "rest"}
RANGE_HALF_WIDTH = 3.0
MAX_ADJUST = 3.0
ADJUST_BY_CONFIDENCE = {"低": 0.5, "中": 1.0, "高": 2.0}


def _half(value: float) -> float:
    """取到最近的 0.5。"""
    return round(value * 2) / 2


def pick_side(rows: list[Factor], market: str, home: str, away: str) -> tuple[str, bool]:
    """返回（倾向的一边, 是否只是微弱倾向）。"""
    reasons = [r for r in rows if r.is_reason and r.points_to]
    for rank in sorted({r.rank for r in reasons}):
        counts: dict[str, int] = {}
        for r in reasons:
            if r.rank == rank:
                counts[r.points_to] = counts.get(r.points_to, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: -item[1])
        if len(ordered) == 1 or ordered[0][1] > ordered[1][1]:
            return ordered[0][0], False
    # 没有理由或互相抵消：先看只作参照的因素或不到门槛的差值方向，再按惯例选主队 / 小分。
    for r in sorted(rows, key=lambda r: r.key not in ("strength", "pace")):
        if r.points_to:
            return r.points_to, True
        if r.key in ("strength", "pace") and r.diff:
            if market == SPREAD:
                return (home if r.diff > 0 else away), True
            return OVER if r.diff > 0 else UNDER, True
    return (home if market == SPREAD else UNDER), True


def confidence(rows: list[Factor], side: str, weak: bool, injuries_known: bool) -> tuple[str, str, list[str]]:
    """返回（把握, 原因, 用作理由的因素 key）。"""
    reasons = [r for r in rows if r.is_reason and r.points_to]
    backing = sorted((r for r in reasons if r.points_to == side), key=lambda r: r.rank or RANK_FALLBACK)
    against = [r for r in reasons if r.points_to != side]
    basis = [r.key for r in backing]
    if weak:
        why = "因素互相抵消，只是微弱倾向" if reasons else "两边差距很小，只是微弱倾向"
        return "低", why, basis
    medium = [r for r in backing if r.key in MEDIUM_KEYS]
    medium_against = [r for r in against if r.key in MEDIUM_KEYS]
    labels = "、".join(r.label for r in backing)
    if not medium:
        return "低", f"只有{labels}，没有更有分量的理由", basis
    if medium_against:
        return "低", f"{labels}和{'、'.join(r.label for r in medium_against)}方向相反", basis
    keys = {r.key for r in medium}
    if len(keys) >= 2 and keys & STRONG_KEYS and not against and injuries_known:
        return "高", f"{labels}同向，没有反向因素，伤病已知", basis
    if keys <= NEWS_AND_REST:
        return "中", f"{labels}指向这边；盘口通常已计入伤病和休息差，最多到中", basis
    reasons_text = f"{labels}指向这边"
    if against:
        reasons_text += f"；反向只有{'、'.join(r.label for r in against)}"
    elif len(keys) >= 2 and not injuries_known:
        reasons_text += "；伤病未知，不到高"
    elif len(keys) < 2:
        reasons_text += "；只有一项中等理由"
    return "中", reasons_text, basis


def _adjust(rows: list[Factor], market: str, side_sign: int, level: str, weak: bool) -> float:
    """战绩推算同向时按差值的一半调整，否则按把握。结果在 0.5 到 3 之间。"""
    if weak:
        return ADJUST_BY_CONFIDENCE["低"]
    key = "strength" if market == SPREAD else "pace"
    gap = next((r for r in rows if r.key == key and r.diff and r.diff * side_sign > 0), None)
    if gap:
        return min(MAX_ADJUST, max(0.5, _half(abs(gap.diff) / 2)))
    return ADJUST_BY_CONFIDENCE[level]


def expected_range(base: float, line_value: float | None, side_sign: int, adjust: float) -> tuple[float, list[float], str]:
    """base 是共识（主队净胜分或总分），line_value 是所选那条线换成同一口径后的数值。
    返回（实际调整, [下限, 上限], 说明）；中点必须落在所选线有利的一侧，否则挪到线外半分。"""
    center = _half(base + side_sign * adjust)
    note = ""
    if line_value is not None and (center - line_value) * side_sign <= 0:
        center = _half(line_value + side_sign * 0.5)
        note = "中点按所选的线挪到线外半分"
    actual = round(abs(center - base), 2)
    if actual > MAX_ADJUST:
        note = f"所选的线离共识超过 {MAX_ADJUST:g} 分，中点按线外半分取"
    return actual, [center - RANGE_HALF_WIDTH, center + RANGE_HALF_WIDTH], note


def margin_text(home: str, away: str, low: float, high: float) -> str:
    if low >= 0:
        return f"{home} 赢 {low:g}–{high:g} 分"
    if high <= 0:
        return f"{away} 赢 {-high:g}–{-low:g} 分"
    return f"{away} 赢 {-low:g} 分到 {home} 赢 {high:g} 分"


def build_lean(market: str, rows: list[Factor], home: str, away: str, base: float | None,
               line: Callable[[str], tuple[float | None, str | None]], injuries_known: bool) -> Lean | None:
    """line(倾向的一边) 返回（这一边的线, 平台）；base 为 None 表示这个玩法没有报价。"""
    if base is None:
        return None
    side, weak = pick_side(rows, market, home, away)
    level, why, basis = confidence(rows, side, weak, injuries_known)
    sign = (1 if side == home else -1) if market == SPREAD else (1 if side == OVER else -1)
    point, book = line(side)
    # 换成和 base 一样的口径：让分用主队净胜分，球队视角的让分点取相反数再按主客换号。
    line_value = None if point is None else (-point if side == home else point) if market == SPREAD else point
    if weak and not any(r.key in ("strength", "pace") and r.market == market for r in rows):
        why = f"缺少{'实力与战绩' if market == SPREAD else '节奏与得分'}资料，只是微弱倾向"
    adjust, span, note = expected_range(base, line_value, sign, _adjust(rows, market, sign, level, weak))
    text = margin_text(home, away, *span) if market == SPREAD else f"总分 {span[0]:g}–{span[1]:g}"
    if note:
        why = f"{why}；{note}"
    return Lean(market, side, point, book, level, why, basis, weak, adjust, span, text)
