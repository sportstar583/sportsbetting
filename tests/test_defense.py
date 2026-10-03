import unittest

from cfb_stats import defense


def box(game_id, team, defenders, passers=(), with_defense=True):
    """A /games/players team entry: defenders [(id, tackles)], passers [(id, 'C/ATT')]."""
    cats = [{"name": "passing", "types": [{"name": "C/ATT", "athletes": [
        {"id": pid, "name": pid, "stat": s} for pid, s in passers]}]}]
    if with_defense:
        cats.append({"name": "defensive", "types": [{"name": "TOT", "athletes": [
            {"id": pid, "name": pid, "stat": str(t)} for pid, t in defenders]}]})
    cats.append({"name": "interceptions", "types": [{"name": "INT", "athletes": [
        {"id": defenders[0][0], "name": defenders[0][0], "stat": "1"}]}]})
    return {"id": game_id, "teams": [{"team": team, "categories": cats}]}


def full_defense(prefix, star_tackles=10):
    """11 defenders; the first is the star."""
    return [(f"{prefix}{i}", star_tackles if i == 0 else 3) for i in range(11)]


class DefenseTests(unittest.TestCase):
    def test_box_rows_and_appearance_filter(self):
        rows = defense.box_defense_rows([box(1, "A", full_defense("a"))])
        self.assertEqual(sum(r["category"] == "defensive" for r in rows), 11)
        self.assertEqual(sum(r["statType"] == "INT" for r in rows), 1)
        # A team whose box has no defensive category is not "boxed": nobody is marked missing.
        played = defense.defenders_in_game([box(2, "B", full_defense("b"), with_defense=False)])
        self.assertEqual(played, {})

    def test_missing_defenders(self):
        prior = [box(1, "A", full_defense("a")), box(2, "A", full_defense("a")),
                 box(1, "B", full_defense("b")), box(2, "B", full_defense("b"))]
        # Week 3: A's star (a0) has no line; a depth player (a10) is also absent. B is complete
        # but its box has no defensive category.
        week = [box(3, "A", [(f"a{i}", 3) for i in range(1, 10)] + [("a11", 3), ("a12", 3)]),
                box(3, "B", full_defense("b"), with_defense=False)]
        games = [{"id": 3, "homeTeam": "A", "awayTeam": "B"}]
        shares, notes = defense.missing_defenders(prior, week, games, min_share=0.03)
        # Star: (10 + 3 INT) / (10 + 3 + 10*3 + 3 INT)... INT weight 3 counted once per game.
        self.assertIn("A", shares)
        self.assertNotIn("B", shares)
        self.assertEqual(len(notes[3]), 2)  # star and a10 both regulars with 3%+ share
        self.assertTrue(any("a0" in n for n in notes[3]))
        # A rotational player who appeared in only 1 of 2 games is not counted.
        prior2 = prior + [box(0, "A", [(f"a{i}", 3) for i in range(1, 11)])]  # a0 absent in game 0
        shares2, _ = defense.missing_defenders(prior2, week, games, min_share=0.03, min_appearances=0.7)
        self.assertLess(shares2.get("A", 0), shares["A"])

    def test_starting_qbs(self):
        prior = [box(1, "A", full_defense("a"), passers=[("qb1", "20/30"), ("qb2", "2/3")]),
                 box(2, "A", full_defense("a"), passers=[("qb1", "15/25")])]
        self.assertEqual(defense.starting_qbs(prior), {"A": "qb1"})
        threw = defense.qb_played([box(3, "A", full_defense("a"), passers=[("qb2", "18/28"), ("qb1", "1/2")])])
        self.assertEqual(threw[(3, "A")], {"qb2"})

    def test_fit_scale(self):
        rows = [{"adj_unit": u, "actual_total": 50 + 0.5 * u + 1.0, "proj_healthy": 50.0} for u in (0, 10, 20, 30)]
        scale, bias = defense.fit_scale(rows)
        self.assertAlmostEqual(scale, 0.5)
        self.assertAlmostEqual(bias, 1.0)


if __name__ == "__main__":
    unittest.main()
