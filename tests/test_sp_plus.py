import os
import tempfile
import unittest

from cfb_stats import sp_plus, tracking
from cfb_stats.weekly import card_markdown
from tests.test_weekly import row


class SpPlusTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sp.csv")
        with open(self.path, "w") as f:
            f.write("team,conference,sp,off,def,st\nGeorgia,SEC,32.0,43.5,11.8,0.3\n"
                    "Miami-FL,ACC,28.2,42.8,14.9,0.2\nbad,,x,1,2,\n")

    def test_load_aliases_and_defaults(self):
        sp = sp_plus.load(self.path)
        self.assertEqual(set(sp["teams"]), {"Georgia", "Miami"})  # Miami-FL -> CFBD name, bad row skipped
        self.assertEqual((sp["off_avg"], sp["def_avg"]), (sp_plus.DEFAULT_OFF, sp_plus.DEFAULT_DEF))  # partial table
        self.assertIsNone(sp_plus.load(os.path.join(self.dir, "missing.csv")))

    def test_project(self):
        sp = {"teams": {"H": {"sp": 10.0, "off": 35.0, "def": 25.0}, "A": {"sp": 0.0, "off": 27.0, "def": 27.0}},
              "off_avg": 27.0, "def_avg": 27.0}
        # home 35 + 27 - 27 = 35; away 27 + 25 - 27 = 25
        self.assertEqual(sp_plus.project(sp, "H", "A"), (60.0, 12.5))
        self.assertEqual(sp_plus.project(sp, "H", "A", neutral=True), (60.0, 10.0))
        self.assertEqual(sp_plus.project(sp, "H", "X"), (None, None))
        self.assertEqual(sp_plus.project(None, "H", "A"), (None, None))

    def test_card_column(self):
        md = card_markdown([dict(row("A", "B", 6), sp_total="58.2"), row("C", "D", -4)], 6, 2026, 3, "note")
        self.assertIn("| Model total | SP+ total | Edge |", md)
        self.assertIn("| A @ B | 56.0 | 58.2 | +6.0 |", md)
        self.assertIn("| C @ D | 46.0 | - | -4.0 |", md)
        self.assertNotIn("SP+", card_markdown([row("A", "B", 6)], 6, 2026, 3, "note"))

    def test_summary_tracks_sp_agreement(self):
        base = {"week": "6", "median_line": "50", "best_line": "50", "clv": "0"}
        rows = [dict(base, game_id="1", side="OVER", result="W", sp_total="55"),   # agreed
                dict(base, game_id="2", side="UNDER", result="L", sp_total="47"),  # agreed
                dict(base, game_id="3", side="OVER", result="W", sp_total="44"),   # disagreed
                dict(base, game_id="4", side="OVER", result="L", sp_total="")]     # no SP+
        text = tracking.summary(rows)
        self.assertEqual(text[1], "SP+ check (reference only): SP+ agreed with the pick 1-1-0; "
                                  "SP+ disagreed with the pick 1-0-0.")
        self.assertEqual(len(tracking.summary(rows[3:])), 1)


if __name__ == "__main__":
    unittest.main()
