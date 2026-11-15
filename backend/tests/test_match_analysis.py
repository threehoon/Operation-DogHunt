"""比赛分析层：门槛（含恰好等于门槛）、新伤病三种情况、小样本、理由优先顺序、把握分档和预计范围。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.schemas.chat import OddsSelection
from app.services.chat import prompt as chat_prompt
from app.services.chat.context.payload import build_payload
from app.services.match_analysis import factors
from app.services.match_analysis.card import OVER, RANK_GAP, RANK_NEWS, RANK_PRICE, RANK_REST, SPREAD, TOTAL, UNDER, Factor
from app.services.match_analysis.lean import build_lean, confidence, expected_range, margin_text, pick_side
from app.services.odds.board import build_board

HOME, AWAY = "Denver Nuggets", "Los Angeles Lakers"


def form(home_games=10, away_games=10, margin=0.0, home_total=230.0, away_total=230.0):
    return {"home": {"games": home_games, "avg_game_total": home_total},
            "away": {"games": away_games, "avg_game_total": away_total},
            "home_minus_away_avg_margin": margin}


def reason(key, side, rank, market=SPREAD, label=None):
    return Factor(market, key, label or key, "A", "test", points_to=side, is_reason=True, rank=rank)


class StrengthTests(unittest.TestCase):
    def strength(self, margin, market_margin, **kw):
        missing = []
        return factors.strength(HOME, AWAY, market_margin, form(margin=margin, **kw), None, {}, missing), missing

    def test_gap_exactly_three_is_a_reason(self):
        row, _ = self.strength(4.0, 3.0)  # 4 + 主场 2 = 6，市场 3
        self.assertEqual((row.diff, row.points_to, row.is_reason, row.rank), (3.0, HOME, True, RANK_GAP))

    def test_gap_below_three_is_not_a_reason(self):
        row, _ = self.strength(3.9, 3.0)
        self.assertEqual((row.diff, row.points_to, row.is_reason), (2.9, None, False))

    def test_away_side_when_market_rates_home_higher(self):
        row, _ = self.strength(0.0, 5.0)
        self.assertEqual((row.diff, row.points_to, row.is_reason), (-3.0, AWAY, True))

    def test_small_sample_is_reference_only(self):
        row, _ = self.strength(10.0, 3.0, away_games=4)
        self.assertEqual((row.points_to, row.is_reason, row.rank), (HOME, False, None))
        self.assertIn("不足 5 场", row.note)

    def test_season_rating_wins_and_opposite_form_is_noted(self):
        season = {"model_home_margin": 8.0, "home": {"games": 12}, "away": {"games": 12}}
        row = factors.strength(HOME, AWAY, 4.0, form(margin=-1.0), season, {"season": "t"}, [])
        self.assertEqual((row.ours, row.diff, row.points_to, row.fetched_at), (8.0, 4.0, HOME, "t"))
        self.assertIn("方向和整季相反", row.note)

    def test_season_source_names_ratings_and_home_edge(self):
        season = {"model_home_margin": 5.5, "home_edge": 2.5, "home": {"games": 12, "rating": 4.0}, "away": {"games": 12, "rating": 1.0}}
        row = factors.strength(HOME, AWAY, 4.0, None, season, {"season": "t"}, [])
        self.assertIn("主队实力分 4，客队 1，主场优势 2.5", row.source)

    def test_missing_form_and_season(self):
        missing = []
        self.assertIsNone(factors.strength(HOME, AWAY, 3.0, None, None, {}, missing))
        self.assertEqual(missing, ["实力与战绩（让分）"])


class PaceTests(unittest.TestCase):
    def test_gap_exactly_four_points_to_over(self):
        row = factors.pace(226.0, form(home_total=228.0, away_total=232.0), None, {}, [])
        self.assertEqual((row.ours, row.diff, row.points_to, row.is_reason), (230.0, 4.0, OVER, True))

    def test_gap_below_four(self):
        row = factors.pace(233.9, form(), None, {}, [])
        self.assertEqual((row.diff, row.points_to, row.is_reason), (-3.9, None, False))

    def test_efficiency_projection_first(self):
        row = factors.pace(230.0, form(), {"projection": {"total": 224.0}}, {"team_stats": "t"}, [])
        self.assertEqual((row.ours, row.points_to, row.is_reason, row.fetched_at), (224.0, UNDER, True, "t"))

    def test_small_sample_is_reference_only(self):
        row = factors.pace(220.0, form(home_games=3), None, {}, [])
        self.assertEqual((row.points_to, row.is_reason), (OVER, False))


class RestTests(unittest.TestCase):
    def rest(self, home, away, listed=True):
        event = {"status": "available", "listed_in_schedule": listed,
                 "home": {"team": HOME, **home}, "away": {"team": AWAY, **away}}
        return factors.rest(event, {}, [])

    def test_back_to_back_against_rested(self):
        row = self.rest({"rest_days": 2}, {"back_to_back": True, "rest_days": 0})
        self.assertEqual((row.points_to, row.is_reason, row.rank), (HOME, True, RANK_REST))

    def test_three_in_four_days_counts_as_tired(self):
        row = self.rest({"games_in_4_days": 3, "rest_days": 1}, {"rest_days_at_least": 5})
        self.assertEqual(row.points_to, AWAY)
        self.assertIn("至少休 5 天", row.note)

    def test_both_tired_or_no_rest_day_is_no_reason(self):
        self.assertIsNone(self.rest({"back_to_back": True}, {"back_to_back": True}).points_to)
        self.assertIsNone(self.rest({"back_to_back": True}, {"rest_days": 0}).points_to)

    def test_unmatched_schedule_is_reference_only(self):
        row = self.rest({"rest_days": 2}, {"back_to_back": True}, listed=False)
        self.assertEqual((row.points_to, row.is_reason), (HOME, False))
        self.assertIn("可能对不上", row.note)

    def test_missing_schedule(self):
        missing = []
        self.assertIsNone(factors.rest({"status": "unavailable"}, {}, missing))
        self.assertEqual(missing, ["赛程与体能（让分）"])


class InjuryTests(unittest.TestCase):
    def injury(self, home_players, away_players, stale=False, listed=True):
        event = {"home": {"listed": listed, "players": home_players}, "away": {"listed": True, "players": away_players}}
        return factors.injury(HOME, AWAY, {"status": "available", "stale": stale}, event, {}, [])

    def out(self, name, new):
        return {"player": name, "status": "Out", "new_since_last_game": new}

    def test_new_out_points_to_opponent(self):
        row = self.injury([], [self.out("A", True)])
        self.assertEqual((row.points_to, row.is_reason, row.rank), (HOME, True, RANK_NEWS))
        self.assertIn("新伤病缺阵：A", row.note)

    def test_out_already_in_form_is_no_reason(self):
        row = self.injury([], [self.out("B", False)])
        self.assertEqual((row.points_to, row.is_reason), (None, False))
        self.assertIn("已在战绩里", row.note)

    def test_unknown_newness_is_no_reason(self):
        row = self.injury([self.out("C", None)], [])
        self.assertIsNone(row.points_to)
        self.assertIn("无法确定是否新伤病", row.note)

    def test_new_outs_on_both_sides_cancel(self):
        row = self.injury([self.out("C", True)], [self.out("A", True)])
        self.assertIsNone(row.points_to)
        self.assertIn("互相抵消", row.note)

    def test_unlisted_and_stale_notes(self):
        row = self.injury([], [{"player": "D", "status": "Day-To-Day"}], stale=True, listed=False)
        self.assertIn("未列出伤病，不等于确认全员可出场", row.note)
        self.assertIn("出场待定：D", row.note)
        self.assertIn("超过 12 小时", row.note)

    def test_missing_injuries(self):
        missing = []
        self.assertIsNone(factors.injury(HOME, AWAY, None, None, {}, missing))
        self.assertEqual(missing, ["伤病情况未知（让分、大小分）"])


class MarketFactorTests(unittest.TestCase):
    def signal(self, home_pair, total_pair):
        return {"movement": {"snapshots": 2, "consensus_home_point": home_pair, "consensus_total": total_pair}}

    def test_spread_move_of_exactly_one(self):
        row = factors.movement(SPREAD, self.signal([-5.0, -6.0], [230.0, 230.0]), HOME, AWAY)
        self.assertEqual((row.diff, row.points_to, row.is_reason, row.rank), (-1.0, HOME, True, RANK_NEWS))
        row = factors.movement(SPREAD, self.signal([-5.0, -4.0], [230.0, 230.0]), HOME, AWAY)
        self.assertEqual(row.points_to, AWAY)

    def test_small_move_and_single_snapshot(self):
        row = factors.movement(TOTAL, self.signal([-5.0, -5.0], [230.0, 230.5]), HOME, AWAY)
        self.assertEqual((row.points_to, row.is_reason), (None, False))
        row = factors.movement(TOTAL, {"movement": {"snapshots": 1}}, HOME, AWAY)
        self.assertIn("只有一份快照", row.note)

    def test_total_move_up_points_to_over(self):
        row = factors.movement(TOTAL, self.signal([-5.0, -5.0], [228.0, 229.0]), HOME, AWAY)
        self.assertEqual(row.points_to, OVER)

    def test_price_only_with_selected_book(self):
        signal = {"spread": {"consensus_home_point": -6.75, "selected": {"home_point": -6.5, "diff_vs_consensus": 0.25, "better_for": HOME}}}
        self.assertIsNone(factors.price(SPREAD, signal, None))
        row = factors.price(SPREAD, signal, "draftkings")
        self.assertEqual((row.points_to, row.is_reason, row.rank), (HOME, True, RANK_PRICE))
        signal["spread"]["selected"].update(better_for=None, diff_vs_consensus=0)
        self.assertFalse(factors.price(SPREAD, signal, "draftkings").is_reason)


class LeanTests(unittest.TestCase):
    def test_earlier_rank_wins(self):
        rows = [reason("injury", HOME, RANK_NEWS), reason("strength", AWAY, RANK_GAP), reason("rest", AWAY, RANK_REST)]
        self.assertEqual(pick_side(rows, SPREAD, HOME, AWAY), (HOME, False))

    def test_tie_in_rank_moves_to_next_rank(self):
        rows = [reason("injury", HOME, RANK_NEWS), reason("movement", AWAY, RANK_NEWS), reason("rest", AWAY, RANK_REST)]
        self.assertEqual(pick_side(rows, SPREAD, HOME, AWAY), (AWAY, False))

    def test_more_reasons_in_same_rank_wins(self):
        rows = [reason("injury", HOME, RANK_NEWS), reason("movement", HOME, RANK_NEWS), reason("x", AWAY, RANK_NEWS)]
        self.assertEqual(pick_side(rows, SPREAD, HOME, AWAY), (HOME, False))

    def test_fallback_uses_reference_then_gap_direction_then_default(self):
        ref = Factor(SPREAD, "rest", "r", "A", "t", points_to=AWAY)
        self.assertEqual(pick_side([ref], SPREAD, HOME, AWAY), (AWAY, True))
        gap = Factor(TOTAL, "pace", "p", "A", "t", diff=2.0)
        self.assertEqual(pick_side([gap], TOTAL, HOME, AWAY), (OVER, True))
        self.assertEqual(pick_side([], SPREAD, HOME, AWAY), (HOME, True))
        self.assertEqual(pick_side([], TOTAL, HOME, AWAY), (UNDER, True))

    def test_high_needs_two_medium_with_a_strong_one_and_known_injuries(self):
        rows = [reason("strength", HOME, RANK_GAP), reason("rest", HOME, RANK_REST)]
        self.assertEqual(confidence(rows, HOME, False, True)[0], "高")
        self.assertEqual(confidence(rows, HOME, False, False)[0], "中")
        self.assertEqual(confidence(rows + [reason("price", AWAY, RANK_PRICE)], HOME, False, True)[0], "中")

    def test_news_and_rest_only_caps_at_medium(self):
        rows = [reason("injury", HOME, RANK_NEWS), reason("rest", HOME, RANK_REST)]
        level, why, basis = confidence(rows, HOME, False, True)
        self.assertEqual((level, basis), ("中", ["injury", "rest"]))
        self.assertIn("最多到中", why)

    def test_price_alone_or_opposing_medium_is_low(self):
        self.assertEqual(confidence([reason("price", HOME, RANK_PRICE)], HOME, False, True)[0], "低")
        rows = [reason("injury", HOME, RANK_NEWS), reason("rest", AWAY, RANK_REST)]
        self.assertEqual(confidence(rows, HOME, False, True)[0], "低")
        self.assertEqual(confidence([], HOME, True, True)[0], "低")

    def test_expected_range_width_and_line_side(self):
        adjust, span, note = expected_range(6.75, 6.5, 1, 1.0)
        self.assertEqual((adjust, span, note), (1.25, [5.0, 11.0], ""))
        # 所选的线在共识更外侧：中点挪到线外半分
        adjust, span, note = expected_range(3.0, 4.0, 1, 0.5)
        self.assertEqual(span, [1.5, 7.5])
        self.assertIn("线外半分", note)
        self.assertLessEqual(span[1] - span[0], 6)

    def test_margin_text(self):
        self.assertEqual(margin_text(HOME, AWAY, 2, 8), f"{HOME} 赢 2–8 分")
        self.assertEqual(margin_text(HOME, AWAY, -8, -2), f"{AWAY} 赢 2–8 分")
        self.assertEqual(margin_text(HOME, AWAY, -2, 4), f"{AWAY} 赢 2 分到 {HOME} 赢 4 分")

    def test_build_lean_adjusts_by_half_the_gap(self):
        gap = Factor(SPREAD, "strength", "实力差距", "A", "t", diff=4.0, points_to=HOME, is_reason=True, rank=RANK_GAP)
        lean = build_lean(SPREAD, [gap], HOME, AWAY, 3.0, lambda side: (-3.0, "fanduel"), True)
        self.assertEqual((lean.side, lean.point, lean.book, lean.confidence, lean.adjust, lean.expected),
                         (HOME, -3.0, "fanduel", "中", 2.0, [2.0, 8.0]))
        self.assertIsNone(build_lean(TOTAL, [], HOME, AWAY, None, lambda side: (None, None), True))

    def test_build_lean_says_when_data_is_missing(self):
        lean = build_lean(TOTAL, [], HOME, AWAY, 224.5, lambda side: (224.5, "draftkings"), True)
        self.assertEqual((lean.side, lean.weak, lean.confidence), (UNDER, True, "低"))
        self.assertIn("缺少节奏与得分资料", lean.confidence_reason)


class SnapshotCardTests(unittest.TestCase):
    """用 S-001 + D-001 固定快照走完整的 payload，核对湖人对掘金那张卡。"""

    def payload(self, selected=True, stats=None, missing_market=None):
        from app.services.odds.injuries import load_injuries
        from app.services.odds.schedule import load_schedule
        folder = chat_prompt.CHAT_DIR / chat_prompt.EVAL_DIR / "snapshots"
        data = json.loads((folder / "D-001.json").read_text(encoding="utf-8"))
        snap = json.loads((folder / "S-001.json").read_text(encoding="utf-8"))
        if missing_market:
            event = next(e for e in snap["raw_events"] if e["id"] == snap["_eval"]["selection"]["event_id"])
            book = next(b for b in event["bookmakers"] if b["key"] == snap["_eval"]["selection"]["bookmaker_key"])
            book["markets"] = [m for m in book["markets"] if m["key"] != missing_market]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("schedule", "injuries"):
                (root / name / "x").mkdir(parents=True)
                (root / name / "x" / f"{name}.json").write_text(json.dumps(data[name]), encoding="utf-8")
            schedule, injuries = load_schedule(root / "schedule"), load_injuries(root / "injuries")
        now = datetime.fromisoformat(snap["_eval"]["now"].replace("Z", "+00:00"))
        selection = OddsSelection(**snap["_eval"]["selection"]) if selected else None
        with patch("app.services.odds.injuries.datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            return build_payload(build_board(snap, now), selection, None, [], schedule, injuries, stats=stats)

    def test_selected_card(self):
        card = self.payload()["analysis"]["s001-lal-den"]
        self.assertEqual((card["book"], card["consensus_home_point"], card["consensus_total"]), ("draftkings", -6.75, 231.0))
        spread = card["spread"]
        self.assertEqual((spread["side"], spread["point"], spread["book"], spread["confidence"]), (HOME, -6.5, "draftkings", "中"))
        self.assertEqual(spread["basis"], ["injury", "price", "rest"])
        self.assertEqual(spread["expected"], [5.0, 11.0])
        self.assertEqual((card["total"]["side"], card["total"]["confidence"]), (UNDER, "低"))
        keys = {(f["market"], f["key"]): f for f in card["factors"]}
        self.assertFalse(keys[(SPREAD, "strength")]["is_reason"])  # 9 对 6.75，差 2.2
        self.assertEqual(keys[(SPREAD, "rest")]["points_to"], HOME)
        self.assertEqual(card["missing"], [])

    def test_whole_board_has_a_card_per_event_and_no_price_rows(self):
        analysis = self.payload(selected=False)["analysis"]
        self.assertEqual(len(analysis), 3)
        for card in analysis.values():
            self.assertIsNone(card["book"])
            self.assertFalse([f for f in card["factors"] if f["key"] == "price"])
            self.assertLessEqual(card["spread"]["expected"][1] - card["spread"]["expected"][0], 6)

    def test_selected_missing_market_does_not_borrow_another_book(self):
        for market, lean, other, label in (("spreads", "spread", "total", "让分"), ("totals", "total", "spread", "总分")):
            with self.subTest(market=market):
                card = self.payload(missing_market=market)["analysis"]["s001-lal-den"]
                self.assertEqual(card["book"], "draftkings")
                self.assertIsNone(card[lean])
                self.assertIn(f"draftkings {label}报价", card["missing"])
                self.assertEqual(card[other]["book"], "draftkings")

    def test_unselected_missing_market_can_use_available_book(self):
        for market, lean in (("spreads", "spread"), ("totals", "total")):
            with self.subTest(market=market):
                card = self.payload(selected=False, missing_market=market)["analysis"]["s001-lal-den"]
                self.assertIsNone(card["book"])
                self.assertEqual(card[lean]["book"], "fanduel")

    def test_cache_hits_when_sources_unchanged(self):
        from app.services.match_analysis import cache
        cache.clear()
        first, second = {}, {}
        cards = self.payload(selected=False, stats=first)["analysis"]
        again = self.payload(selected=False, stats=second)["analysis"]
        self.assertEqual((first["cache_hits"], first["cache_misses"]), (0, 3))
        self.assertEqual((second["cache_hits"], second["cache_misses"]), (3, 0))
        self.assertEqual(cards, again)
        self.assertIn("analysis_ms", second)
        selected = {}
        self.payload(stats=selected)  # 换成选场（带平台）是另一张卡
        self.assertEqual(selected["cache_misses"], 1)


if __name__ == "__main__":
    unittest.main()
