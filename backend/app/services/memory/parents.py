"""固定父块目录（开发文档第 7.3 节、D12）：种子命令和将来的抽取校验共用这一份。

模型只能从这里选 parent_key，不能自造父块；增删父块要同时改这里和开发文档第 7.3 节。
key 稳定不改；标题可以改，重新执行 seed --parents 会同步到库里。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ParentSpec:
    key: str
    title: str
    collects: str  # “收什么”：种子的初始摘要，也给抽取提示词说明每个父块的范围


PARENT_CATALOG: tuple[ParentSpec, ...] = (
    ParentSpec("output_pref", "用户输出偏好", "回答顺序、长度、格式、赔率显示"),
    ParentSpec("method_pref", "分析方法偏好", "用户要求看重或忽略的因素、玩法偏好"),
    ParentSpec("team_focus", "关注的球队与平台", "常看的球队、默认参考平台"),
    ParentSpec("event_research", "比赛研究笔记", "某场比赛的假设、观察、AI 判断（必须有期限）"),
    ParentSpec("review_notes", "复盘观察", "赛后复盘得出的、待验证的规律"),
)

PARENT_KEYS: tuple[str, ...] = tuple(spec.key for spec in PARENT_CATALOG)
_BY_KEY = {spec.key: spec for spec in PARENT_CATALOG}


def get_parent_spec(key: str) -> ParentSpec | None:
    """按 key 取目录项；不在目录里返回 None（抽取校验据此拒绝模型自造的父块）。"""
    return _BY_KEY.get(key)


def is_known_parent_key(key: str) -> bool:
    return key in _BY_KEY
