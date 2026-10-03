import random
import unittest

from cfb_stats.totals import TotalsModel, board, consensus_total

TEAMS = ["A", "B", "C", "D", "E", "F"]
# True offensive quality (EPA/play); defenses all average.
QUALITY = {"A": 0.35, "B": 0.25, "C": 0.15, "D": 0.05, "E": -0.05, "F": -0.15}


def synthetic_season(weeks=6, seed=1):
    rng = random.Random(seed)
    games, rows, gid = [], [], 0
    for week in range(1, weeks + 1):
        order = TEAMS[:]
        rng.shuffle(order)
        for home, away in zip(order[::2], order[1::2]):
            gid += 1
            sides = {}
            for team in (home, away):
                epa = QUALITY[team] + rng.gauss(0, 0.03)
                plays = 60
                sides[team] = ({"ppa": epa, "plays": plays}, round(plays * (0.2 + 0.6 * epa) + rng.gauss(0, 2)))
            games.append({"id": gid, "week": week, "homeTeam": home, "awayTeam": away, "neutralSite": False,
                          "homePoints": sides[home][1], "awayPoints": sides[away][1], "completed": True})
            for team, opp in ((home, away), (away, home)):
                rows.append({"gameId": gid, "team": team, "opponent": opp,
                             "offense": sides[team][0], "defense": sides[opp][0]})
    return games, rows


class TotalsTests(unittest.TestCase):
    def test_consensus_total(self):
        lr = {"lines": [{"provider": "X", "overUnder": 50, "overUnderOpen": 48},
                        {"provider": "Y", "overUnder": 52, "overUnderOpen": None},
                        {"provider": "Z", "overUnder": None}]}
        self.assertEqual(consensus_total(lr), (51, 48, "X, Y"))
        self.assertEqual(consensus_total({"lines": []})[0], None)

    def test_better_offenses_project_higher(self):
        games, rows = synthetic_season()
        model = TotalsModel(rows, games, alpha=10)
        high = sum(model.project({"homeTeam": "A", "awayTeam": "B", "neutralSite": True}))
        low = sum(model.project({"homeTeam": "E", "awayTeam": "F", "neutralSite": True}))
        self.assertGreater(high, low + 10)

    def test_board(self):
        games, rows = synthetic_season()
        model = TotalsModel(rows, games, alpha=10)
        upcoming = [{"id": 999, "week": 7, "homeTeam": "A", "awayTeam": "B", "neutralSite": False,
                     "homePoints": None, "awayPoints": None},
                    {"id": 998, "week": 7, "homeTeam": "E", "awayTeam": "F", "neutralSite": False}]
        lines = [{"id": 999, "lines": [{"provider": "X", "overUnder": 20.5}]},
                 {"id": 998, "lines": [{"provider": "X", "overUnder": 99.5}]},
                 {"id": 997, "lines": [{"provider": "X", "overUnder": 50}]}]  # no matching game
        rows_out = {r["game_id"]: r for r in board(model, upcoming, lines)}
        self.assertEqual(set(rows_out), {998, 999})
        self.assertEqual(rows_out[999]["pick"], "OVER")
        self.assertEqual(rows_out[998]["pick"], "UNDER")
        self.assertTrue(rows_out[999]["enough_data"])
        self.assertIsNone(rows_out[999]["actual_total"])


if __name__ == "__main__":
    unittest.main()
