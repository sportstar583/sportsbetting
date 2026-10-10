import unittest

from cfb_stats import boxscore as bx


class BoxscoreTests(unittest.TestCase):
    def test_missing_means_zero_only_with_defensive_stats(self):
        games = [{"id": 1, "teams": [
            {"team": "A", "stats": [{"category": "tackles", "stat": "60"}, {"category": "sacks", "stat": "2"},
                                    {"category": "completionAttempts", "stat": "20-30"}]},
            {"team": "B", "stats": [{"category": "interceptions", "stat": "1"}]}]}]
        p = bx.parse(games)[1]
        self.assertEqual(p["A"]["passesIntercepted"], 0)  # left out because it was zero
        self.assertEqual(p["A"]["sacks"], 2)
        self.assertEqual(p["A"]["pass_att"], 30)
        self.assertIsNone(p["B"]["sacks"])  # no defensive box score at all
        self.assertEqual(p["B"]["interceptions"], 1)

    def test_rate_fit(self):
        rows = []
        for g in range(20):
            for off, opp, rate in (("A", "B", 0.12), ("B", "A", 0.04), ("A", "C", 0.10), ("C", "A", 0.06),
                                   ("B", "C", 0.04), ("C", "B", 0.08)):
                rows.append({"team": off, "opp": opp, "home": 0, "num": rate * 40, "den": 40})
        f = bx.rate_fit(rows, alpha=10)
        self.assertGreater(f["off"]["A"], f["off"]["B"])  # A's line gives up more sacks


if __name__ == "__main__":
    unittest.main()
