import unittest

from cfb_stats import game_script as gs


def drive(off, dfn, so, sd, period, pts, gid=1):
    return {"gameId": gid, "offense": off, "defense": dfn, "startOffenseScore": so, "startDefenseScore": sd,
            "endOffenseScore": so + pts, "startPeriod": period, "plays": 6}


class GameScriptTests(unittest.TestCase):
    def test_situation(self):
        self.assertEqual(gs.situation(drive("A", "B", 21, 0, 3, 0)), "lead")
        self.assertEqual(gs.situation(drive("A", "B", 0, 21, 4, 0)), "trail")
        self.assertEqual(gs.situation(drive("A", "B", 21, 0, 2, 0)), None)  # big lead, 1st half
        self.assertEqual(gs.situation(drive("A", "B", 10, 7, 1, 0)), "close")
        self.assertEqual(gs.situation({"offense": "A"}), None)

    def test_drive_points(self):
        self.assertEqual(gs.drive_points(drive("A", "B", 7, 0, 1, 7)), 7)
        self.assertEqual(gs.drive_points({"driveResult": "FG"}), 3)
        self.assertEqual(gs.drive_points({"driveResult": "PUNT"}), 0)

    def test_tendencies(self):
        ds = []
        for g in range(10):
            # Both offenses score 3/drive in close games; with a big lead A keeps scoring,
            # C kneels. B's and D's offenses face them while trailing: A's defense stays
            # tight, C's sits in prevent.
            for off, dfn in (("A", "B"), ("B", "A"), ("C", "D"), ("D", "C")):
                ds.append(drive(off, dfn, 7, 7, 1, 3, g))
            ds += [drive("A", "B", 28, 0, 3, 7, g), drive("B", "A", 0, 28, 4, 0, g),
                   drive("C", "D", 28, 0, 3, 0, g), drive("D", "C", 0, 28, 4, 7, g)]
        t = gs.tendencies(ds, prior=0)
        self.assertGreater(t["A"]["gas"], 0)
        self.assertLess(t["C"]["gas"], 0)
        self.assertGreater(t["C"]["prevent"], 0)
        self.assertLess(t["A"]["prevent"], 0)
        self.assertGreater(t["D"]["fight"], 0)  # D keeps scoring when down big
        self.assertLess(t["B"]["fight"], 0)
        shrunk = gs.tendencies(ds, prior=10)
        self.assertLess(abs(shrunk["A"]["gas"]), abs(t["A"]["gas"]))

    def test_hurry_and_downs(self):
        ds = []
        for g in range(10):
            for off in ("A", "B"):
                ds.append(dict(drive(off, "X", 7, 7, 1, 0, g), elapsed={"minutes": 3, "seconds": 0}))  # 30 s/play
            ds.append(dict(drive("A", "X", 0, 28, 4, 0, g), elapsed={"minutes": 1, "seconds": 30},
                           driveResult="DOWNS"))  # 15 s/play, goes for it
            ds.append(dict(drive("B", "X", 0, 28, 4, 0, g), elapsed={"minutes": 3, "seconds": 0},
                           driveResult="PUNT"))
        t = gs.tendencies(ds, prior=0)
        self.assertLess(t["A"]["hurry"], 0)
        self.assertGreater(t["B"]["hurry"], 0)
        self.assertEqual(t["A"]["downs_rate"], 1.0)
        self.assertEqual(t["B"]["downs_rate"], 0.0)

    def test_adjustments(self):
        self.assertAlmostEqual(gs.lead_drives(gs.LEAD_MID), gs.LEAD_MAX / 2)
        self.assertLess(gs.lead_drives(-20), 0.1)
        tend = {"A": {"gas": 0.5, "prevent": 0.5}, "B": {"gas": 0.0, "prevent": 0.0}}
        total, margin = gs.adjustments(tend, "A", "B", 30, 1.0, 1.0)
        self.assertGreater(total, 0)
        self.assertAlmostEqual(margin, 0, places=1)  # gas and prevent cancel on the margin
        # A heavy underdog that fights back adds to the total and its own margin.
        fight = {"A": {}, "B": {"fight": 1.0}}
        total, margin = gs.adjustments(fight, "A", "B", 30, 1.0, 1.0)
        self.assertGreater(total, 3)
        self.assertLess(margin, -3)
        # Underdog's gas and prevent barely matter.
        self.assertAlmostEqual(gs.adjustments(tend, "B", "A", 30, 1.0, 1.0)[0], 0, places=1)


if __name__ == "__main__":
    unittest.main()
