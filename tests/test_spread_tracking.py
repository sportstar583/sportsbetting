import os
import tempfile
import unittest

from cfb_stats import spread_tracking, tracking
from cfb_stats.weekly import card_markdown
from tests.test_weekly import row


def game(gid, away, home, spread, edge, margin=0.0, sp=None):
    return dict(row(away, home, 1), game_id=gid, market_spread=str(spread), spread_edge=str(edge),
                proj_margin=str(margin), sp_margin="" if sp is None else str(sp))


class SpreadTrackingTests(unittest.TestCase):
    def test_model_picks(self):
        rows = [game("1", "A", "B", -3.5, 16.3, 19.8, 7.3), game("2", "C", "D", 10, -14.9, -24.9, -13.0),
                game("3", "E", "F", 2.75, 10.9, 8.1, -10.4), game("4", "G", "H", -1, 0.5)]
        picks = spread_tracking.model_picks(rows)
        self.assertEqual([p["pick"] for p in picks], ["B -3.5", "C -10", "F +2.75"])
        self.assertIn("SP+ agrees", picks[0]["detail"])
        self.assertIn("SP+ disagrees", picks[2]["detail"])

    def test_coach_angle(self):
        rows = [game("1", "A", "B", 7.5, 0), game("2", "C", "D", -3, 0)]
        ranks = {"A": 4, "D": 30}
        cmap = {("B", 2026): "Good Coach", ("C", 2026): "Other"}
        recs = {"Good Coach": (2, 6, 9), "Other": (5, 5, 8)}  # 6-3 ATS (shrunk .58) qualifies
        picks = spread_tracking.coach_angle(rows, ranks, cmap, recs, 2026)
        self.assertEqual([(p["side"], p["pick"]) for p in picks], [("B", "B +7.5")])  # D isn't top 10
        self.assertIn("A is #4", picks[0]["detail"])
        self.assertEqual(spread_tracking.coach_angle(rows, ranks, cmap, {"Good Coach": (1, 2, 3)}, 2026), [])
        both = [game("3", "X", "Y", -2, 0)]
        two = spread_tracking.coach_angle(both, {"X": 2, "Y": 6}, {("X", 2026): "Good Coach", ("Y", 2026): "Good Coach"},
                                          recs, 2026)
        self.assertEqual(two, [])  # both coaches qualify: skipped

    def test_grade_and_summary(self):
        home = {"home": "B", "side": "B", "home_line": "-3.5"}
        away = {"home": "B", "side": "A", "home_line": "-3.5"}
        self.assertEqual(tracking.grade_spread(home, -5.0, 7), (1.5, "W"))   # B -3.5 wins by 7; line moved to -5
        self.assertEqual(tracking.grade_spread(away, -5.0, 7), (-1.5, "L"))
        self.assertEqual(tracking.grade_spread(away, -3.5, 3), (0.0, "W"))   # A +3.5 loses by 3
        self.assertEqual(tracking.grade_spread({"home": "B", "side": "B", "home_line": "-3"}, -3, 3), (0.0, "P"))
        rows = [dict(home, week="6", game_id="1", angle="model", result="W", clv="1.5"),
                dict(home, week="6", game_id="1", angle="model", result="W", clv="1.5"),  # re-logged Friday
                dict(away, week="6", game_id="2", angle="model", result="L", clv="-0.5"),
                dict(away, week="6", game_id="3", angle="coach vs top 10", result="", clv="")]
        self.assertEqual(tracking.spread_summary(rows),
                         ["Spread tracking, model: 1-1-0 (50.0%), average closing line value +0.50 pts."])

    def test_log_and_card(self):
        path = os.path.join(tempfile.mkdtemp(), "spread_log.csv")
        p = spread_tracking._pick(game("1", "A", "B", -3.5, 5), "model", "B", "why")
        tracking.log_spreads(path, [p], 6)
        logged = tracking.read_log(path)
        self.assertEqual((logged[0]["pick"], logged[0]["week"], logged[0]["home_line"]), ("B -3.5", "6", "-3.5"))
        md = card_markdown([row("A", "B", 6)], 6, 2026, 3, "note", spread_picks=[p], spread_record=["rec"])
        self.assertIn("## Spread tracking (paper only, not bets)", md)
        self.assertIn("| model | B -3.5 | A @ B | why |", md)
        self.assertNotIn("Spread tracking", card_markdown([row("A", "B", 6)], 6, 2026, 3, "note"))


if __name__ == "__main__":
    unittest.main()
