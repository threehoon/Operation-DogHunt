"""整季比分实力分（BALLDONTLIE）、球队效率（DunkAPI）与额度账本；不访问真实接口，Key 用假值。"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app.integrations import balldontlie, dunkapi
from app.schemas.chat import ChatMessage
from app.services.chat.odds_context import odds_context
from app.services.odds import season, team_stats
from app.services.odds.board import build_board
from tests.test_odds import bdl_game, snapshot

REAL_CLIENT = httpx.Client


def mock_client(handler):
    return lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)


def round_robin(abbrs, strength, home_edge=2, rounds=2):
    """每对球队主客各打 rounds 次；主队净胜 = 实力差 + home_edge，比分为 100 + 净胜 / 100。"""
    games, gid = [], 0
    day = datetime(2026, 10, 21)
    for _ in range(rounds):
        for home in abbrs:
            for away in abbrs:
                if home == away:
                    continue
                gid += 1
                margin = strength[home] - strength[away] + home_edge
                games.append(bdl_game(gid, (day + timedelta(days=gid // 4)).strftime("%Y-%m-%d"), home, away,
                                      home_team_score=100 + margin, visitor_team_score=100, status_state="final"))
    return games


def dunk_team(name, **extra):
    row = {"team_id": "1", "abbrev": "X", "display_name": name, "games": 10, "wins": 6, "losses": 4, "pts": 115.0, "opp_pts": 110.0,
           "pace": 100.0, "off_rating": 115.0, "def_rating": 110.0, "efg_pct": 0.55, "tov_pct": 0.12, "oreb_pct": 0.28,
           "ft_rate": 0.25, "three_rate": 0.4, "opp_efg_pct": 0.52, "ast": 27.0, "reb": 44.0, "tov": 13.0, "net_rating": 5.0}
    return {**row, **extra}


class SeasonStrengthTests(unittest.TestCase):
    def setUp(self):
        balldontlie._recent.clear()
        self.events = build_board(snapshot(), datetime(2026, 11, 12, tzinfo=timezone.utc)).events  # 猛龙主场对凯尔特人，东部日期 2026-11-11

    def test_season_year_and_regular_only(self):
        self.assertEqual((season.season_start_year(datetime(2026, 11, 1).date()), season.season_start_year(datetime(2027, 5, 1).date())), (2026, 2026))
        self.assertEqual(season.season_label(2026), "2026-27")
        games = season.regular_games([bdl_game(1, "2026-11-01", "TOR", "BOS"), bdl_game(2, "2027-05-01", "TOR", "BOS", postseason=True)])
        self.assertEqual([g["id"] for g in games], [1])

    def test_ratings_recover_strength_and_home_edge(self):
        strength = {"TOR": 6, "BOS": 2, "MIA": -2, "NYK": -6}
        normalized = season.regular_games(round_robin(list(strength), strength, rounds=3))
        solved = season.ratings(normalized, "2027-01-01")
        self.assertAlmostEqual(solved["home_edge"], 2.0, places=1)
        self.assertEqual([solved["teams"][t]["rank"] for t in ("Toronto Raptors", "Boston Celtics", "Miami Heat", "New York Knicks")], [1, 2, 3, 4])
        self.assertAlmostEqual(solved["rating"]["Toronto Raptors"] - solved["rating"]["Boston Celtics"], 4.0, places=1)
        self.assertIsNone(season.ratings(normalized[:29], "2027-01-01"))  # 样本太少不给
        self.assertIsNone(season.ratings(normalized, normalized[0]["date"]))  # 只用本场之前的比赛

    def test_context_gives_model_margin_only_with_enough_games(self):
        strength = {"TOR": 6, "BOS": 2, "MIA": -2, "NYK": -6}
        record = {"provider": "BALLDONTLIE", "fetched_at": "2026-11-11T00:00:00Z", "season": 2026,
                  "games": season.regular_games(round_robin(list(strength), strength, rounds=3))}
        for game in record["games"]:
            game["date"] = "2026-10-30"  # 都在本场之前
        game = season.strength_context(record, self.events)["events"]["game"]
        self.assertEqual((game["home"]["games"], game["away"]["team"]), (18, "Boston Celtics"))
        self.assertAlmostEqual(game["model_home_margin"], 6.0, places=0)  # 实力差 4 + 主场 2
        few = dict(record, games=[g for g in record["games"] if "Boston Celtics" not in (g["home"], g["away"])][:30] +
                   [g for g in record["games"] if "Boston Celtics" in (g["home"], g["away"])][:3])
        self.assertNotIn("model_home_margin", season.strength_context(few, self.events)["events"]["game"])
        self.assertEqual(season.strength_context(None, self.events)["status"], "unavailable")

    def test_refresh_full_then_incremental_and_failure_keeps_old(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        first = [bdl_game(1, "2026-11-01", "TOR", "BOS", home_team_score=100, visitor_team_score=90, status_state="final"),
                 bdl_game(2, "2026-11-10", "TOR", "MIA", home_team_score=100, visitor_team_score=99, status_state="final")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(balldontlie, "load_key", return_value=None), patch.object(balldontlie, "fetch_season_games") as fetch:
                self.assertIn("未配置", season.refresh_season(root, now))
                fetch.assert_not_called()
            with patch.object(balldontlie, "load_key", return_value="k"), patch.object(balldontlie, "fetch_season_games", return_value=first) as fetch:
                self.assertIn("全季", season.refresh_season(root, now))
                fetch.assert_called_once_with("k", 2026, None, "2026-11-14")
                self.assertIn("跳过", season.refresh_season(root, now + timedelta(hours=1)))
                self.assertEqual(fetch.call_count, 1)
            corrected = [bdl_game(2, "2026-11-10", "TOR", "MIA", home_team_score=101, visitor_team_score=99, status_state="final"),
                         bdl_game(3, "2026-11-13", "BOS", "MIA", home_team_score=95, visitor_team_score=90, status_state="final")]
            with patch.object(balldontlie, "load_key", return_value="k"), patch.object(balldontlie, "fetch_season_games", return_value=corrected) as fetch:
                self.assertIn("增量", season.refresh_season(root, now + timedelta(hours=7)))
                fetch.assert_called_once_with("k", 2026, "2026-11-07", "2026-11-14")
            saved = season.load_season(root)
            self.assertEqual([(g["id"], g["home_score"]) for g in saved["games"]], [(1, 100), (2, 101), (3, 95)])
            with patch.object(balldontlie, "load_key", return_value="k"), \
                 patch.object(balldontlie, "fetch_season_games", side_effect=balldontlie.ScheduleError("HTTP 429")):
                self.assertIn("未更新", season.refresh_season(root, now + timedelta(hours=14)))
            self.assertEqual(len(list(root.glob("*/season.json"))), 2)

    def test_rate_limiter_waits_instead_of_exceeding(self):
        waits = []
        clock = [1000.0]
        with patch.object(balldontlie, "_sleep", lambda s: waits.append(s) or clock.__setitem__(0, clock[0] + s)), \
             patch.object(balldontlie.time, "monotonic", lambda: clock[0]):
            for _ in range(6):
                balldontlie._wait_turn()
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 60)

    def test_season_fetch_params(self):
        seen = []

        def handler(request):
            seen.append((request.url.params.get_list("seasons[]"), request.url.params.get("start_date"), request.url.params.get("end_date")))
            return httpx.Response(200, json={"data": [{"id": 1}], "meta": {}})

        with patch.object(balldontlie.httpx, "Client", mock_client(handler)):
            balldontlie.fetch_season_games("k", 2026, "2026-11-01", "2026-11-14")
        self.assertEqual(seen, [(["2026"], "2026-11-01", "2026-11-14")])


class TeamStatsTests(unittest.TestCase):
    def setUp(self):
        self.events = build_board(snapshot(), datetime(2026, 11, 12, tzinfo=timezone.utc)).events

    def test_normalize_maps_clippers_converts_ratios_and_skips_unknown(self):
        teams = team_stats.normalize_teams([dunk_team("LA Clippers"), dunk_team("Seattle SuperSonics"), dunk_team("Toronto Raptors", pace=None)])
        self.assertEqual(sorted(teams), ["Los Angeles Clippers", "Toronto Raptors"])
        self.assertEqual((teams["Los Angeles Clippers"]["efg_pct"], teams["Los Angeles Clippers"]["games"]), (55.0, 10))
        self.assertIsNone(teams["Toronto Raptors"]["pace"])
        with self.assertRaises(ValueError):
            team_stats.normalize_teams([dunk_team("Seattle SuperSonics")])

    def test_projection_and_context(self):
        home = team_stats.normalize_teams([dunk_team("Toronto Raptors", pace=98.0, off_rating=118.0, def_rating=108.0)])["Toronto Raptors"]
        away = team_stats.normalize_teams([dunk_team("Boston Celtics", pace=102.0, off_rating=112.0, def_rating=114.0)])["Boston Celtics"]
        self.assertEqual(team_stats.projected_total(home, away), {"possessions": 100.0, "home_points": 116.0, "away_points": 110.0, "total": 226.0})
        record = {"provider": "DunkAPI", "fetched_at": "2026-11-11T00:00:00Z", "season": 2027,
                  "teams": {"Toronto Raptors": home, "Boston Celtics": away}}
        context = team_stats.efficiency_context(record, self.events, datetime(2026, 11, 11, 12, tzinfo=timezone.utc))
        self.assertEqual((context["season"], context["stale"], context["events"]["game"]["projection"]["total"]), ("2026-27", False, 226.0))
        self.assertTrue(team_stats.efficiency_context(record, self.events, datetime(2026, 11, 13, tzinfo=timezone.utc))["stale"])
        thin = dict(record, teams={"Toronto Raptors": home, "Boston Celtics": dict(away, games=3)})
        self.assertNotIn("projection", team_stats.efficiency_context(thin, self.events)["events"]["game"])
        self.assertEqual(team_stats.efficiency_context(None, self.events)["status"], "unavailable")

    def test_refresh_skips_when_fresh_and_keeps_old_on_failure(self):
        now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dunkapi, "load_key", return_value=None), patch.object(dunkapi, "call") as call:
                self.assertIn("未配置", team_stats.refresh_team_stats(root, now))
                call.assert_not_called()
            with patch.object(dunkapi, "load_key", return_value="k"), \
                 patch.object(dunkapi, "call", return_value={"teams": [dunk_team("Toronto Raptors")], "method": "m"}) as call:
                self.assertIn("已保存 1 队", team_stats.refresh_team_stats(root, now))
                call.assert_called_once_with("get_team_stats", {"season": 2027, "season_type": "regular"}, "k")
                self.assertIn("跳过", team_stats.refresh_team_stats(root, now + timedelta(hours=2)))
                self.assertEqual(call.call_count, 1)
            with patch.object(dunkapi, "load_key", return_value="k"), patch.object(dunkapi, "call", side_effect=dunkapi.DunkError("HTTP 402")):
                self.assertIn("未更新", team_stats.refresh_team_stats(root, now + timedelta(hours=21)))
            self.assertEqual(len(list(root.glob("*/team_stats.json"))), 1)


class DunkApiTests(unittest.TestCase):
    def test_ledger_blocks_over_budget_and_unknown_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "usage.json"
            seen = []

            def handler(request):
                seen.append((request.url.path, request.headers["x-api-key"], json.loads(request.content)))
                return httpx.Response(200, json={"data": {"teams": [], "count": 0}})

            with patch.object(dunkapi.httpx, "Client", mock_client(handler)):
                self.assertEqual(dunkapi.call("get_team_stats", {"season": 2027}, "k", ledger), {"teams": [], "count": 0})
                self.assertEqual(seen, [("/v1/get_team_stats", "k", {"season": 2027})])
                self.assertEqual(dunkapi.read_ledger(ledger)["credits"], 5)
                with self.assertRaises(dunkapi.DunkError):
                    dunkapi.call("get_plays", {}, "k", ledger)
                full = dunkapi.read_ledger(ledger)
                full["credits"] = dunkapi.MONTHLY_BUDGET - 4
                ledger.write_text(json.dumps(full))
                with self.assertRaises(dunkapi.DunkError):
                    dunkapi.call("get_team_stats", {}, "k", ledger)
                self.assertEqual(len(seen), 1)  # 超预算没有发请求
            ledger.write_text("{broken")
            with self.assertRaises(dunkapi.DunkError):
                dunkapi.call("get_injuries", {}, "k", ledger)
            last_month = {"month": "2026-10", "credits": 999, "calls": []}
            ledger.write_text(json.dumps(last_month))
            self.assertEqual(dunkapi.read_ledger(ledger, datetime(2026, 11, 1, tzinfo=timezone.utc))["credits"], 0)

    def test_http_errors_do_not_retry(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "usage.json"
            for response in (httpx.Response(401, json={"error": "x"}), httpx.Response(402), httpx.Response(429), httpx.Response(200, json={"items": []})):
                with patch.object(dunkapi.httpx, "Client", mock_client(lambda r, resp=response: calls.append(1) or resp)):
                    with self.assertRaises(dunkapi.DunkError):
                        dunkapi.call("get_injuries", {}, "k", ledger)
        self.assertEqual(len(calls), 4)


class ChatContextTests(unittest.TestCase):
    def test_new_sections_follow_focused_events(self):
        board = build_board(snapshot())
        record = {"provider": "BALLDONTLIE", "fetched_at": "2026-11-11T00:00:00Z", "season": 2026, "games": []}
        stats = {"provider": "DunkAPI", "fetched_at": "2026-11-11T00:00:00Z", "season": 2027, "teams": {}}
        ask = [ChatMessage(role="user", content="猛龙这场")]
        context = json.loads(odds_context(board, None, ask, None, None, None, record, stats))
        self.assertEqual(context["season_strength"]["events"]["game"]["status"], "too_few_games")
        self.assertEqual(context["team_efficiency"]["events"]["game"]["home"], {"team": "Toronto Raptors", "games": 0})
        empty = json.loads(odds_context(board, None, ask))
        self.assertEqual((empty["season_strength"]["status"], empty["team_efficiency"]["status"]), ("unavailable", "unavailable"))


if __name__ == "__main__":
    unittest.main()
