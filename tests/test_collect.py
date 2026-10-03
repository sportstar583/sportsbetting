import datetime
import unittest

from cfb_stats.collect import build_tables, default_year, p4_teams, stat_side, upcoming_week

FBS = [
    {"school": "Georgia", "conference": "SEC"},
    {"school": "Ohio State", "conference": "Big Ten"},
    {"school": "Notre Dame", "conference": "FBS Independents"},
    {"school": "Boise State", "conference": "Mountain West"},
]

SEASON = [
    {"team": "Georgia", "statName": n, "statValue": v}
    for n, v in {
        "games": 4, "totalYards": 1800, "rushingYards": 800, "rushingAttempts": 140,
        "netPassingYards": 1000, "passAttempts": 120, "passCompletions": 84,
        "thirdDowns": 50, "thirdDownConversions": 25, "turnovers": 3,
        "sacks": 10, "passesIntercepted": 4, "fumblesRecovered": 2,
        "totalYardsOpponent": 1200, "rushingAttemptsOpponent": 110, "passAttemptsOpponent": 130,
        "sacksOpponent": 5, "kickReturns": 6,
    }.items()
] + [{"team": "Boise State", "statName": "games", "statValue": 4}]

ADVANCED = [
    {
        "team": "Georgia",
        "offense": {"ppa": 0.3, "successRate": 0.5, "havoc": {"total": 0.1}},
        "defense": {"ppa": -0.1, "successRate": 0.35},
    }
]

GAMES = [
    {"homeTeam": "Georgia", "awayTeam": "Clemson", "homePoints": 34, "awayPoints": 3, "completed": True},
    {"homeTeam": "Alabama", "awayTeam": "Georgia", "homePoints": 24, "awayPoints": 21, "completed": True},
    {"homeTeam": "Georgia", "awayTeam": "Ole Miss", "homePoints": None, "awayPoints": None, "completed": False},
]


class CollectTests(unittest.TestCase):
    def test_p4_filter(self):
        self.assertEqual(set(p4_teams(FBS)), {"Georgia", "Ohio State"})
        self.assertIn("Notre Dame", p4_teams(FBS, include_notre_dame=True))

    def test_stat_side(self):
        self.assertEqual(stat_side("totalYards"), "offense")
        self.assertEqual(stat_side("totalYardsOpponent"), "defense")
        self.assertEqual(stat_side("sacks"), "defense")
        self.assertEqual(stat_side("sacksOpponent"), "offense")
        self.assertEqual(stat_side("kickReturns"), "special_teams")

    def test_default_year(self):
        self.assertEqual(default_year(datetime.date(2026, 10, 3)), 2026)
        self.assertEqual(default_year(datetime.date(2026, 3, 1)), 2025)

    def test_upcoming_week_skips_stale_unplayed_games(self):
        games = [
            {"week": 2, "completed": False, "startDate": "2026-09-12T16:00:00.000Z"},  # postponed
            {"week": 5, "completed": True, "startDate": "2026-10-03T16:00:00.000Z"},
            {"week": 5, "completed": False, "startDate": "2026-10-04T02:00:00.000Z"},  # late game
            {"week": 6, "completed": False, "startDate": "2026-10-10T16:00:00.000Z"},
        ]
        utc = datetime.timezone.utc
        # Saturday afternoon: the late week 5 game hasn't kicked off yet.
        self.assertEqual(upcoming_week(games, datetime.datetime(2026, 10, 3, 20, 0, tzinfo=utc)), 5)
        # Sunday morning: it has kicked off (maybe not marked final yet), so it's week 6.
        self.assertEqual(upcoming_week(games, datetime.datetime(2026, 10, 4, 13, 47, tzinfo=utc)), 6)

    def test_build_tables(self):
        tables = build_tables(p4_teams(FBS), SEASON, ADVANCED, GAMES)
        off = {r["team"]: r for r in tables["offense"]}
        dfn = {r["team"]: r for r in tables["defense"]}

        uga = off["Georgia"]
        self.assertEqual((uga["wins"], uga["losses"], uga["points_for"]), (1, 1, 55))
        self.assertEqual(uga["points_per_game"], 27.5)  # 55 pts / 2 completed games
        self.assertEqual(uga["yards_per_play"], 6.92)
        self.assertEqual(uga["third_down_pct"], 0.5)
        self.assertEqual(uga["adv_off_havoc_total"], 0.1)
        self.assertIn("sacksOpponent", uga)
        self.assertNotIn("sacks", uga)

        d = dfn["Georgia"]
        self.assertEqual(d["points_against"], 27)
        self.assertEqual(d["yards_per_play_allowed"], 5.0)
        self.assertEqual((d["takeaways"], d["turnover_margin"]), (6, 3))
        self.assertEqual(d["adv_def_ppa"], -0.1)
        self.assertIn("sacks", d)

        self.assertEqual(tables["special_teams"][0]["kickReturns"], 6)
        # Team with no data yet still gets a row; non-P4 teams are excluded.
        self.assertIn("Ohio State", off)
        self.assertIsNone(off["Ohio State"]["points_per_game"])
        self.assertNotIn("Boise State", off)

    def test_takeaways_prefer_turnovers_opponent(self):
        season = SEASON + [{"team": "Georgia", "statName": "turnoversOpponent", "statValue": 8}]
        d = {r["team"]: r for r in build_tables(p4_teams(FBS), season, ADVANCED, GAMES)["defense"]}["Georgia"]
        self.assertEqual((d["takeaways"], d["turnover_margin"]), (8, 5))


if __name__ == "__main__":
    unittest.main()
