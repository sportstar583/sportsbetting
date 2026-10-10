import unittest

from cfb_stats import special_teams as st


def drive(gid, num, off, dfn, start, end, result, so=0, sd=0, pts=0, period=1):
    return {"gameId": gid, "driveNumber": num, "offense": off, "defense": dfn, "startYardsToGoal": start,
            "endYardsToGoal": end, "driveResult": result, "startOffenseScore": so, "startDefenseScore": sd,
            "endOffenseScore": so + pts, "endDefenseScore": sd, "startPeriod": period, "endPeriod": period, "plays": 5}


def season():
    """A punts well (opponents start deep) and makes its kicks; B shanks punts and misses."""
    ds = []
    for g in range(12):
        for team, opp in (("A", "C"), ("B", "D")):
            good = team == "A"
            ds += [
                drive((team, g), 1, team, opp, 75, 45, "PUNT"),
                drive((team, g), 2, opp, team, 90 if good else 60, 50, "PUNT"),
                drive((team, g), 3, team, opp, 70, 25, "FG" if good else "MISSED FG", pts=3 if good else 0),
                drive((team, g), 4, opp, team, 75 if good else 60, 75, "PUNT", sd=3 if good else 0),
                drive((team, g), 5, team, opp, 70, 0, "TD", so=3 if good else 0, pts=7),
                drive((team, g), 6, opp, team, 80 if good else 65, 40, "PUNT", sd=10 if good else 7),
            ]
    return ds


def ep(y):
    return 6 - y / 20  # closer to the goal line = more expected points


class SpecialTeamsTests(unittest.TestCase):
    def test_events(self):
        ev = st.events(season(), ep)
        phases = {e["phase"] for e in ev}
        self.assertEqual(phases, {"fg", "punt", "kick"})
        fg = [e for e in ev if e["phase"] == "fg"]
        self.assertEqual(fg[0]["key"], 25 + st.FG_EXTRA)

    def test_ratings(self):
        r = st.ratings(season(), prior=0, ep=ep)
        self.assertGreater(r["A"]["punt"], 0)
        self.assertLess(r["B"]["punt"], 0)
        self.assertGreater(r["A"]["fg"], 0)
        self.assertLess(r["B"]["fg"], 0)
        self.assertGreater(r["A"]["kick"], r["B"]["kick"])
        self.assertGreater(r["A"]["total"], r["B"]["total"])
        # The receivers' side of punts and kickoffs is the mirror image.
        self.assertLess(r["C"]["punt"], 0)
        shrunk = st.ratings(season(), prior=12, ep=ep)
        self.assertLess(abs(shrunk["A"]["total"]), abs(r["A"]["total"]))


if __name__ == "__main__":
    unittest.main()
