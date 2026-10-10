import os
import tempfile
import unittest

from cfb_stats import spots


def g(gid, week, home, away, hp=None, ap=None):
    return {"id": gid, "week": week, "homeTeam": home, "awayTeam": away, "homePoints": hp, "awayPoints": ap,
            "startDate": f"2026-09-{week:02d}", "completed": hp is not None}


class SpotsTests(unittest.TestCase):
    def setUp(self):
        self.games = [
            g(1, 1, "A", "R1", 10, 30),   # A loses to ranked R1
            g(2, 1, "B", "R2", 31, 28),   # B beats ranked R2
            g(3, 2, "A", "X"),            # A: bounce-back spot (next opp unranked)
            g(4, 2, "Y", "B"),            # B: sandwich (ranked R3 next)
            g(5, 3, "A", "Z"),
            g(6, 3, "B", "R3"),
        ]
        self.polls = {1: {"R1", "R2", "R3"}, 2: {"R1", "R3"}}

    def test_find_spots(self):
        rows = [{"game_id": "3", "home": "A", "away": "X", "market_spread": "-3"},
                {"game_id": "4", "home": "Y", "away": "B", "market_spread": "4.5"}]
        found = {s["game_id"]: s for s in spots.find_spots(self.games, self.polls, 2, rows)}
        self.assertEqual(found[3]["spot"], "bounce-back")
        self.assertEqual((found[3]["bet_team"], found[3]["line"]), ("A", -3.0))
        self.assertEqual(found[4]["spot"], "sandwich")
        self.assertEqual((found[4]["bet_team"], found[4]["line"]), ("Y", 4.5))  # fade B: take Y +4.5

    def test_log_and_grade(self):
        path = os.path.join(tempfile.mkdtemp(), "spots_log.csv")
        found = [{"game_id": 3, "spot": "bounce-back", "team_in_spot": "A", "bet_team": "A", "opponent": "X",
                  "line": -3.0}]
        spots.log_spots(path, found, 2)
        spots.log_spots(path, found, 2)  # Friday re-run doesn't duplicate
        self.assertEqual(len(spots.read_log(path)), 1)

        class Client:
            def get(self, path, **kw):
                return [{"id": 3, "lines": [{"spread": -4.0}, {"spread": -5.0}]}]
        games = [dict(x) for x in self.games]
        games[2].update(homePoints=24, awayPoints=20, completed=True)  # A wins by 4, covers -3
        rows = spots.update_log(path, Client(), 2026, games)
        self.assertEqual(rows[0]["result"], "W")
        self.assertEqual(float(rows[0]["clv"]), 1.5)  # took -3, closed -4.5
        md = "\n".join(spots.card_section(found, [{"game_id": "3", "home": "A", "away": "X"}], rows))
        self.assertIn("| bounce-back | A -3 | X @ A |", md)
        self.assertIn("bounce-back 1-0", md)

    def test_recency(self):
        games = [g(10, 1, "A", "B", 45, 10), g(11, 1, "C", "D", 30, 27), g(12, 2, "A", "C")]
        lines = {10: (-7.0, 40.0), 11: (-3.0, 55.0)}  # A covered by 28 and went over by 15; C over by 2
        rows = [{"game_id": "12", "home": "A", "away": "C", "market_spread": "-10", "market_total": "52"}]
        found = spots.find_recency(games, rows, spots.previous_games(games, rows), lines)
        self.assertEqual([(s["spot"], s["bet_team"], s["line"]) for s in found], [("ats-revert fade", "C", 10.0)])
        games[1].update(homePoints=45, awayPoints=25)  # C covers by 17, total over by 15: both overs
        found = spots.find_recency(games, rows, spots.previous_games(games, rows), lines)
        self.assertIn(("over run", "OVER", 52.0), [(s["spot"], s["bet_team"], s["line"]) for s in found])

    def test_grade_total(self):
        path = os.path.join(tempfile.mkdtemp(), "spots_log.csv")
        spots.log_spots(path, [{"game_id": 3, "spot": "under run", "team_in_spot": "X @ A", "bet_team": "UNDER",
                                "opponent": "", "line": 50.0}], 2)

        class Client:
            def get(self, path, **kw):
                return [{"id": 3, "lines": [{"overUnder": 48.5}]}]
        games = [dict(x) for x in self.games]
        games[2].update(homePoints=24, awayPoints=20, completed=True)  # 44 < 50
        r = spots.update_log(path, Client(), 2026, games)[0]
        self.assertEqual((r["result"], float(r["clv"])), ("W", 1.5))


if __name__ == "__main__":
    unittest.main()
