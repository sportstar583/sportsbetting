import unittest

from cfb_stats import resume


def game(gid, home, away, hp, ap, neutral=True, done=True):
    return {"id": gid, "homeTeam": home, "awayTeam": away, "neutralSite": neutral,
            "homePoints": hp if done else None, "awayPoints": ap if done else None, "completed": done}


def season():
    # A > B > C > D > E: each team beats everyone below it.
    gs, i = [], 0
    order = ["A", "B", "C", "D", "E"]
    for x in range(len(order)):
        for y in range(x + 1, len(order)):
            i += 1
            gs.append(game(i, order[x], order[y], 30 + 3 * (y - x), 20))
    gs.append(game(99, "A", "E", 0, 0, done=False))
    return gs


class ResumeTests(unittest.TestCase):
    def test_ratings_order(self):
        r, hfa, sd = resume.power_ratings(season())
        self.assertEqual(sorted(r, key=lambda t: -r[t]), ["A", "B", "C", "D", "E"])
        self.assertAlmostEqual(sum(r.values()), 0, places=6)

    def test_table(self):
        rows = {r["team"]: r for r in resume.resume_table(season(), top_n=2)}
        self.assertEqual((rows["A"]["wins"], rows["A"]["losses"]), (4, 0))
        self.assertEqual(rows["A"]["sor_rank"], 1)
        self.assertLess(rows["A"]["sor"], rows["B"]["sor"])
        # E faced the best opponents (everyone but itself); A the worst.
        self.assertGreater(rows["E"]["sos"], rows["A"]["sos"])
        self.assertIsNone(rows["E"]["sov"])  # no wins
        self.assertGreater(rows["B"]["sov"], rows["D"]["sov"])
        self.assertEqual(rows["A"]["games_remaining"], 1)

    def test_at_least(self):
        self.assertAlmostEqual(resume._at_least([0.5, 0.5], 1), 0.75)
        self.assertEqual(resume._at_least([0.5], 2), 0.0)


if __name__ == "__main__":
    unittest.main()
