import random
import unittest

from cfb_stats import adjust, injuries as inj
from cfb_stats.totals import TotalsModel, board, consensus_total, tempo

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

    def test_tempo(self):
        drives = [
            # game 1: A runs 10 plays in 300s, B runs 10 plays in 200s
            {"gameId": 1, "offense": "A", "defense": "B", "plays": 10, "elapsed": {"minutes": 5, "seconds": 0}},
            {"gameId": 1, "offense": "B", "defense": "A", "plays": 10, "elapsed": {"minutes": 3, "seconds": 20}},
            {"gameId": 1, "offense": "B", "defense": "A", "plays": 0, "elapsed": {"minutes": 0, "seconds": 5}},  # ignored
        ]
        t, lg, clock = tempo(drives)
        self.assertEqual(t["A"]["s_off"], 30)
        self.assertEqual(t["B"]["s_off"], 20)
        self.assertEqual(t["A"]["s_def"], 20)
        self.assertAlmostEqual(t["A"]["pos_share"], 0.6)
        self.assertEqual((lg, clock), (25, 500))

    def test_fcs_weight_and_huber_damp_a_rout(self):
        games, rows = synthetic_season()
        # A also routs an FCS team, posting an absurd EPA.
        games.append({"id": 500, "week": 6, "homeTeam": "A", "awayTeam": "FCS U", "neutralSite": False,
                      "homeClassification": "fbs", "awayClassification": "fcs",
                      "homePoints": 80, "awayPoints": 0, "completed": True})
        rows += [{"gameId": 500, "team": "A", "opponent": "FCS U",
                  "offense": {"ppa": 1.5, "plays": 60}, "defense": {"ppa": -0.5, "plays": 60}}]
        for g in games:
            g.setdefault("homeClassification", "fbs")
            g.setdefault("awayClassification", "fbs")
        full = TotalsModel(rows, games, alpha=10).epa["off"]["A"]
        damped = TotalsModel(rows, games, alpha=10, fcs_weight=0.5, huber_k=1.5).epa["off"]["A"]
        self.assertLess(damped, full)

    def test_huber_downweights_outlier(self):
        obs = [{"offense": o, "defense": d, "home": h, "stats": {"ppa": v, "plays": 60}}
               for o, d, h, v in [("A", "B", 1, 0.1), ("A", "C", -1, 0.1), ("A", "D", 1, 0.1), ("A", "E", -1, 1.5),
                                  ("B", "A", -1, 0.1), ("C", "A", 1, 0.1), ("D", "A", -1, 0.1), ("E", "A", 1, 0.1)]]
        plain = adjust.fit(obs, ("ppa",), None, alpha=1)["off"]["A"]
        robust = adjust.fit(obs, ("ppa",), None, alpha=1, huber_k=1.5)["off"]["A"]
        self.assertLess(robust, plain)

    def test_injury_offsets_lower_projection(self):
        games, rows = synthetic_season()
        healthy = TotalsModel(rows, games, alpha=10)
        hurt = TotalsModel(rows, games, alpha=10, off_offsets={"A": -0.1})
        g = {"homeTeam": "A", "awayTeam": "B", "neutralSite": True}
        self.assertLess(sum(hurt.project(g)), sum(healthy.project(g)))
        self.assertEqual(sum(hurt.project(g, injuries=False)), sum(healthy.project(g)))


class InjuryTests(unittest.TestCase):
    def setUp(self):
        season = [
            {"id": "1", "name": "Star QB", "position": "QB", "team": "A",
             "averagePPA": {"all": 0.4}, "totalPPA": {"all": 120.0}},
            {"id": "2", "name": "Backup QB", "position": "QB", "team": "B",
             "averagePPA": {"all": -0.1}, "totalPPA": {"all": -10.0}},
            {"id": "3", "name": "Other QB", "position": "QB", "team": "C",
             "averagePPA": {"all": 0.1}, "totalPPA": {"all": 20.0}},
            {"id": "4", "name": "WR One", "position": "WR", "team": "A",
             "averagePPA": {"all": 0.6}, "totalPPA": {"all": 30.0}},
            {"id": "5", "name": "WR Two", "position": "WR", "team": "C",
             "averagePPA": {"all": 0.0}, "totalPPA": {"all": 0.0001}},
            {"id": "6", "name": "WR Three", "position": "WR", "team": "B",
             "averagePPA": {"all": 0.05}, "totalPPA": {"all": 2.0}},
        ]
        usage = [{"id": "1", "usage": {"overall": 0.5}}, {"id": "4", "usage": {"overall": 0.15}}]
        self.players, self.positions = inj.player_values(season, usage)

    def test_status_probabilities(self):
        self.assertEqual(inj._prob("Out"), 1.0)
        self.assertEqual(inj._prob("0.3"), 0.3)
        with self.assertRaises(ValueError):
            inj._prob("maybe")

    def test_offsets(self):
        injuries = [
            {"team": "A", "player": "star qb", "prob_out": 1.0, "side": "off", "epa_delta": None},
            {"team": "A", "player": "WR One", "prob_out": 0.4, "side": "off", "epa_delta": None},
            {"team": "B", "player": "Edge Rusher", "prob_out": 1.0, "side": "def", "epa_delta": 0.02},
            {"team": "B", "player": "Corner", "prob_out": 1.0, "side": "def", "epa_delta": None},
            {"team": "A", "player": "Nobody", "prob_out": 1.0, "side": "off", "epa_delta": None},
        ]
        off, dfn, detail = inj.team_offsets(injuries, self.players, self.positions)
        self.assertLess(off["A"], 0)  # losing a good QB and WR hurts the offense
        qb_only = inj.team_offsets(injuries[:1], self.players, self.positions)[0]["A"]
        self.assertLess(off["A"], qb_only)
        self.assertEqual(dfn, {"B": 0.02})
        notes = {d["player"]: d["note"] for d in detail}
        self.assertIn("skipped", notes["Corner"])
        self.assertIn("no offensive EPA", notes["Nobody"])


if __name__ == "__main__":
    unittest.main()
