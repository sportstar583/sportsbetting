import unittest

from cfb_stats.weekly import card_markdown


def row(away, home, edge, p4=True, ok=True, total=50.0, open_=None):
    return {"away": away, "home": home, "edge": str(edge), "p4_game": str(p4), "enough_data": str(ok),
            "market_total": str(total), "market_open": "" if open_ is None else str(open_),  # CSV text, e.g. "47"
            "proj_total": str(total + edge), "pick": "OVER" if edge > 0 else "UNDER", "start": "2026-10-10T00:00",
            "injury_adj": "0.0", "matchup_adj": "1.5"}


class WeeklyTests(unittest.TestCase):
    def test_card_markdown(self):
        rows = [row("A", "B", 6), row("C", "D", 4, open_=47), row("E", "F", 2), row("G", "H", 1),
                row("I", "J", -5), row("K", "L", 9, p4=False), row("M", "N", 12, ok=False)]
        md = card_markdown(rows, 6, 2026, 3, "note")
        self.assertIn("| OVER 50.0 | A @ B |", md)
        self.assertIn("| OVER 50.0 (opened 47) | C @ D |", md)
        self.assertIn("| UNDER 50.0 | I @ J |", md)
        self.assertNotIn("G @ H", md)  # 4th-biggest over
        self.assertNotIn("K @ L", md)  # card is Power 4 only
        self.assertNotIn("M @ N", md)  # not enough data


if __name__ == "__main__":
    unittest.main()
