import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import arb_scanner as arb  # noqa: E402

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


def book(title, markets):
    return {"title": title, "last_update": ISO,
            "markets": [{"key": k, "last_update": ISO, "outcomes": o} for k, o in markets.items()]}


def event(*books):
    return {"home_team": "Home", "away_team": "Away", "bookmakers": list(books)}


class FindArbTest(unittest.TestCase):
    def test_h2h_arb_across_books(self):
        ev = event(book("A", {"h2h": [{"name": "Home", "price": 2.10}, {"name": "Away", "price": 1.80}]}),
                   book("B", {"h2h": [{"name": "Home", "price": 1.80}, {"name": "Away", "price": 2.10}]}))
        legs, profit = arb.find_arb(arb.group_markets(ev)["h2h"], 120, NOW)
        self.assertEqual({l: p.book for l, p in legs.items()}, {"Home": "A", "Away": "B"})
        self.assertAlmostEqual(profit, 5.0)

    def test_no_arb_when_implied_over_one(self):
        ev = event(book("A", {"h2h": [{"name": "Home", "price": 1.91}, {"name": "Away", "price": 1.91}]}),
                   book("B", {"h2h": [{"name": "Home", "price": 1.91}, {"name": "Away", "price": 1.91}]}))
        self.assertIsNone(arb.find_arb(arb.group_markets(ev)["h2h"], 120, NOW))

    def test_stale_prices_ignored(self):
        old = (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        b = book("B", {"h2h": [{"name": "Home", "price": 1.80}, {"name": "Away", "price": 2.10}]})
        b["markets"][0]["last_update"] = old
        ev = event(book("A", {"h2h": [{"name": "Home", "price": 2.10}, {"name": "Away", "price": 1.80}]}), b)
        self.assertIsNone(arb.find_arb(arb.group_markets(ev)["h2h"], 120, NOW))

    def test_three_way_market_needs_the_draw(self):
        # Book A lists the draw, book B doesn't; B's prices alone must not form a 2-leg "arb".
        ev = event(book("A", {"h2h": [{"name": "Home", "price": 2.5}, {"name": "Away", "price": 3.0},
                                      {"name": "Draw", "price": 3.2}]}),
                   book("B", {"h2h": [{"name": "Home", "price": 2.6}, {"name": "Away", "price": 3.1}]}))
        groups = arb.group_markets(ev)
        self.assertEqual(groups["h2h"]["_size"], 3)
        stale = dict(groups["h2h"], Draw=[])          # draw price unavailable
        self.assertIsNone(arb.find_arb(stale, 120, NOW))

    def test_spreads_pair_opposite_sides(self):
        ev = event(book("A", {"spreads": [{"name": "Home", "price": 2.05, "point": -3.5},
                                          {"name": "Away", "price": 1.80, "point": 3.5}]}),
                   book("B", {"spreads": [{"name": "Home", "price": 1.80, "point": -3.5},
                                          {"name": "Away", "price": 2.05, "point": 3.5}]}))
        groups = arb.group_markets(ev)
        self.assertEqual(set(groups), {"spreads home -3.5"})
        self.assertIsNotNone(arb.find_arb(groups["spreads home -3.5"], 120, NOW))

    def test_pickem_spread_groups_together(self):
        ev = event(book("A", {"spreads": [{"name": "Home", "price": 2.05, "point": 0.0},
                                          {"name": "Away", "price": 1.80, "point": 0.0}]}))
        self.assertEqual(set(arb.group_markets(ev)), {"spreads home +0"})


class SizeStakesTest(unittest.TestCase):
    def test_equal_returns(self):
        legs = {"Home": arb.Price("A", 2.10, NOW), "Away": arb.Price("B", 2.10, NOW)}
        stakes, profit = arb.size_stakes(legs, 100)
        self.assertEqual(stakes, {"Home": 50, "Away": 50})
        self.assertAlmostEqual(profit, 5.0)


if __name__ == "__main__":
    unittest.main()
