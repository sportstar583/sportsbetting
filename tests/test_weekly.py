import unittest

from cfb_stats import recruiting, tracking
from cfb_stats.totals import best_totals
from cfb_stats.weekly import card_markdown, key_injuries


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
        self.assertIn("| OVER 50.0 | same | A @ B |", md)
        self.assertIn("| OVER 50.0 (opened 47) | same | C @ D |", md)
        self.assertIn("| UNDER 50.0 | same | I @ J |", md)
        self.assertNotIn("G @ H", md)  # 4th-biggest over
        self.assertNotIn("K @ L", md)  # card is Power 4 only
        self.assertNotIn("M @ N", md)  # not enough data
        rows[0]["game_id"], rows[4]["game_id"] = "1", "5"
        md = card_markdown(rows, 6, 2026, 3, "note", spot_list=[
            {"game_id": 1, "spot": "over run", "bet_team": "OVER"},
            {"game_id": 5, "spot": "over run", "bet_team": "OVER"},
            {"game_id": 1, "spot": "sandwich", "bet_team": "A"}])  # spread spots don't touch totals picks
        self.assertIn("over run spot agrees", md.splitlines()[[i for i, x in enumerate(md.splitlines()) if "A @ B" in x][0]])
        self.assertIn("over run spot disagrees: consider passing", [x for x in md.splitlines() if "I @ J" in x][0])

    def test_key_injuries_and_recruiting(self):
        index = {"athlete:7": {"stars": "5", "rating": "0.99", "ranking": "4", "year": "2025"},
                 "recruit:900": {"stars": "4", "rating": "0.93", "ranking": "120", "year": "2024"}}
        rosters = {"A": [{"id": "8", "recruitIds": ["900"]}]}
        inj = [{"team": "A", "player": "Five Star", "player_id": "7", "position": "WR", "status": "Out"},
               {"team": "A", "player": "Four Star", "player_id": "8", "position": "LB", "status": "Doubtful"},
               {"team": "A", "player": "Walk On", "player_id": "9", "position": "RB", "status": "Out"},
               {"team": "A", "player": "Backup QB", "player_id": "10", "position": "QB", "status": "Questionable"},
               {"team": "A", "player": "Starter QB", "player_id": "11", "position": "QB", "status": "Game Time Decision"}]
        recruiting.add_recruiting(inj, index, rosters)
        self.assertEqual((inj[0]["stars"], inj[1]["stars"], inj[2]["stars"]), ("5", "4", ""))
        self.assertEqual(key_injuries(inj, "A"), ["Five Star (WR, 5-star, out)", "Four Star (LB, 4-star, doubtful)",
                                                  "Starter QB (QB, unrated, game time decision)"])
        md = card_markdown([row("A", "B", 6)], 6, 2026, 3, "note", inj)
        self.assertIn("- A: Five Star (WR, 5-star, out)", md)

    def test_best_line_shown(self):
        r = dict(row("A", "B", 6), best_over="49.0", best_over_book="Bovada", best_under="51.0", best_under_book="DK")
        md = card_markdown([r], 6, 2026, 3, "note")
        self.assertIn("| OVER 50.0 | 49.0 (Bovada) | A @ B |", md)

    def test_best_totals(self):
        lr = {"lines": [{"provider": "DK", "overUnder": 50.5}, {"provider": "Bovada", "overUnder": 49.5},
                        {"provider": "ESPN Bet", "overUnder": 51.0}, {"provider": "X", "overUnder": None}]}
        self.assertEqual(best_totals(lr), (49.5, "Bovada", 51.0, "ESPN Bet"))
        self.assertEqual(best_totals({"lines": []}), (None, "", None, ""))

    def test_tracking_grade_and_summary(self):
        over = {"week": "6", "game_id": "1", "side": "OVER", "median_line": "50", "best_line": "49"}
        under = {"week": "6", "game_id": "2", "side": "UNDER", "median_line": "60", "best_line": "61"}
        self.assertEqual(tracking.grade(over, 52, 49.0), (2.0, "P"))  # line rose 2 after the pick; push at 49
        self.assertEqual(tracking.grade(under, 58.5, 70), (1.5, "L"))
        rows = [dict(over, clv=2.0, result="W"), dict(over, clv=1.0, result="L"),  # same pick re-logged Friday
                dict(under, clv=-0.5, result="L"), {"week": "7", "game_id": "3", "side": "OVER"}]
        text = tracking.summary(rows)[0]
        self.assertIn("(2 picks): 1-1-0", text)
        self.assertIn("+0.75 pts", text)
        self.assertIn("beat the close on 1 of 2", text)


if __name__ == "__main__":
    unittest.main()
